"""Simulator events: a single deterministic, time-ordered stream per run (P3A task 1).

Event = (ts_us, prio, seq, venue, base, kind, payload). Order: time, then kind priority (book before trades before
prices before funding), then the venue's own sequence, then the load order. Recorded data is ordered by recv_ts_us
(what the bot would have seen); venue timestamps are kept for diagnostics.
"""

from __future__ import annotations

import heapq
import itertools
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import polars as pl

from bot.research.loaders.read import read_table
from bot.venues.base import PublicTrade, Side, Venue

PRIO = {"book_snapshot": 0, "book_delta": 1, "trade": 2, "price": 3, "attrs": 4, "funding_pred": 5, "funding_paid": 6}


@dataclass(frozen=True, slots=True)
class Event:
    ts_us: int
    prio: int
    seq: int
    order: int
    venue: Venue
    base: str
    kind: str
    payload: Any

    def key(self) -> tuple[int, int, int, int]:
        return (self.ts_us, self.prio, self.seq, self.order)


_counter = itertools.count()


def ev(ts: int, venue: Venue, base: str, kind: str, payload: Any, seq: int = 0) -> Event:
    return Event(ts, PRIO[kind], seq, next(_counter), venue, base, kind, payload)


def merge(streams: Iterable[Iterable[Event]]) -> Iterator[Event]:
    return heapq.merge(*streams, key=Event.key)


# ------------------------------------------------------------------------------------------------ recorded data
def _dec_pairs(rows: pl.DataFrame, side: str) -> list[tuple[Decimal, Decimal]]:
    sub = rows.filter(pl.col("side") == side)
    return [(Decimal(p), Decimal(s)) for p, s in zip(sub["price"].to_list(), sub["size"].to_list(), strict=True)]


def recorded_events(root: Path, venue: Venue, base: str, *, start_us: int | None = None,
                    end_us: int | None = None) -> list[Event]:
    """Load one venue/market from the recorder's Parquet into sorted events."""
    v = venue.value
    where = []
    if start_us is not None:
        where.append(f"recv_ts_us >= {start_us}")
    if end_us is not None:
        where.append(f"recv_ts_us < {end_us}")
    w = " AND ".join(where) or None
    out: list[Event] = []
    bd = read_table(root, "book_deltas", venue=v, market=base, where=w)
    if not bd.is_empty():
        bd = bd.sort(["recv_ts_us", "seq"])
        for (ts, seq, snap), grp in bd.group_by(["recv_ts_us", "seq", "is_snapshot"], maintain_order=True):
            kind = "book_snapshot" if snap else "book_delta"
            out.append(ev(int(ts), venue, base, kind, (_dec_pairs(grp, "b"), _dec_pairs(grp, "a")), int(seq or 0)))
    tr = read_table(root, "trades", venue=v, market=base, where=w)
    for r in ([] if tr.is_empty() else tr.sort("recv_ts_us").iter_rows(named=True)):
        out.append(ev(int(r["recv_ts_us"]), venue, base, "trade", PublicTrade(
            venue, base, int(r["venue_ts_us"] or r["recv_ts_us"]), Decimal(r["price"]), Decimal(r["size"]),
            Side.BUY if r["taker_side"] == "buy" else Side.SELL, str(r["trade_id"]), r.get("maker_address"),
            r.get("taker_address"), r.get("maker_order_id"), r.get("seq"), bool(r.get("is_liquidation")))))
    px = read_table(root, "mark_oracle_index", venue=v, market=base, where=w)
    for r in ([] if px.is_empty() else px.sort("recv_ts_us").iter_rows(named=True)):
        out.append(ev(int(r["recv_ts_us"]), venue, base, "price",
                      {k: (Decimal(r[k]) if r.get(k) else None) for k in ("mark", "oracle", "index")}))
    fp = read_table(root, "funding_pred", venue=v, market=base, where=w)
    for r in ([] if fp.is_empty() else fp.sort("recv_ts_us").iter_rows(named=True)):
        out.append(ev(int(r["recv_ts_us"]), venue, base, "funding_pred", {"rate_h": r["predicted_rate_h"]}))
    ma = read_table(root, "market_attrs", venue=v, market=base, where=w)
    for r in ([] if ma.is_empty() else ma.sort("recv_ts_us").iter_rows(named=True)):
        out.append(ev(int(r["recv_ts_us"]), venue, base, "attrs", r))
    fw = None
    if start_us is not None or end_us is not None:
        fw = " AND ".join(x.replace("recv_ts_us", "funding_ts_us") for x in where)
    for sub in (root, root / "history"):
        fd = read_table(sub, "funding_paid", venue=v, market=base, where=fw)
        for r in ([] if fd.is_empty() else fd.sort("funding_ts_us").iter_rows(named=True)):
            out.append(ev(int(r["funding_ts_us"]), venue, base, "funding_paid",
                          {"rate_h": r["rate_h"], "pay_price": r.get("pay_price")}))
    out.sort(key=Event.key)
    # drop duplicate funding ticks loaded from both live and history
    seen: set[tuple[int, str]] = set()
    dedup = []
    for e in out:
        if e.kind == "funding_paid":
            k = (e.ts_us, e.base)
            if k in seen:
                continue
            seen.add(k)
        dedup.append(e)
    return dedup
