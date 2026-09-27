"""A Lighter L2 order book kept in sync from the order_book/{market} channel.

The channel sends a full snapshot on subscribe, then changes every 50 ms: absolute sizes per price, size 0 removes the
level. Continuity: a change applies only if its begin_nonce equals the nonce of the last one applied; a change whose
nonce is not above the last is stale and dropped; anything else is a gap and the book must be re-subscribed.
"""

from __future__ import annotations

import bisect
from collections.abc import Iterable
from typing import Any


class Side:
    """One side: price -> size, with the prices kept sorted ascending."""

    def __init__(self, descending: bool) -> None:
        self.desc = descending
        self.size: dict[float, float] = {}
        self.prices: list[float] = []

    def clear(self) -> None:
        self.size.clear()
        self.prices.clear()

    def set(self, px: float, sz: float) -> None:
        if sz <= 0:
            if px in self.size:
                del self.size[px]
                i = bisect.bisect_left(self.prices, px)
                if i < len(self.prices) and self.prices[i] == px:
                    self.prices.pop(i)
            return
        if px not in self.size:
            bisect.insort(self.prices, px)
        self.size[px] = sz

    def best(self) -> tuple[float, float] | None:
        if not self.prices:
            return None
        px = self.prices[-1] if self.desc else self.prices[0]
        return px, self.size[px]

    def top(self, n: int) -> list[tuple[float, float]]:
        ps = self.prices[-n:][::-1] if self.desc else self.prices[:n]
        return [(p, self.size[p]) for p in ps]

    def remove_through(self, px: float) -> None:
        """Drop levels at or through `px` (a bid side drops prices >= px, an ask side prices <= px)."""
        if self.desc:
            while self.prices and self.prices[-1] >= px:
                del self.size[self.prices.pop()]
        else:
            while self.prices and self.prices[0] <= px:
                del self.size[self.prices.pop(0)]

    def __len__(self) -> int:
        return len(self.prices)


class Book:
    def __init__(self, market_id: int) -> None:
        self.market_id = market_id
        self.bids = Side(descending=True)
        self.asks = Side(descending=False)
        self.nonce: int | None = None
        self.updated_ms = 0
        self.synced = False
        self.gaps = 0

    @staticmethod
    def _levels(rows: Iterable[dict[str, Any]]) -> Iterable[tuple[float, float]]:
        for r in rows or ():
            yield float(r["price"]), float(r["size"])

    def snapshot(self, ob: dict[str, Any], ts_ms: int = 0) -> None:
        self.bids.clear()
        self.asks.clear()
        for px, sz in self._levels(ob.get("bids", [])):
            self.bids.set(px, sz)
        for px, sz in self._levels(ob.get("asks", [])):
            self.asks.set(px, sz)
        self.nonce = int(ob.get("nonce") or 0)
        self.updated_ms = ts_ms
        self.synced = True
        self._uncross()

    def update(self, ob: dict[str, Any], ts_ms: int = 0) -> str:
        """Apply one change. Returns "ok", "stale" (dropped) or "gap" (the caller re-subscribes)."""
        nonce = int(ob.get("nonce") or 0)
        begin = int(ob.get("begin_nonce") or 0)
        if not self.synced or self.nonce is None:
            return "gap"
        if nonce <= self.nonce:
            return "stale"
        if begin != self.nonce:
            self.synced = False
            self.gaps += 1
            return "gap"
        for px, sz in self._levels(ob.get("bids", [])):
            self.bids.set(px, sz)
        for px, sz in self._levels(ob.get("asks", [])):
            self.asks.set(px, sz)
        self.nonce = nonce
        self.updated_ms = ts_ms
        self._uncross()
        return "ok"

    def _uncross(self) -> None:
        """Resting orders never cross on Lighter; if the book reads crossed, the side changed last is right, so drop the
        stale opposite levels (the newer best wins: keep the bid if it moved into the asks, else the ask)."""
        b, a = self.bids.best(), self.asks.best()
        if b is not None and a is not None and b[0] >= a[0]:
            self.asks.remove_through(b[0])

    # ---------------------------------------------------------------- reads
    def best_bid(self) -> tuple[float, float] | None:
        return self.bids.best()

    def best_ask(self) -> tuple[float, float] | None:
        return self.asks.best()

    def mid(self) -> float | None:
        b, a = self.bids.best(), self.asks.best()
        return (b[0] + a[0]) / 2 if b and a else None

    def depth(self, n: int) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
        return self.bids.top(n), self.asks.top(n)
