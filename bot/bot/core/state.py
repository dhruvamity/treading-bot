"""State store (P2 task 2): orders keyed by clientId (+ venue orderId), fills, positions, balances, and an
event-sourced log in SQLite (WAL) so state can be replayed after a crash.

Reconciliation (on start and every 5 min): compare venue open orders / positions with local state.
- live venue order we don't know  -> cancel, reason `reconcile_unknown`
- local open order missing at venue -> mark CANCELED (reason `reconcile_missing`)
- position mismatch               -> trust the venue, log, alert
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal
from pathlib import Path
from typing import Any

import orjson

from bot.common.time import now_us
from bot.venues.base import Fill, OrderRequest, OrderState, OrderStatus, Position, Side, Venue

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts_us INTEGER, venue TEXT, kind TEXT,
    client_id TEXT, session TEXT, payload TEXT);
CREATE TABLE IF NOT EXISTS orders (client_id TEXT PRIMARY KEY, venue TEXT, base TEXT, side TEXT, price TEXT,
    size TEXT, tif TEXT, reduce_only INTEGER, tag TEXT, status TEXT, venue_order_id TEXT, filled TEXT,
    avg_px TEXT, reject_reason TEXT, reason TEXT, session TEXT, created_us INTEGER, updated_us INTEGER);
CREATE TABLE IF NOT EXISTS fills (trade_id TEXT, venue TEXT, base TEXT, client_id TEXT, side TEXT, price TEXT,
    size TEXT, fee TEXT, is_maker INTEGER, liquidation INTEGER, ts_us INTEGER, tag TEXT, session TEXT,
    PRIMARY KEY (venue, trade_id));
CREATE TABLE IF NOT EXISTS funding (venue TEXT, base TEXT, ts_us INTEGER, rate_h TEXT, position TEXT, payment TEXT,
    PRIMARY KEY (venue, base, ts_us));
CREATE TABLE IF NOT EXISTS positions (venue TEXT, base TEXT, size TEXT, entry TEXT, mark TEXT, ts_us INTEGER,
    PRIMARY KEY (venue, base));
CREATE TABLE IF NOT EXISTS points (venue TEXT, week TEXT, points REAL, ts_us INTEGER, PRIMARY KEY (venue, week));
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
CREATE INDEX IF NOT EXISTS ev_ts ON events(ts_us);
CREATE INDEX IF NOT EXISTS fills_ts ON fills(ts_us);
"""


@dataclass
class LocalOrder:
    req: OrderRequest
    status: OrderStatus = OrderStatus.PENDING_NEW
    venue_order_id: str | None = None
    filled: Decimal = Decimal(0)
    avg_px: Decimal | None = None
    reject_reason: str | None = None
    session: str = ""
    created_us: int = 0
    updated_us: int = 0

    @property
    def open(self) -> bool:
        return not self.status.is_terminal

    @property
    def remaining(self) -> Decimal:
        return max(Decimal(0), self.req.size - self.filled)


PENDING_GRACE_S = 10.0   # reconcile leaves an unacknowledged order alone this long after it was sent


@dataclass
class ReconcileReport:
    unknown_live: list[OrderState] = field(default_factory=list)
    missing_local: list[str] = field(default_factory=list)
    position_mismatch: list[tuple[str, Decimal, Decimal]] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not (self.unknown_live or self.missing_local or self.position_mismatch)


