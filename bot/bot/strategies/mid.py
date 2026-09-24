"""Mid: 1-3 levels per side at r +/- (anchor + i x level step), skewed around the reservation price.

Tread "Mid-1 / Mid-2" = offset_bps -1 / -2 (quotes inside the spread). Arcus only: Lighter standard's 200-300 ms
cancels make tight quotes easy to pick off. Off-hours on Arcus RWA: Mid is not allowed (spec).
"""

from __future__ import annotations

from bot.strategies import quoting as qt
from bot.strategies.base import StrategyContext, StrategyOutput
from bot.strategies.mm_base import MMBase
from bot.venues.base import Venue


class MidStrategy(MMBase):
    name = "mid"

    def half_spread(self, ctx: StrategyContext, mid: float) -> float:
        if isinstance(self.p.spacing_bps, int | float):
            return float(self.p.spacing_bps) * qt.BP
        return max(2 * self.tick_frac(ctx, mid), ctx.view.vol_1m.sigma())

    def on_tick(self, ctx: StrategyContext) -> StrategyOutput:
        if ctx.venue is Venue.LIGHTER_RH:
            return self.exit_book(ctx, "Mid is blocked on Lighter standard (stale-quote risk)")
        if ctx.off_hours and not self.p.off_hours.allow_mid:
            return self.exit_book(ctx, "Mid disabled off-hours on Arcus RWA")
        why = self.blocked(ctx)
        if why:
            return self.exit_book(ctx, why)
        mid, bbo = self.mid(ctx), self.bbo(ctx)
        if mid is None or bbo is None:
            return self.exit_book(ctx, "no book")
        bb, ba = bbo
        tick = float(ctx.market.tick_size)
        h = self.half_spread(ctx, mid) * self.participation(ctx)
        u = self.u(ctx, mid)
        r = qt.reservation(mid, u, h, self.p.skew_kappa)
        bid0, ask0 = qt.anchors(self.p.execution_style, r=r, mid=mid, h=h, best_bid=bb, best_ask=ba, tick=tick,
                                sigma_1m=ctx.view.vol_1m.sigma(), k_passive=self.p.passive_k_sigma,
                                offset_bps=self.p.offset_bps)
        bid0, ask0 = qt.post_only_guard(bid0, ask0, bb, ba, tick)
        n = 1 if self.p.levels_per_side == "auto" else max(1, min(3, int(self.p.levels_per_side)))
        step = self.p.level_step_bps * qt.BP * mid
        levels = [(bid0 - i * step, ask0 + i * step, str(i)) for i in range(n)]
        q_base = qt.base_for_usd(self.q_usd(ctx, mid, n), mid, ctx.market)
        caps = self.caps(ctx, mid, q_base)
        orders = self.two_sided(ctx, levels=levels, q_base=q_base, u=u, cap_buys=caps[0],
                                cap_sells=caps[1])
        out = StrategyOutput(reason=f"mid {self.p.execution_style} h={h / qt.BP:.1f}bp u={u:+.2f} n={n}",
                             half_spread_ticks=h * mid / tick)
        out.set(ctx.venue, ctx.market.base, orders)
        out.metrics = {"h_bps": h / qt.BP, "u": u, "r": r}
        return out
