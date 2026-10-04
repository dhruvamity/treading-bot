"""What the executor needs from a venue, and a simulated one.

The executor (engine.py) only ever talks to a `TradeVenue`. Three kinds exist:
- `SimVenue`: everything in memory, prices set by hand: the tests.
- `PaperVenue`: a SimVenue whose prices are the real venue's best bid and ask, read from its public API. No order
  leaves the machine. A resting order fills once the other side of the real book reaches its price.
- the live ones (arcus.py, lighter.py): real orders, signed with the keys in bot/.env and lighter/.env.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass
from typing import Any, Protocol

BUY, SELL = 1, -1


@dataclass(frozen=True)
class Top:
    bid: float
    ask: float

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2

    @property
    def spread_bp(self) -> float:
        return (self.ask - self.bid) / self.mid * 1e4 if self.mid > 0 else math.inf


@dataclass
class OrderInfo:
    id: str
    side: int
    price: float
    size: float
    filled: float = 0.0
    open: bool = True
    note: str = ""            # why it ended, when the venue says: "post-only would cross", "canceled", ...
    avg_px: float = 0.0       # average fill price (0 = not known: the order's price is used)


@dataclass(frozen=True)
class Spec:
    """A market's trading rules on one venue."""
    tick: float
    step: float
    min_size: float
    min_notional: float
    taker_bp: float = 0.0

    def floor_size(self, x: float) -> float:
        return math.floor(x / self.step + 1e-9) * self.step if self.step > 0 else x

    def tradeable(self, size: float, price: float) -> bool:
        return size >= max(self.min_size, self.step) - 1e-12 and size * price >= self.min_notional - 1e-9


class TradeVenue(Protocol):
    name: str

    async def start(self, symbol: str) -> Spec: ...
    async def stop(self) -> None: ...
    async def top(self, symbol: str) -> Top | None: ...
    async def position(self, symbol: str) -> float | None: ...
    async def free_collateral(self) -> float | None: ...
    async def maker(self, symbol: str, side: int, size: float, price: float, reduce_only: bool) -> str: ...
    async def taker(self, symbol: str, side: int, size: float, worst: float, reduce_only: bool) -> str: ...
    async def cancel(self, symbol: str, order_id: str) -> None: ...
    async def order(self, symbol: str, order_id: str) -> OrderInfo | None: ...
    async def cancel_all(self, symbol: str) -> None: ...
    async def set_stops(self, symbol: str, position: float, stop: float, take: float) -> bool: ...
    async def set_leverage(self, symbol: str, leverage: float) -> None: ...


class VenueDown(RuntimeError):
    """The venue could not be reached or refused for a reason that may pass: try again next loop."""


