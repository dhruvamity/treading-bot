"""Grid (Tread.fi's Grid): quotes around the last fill.

Flat: one bid and one ask at mid -/+ max(spread, half the book spread). Holding a position: last fill x (1 -/+ spread),
so a sell never goes below the last buy + spread and a buy never above the last sell - spread; each further fill
moves the reference, so the position builds one order per step until the position cap. Soft reset: once the mid has
run more than reset_threshold_pct against the position from the last fill, it stops adding and closes with a
reduce-only maker order at the touch; flat again, it starts over around the mid. A Long or Short bias skews the order
sizes toward holding part of the cap (arcus/strategies/setup.py). The scout backtests the same rules
(arcus/scout/sim.py GridPolicy). After a restart with a position there is no last fill yet: its average entry stands in.
"""

from __future__ import annotations

from arcus.common.config import MMSession
from arcus.strategies import quoting as qt
from arcus.strategies.base import StrategyContext, StrategyOutput
from arcus.strategies.mm_base import MMBase
from arcus.venues.base import Fill


class GridStrategy(MMBase):
    name = "grid"

    def __init__(self, params: MMSession) -> None:
        super().__init__(params)
        self.ref: float | None = None
        self.resetting = False

    def on_fill(self, ctx: StrategyContext, fill: Fill) -> None:
        super().on_fill(ctx, fill)
        if ctx.inventory == 0:
            self.ref, self.resetting = None, False
        elif not (fill.tag or "").startswith("exit"):   # exits (maker or IOC) close; they are not a new level
            self.ref = float(fill.price)

    def on_tick(self, ctx: StrategyContext) -> StrategyOutput:
        why = self.blocked(ctx)
        if why:
            return self.exit_book(ctx, why)
        mid, bbo = self.mid(ctx), self.bbo(ctx)
        if mid is None or bbo is None:
            return self.exit_book(ctx, "no book")
        bb, ba = bbo
        tick = float(ctx.market.tick_size)
        d = self.grid_spacing(ctx, mid)
        if ctx.inventory == 0:
            self.ref, self.resetting = None, False
        elif self.ref is None and ctx.entry_price is not None:
            self.ref = float(ctx.entry_price)
        if self.ref is None:
            half = max(d * mid, (ba - bb) / 2)
            bid, ask = mid - half, mid + half
            where = "around the mid"
        else:
            adverse = (self.ref - mid) / self.ref if ctx.inventory > 0 else (mid - self.ref) / self.ref
            if self.p.reset_threshold_pct > 0 and adverse > self.p.reset_threshold_pct / 100:
                self.resetting = True
            if self.resetting:
                return self.exit_book(ctx, f"grid soft reset: the mid ran {adverse:.3%} against the last fill "
                                           f"{self.ref:.4f}; closing at the touch")
            bid, ask = self.ref * (1 - d), self.ref * (1 + d)
            where = f"around the last fill {self.ref:.4f}"
        bid, ask = qt.post_only_guard(bid, ask, bb, ba, tick)
        q_base = qt.base_for_usd(self.q_usd(ctx, mid, 1), mid, ctx.market)
        caps = self.caps(ctx, mid, q_base)
        u = self.u(ctx, mid) if self.p.bias != "neutral" else 0.0
        orders = self.two_sided(ctx, levels=[(bid, ask, "0")], q_base=q_base, u=u, cap_buys=caps[0],
                                cap_sells=caps[1])
        out = StrategyOutput(reason=f"grid {d / qt.BP:+.1f}bp {self.p.bias} {where}", half_spread_ticks=d * mid / tick)
        out.set(ctx.venue, ctx.market.base, orders)
        out.metrics = {"d_bps": d / qt.BP, "ref": self.ref or 0.0, "inv_usd": self.inventory_usd(ctx, mid), "u": u}
        return out
