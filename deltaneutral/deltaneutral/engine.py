"""The bot: one cycle a day of the two-market delta-neutral position, on any two venues that look like the arbitrage's
`TradeVenue` (paper ones on the real books, or the live ones).

A cycle:
1. flat -> opening, at the day's open time (`open_ny`, New York time): the size is the largest both venues' margins
   allow now (plan.size; Arcus asks 1.5 times the margin while the stock market is closed, so an off-hours open is
   smaller)
   and the way round is the one the funding pays (`way`).
2. opening: Arcus's two legs work toward their size (`arcus_maker`: limit orders at the best price, then after
   `maker_wait_s` the rest is taken); Lighter's legs follow what Arcus HOLDS, by taker orders (Lighter charges none).
3. open: every loop Lighter is made the mirror of Arcus again, so whatever changes Arcus's position (a partial fill,
   a liquidation, the owner's own order, `offhours cut` at the stock market's close) changes Lighter's the same way.
   A venue whose room to liquidation falls under `guard` closes everything early.
4. closing, `hold_h` hours after the open: the same two steps towards nothing, reduce-only.
5. flat: each venue's money is read and the bot says exactly how much to move from which venue to which so both hold
   the same (`min_transfer_usd`). With `wait_transfer` it opens nothing while they are uneven, and says it again every
   `remind_min` minutes. Paper money is moved by the bot itself.

The state is written after every step and read at start: a restart picks up the cycle where it was (positions are
always read from the venues, never assumed).
"""

from __future__ import annotations

import json
import math
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from arbitrage.exec.venue import BUY, SELL, Spec, Top, TradeVenue, VenueDown

from . import plan as P
from .plan import ARCUS, LIGHTER, VENUES
from .series import arcus_open, blocked, next_open

HOUR = 3600
DAY = 86_400
SKIP_LEAD_S = 1800.0                # a position is closed this long before a skip window begins


@dataclass
class Settings:
    """The owner's dials (deltaneutral/settings.json, `deltaneutral set`, Telegram /dn_set)."""
    x: str = "USO"                  # the pair: X and Y (`deltaneutral pair X Y`). USO: Arcus's highest leverage
    y: str = "ORCL"                 # (20x) and its funding pays longs there; ORCL: Arcus's lowest OI at 10x
    open_ny: float = 10.0           # the daily open, New York time (10.0 = 10:00, half an hour after the cash open)
    hold_h: float = 24.0            # hours a position is held before it is closed and opened again
    margin_use: float = 0.9         # share of the poorer venue's money its initial margin may use
    max_leverage: float = 0.0       # each venue's (X + Y dollars) / its money at most this; 0 = the venues' highest
    max_notional_usd: float = 0.0   # dollars of each market at most; 0 = no cap (a small number for a first live run)
    way: str = "auto"               # "auto": the way the funding pays now; "+1": Arcus long X short Y; "-1": reverse
    offhours: str = "keep"          # "keep": a position stays as opened when the stock market closes (Arcus keeps it);
                                    # "cut": cut to Arcus's off-hours size at the close, grown back at the reopen
    guard: float = 0.25             # close early when a venue's (equity - maintenance) / maintenance is under this
    arcus_maker: bool = True        # Arcus's legs rest limit orders (no fee) before taking
    maker_wait_s: float = 600.0     # ... for this long, then the rest is taken
    requote_s: float = 20.0         # a resting order off the best price is moved at most this often
    max_cross_bp: float = 30.0      # a taker order goes no further than this from the best price
    min_transfer_usd: float = 1.0   # below this the money counts as even
    wait_transfer: bool = False     # True: open nothing while the money is uneven (the transfer has not been made)
    remind_min: float = 60.0
    weekends: bool = True           # open on Saturdays and Sundays too (Arcus then has its off-hours margin)
    open_timeout_s: float = 1800.0  # an opening that has not finished by then keeps what it has
    skip: list[str] = field(default_factory=list)   # report dates ("YYYY-MM-DD", New York) of either market: nothing
                                    # is held from 16:00 the day before to 16:00 the day after (`deltaneutral skip`)

    @staticmethod
    def load(path: Path) -> Settings:
        s = Settings()
        try:
            raw = json.loads(path.read_text())
        except (OSError, ValueError):
            return s
        for f in fields(Settings):
            if f.name in raw:
                cur = getattr(s, f.name)
                v = raw[f.name]
                setattr(s, f.name, bool(v) if isinstance(cur, bool) else
                        [str(x) for x in v] if isinstance(cur, list) else type(cur)(v))
        return s


