"""L2 order book shared by the live feed and the paper venue.

Sync rules (verified on live frames 2026-09-23, tests/fixtures/live):
- Arcus `l2OrderbookUpdates`: seed from the `subscribed` snapshot's `lastSequenceId`; apply deltas whose
  `lastSequenceId` exceeds it; the FIRST delta may skip ahead (boundary gap, expected); after that sequences
  are contiguous, and a mid-stream gap means resubscribe. Duplicate prices in one frame: last write wins.
"""

from __future__ import annotations

import bisect
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from bot.common.decimal import D

ZERO = Decimal(0)


class SyncResult(StrEnum):
    APPLIED = "applied"
    STALE = "stale"  # older than the snapshot; ignored
    GAP = "gap"  # resubscribe for a fresh snapshot
    NOT_READY = "not_ready"  # delta before snapshot


@dataclass
class L2Book:
    bids: dict[Decimal, Decimal] = field(default_factory=dict)
    asks: dict[Decimal, Decimal] = field(default_factory=dict)
    _bid_keys: list[Decimal] = field(default_factory=list)  # ascending
    _ask_keys: list[Decimal] = field(default_factory=list)  # ascending
    seq: int | None = None
    ts_us: int = 0
    # Optional (is_bid, price, old_size, new_size) hook: the fill model uses it to tell cancels from trades.
    listener: Callable[[bool, Decimal, Decimal, Decimal], None] | None = None

    # ------------------------------------------------------------------ mutation
    def clear(self) -> None:
        self.bids.clear()
        self.asks.clear()
        self._bid_keys.clear()
        self._ask_keys.clear()
        self.seq = None

    def load(self, bids: Iterable[tuple[Decimal, Decimal]], asks: Iterable[tuple[Decimal, Decimal]],
             seq: int | None = None, ts_us: int = 0) -> None:
        self.clear()
        for p, s in bids:
            if s > 0:
                self.bids[p] = s
        for p, s in asks:
            if s > 0:
                self.asks[p] = s
        self._bid_keys = sorted(self.bids)
        self._ask_keys = sorted(self.asks)
        self.seq = seq
        self.ts_us = ts_us

    def set_level(self, is_bid: bool, price: Decimal, size: Decimal) -> None:
        book, keys = (self.bids, self._bid_keys) if is_bid else (self.asks, self._ask_keys)
        if self.listener is not None:
            old = book.get(price, ZERO)
            if old != max(size, ZERO):
                self.listener(is_bid, price, old, max(size, ZERO))
        if size <= 0:
            if price in book:
                del book[price]
                i = bisect.bisect_left(keys, price)
                if i < len(keys) and keys[i] == price:
                    keys.pop(i)
            return
        if price not in book:
            bisect.insort(keys, price)
        book[price] = size

    def apply(self, bids: Iterable[tuple[Decimal, Decimal]], asks: Iterable[tuple[Decimal, Decimal]]) -> None:
        """In array order, last write wins per price."""
        for p, s in bids:
            self.set_level(True, p, s)
        for p, s in asks:
            self.set_level(False, p, s)

    # ------------------------------------------------------------------ reads
    def best_bid(self) -> tuple[Decimal, Decimal] | None:
        if not self._bid_keys:
            return None
        p = self._bid_keys[-1]
        return p, self.bids[p]

    def best_ask(self) -> tuple[Decimal, Decimal] | None:
        if not self._ask_keys:
            return None
        p = self._ask_keys[0]
        return p, self.asks[p]

    def mid(self) -> Decimal | None:
        b, a = self.best_bid(), self.best_ask()
        if b is None or a is None:
            return None
        return (b[0] + a[0]) / 2

    def microprice(self) -> Decimal | None:
        b, a = self.best_bid(), self.best_ask()
        if b is None or a is None:
            return None
        tot = b[1] + a[1]
        if tot <= 0:
            return (b[0] + a[0]) / 2
        return (b[0] * a[1] + a[0] * b[1]) / tot

    def spread(self) -> Decimal | None:
        b, a = self.best_bid(), self.best_ask()
        return None if b is None or a is None else a[0] - b[0]

    def spread_bps(self) -> float | None:
        m, s = self.mid(), self.spread()
        return None if m is None or s is None or m <= 0 else float(s / m * 10_000)

    def crossed(self) -> bool:
        b, a = self.best_bid(), self.best_ask()
        return b is not None and a is not None and b[0] >= a[0]

    def levels(self, is_bid: bool, n: int) -> list[tuple[Decimal, Decimal]]:
        if is_bid:
            return [(p, self.bids[p]) for p in reversed(self._bid_keys[-n:])] if n else []
        return [(p, self.asks[p]) for p in self._ask_keys[:n]]

    def size_at(self, is_bid: bool, price: Decimal) -> Decimal:
        return (self.bids if is_bid else self.asks).get(price, ZERO)

    def depth_notional(self, is_bid: bool, *, within_bps: float | None = None, levels: int | None = None) -> Decimal:
        m = self.mid()
        if m is None:
            return ZERO
        out = ZERO
        lv = self.levels(is_bid, levels or 10_000)
        lim = None if within_bps is None else m * (1 - Decimal(within_bps) / 10_000 if is_bid else 1 + Decimal(within_bps) / 10_000)
        for p, s in lv:
            if lim is not None and ((is_bid and p < lim) or (not is_bid and p > lim)):
                break
            out += p * s
        return out

    def depth_to_fill(self, is_bid: bool, notional: Decimal) -> Decimal | None:
        """Worst price needed to take `notional` from the given side (is_bid=True walks the bids)."""
        acc = ZERO
        for p, s in self.levels(is_bid, 10_000):
            acc += p * s
            if acc >= notional:
                return p
        return None

    def queue_ahead(self, is_bid: bool, price: Decimal) -> Decimal:
        """Displayed size at our price (queue ahead of a fresh order at that price)."""
        return self.size_at(is_bid, price)


