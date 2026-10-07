"""Smart: Mid that leaves a side out while its fill would likely lose.

On Arcus a quote at the best price earns at most half a tick (0.07 bp on SPY) and pays no fee, but the price moves
against the average fill there by 0.6-1.4 bp within a minute: the takers who hit it are the better informed. Which
fills lose most can be told from the book a second before:
- the other side of the book holds far more than ours: a bid with 10x its size on the ask is about to be traded
  through (imbalance = (bid size - ask size) / (bid + ask), our own orders not counted);
- the mid has just moved against the side: a bid right after a drop, an ask right after a rise.

Each second Smart quotes as Mid and leaves out a side that would add to the position when either holds (imbalance
beyond IMBALANCE, or a move beyond MOVE_BPS over the last LOOKBACK_S seconds). The side that reduces the position
always stays, so the bot can get flat. In the backtests (Sep 19-26, recorded books) this cut SPY's cost per dollar
traded by about a quarter for 80-85% of Mid's volume, and other markets' by 0-16%; it did not make any market
profitable.
The scout backtests the same rule (arcus/scout/sim.py SmartPolicy).
"""

from __future__ import annotations

from collections import deque

from arcus.common.config import MMSession
from arcus.strategies.base import StrategyContext, StrategyOutput
from arcus.strategies.mid import MidStrategy
from arcus.venues.base import Side

IMBALANCE = 0.6    # leave a side out when the other side holds this much more: (ours - theirs) / (ours + theirs) < -0.6
MOVE_BPS = 0.5     # ... or when the mid moved against it by more than this
LOOKBACK_S = 5     # ... over the last 5 s
US = 1_000_000


def imbalance(bid_size: float, ask_size: float) -> float:
    """(bid - ask) / (bid + ask), 0 when both are empty."""
    tot = bid_size + ask_size
    return (bid_size - ask_size) / tot if tot > 0 else 0.0


def left_out(imb: float, move_bps: float, u: float) -> tuple[bool, bool]:
    """(leave the bid out, leave the ask out). imb: the book's imbalance; move_bps: the mid's move over LOOKBACK_S;
    u: the inventory skew (arcus/strategies/quoting.skew_u; >= 0 means buying adds to the position, <= 0 selling)."""
    no_bid = u >= 0 and (imb < -IMBALANCE or move_bps < -MOVE_BPS)
    no_ask = u <= 0 and (imb > IMBALANCE or move_bps > MOVE_BPS)
    return no_bid, no_ask


class SmartStrategy(MidStrategy):
    name = "smart"

    def __init__(self, params: MMSession) -> None:
        super().__init__(params)
        self.mids: deque[tuple[int, float]] = deque()

    def move_bps(self, now_us: int, mid: float) -> float:
        """The mid's move since LOOKBACK_S ago, in bp (0 until there is that much history)."""
        self.mids.append((now_us, mid))
        cut = now_us - LOOKBACK_S * US
        while len(self.mids) > 1 and self.mids[1][0] <= cut:
            self.mids.popleft()
        t0, m0 = self.mids[0]
        fresh = t0 > cut - 3 * US   # after a gap (paused, outside the session) the oldest mid is too old to compare
        return (mid / m0 - 1) * 1e4 if t0 <= cut and fresh and m0 > 0 else 0.0

    def book_imbalance(self, ctx: StrategyContext) -> float:
        """Imbalance of the best bid and ask sizes, without our own orders there (they would make our side look
        heavier than the market is)."""
        b, a = ctx.view.book.best_bid(), ctx.view.book.best_ask()
        if b is None or a is None:
            return 0.0
        own_b, own_a = ctx.own_touch
        return imbalance(max(0.0, float(b[1]) - own_b), max(0.0, float(a[1]) - own_a))

    def on_tick(self, ctx: StrategyContext) -> StrategyOutput:
        mid = self.mid(ctx)
        move = self.move_bps(ctx.now_us, mid) if mid is not None else 0.0
        out = super().on_tick(ctx)
        key = (ctx.venue, ctx.market.base)
        orders = out.desired.get(key) or []
        if mid is None or not orders or any(o.reduce_only for o in orders):
            return out   # no book, or the exit book: nothing to leave out
        imb = self.book_imbalance(ctx)
        no_bid, no_ask = left_out(imb, move, self.u(ctx, mid))
        out.metrics.update(imbalance=imb, move_bps=move)
        if no_bid or no_ask:
            out.set(ctx.venue, ctx.market.base,
                    [o for o in orders if not ((o.side is Side.BUY and no_bid) or (o.side is Side.SELL and no_ask))])
            gone = " and ".join(s for s, x in (("bid", no_bid), ("ask", no_ask)) if x)
            out.reason = f"smart: {gone} out (imbalance {imb:+.2f}, {move:+.1f} bp in {LOOKBACK_S}s) · {out.reason}"
        return out