# What `deltaneutral set` accepts: name -> (lowest, highest) for numbers, or the words allowed
ADJUSTABLE: dict[str, tuple[float, float] | tuple[str, ...]] = {
    "open_ny": (0, 23.99), "hold_h": (1, 720), "margin_use": (0.1, 0.95), "max_leverage": (0, 50),
    "max_notional_usd": (0, 1e7), "guard": (0, 5), "maker_wait_s": (0, 7200), "requote_s": (2, 600),
    "max_cross_bp": (1, 200), "min_transfer_usd": (0, 1e6), "remind_min": (1, 1440), "open_timeout_s": (60, 7200),
    "way": ("auto", "+1", "-1"), "offhours": ("keep", "cut"), "arcus_maker": (0, 1), "wait_transfer": (0, 1),
    "weekends": (0, 1),
}


def set_value(path: Path, name: str, text: str) -> Any:
    """Store one setting after checking it. Raises ValueError with what to type."""
    if name not in ADJUSTABLE:
        raise ValueError(f"unknown setting {name!r}: one of {', '.join(sorted(ADJUSTABLE))}")
    rng = ADJUSTABLE[name]
    word = text.strip().lower()
    v: Any
    if isinstance(rng[0], str):
        if word not in rng:
            raise ValueError(f"{name} is one of: {', '.join(str(x) for x in rng)}")
        v = word
    else:
        lo, hi = float(rng[0]), float(rng[1])
        try:
            v = 0.0 if word in ("off", "none", "max", "auto") and name in ("max_leverage", "max_notional_usd") \
                else 1.0 if word in ("on", "yes", "true") else 0.0 if word in ("off", "no", "false") else float(word)
        except ValueError:
            raise ValueError(f"{name}: {text!r} is not a number") from None
        if not lo <= v <= hi:
            raise ValueError(f"{name} must be between {lo:g} and {hi:g}")
        if name in ("arcus_maker", "wait_transfer", "weekends"):
            v = bool(v)
    try:
        cur = json.loads(path.read_text())
    except (OSError, ValueError):
        cur = {}
    cur[name] = v
    path.write_text(json.dumps(cur, indent=1) + "\n")
    return v


def set_skip(path: Path, days: list[str], clear: bool = False) -> list[str]:
    """Add report dates to the skip list (or clear it). Each must be YYYY-MM-DD."""
    import datetime as dt

    for d in days:
        try:
            dt.date.fromisoformat(d)
        except ValueError:
            raise ValueError(f"{d!r} is not a date: YYYY-MM-DD, New York") from None
    try:
        cur = json.loads(path.read_text())
    except (OSError, ValueError):
        cur = {}
    have = [] if clear else list(cur.get("skip") or [])
    cur["skip"] = sorted(set(have) | set(days))
    path.write_text(json.dumps(cur, indent=1) + "\n")
    return cur["skip"]


def set_pair(path: Path, x: str, y: str) -> tuple[str, str]:
    x, y = x.upper(), y.upper()
    if x == y:
        raise ValueError("the two markets must differ")
    try:
        cur = json.loads(path.read_text())
    except (OSError, ValueError):
        cur = {}
    cur["x"], cur["y"] = x, y
    path.write_text(json.dumps(cur, indent=1) + "\n")
    return x, y