def _pairs(rows: Sequence[Sequence[str]] | Sequence[dict[str, str]]) -> list[tuple[Decimal, Decimal]]:
    out = []
    for r in rows:
        if isinstance(r, dict):
            out.append((D(r["price"]), D(r["size"])))
        else:
            out.append((D(r[0]), D(r[1])))
    return out


@dataclass
class ArcusBookSync:
    book: L2Book = field(default_factory=L2Book)
    snapshot_seq: int | None = None
    last_seq: int | None = None
    first_delta: bool = True
    boundary_gaps: int = 0
    gaps: int = 0

    def on_snapshot(self, contents: dict[str, object], ts_us: int = 0) -> SyncResult:
        """Load a snapshot. Arcus answers a subscription to a market that is not available (OFFLINE, a pre-listing,
        delisted, unknown) with `contents: {}`: the book is emptied and stays NOT_READY until a real snapshot comes.
        (Raising here used to drop the whole connection, and the replayed subscription raised again: a reconnect
        loop that stopped every market on that connection.)"""
        if not contents or contents.get("lastSequenceId") is None:
            self.invalidate(ts_us)
            return SyncResult.NOT_READY
        seq = int(contents["lastSequenceId"])  # type: ignore[call-overload]
        self.book.load(_pairs(contents.get("bids") or []), _pairs(contents.get("asks") or []), seq,  # type: ignore[arg-type]
                       int(contents.get("timestamp") or ts_us))  # type: ignore[call-overload]
        self.snapshot_seq = seq
        self.last_seq = seq
        self.first_delta = True
        return SyncResult.APPLIED

    def invalidate(self, ts_us: int = 0) -> None:
        """The stream can no longer be trusted (empty snapshot, `degraded` frame): drop the book and ignore deltas
        until the next snapshot."""
        self.book.load([], [], None, ts_us)
        self.snapshot_seq = self.last_seq = None
        self.first_delta = True

    def on_delta(self, contents: dict[str, object], ts_us: int = 0) -> SyncResult:
        if self.snapshot_seq is None or self.last_seq is None:
            return SyncResult.NOT_READY
        seq = int(contents["lastSequenceId"])  # type: ignore[call-overload]
        if seq <= self.last_seq:
            return SyncResult.STALE
        if self.first_delta:
            if seq != self.last_seq + 1:
                self.boundary_gaps += 1  # expected, self-heals (docs splice rule)
            self.first_delta = False
        elif seq != self.last_seq + 1:
            self.gaps += 1
            return SyncResult.GAP
        self.book.apply(_pairs(contents.get("bids") or []), _pairs(contents.get("asks") or []))  # type: ignore[arg-type]
        self.last_seq = seq
        self.book.seq = seq
        self.book.ts_us = ts_us
        return SyncResult.APPLIED
