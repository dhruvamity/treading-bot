"""Queue-aware passive fill model (P3A task 4). Shared by the simulator and the paper adapter (P5 task 1).

Rules:
- On arrival (after latency) a post-only order that would cross the book is REJECTED (Arcus POST_ONLY_WOULD_CROSS,
  Lighter canceled-post-only). Otherwise its queue position = displayed size at its price (0 if it improves).
- Trades at our price on our side reduce the queue ahead; volume beyond the queue fills us (partially, on step).
- A displayed-size decrease larger than the traded volume is cancellation. PESSIMISTIC: cancels are spread
  pro-rata over the level (only the fraction ahead of us helps); OPTIMISTIC: all cancels were ahead of us (FIFO).
- A trade strictly through our price (better for the taker than our quote) fills us fully at our price.
- Touch-only fallback (no L2 for the market): fill only on trade-through by >= 1 tick; flagged optimistic-bounded.
- Self-impact: the trade volume that fills us is consumed and not available to our other orders at that level.
- An order is live only between `active_from_us` and `cancel_effective_us` (cancel latency): a trade that
  crosses a stale quote before its cancel takes effect fills it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from bot.core.book import L2Book
from bot.venues.base import Side


class FillMode(StrEnum):
    PESSIMISTIC = "pessimistic"
    OPTIMISTIC = "optimistic"


@dataclass
class SimOrder:
    client_id: str
    side: Side
    price: Decimal
    size: Decimal
    post_only: bool = True
    reduce_only: bool = False
    tag: str = ""
    submitted_us: int = 0
    active_from_us: int = 0
    cancel_effective_us: int | None = None
    remaining: Decimal = Decimal(0)
    queue_ahead: Decimal = Decimal(0)
    status: str = "pending"  # pending -> open -> filled | canceled | rejected
    arrived: bool = False
    filled: Decimal = Decimal(0)
    queue_at_arrival: Decimal = Decimal(0)

    def __post_init__(self) -> None:
        if self.remaining == 0:
            self.remaining = self.size

    def live_at(self, ts_us: int) -> bool:
        return (self.status == "open" and ts_us >= self.active_from_us
                and (self.cancel_effective_us is None or ts_us < self.cancel_effective_us))


@dataclass(frozen=True, slots=True)
class SimFill:
    client_id: str
    side: Side
    price: Decimal
    size: Decimal
    ts_us: int
    queue_at_arrival: Decimal
    trade_through: bool


@dataclass
class QueueFillModel:
    mode: FillMode = FillMode.PESSIMISTIC
    step: Decimal = Decimal("0.00000001")
    tick: Decimal = Decimal("0.01")
    touch_only: bool = False  # no L2 available for this market
    orders: dict[str, SimOrder] = field(default_factory=dict)
    _traded_at_level: dict[tuple[Side, Decimal], Decimal] = field(default_factory=dict)
    post_only_rejects: int = 0

    # ---------------------------------------------------------------- lifecycle
    def submit(self, o: SimOrder) -> None:
        self.orders[o.client_id] = o

    def arrive(self, o: SimOrder, book: L2Book, ts_us: int) -> bool:
        """Order reaches the venue. Returns False if rejected (post-only would cross)."""
        o.arrived = True
        bb, ba = book.best_bid(), book.best_ask()
        if o.post_only:
            if o.side is Side.BUY and ba is not None and o.price >= ba[0]:
                o.status = "rejected"
                self.post_only_rejects += 1
                return False
            if o.side is Side.SELL and bb is not None and o.price <= bb[0]:
                o.status = "rejected"
                self.post_only_rejects += 1
                return False
        o.status = "open"
        o.queue_ahead = book.size_at(o.side is Side.BUY, o.price)
        o.queue_at_arrival = o.queue_ahead
        return True

    def request_cancel(self, client_id: str, effective_us: int) -> None:
        o = self.orders.get(client_id)
        if o is not None and o.status in ("open", "pending"):
            o.cancel_effective_us = effective_us

    def expire_cancels(self, ts_us: int) -> list[SimOrder]:
        out = []
        for o in self.orders.values():
            if o.status in ("open", "pending") and o.cancel_effective_us is not None and ts_us >= o.cancel_effective_us:
                o.status = "canceled"
                out.append(o)
        return out

    def modify(self, client_id: str, price: Decimal, size: Decimal, book: L2Book, ts_us: int) -> None:
        """Same-price size decrease keeps priority (Arcus docs); anything else re-queues at the back."""
        o = self.orders[client_id]
        new_rem = max(Decimal(0), size - o.filled)
        if price == o.price and size <= o.size:
            o.size, o.remaining = size, new_rem
            return
        o.price, o.size, o.remaining = price, size, new_rem
        o.queue_ahead = book.size_at(o.side is Side.BUY, price)
        o.queue_at_arrival = o.queue_ahead

    # ---------------------------------------------------------------- market events
    def on_level_change(self, is_bid: bool, price: Decimal, old: Decimal, new: Decimal) -> None:
        side = Side.BUY if is_bid else Side.SELL
        traded = self._traded_at_level.pop((side, price), Decimal(0))
        if new >= old or self.touch_only:
            return
        cancels = max(Decimal(0), (old - new) - traded)
        if cancels <= 0:
            return
        for o in self.orders.values():
            if o.status != "open" or o.side is not side or o.price != price or o.queue_ahead <= 0:
                continue
            if self.mode is FillMode.OPTIMISTIC:
                o.queue_ahead = max(Decimal(0), o.queue_ahead - cancels)
            else:
                frac = o.queue_ahead / old if old > 0 else Decimal(0)
                o.queue_ahead = max(Decimal(0), o.queue_ahead - cancels * frac)

    def on_trade(self, price: Decimal, size: Decimal, taker_side: Side, ts_us: int) -> list[SimFill]:
        """A public trade. taker SELL hits bids (our BUY orders); taker BUY lifts asks (our SELL orders)."""
        maker_side = Side.BUY if taker_side is Side.SELL else Side.SELL
        self._traded_at_level[(maker_side, price)] = self._traded_at_level.get((maker_side, price), Decimal(0)) + size
        fills: list[SimFill] = []
        cands = [o for o in self.orders.values() if o.side is maker_side and o.live_at(ts_us)]
        # Priority: better-priced orders first (a sell trade walks bids from the top), then earlier arrival.
        cands.sort(key=lambda o: (-o.price if maker_side is Side.BUY else o.price, o.active_from_us))
        avail = size
        for o in cands:
            through = (price < o.price) if maker_side is Side.BUY else (price > o.price)
            at = price == o.price
            if not (through or at):
                continue
            if self.touch_only and not through:
                continue
            if through:
                q = o.remaining
            else:
                o.queue_ahead -= avail
                if o.queue_ahead >= 0:
                    continue
                q = min(o.remaining, -o.queue_ahead)
                o.queue_ahead = Decimal(0)
            q = (q / self.step).to_integral_value(rounding="ROUND_FLOOR") * self.step
            if q <= 0:
                continue
            o.remaining -= q
            o.filled += q
            if o.remaining <= 0:
                o.status = "filled"
            fills.append(SimFill(o.client_id, o.side, o.price, q, ts_us, o.queue_at_arrival, through))
            if at:
                avail = max(Decimal(0), avail - q)  # self-impact: consumed volume is gone
        return fills

    def open_orders(self) -> list[SimOrder]:
        return [o for o in self.orders.values() if o.status in ("open", "pending")]

    def gc(self) -> None:
        for k in [k for k, o in self.orders.items() if o.status in ("filled", "canceled", "rejected")]:
            del self.orders[k]