@dataclass
class State:
    phase: str = "flat"                               # flat | opening | open | closing
    cycle: int = 0
    x: str = ""
    y: str = ""
    way: int = 0
    target: dict[str, float] = field(default_factory=dict)       # Arcus's units wanted now, per market (+ long)
    full: dict[str, float] = field(default_factory=dict)         # ... at the open (what "cut" grows back to)
    off_size: bool = False                            # the position is at Arcus's off-hours size
    opened_at: float = 0.0
    close_at: float = 0.0
    next_open: float = 0.0
    phase_since: float = 0.0
    why: str = ""                                     # why it is closing
    start_equity: dict[str, float] = field(default_factory=dict)
    volume: float = 0.0                               # dollars traded this cycle, both venues
    last_pos: dict[str, float] = field(default_factory=dict)     # "venue:symbol" -> units at the last step
    orders: dict[str, dict[str, Any]] = field(default_factory=dict)   # Arcus's resting order per market
    paused: bool = False
    transfer: dict[str, Any] = field(default_factory=dict)       # the last transfer asked for
    reminded: float = 0.0
    min_room: float = math.inf
    funding: dict[str, float] = field(default_factory=dict)      # paper: funding credited this cycle, per venue

    @staticmethod
    def load(d: dict[str, Any]) -> State:
        names = {f.name for f in fields(State)}
        st = State(**{k: v for k, v in d.items() if k in names})
        if st.min_room is None:
            st.min_room = math.inf
        return st


@dataclass
class Snapshot:
    """One read of both venues."""
    tops: dict[str, dict[str, Top]]
    pos: dict[str, dict[str, float]]
    equity: dict[str, float]
    rules: dict[str, dict[str, P.Rules]]          # venue -> symbol -> rules
    off_hours: bool

    def mid(self, v: str, s: str) -> float:
        return self.tops[v][s].mid


class Store:
    """state/<mode>-state.json, <mode>-events.jsonl, <mode>-cycles.jsonl, and a control file for commands."""

    def __init__(self, root: Path, mode: str, echo: Callable[[str], None] | None = print,
                 alert: Callable[[str], None] | None = None) -> None:
        self.root, self.mode, self.echo, self.alert = root, mode, echo, alert
        root.mkdir(parents=True, exist_ok=True)
        self.state = root / f"{mode}-state.json"
        self.events = root / f"{mode}-events.jsonl"
        self.cycles = root / f"{mode}-cycles.jsonl"
        self.control = root / f"{mode}-control.json"
        self._last_down = 0.0

    def save(self, st: State) -> None:
        tmp = self.state.with_suffix(".tmp")
        d = asdict(st)
        d["min_room"] = None if not math.isfinite(st.min_room) else st.min_room
        tmp.write_text(json.dumps(d, indent=1))
        os.replace(tmp, self.state)

    def load(self) -> State | None:
        try:
            return State.load(json.loads(self.state.read_text()))
        except (OSError, ValueError, TypeError):
            return None

    def commands(self) -> dict[str, Any]:
        try:
            cmd = json.loads(self.control.read_text())
            self.control.unlink()
            return cmd if isinstance(cmd, dict) else {}
        except (OSError, ValueError):
            return {}

    def send(self, **cmd: Any) -> None:
        try:
            cur = json.loads(self.control.read_text())
        except (OSError, ValueError):
            cur = {}
        self.control.write_text(json.dumps({**cur, **cmd}))

    ALERTS = ("opened", "closed", "transfer", "remind", "guard", "resized", "stopped", "started", "refused",
              "too_small", "venue_down", "uneven")

    def event(self, kind: str, text: str, **data: Any) -> None:
        now = time.time()
        if kind == "venue_down":
            if now - self._last_down < 300:
                return
            self._last_down = now
        with self.events.open("a") as fh:
            fh.write(json.dumps({"ts": now, "kind": kind, "text": text, **data}, default=str) + "\n")
        if self.echo:
            self.echo(f"{time.strftime('%H:%M:%S')} {'PAPER ' if self.mode == 'paper' else ''}{text}")
        if self.alert and kind in self.ALERTS:
            self.alert(("PAPER · " if self.mode == "paper" else "") + text)

    def cycle(self, row: dict[str, Any]) -> None:
        with self.cycles.open("a") as fh:
            fh.write(json.dumps(row, default=str) + "\n")

    def tail(self, path: Path, n: int) -> list[dict[str, Any]]:
        try:
            return [json.loads(x) for x in path.read_text().splitlines()[-n:]]
        except (OSError, ValueError):
            return []


Reader = Callable[[], Awaitable[tuple[dict[str, float], dict[str, dict[str, P.Rules]], bool]]]