class SimVenue:
    """A venue in memory. Maker orders rest at their price and fill when `cross(symbol)` finds the book through them
    (or a test calls `fill`); taker orders fill at the touch if it is within their worst price."""

    def __init__(self, name: str, spec: Spec | None = None, collateral: float = 1000.0) -> None:
        self.name = name
        self.spec = spec or Spec(tick=0.01, step=0.0001, min_size=0.0001, min_notional=5.0)
        self.collateral = collateral
        self.tops: dict[str, Top] = {}
        self.pos: dict[str, float] = {}
        self.orders: dict[str, OrderInfo] = {}
        self.stops: dict[str, tuple[float, float, float]] = {}     # symbol -> (position, stop, take)
        self.leverage: dict[str, float] = {}
        self.down = False            # tests: every call raises VenueDown
        self.fail_stops = False      # tests: set_stops returns False
        self.sent: list[tuple[Any, ...]] = []    # every order sent: (kind, symbol, side, size, price, reduce_only)
        self.fees = 0.0
        self.cash = 0.0              # realised price result (not counting fees)
        self._ids = itertools.count(1)
        self._ro: dict[str, bool] = {}
        self._sym: dict[str, str] = {}

    # ---- what must outlive a restart (the paper run keeps it on disk; resting orders are simply placed again)
    def dump(self) -> dict[str, Any]:
        return {"collateral": self.collateral, "cash": self.cash, "fees": self.fees, "pos": self.pos,
                "stops": {k: list(x) for k, x in self.stops.items()}}

    def restore(self, d: dict[str, Any]) -> None:
        self.collateral, self.cash = float(d.get("collateral", self.collateral)), float(d.get("cash", 0.0))
        self.fees = float(d.get("fees", 0.0))
        self.pos = {k: float(x) for k, x in (d.get("pos") or {}).items()}
        self.stops = {k: (float(x[0]), float(x[1]), float(x[2])) for k, x in (d.get("stops") or {}).items()}

    # ---- test and paper controls
    def set_top(self, symbol: str, bid: float, ask: float) -> None:
        self.tops[symbol] = Top(bid, ask)

    def _apply(self, symbol: str, o: OrderInfo, qty: float, px: float) -> None:
        qty = min(qty, o.size - o.filled)
        if self._ro.get(o.id):       # a reduce-only order never grows or flips the position
            have = self.pos.get(symbol, 0.0)
            qty = min(qty, max(0.0, -have * o.side))
        if qty <= 0:
            return
        o.avg_px = (o.avg_px * o.filled + px * qty) / (o.filled + qty)
        o.filled += qty
        self.pos[symbol] = self.pos.get(symbol, 0.0) + o.side * qty
        self.cash -= o.side * qty * px
        if o.filled >= o.size - 1e-12:
            o.open, o.note = False, "filled"

    def fill(self, order_id: str, qty: float | None = None, symbol: str = "") -> None:
        o = self.orders[order_id]
        sym = symbol or self._sym[order_id]
        if o.open:
            self._apply(sym, o, o.size - o.filled if qty is None else qty, o.price)

    def cross(self, symbol: str) -> None:
        """Fill resting orders the book has reached: a buy once the best ask is at or under its price, a sell once
        the best bid is at or over it."""
        t = self.tops.get(symbol)
        if t is None:
            return
        for oid, o in self.orders.items():
            if o.open and self._sym[oid] == symbol and ((o.side == BUY and t.ask <= o.price)
                                                        or (o.side == SELL and t.bid >= o.price)):
                self._apply(symbol, o, o.size - o.filled, o.price)

    def trigger_stop(self, symbol: str) -> None:
        """The venue's own stop (or take profit) fires: the position is closed there."""
        if symbol in self.stops:
            self.pos[symbol] = 0.0
            del self.stops[symbol]

    def _check(self) -> None:
        if self.down:
            raise VenueDown(f"{self.name} is not answering")

    # ---- TradeVenue
    async def start(self, symbol: str) -> Spec:
        self._check()
        return self.spec

    async def stop(self) -> None:
        return None

    async def top(self, symbol: str) -> Top | None:
        self._check()
        return self.tops.get(symbol)

    async def position(self, symbol: str) -> float | None:
        self._check()
        return self.pos.get(symbol, 0.0)

    async def free_collateral(self) -> float | None:
        """What the account is worth: the starting collateral, what closed trades made, open positions at the mid."""
        self._check()
        mark = sum(p * self.tops[sym].mid for sym, p in self.pos.items() if p and sym in self.tops)
        return self.collateral + self.cash + mark - self.fees

    def _new(self, symbol: str, side: int, size: float, price: float, reduce_only: bool) -> OrderInfo:
        o = OrderInfo(f"{self.name[0]}{next(self._ids)}", side, price, size)
        self.orders[o.id] = o
        self._sym[o.id] = symbol
        self._ro[o.id] = reduce_only
        return o

    async def maker(self, symbol: str, side: int, size: float, price: float, reduce_only: bool) -> str:
        self._check()
        self.sent.append(("maker", symbol, side, size, price, reduce_only))
        o = self._new(symbol, side, size, price, reduce_only)
        t = self.tops.get(symbol)
        if t is not None and ((side == BUY and price >= t.ask) or (side == SELL and price <= t.bid)):
            o.open, o.note = False, "post-only would cross"
        return o.id

    async def taker(self, symbol: str, side: int, size: float, worst: float, reduce_only: bool) -> str:
        self._check()
        self.sent.append(("taker", symbol, side, size, worst, reduce_only))
        o = self._new(symbol, side, size, worst, reduce_only)
        t = self.tops.get(symbol)
        if t is not None:
            px = t.ask if side == BUY else t.bid
            if (side == BUY and px <= worst) or (side == SELL and px >= worst):
                before = o.filled
                self._apply(symbol, o, size, px)
                self.fees += (o.filled - before) * px * self.spec.taker_bp / 1e4
        if o.open:
            o.open, o.note = False, "not filled within the worst price" if o.filled == 0 else "partly filled"
        return o.id

    async def cancel(self, symbol: str, order_id: str) -> None:
        self._check()
        o = self.orders.get(order_id)
        if o is not None and o.open:
            o.open, o.note = False, "canceled"

    async def order(self, symbol: str, order_id: str) -> OrderInfo | None:
        self._check()
        return self.orders.get(order_id)

    async def cancel_all(self, symbol: str) -> None:
        self._check()
        for oid, o in self.orders.items():
            if o.open and self._sym[oid] == symbol:
                o.open, o.note = False, "canceled"
        self.stops.pop(symbol, None)

    async def set_stops(self, symbol: str, position: float, stop: float, take: float) -> bool:
        self._check()
        if self.fail_stops:
            return False
        self.stops[symbol] = (position, stop, take)
        return True

    async def set_leverage(self, symbol: str, leverage: float) -> None:
        self._check()
        self.leverage[symbol] = leverage


class PaperVenue(SimVenue):
    """A SimVenue on the real venue's prices: `reader(symbol)` returns its best bid and ask (arb.venues). Orders,
    fills and the position exist only here."""

    def __init__(self, name: str, reader: Any, spec: Spec, collateral: float) -> None:
        super().__init__(name, spec, collateral)
        self.reader = reader

    async def top(self, symbol: str) -> Top | None:
        try:
            bid, ask = await self.reader(symbol)
        except Exception as e:   # a public read failing is a venue being down, whatever the reason
            raise VenueDown(f"{self.name}: {e}") from e
        self.set_top(symbol, bid, ask)
        self.cross(symbol)
        st = self.stops.get(symbol)
        if st is not None:       # the paper venue's own stop and take profit
            pos, stop, take = st
            mid = (bid + ask) / 2
            lo, hi = min(stop, take), max(stop, take)
            if pos and (mid <= lo or mid >= hi):
                self.cash += self.pos.get(symbol, 0.0) * mid
                self.trigger_stop(symbol)
        return self.tops[symbol]