class StateStore:
    def __init__(self, db_path: Path | str = ":memory:") -> None:
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
        self._db.executescript(SCHEMA)
        self._lock = threading.Lock()
        self.orders: dict[str, LocalOrder] = {}
        self.by_venue_id: dict[tuple[Venue, str], str] = {}
        self.positions: dict[tuple[Venue, str], Decimal] = {}
        self.entry: dict[tuple[Venue, str], Decimal] = {}
        self.fills: list[Fill] = []
        self._fill_ids: set[tuple[str, str]] = set()
        self._load()

    # ---------------------------------------------------------------- persistence
    def _exec(self, sql: str, args: Sequence[Any] = ()) -> None:
        with self._lock:
            self._db.execute(sql, args)

    def event(self, kind: str, *, venue: str | None = None, client_id: str | None = None, session: str = "",
              ts_us: int | None = None, **payload: Any) -> None:
        self._exec("INSERT INTO events (ts_us, venue, kind, client_id, session, payload) VALUES (?,?,?,?,?,?)",
                   (ts_us or now_us(), venue, kind, client_id, session, orjson.dumps(payload, default=str).decode()))

    def _save_order(self, o: LocalOrder) -> None:
        r = o.req
        self._exec("INSERT OR REPLACE INTO orders VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            r.client_id, r.venue.value, r.base, r.side.value, str(r.price), str(r.size), r.tif.value,
            int(r.reduce_only), r.tag, o.status.value, o.venue_order_id, str(o.filled),
            str(o.avg_px) if o.avg_px is not None else None, o.reject_reason, r.reason, o.session, o.created_us,
            o.updated_us))

    def _load(self) -> None:
        from bot.venues.base import TIF

        for row in self._db.execute("SELECT * FROM orders WHERE status NOT IN ('FILLED','CANCELED','REJECTED','EXPIRED')"):
            (cid, venue, base, side, price, size, tif, ro, tag, status, void, filled, avg, rej, reason, sess,
             created, updated) = row
            req = OrderRequest(Venue(venue), base, Side(side), Decimal(price), Decimal(size), TIF(tif), bool(ro), cid,
                               tag, reason or "")
            o = LocalOrder(req, OrderStatus(status), void, Decimal(filled), Decimal(avg) if avg else None, rej, sess,
                           created, updated)
            self.orders[cid] = o
            if void:
                self.by_venue_id[(req.venue, void)] = cid
        for venue, base, size, entry, *_ in self._db.execute("SELECT * FROM positions"):
            self.positions[(Venue(venue), base)] = Decimal(size)
            self.entry[(Venue(venue), base)] = Decimal(entry)
        for venue, tid in self._db.execute("SELECT venue, trade_id FROM fills"):
            self._fill_ids.add((venue, tid))

    # ---------------------------------------------------------------- orders
    def on_intent(self, req: OrderRequest, session: str = "") -> LocalOrder:
        ts = now_us()
        o = LocalOrder(req, OrderStatus.PENDING_NEW, session=session, created_us=ts, updated_us=ts)
        self.orders[req.client_id] = o
        self._save_order(o)
        self.event("intent", venue=req.venue.value, client_id=req.client_id, session=session, side=req.side.value,
                   price=str(req.price), size=str(req.size), tif=req.tif.value, tag=req.tag, reason=req.reason)
        return o

    def on_modify_intent(self, client_id: str, price: Decimal, size: Decimal, reason: str) -> None:
        o = self.orders.get(client_id)
        if o is None:
            return
        o.req = replace(o.req, price=price, size=size, reason=reason)
        o.updated_us = now_us()
        self._save_order(o)
        self.event("modify_intent", venue=o.req.venue.value, client_id=client_id, price=str(price), size=str(size),
                   reason=reason)

    def on_update(self, st: OrderState) -> LocalOrder | None:
        cid = st.client_id
        if not cid and st.venue_order_id and st.venue is not None:
            cid = self.by_venue_id.get((st.venue, st.venue_order_id), "")
        o = self.orders.get(cid) if cid else None
        if o is None:
            self.event("update_unknown", venue=st.venue.value if st.venue else None, client_id=cid,
                       status=st.status.value, venue_order_id=st.venue_order_id)
            return None
        if st.venue_order_id:
            o.venue_order_id = st.venue_order_id
            self.by_venue_id[(o.req.venue, st.venue_order_id)] = cid
        # Never regress a terminal state (out-of-order frames).
        if not o.status.is_terminal or st.status.is_terminal:
            o.status = st.status
        if st.filled_size and st.filled_size > o.filled:
            o.filled = st.filled_size
        if st.avg_fill_price is not None:
            o.avg_px = st.avg_fill_price
        if st.reject_reason:
            o.reject_reason = st.reject_reason
        o.updated_us = st.ts_us or now_us()
        self._save_order(o)
        self.event("update", venue=o.req.venue.value, client_id=cid, status=o.status.value,
                   filled=str(o.filled), reject=o.reject_reason)
        return o

    def open_orders(self, venue: Venue | None = None, base: str | None = None) -> list[LocalOrder]:
        return [o for o in self.orders.values() if o.open and (venue is None or o.req.venue is venue)
                and (base is None or o.req.base == base)]

    def gc(self, keep_terminal: int = 2000) -> None:
        term = [k for k, o in self.orders.items() if not o.open]
        for k in term[:-keep_terminal] if len(term) > keep_terminal else []:
            del self.orders[k]

    # ---------------------------------------------------------------- fills and positions
    def on_fill(self, f: Fill, session: str = "", tag: str = "") -> bool:
        key = (f.venue.value, f.trade_id)
        if key in self._fill_ids:
            return False
        self._fill_ids.add(key)
        self.fills.append(f)
        pk = (f.venue, f.base)
        old = self.positions.get(pk, Decimal(0))
        signed = f.size * f.side.sign
        new = old + signed
        # Average entry: extend when adding in the same direction, reset when flipping.
        if old == 0 or (old > 0) == (signed > 0):
            prev = self.entry.get(pk, f.price)
            self.entry[pk] = (abs(old) * prev + abs(signed) * f.price) / abs(new) if new != 0 else f.price
        elif (old > 0) != (new > 0) and new != 0:
            self.entry[pk] = f.price
        self.positions[pk] = new
        o = self.orders.get(f.client_id)
        t = tag or (o.req.tag if o else "")
        self._exec("INSERT OR IGNORE INTO fills VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            f.trade_id, f.venue.value, f.base, f.client_id, f.side.value, str(f.price), str(f.size), str(f.fee),
            int(f.is_maker), int(f.liquidation), f.ts_us, t, session))
        self._exec("INSERT OR REPLACE INTO positions VALUES (?,?,?,?,?,?)", (
            f.venue.value, f.base, str(new), str(self.entry.get(pk, f.price)), str(f.price), f.ts_us))
        return True

    def position(self, venue: Venue, base: str) -> Decimal:
        return self.positions.get((venue, base.upper()), Decimal(0))

    def set_position(self, venue: Venue, base: str, size: Decimal, entry: Decimal | None = None) -> None:
        self.positions[(venue, base)] = size
        if entry is not None:
            self.entry[(venue, base)] = entry
        self._exec("INSERT OR REPLACE INTO positions VALUES (?,?,?,?,?,?)", (
            venue.value, base, str(size), str(entry or 0), "", now_us()))

    def on_funding(self, venue: Venue, base: str, ts_us: int, rate_h: Decimal, position: Decimal,
                   payment: Decimal) -> None:
        self._exec("INSERT OR IGNORE INTO funding VALUES (?,?,?,?,?,?)",
                   (venue.value, base, ts_us, str(rate_h), str(position), str(payment)))

    # ---------------------------------------------------------------- points (S4 study)
    def add_points(self, venue: str, week: str, points: float) -> None:
        self._exec("INSERT OR REPLACE INTO points VALUES (?,?,?,?)", (venue, week, points, now_us()))

    def points(self) -> list[tuple[str, str, float]]:
        return [(v, w, float(p)) for v, w, p, _ in self._db.execute("SELECT * FROM points ORDER BY week")]

    def kv_set(self, k: str, v: str) -> None:
        self._exec("INSERT OR REPLACE INTO kv VALUES (?,?)", (k, v))

    def kv_get(self, k: str) -> str | None:
        row = self._db.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return row[0] if row else None

    # ---------------------------------------------------------------- reconciliation
    def reconcile(self, venue: Venue, venue_open: Sequence[OrderState], venue_positions: Sequence[Position]) -> ReconcileReport:
        rep = ReconcileReport()
        seen: set[str] = set()
        for st in venue_open:
            cid = st.client_id or (self.by_venue_id.get((venue, st.venue_order_id or ""), ""))
            o = self.orders.get(cid) if cid else None
            if o is None or not o.open:
                rep.unknown_live.append(st)
            else:
                seen.add(cid)
                self.on_update(st)
        now = now_us()
        for o in self.open_orders(venue):
            if o.req.client_id in seen:
                continue
            # A placement (or a cancel-replace modify) can still be on its way for a few seconds; past that, an order
            # the venue does not list is gone, whether or not its ack ever arrived. Skipping PENDING_NEW for good
            # left orders whose placement errored as permanent ghosts: the strategy kept modifying them by clientId
            # ("order could not be found") and never placed a real quote again.
            if o.status is OrderStatus.PENDING_NEW and now - max(o.created_us, o.updated_us) < PENDING_GRACE_S * 1e6:
                continue
            rep.missing_local.append(o.req.client_id)
            o.status = OrderStatus.CANCELED
            o.reject_reason = "reconcile_missing"
            self._save_order(o)
        vpos = {p.base: p for p in venue_positions}
        bases = {b for (v, b) in self.positions if v is venue} | set(vpos)
        for b in bases:
            local = self.position(venue, b)
            remote = vpos[b].size if b in vpos else Decimal(0)
            if local != remote:
                rep.position_mismatch.append((b, local, remote))
                self.set_position(venue, b, remote, vpos[b].entry_price if b in vpos else None)
        self.event("reconcile", venue=venue.value, unknown=len(rep.unknown_live), missing=len(rep.missing_local),
                   pos_mismatch=[(b, str(a), str(c)) for b, a, c in rep.position_mismatch])
        return rep

    def close(self) -> None:
        self._db.close()