class Engine:
    """`venues`: {"arcus": TradeVenue, "lighter": TradeVenue}. `reader()` returns (each venue's equity, each venue's
    rules per market, whether Arcus's stock markets are closed now). `mover(amount, source, target)`, paper only,
    moves pretend money between the venues."""

    def __init__(self, venues: dict[str, TradeVenue], reader: Reader, settings: Callable[[], Settings], store: Store,
                 state: State | None = None, *, clock: Callable[[], float] = time.time,
                 mover: Callable[[float, str, str], None] | None = None,
                 names: dict[str, str] | None = None) -> None:
        self.v, self.reader, self.settings, self.store = venues, reader, settings, store
        self.st = state or State()
        self.clock = clock
        self.mover = mover
        self.names = names or {ARCUS: "Arcus", LIGHTER: "Lighter"}
        self.specs: dict[str, dict[str, Spec]] = {ARCUS: {}, LIGHTER: {}}
        self.snap: Snapshot | None = None

    # ------------------------------------------------------------------ reading
    def _say(self, kind: str, text: str, **data: Any) -> None:
        self.store.event(kind, text, **data)

    async def _ensure(self, syms: tuple[str, ...]) -> None:
        for v in VENUES:
            for s in syms:
                if s not in self.specs[v]:
                    self.specs[v][s] = await self.v[v].start(s)

    async def read(self, syms: tuple[str, ...]) -> Snapshot | None:
        await self._ensure(syms)
        equity, rules, off = await self.reader()
        tops: dict[str, dict[str, Top]] = {v: {} for v in VENUES}
        pos: dict[str, dict[str, float]] = {v: {} for v in VENUES}
        for v in VENUES:
            for s in syms:
                t = await self.v[v].top(s)
                p = await self.v[v].position(s)
                if t is None or p is None or not (t.bid > 0 and t.ask > t.bid):
                    return None
                tops[v][s], pos[v][s] = t, p
        return Snapshot(tops, pos, equity, rules, off)

    def symbols(self) -> tuple[str, ...]:
        if self.st.phase != "flat" and self.st.x:
            return self.st.x, self.st.y
        s = self.settings()
        return s.x, s.y

    # ------------------------------------------------------------------ the loop
    async def step(self) -> None:
        cmd = self.store.commands()
        if cmd.get("pause"):
            self.st.paused = True
            self._say("pause", "paused: no new cycle opens (an open one is held to its close)")
        if cmd.get("resume"):
            self.st.paused = False
            self._say("resume", "resumed")
        syms = self.symbols()
        try:
            snap = await self.read(syms)
        except VenueDown as e:
            self._say("venue_down", f"a venue is not answering: {e}")
            return
        if snap is None:
            self._say("venue_down", "no price yet on one of the books; trying again")
            return
        self.snap = snap
        self._count_volume(snap)
        if cmd.get("close") and self.st.phase in ("opening", "open"):
            self._begin_close("asked to close")
        now = self.clock()
        if self.st.phase == "flat":
            await self._flat(snap, now)
        else:
            await self._work(snap, now)
        self.store.save(self.st)

    def _count_volume(self, snap: Snapshot) -> None:
        for v in VENUES:
            for s, p in snap.pos[v].items():
                k = f"{v}:{s}"
                was = self.st.last_pos.get(k)
                if was is not None and p != was:
                    self.st.volume += abs(p - was) * snap.mid(v, s)
                self.st.last_pos[k] = p

    # ------------------------------------------------------------------ flat
    def _scheduled(self, after: float, s: Settings) -> float:
        """The first daily open time at or after `after`."""
        return next_open(after, s.open_ny, s.weekends)

    async def _flat(self, snap: Snapshot, now: float) -> None:
        s = self.settings()
        if self.st.next_open <= 0:
            self.st.next_open = self._scheduled(now, s)
            self._say("waiting", f"flat; the first cycle opens at {_utc(self.st.next_open)}")
        held = {f"{v}:{k}": p for v in VENUES for k, p in snap.pos[v].items() if p}
        if held:            # flat by our count but something is held (a restart mid-close, a manual order): close it
            self.st.x, self.st.y = s.x, s.y
            self._begin_close("found positions while flat")
            return
        tr = P.transfer(snap.equity, s.min_transfer_usd)
        if tr.amount > 0 and s.wait_transfer:
            if now - self.st.reminded >= s.remind_min * 60:
                self.st.reminded = now
                self._say("remind", "waiting for the money to be even before the next cycle: "
                          + tr.text(self.names))
            return
        if self.st.paused or now < self.st.next_open:
            return
        hit = blocked(now, now + s.hold_h * HOUR, s.skip)
        if hit is not None:
            self.st.next_open = self._scheduled(hit[1], s)
            self._say("skipped", f"no cycle now: a skip date is within the next {s.hold_h:g} hours; next open "
                      f"{_utc(self.st.next_open)}")
            return
        await self._open(snap, now, s)

    async def _open(self, snap: Snapshot, now: float, s: Settings) -> None:
        x, y = s.x, s.y
        rx = {v: snap.rules[v][x] for v in VENUES}
        ry = {v: snap.rules[v][y] for v in VENUES}
        sz = P.size(snap.equity, rx, ry, snap.off_hours, s.margin_use, s.max_leverage)
        nx, ny = sz.nx, sz.ny
        if s.max_notional_usd > 0:
            nx, ny = min(nx, s.max_notional_usd), min(ny, s.max_notional_usd)
        way = await self._way(s, snap, x, y)
        ux, uy = self._units(x, nx, snap), self._units(y, ny, snap)
        self.st.next_open = self._scheduled(now + 1, s)
        if ux <= 0 or uy <= 0:
            self._say("too_small", f"{x}/{y}: the money allows ${nx:,.2f} a market, under a venue's smallest order; "
                      f"nothing opened. Next try {_utc(self.st.next_open)}")
            return
        for v in VENUES:            # each market's leverage setting at its highest: the account's margin decides
            for sym, r in ((x, rx[v]), (y, ry[v])):
                try:
                    await self.v[v].set_leverage(sym, r.max_leverage(False))
                except VenueDown:
                    raise
                except Exception as e:      # noqa: BLE001  a refused setting is reported, the open goes on
                    self._say("refused", f"{self.names[v]} refused leverage {r.max_leverage(False):.0f}x on {sym}: "
                              f"{str(e)[:120]}")
        sd = P.sides(way)
        st = self.st
        st.phase, st.cycle, st.x, st.y, st.way = "opening", st.cycle + 1, x, y, way
        st.target = {x: sd[ARCUS]["x"] * ux, y: sd[ARCUS]["y"] * uy}
        st.full = dict(st.target)
        st.off_size = snap.off_hours
        st.opened_at, st.close_at, st.phase_since = now, now + s.hold_h * HOUR, now
        st.start_equity = dict(snap.equity)
        st.volume, st.why, st.min_room, st.orders, st.funding = 0.0, "", math.inf, {}, {}
        lev = {v: sz.leverage[v] for v in VENUES}
        self._say("opening", f"cycle {st.cycle}: opening {x}/{y}, ${nx:,.2f} of {x} and ${ny:,.2f} of {y} on each "
                  f"venue ({self._ways(way, x, y)}); leverage Arcus {lev[ARCUS]:.1f}x, Lighter {lev[LIGHTER]:.1f}x"
                  + ("; the stock market is closed: Arcus's off-hours margin sets the size" if snap.off_hours else ""),
                  cycle=st.cycle, nx=nx, ny=ny, way=way)

    def _ways(self, way: int, x: str, y: str) -> str:
        sd = P.sides(way)
        word = {1: "long", -1: "short"}
        return (f"Arcus {word[sd[ARCUS]['x']]} {x} / {word[sd[ARCUS]['y']]} {y}, "
                f"Lighter {word[sd[LIGHTER]['x']]} {x} / {word[sd[LIGHTER]['y']]} {y}")

    async def _way(self, s: Settings, snap: Snapshot, x: str, y: str) -> int:
        """`way` +1 / -1, or "auto": the way the last 24 hours of funding paid (the rates of the moment when those
        cannot be read)."""
        if s.way == "+1":
            return 1
        if s.way == "-1":
            return -1
        rates: dict[tuple[str, str], float] = {}
        tr = getattr(self.reader, "trailing", None)
        if callable(tr):
            try:
                rates = await tr((x, y))
            except (VenueDown, RuntimeError, OSError, KeyError) as e:
                self._say("venue_down", f"the funding history could not be read ({e}); the rates of the moment decide")
        fr = getattr(self.reader, "funding", None)
        if not rates and callable(fr):
            rates = fr()
        if rates:
            dx = rates.get((LIGHTER, x), 0.0) - rates.get((ARCUS, x), 0.0)
            dy = rates.get((LIGHTER, y), 0.0) - rates.get((ARCUS, y), 0.0)
            return P.way_from(dx, dy)
        return 1

    def _units(self, sym: str, usd: float, snap: Snapshot) -> float:
        """Units for `usd` dollars, on the coarser of the two venues' size steps, 0 under either venue's smallest."""
        px = snap.mid(LIGHTER, sym)
        step = max(self.specs[v][sym].step for v in VENUES)
        u = math.floor(usd / px / step + 1e-9) * step if step > 0 else usd / px
        for v in VENUES:
            if not self.specs[v][sym].tradeable(u, snap.mid(v, sym)):
                return 0.0
        return u

    # ------------------------------------------------------------------ working the legs
    def _begin_close(self, why: str) -> None:
        st = self.st
        st.phase, st.why, st.phase_since = "closing", why, self.clock()
        st.target = {k: 0.0 for k in (st.x, st.y)}
        self._say("closing", f"cycle {st.cycle}: closing {st.x}/{st.y} ({why})")

    async def _work(self, snap: Snapshot, now: float) -> None:
        st, s = self.st, self.settings()
        closing = st.phase == "closing"
        if not closing:
            self._watch(snap, now, s)
            if st.phase == "closing":
                closing = True
        late = now - st.phase_since >= (0 if st.why == "guard" else s.maker_wait_s) or not s.arcus_maker
        done = True
        for sym in (st.x, st.y):
            ok = await self._arcus_leg(sym, st.target.get(sym, 0.0), snap, now, s, late, closing)
            done = done and ok
        for sym in (st.x, st.y):            # Lighter mirrors what Arcus holds now, after this step's Arcus orders
            pa = await self.v[ARCUS].position(sym)
            if pa is None:
                done = False
                continue
            ok = await self._lighter_leg(sym, -pa, snap, s, closing)
            done = done and ok
        if st.phase == "opening":
            if done:
                st.phase, st.phase_since = "open", now
                self._opened(snap)
            elif now - st.opened_at >= s.open_timeout_s:
                held = {sym: await self.v[ARCUS].position(sym) or 0.0 for sym in (st.x, st.y)}
                st.target = held
                st.full = dict(held)
                st.phase, st.phase_since = "open", now
                await self._cancel_all_makers()
                self._opened(snap, partial=True)
        elif closing and done and all(abs(snap.pos[v][k]) < 1e-12 for v in VENUES for k in (st.x, st.y)):
            await self._finish(snap, now)

    async def _arcus_leg(self, sym: str, want: float, snap: Snapshot, now: float, s: Settings, late: bool,
                         closing: bool) -> bool:
        """Move Arcus's position in `sym` toward `want`. True when it is there (within a step or a dust size)."""
        v = self.v[ARCUS]
        spec = self.specs[ARCUS][sym]
        have = snap.pos[ARCUS][sym]
        d = want - have
        top = snap.tops[ARCUS][sym]
        rec = self.st.orders.get(sym)
        if rec:                               # what became of the resting order
            info = await v.order(sym, rec["id"])
            if info is None or not info.open:
                self.st.orders.pop(sym, None)
                rec = None
        if abs(d) < spec.step / 2 or not spec.tradeable(abs(d), top.mid):
            if rec:
                await v.cancel(sym, rec["id"])
                self.st.orders.pop(sym, None)
            return not (want == 0 and abs(have) > 0 and abs(d) >= spec.step / 2)
        side = BUY if d > 0 else SELL
        size = spec.floor_size(abs(d))
        reduce = closing or (have != 0 and (have > 0) != (d > 0) and abs(want) < abs(have))
        if not late:
            px = top.bid if side == BUY else top.ask
            if rec and (abs(rec["price"] - px) > spec.tick / 2 or abs(rec["size"] - size) > spec.step / 2):
                if now - rec["at"] >= s.requote_s or abs(rec["size"] - size) > spec.step / 2:
                    await v.cancel(sym, rec["id"])
                    self.st.orders.pop(sym, None)
                    rec = None
            if rec is None:
                oid = await v.maker(sym, side, size, px, reduce)
                self.st.orders[sym] = {"id": oid, "price": px, "size": size, "at": now, "side": side}
            return False
        if rec:
            await v.cancel(sym, rec["id"])
            self.st.orders.pop(sym, None)
        worst = top.ask * (1 + s.max_cross_bp / 1e4) if side == BUY else top.bid * (1 - s.max_cross_bp / 1e4)
        await v.taker(sym, side, size, worst, reduce)
        return False

    async def _lighter_leg(self, sym: str, want: float, snap: Snapshot, s: Settings, closing: bool) -> bool:
        """Lighter's position in `sym` made `want` (the mirror of Arcus's) by a taker order."""
        spec = self.specs[LIGHTER][sym]
        have = await self.v[LIGHTER].position(sym)
        if have is None:
            return False
        d = want - have
        top = snap.tops[LIGHTER][sym]
        if abs(d) < spec.step / 2:
            return True
        if not spec.tradeable(abs(d), top.mid) and not (want == 0 and closing):
            return True                       # dust Lighter would refuse: within one smallest order of the mirror
        side = BUY if d > 0 else SELL
        worst = top.ask * (1 + s.max_cross_bp / 1e4) if side == BUY else top.bid * (1 - s.max_cross_bp / 1e4)
        reduce = want == 0 or (have != 0 and (have > 0) != (d > 0) and abs(want) < abs(have))
        await self.v[LIGHTER].taker(sym, side, spec.floor_size(abs(d)) or abs(d), worst, reduce)
        return False

    async def _cancel_all_makers(self) -> None:
        for sym, rec in list(self.st.orders.items()):
            await self.v[ARCUS].cancel(sym, rec["id"])
        self.st.orders = {}

    def _opened(self, snap: Snapshot, partial: bool = False) -> None:
        st = self.st
        nx = abs(st.target.get(st.x, 0.0)) * snap.mid(LIGHTER, st.x)
        ny = abs(st.target.get(st.y, 0.0)) * snap.mid(LIGHTER, st.y)
        self._say("opened", f"cycle {st.cycle} open{' (partly: the rest did not fill in time)' if partial else ''}: "
                  f"{self._ways(st.way, st.x, st.y)}; ${nx:,.2f} of {st.x} and ${ny:,.2f} of {st.y} on each venue; "
                  f"closes {_utc(st.close_at)}", cycle=st.cycle, nx=nx, ny=ny)

    def rooms(self, snap: Snapshot) -> dict[str, float]:
        """Each venue's (equity - maintenance) / maintenance now."""
        st = self.st
        out = {}
        for v in VENUES:
            nx = abs(snap.pos[v][st.x]) * snap.mid(v, st.x)
            ny = abs(snap.pos[v][st.y]) * snap.mid(v, st.y)
            out[v] = P.room(snap.equity[v], nx, ny, snap.rules[v][st.x], snap.rules[v][st.y])
        return out

    def _watch(self, snap: Snapshot, now: float, s: Settings) -> None:
        st = self.st
        rooms = self.rooms(snap)
        st.min_room = min(st.min_room, *rooms.values())
        worst = min(rooms, key=lambda v: rooms[v])
        if st.phase == "open" and s.guard > 0 and rooms[worst] < s.guard:
            self._say("guard", f"cycle {st.cycle}: {self.names[worst]} is near liquidation (room {rooms[worst]:.2f} "
                      f"under {s.guard:.2f}): closing everything now", room=rooms[worst])
            st.why = "guard"
            self._begin_close("guard")
            st.why = "guard"
            return
        if st.phase == "open" and s.offhours == "cut" and snap.off_hours != st.off_size:
            self._resize(snap, s)
        if st.phase == "open" and blocked(now, now + SKIP_LEAD_S, s.skip) is not None:
            self._begin_close("a skip date (earnings) begins")
            return
        if st.phase == "open" and now >= st.close_at:
            self._begin_close(f"{s.hold_h:g} hours are up")

    def _resize(self, snap: Snapshot, s: Settings) -> None:
        """The stock market closed (cut to Arcus's off-hours size) or reopened (back to the size opened with)."""
        st = self.st
        rx = {v: snap.rules[v][st.x] for v in VENUES}
        ry = {v: snap.rules[v][st.y] for v in VENUES}
        sz = P.size(snap.equity, rx, ry, snap.off_hours, s.margin_use, s.max_leverage)
        ux = min(abs(st.full.get(st.x, 0.0)), sz.nx / snap.mid(LIGHTER, st.x))
        uy = min(abs(st.full.get(st.y, 0.0)), sz.ny / snap.mid(LIGHTER, st.y))
        step = {sym: max(self.specs[v][sym].step for v in VENUES) for sym in (st.x, st.y)}
        ux = math.floor(ux / step[st.x] + 1e-9) * step[st.x]
        uy = math.floor(uy / step[st.y] + 1e-9) * step[st.y]
        sd = P.sides(st.way)
        new = {st.x: sd[ARCUS]["x"] * ux, st.y: sd[ARCUS]["y"] * uy}
        if snap.off_hours:                  # only ever smaller at the close
            new = {k: (n if abs(n) < abs(st.target[k]) else st.target[k]) for k, n in new.items()}
        st.target, st.off_size, st.phase_since = new, snap.off_hours, self.clock()
        self._say("resized", f"cycle {st.cycle}: the stock market {'closed' if snap.off_hours else 'reopened'}: "
                  f"Arcus {'cut' if snap.off_hours else 'back'} to {ux:g} {st.x} and {uy:g} {st.y}; Lighter follows")

    async def _finish(self, snap: Snapshot, now: float) -> None:
        st, s = self.st, self.settings()
        await self._cancel_all_makers()
        end = dict(snap.equity)
        made = {v: end[v] - st.start_equity.get(v, end[v]) for v in VENUES}
        tr = P.transfer(end, s.min_transfer_usd)
        row = {"cycle": st.cycle, "pair": f"{st.x}/{st.y}", "way": st.way, "opened": st.opened_at, "closed": now,
               "why": st.why, "start": st.start_equity, "end": end, "made": made, "volume": st.volume,
               "min_room": None if not math.isfinite(st.min_room) else st.min_room, "funding": st.funding,
               "transfer": {"amount": tr.amount, "from": tr.source, "to": tr.target}}
        self.store.cycle(row)
        total = sum(made.values())
        self._say("closed", f"cycle {st.cycle} closed ({st.why}): Arcus {_usd(made[ARCUS])}, Lighter "
                  f"{_usd(made[LIGHTER])}, together {_usd(total)}; ${st.volume:,.0f} traded. Money now: Arcus "
                  f"${end[ARCUS]:,.2f}, Lighter ${end[LIGHTER]:,.2f}", **row)
        st.transfer = {"amount": tr.amount, "from": tr.source, "to": tr.target, "at": now}
        self._say("transfer", tr.text(self.names), amount=tr.amount, source=tr.source, target=tr.target)
        if tr.amount > 0 and self.mover is not None:
            self.mover(tr.amount, tr.source, tr.target)
            self._say("moved", f"paper: ${tr.amount:,.2f} moved from {self.names[tr.source]} to "
                      f"{self.names[tr.target]}")
        if st.why in ("guard", "asked to close", "found positions while flat"):
            st.next_open = max(st.next_open, self._scheduled(now + 1, s))
        st.phase, st.phase_since, st.target, st.orders, st.reminded = "flat", now, {}, {}, now


def _utc(t: float) -> str:
    import datetime as dt
    return dt.datetime.fromtimestamp(t, dt.UTC).strftime("%a %d %b %H:%M UTC")


def _usd(x: float) -> str:
    return f"-${-x:,.2f}" if x < 0 else f"+${x:,.2f}"


def arcus_closed(now: float) -> bool:
    return not arcus_open(now)
