"""Static geometric grid.

Grid points j in [-N, N] at C(1+delta)^j. Start: buys at j = -1..-N, sells at j = 1..N (point 0 empty). A filled buy
at j re-lists as a sell at j+1 (one step up); a filled sell at j re-lists as a buy at j-1. Each point holds at most one
order, tagged `g{j}` so the order manager keeps queue priority across ticks.
No new buys once inventory x P >= I_cap (symmetric for sells). Re-centre when |m - C| / C > R for longer than
T_recentre; inventory then follows `recentre_inventory`: skew_exit (keep, skew the new grid against it),
maker_unwind (reduce-only maker order at the touch, grid side that adds is paused), hedge_other_venue (hedge intent).
Lighter: N <= 5 per side; the grid only changes on fills or re-centre, so it barely touches the order budget.
"""

from __future__ import annotations

from decimal import Decimal

from bot.common.config import MMSession
from bot.core.order_manager import DesiredOrder
from bot.strategies import quoting as qt
from bot.strategies.base import StrategyContext, StrategyOutput
from bot.strategies.mm_base import MMBase
from bot.venues.base import TIF, Fill, OrderRequest, Side, Venue


class GridStrategy(MMBase):
    name = "grid"

    def __init__(self, params: MMSession, *, delta: float | None = None, levels: int | None = None) -> None:
        super().__init__(params)
        self.delta_override = delta
        self.levels_override = levels
        self.center: float | None = None
        self.delta: float = 0.0
        self.n: int = 0
        self.points: dict[int, Side] = {}
        self.out_of_range_since: int | None = None
        self.recentres = 0
        self.unwind_active = False
        self.skew_active = False  # skew_exit: inventory carried over a re-centre is exited by skewing sizes

    # ---------------------------------------------------------------- parameters
    def spacing(self, ctx: StrategyContext, mid: float) -> float:
        if self.delta_override is not None:
            d = self.delta_override
        elif isinstance(self.p.spacing_bps, int | float):
            d = float(self.p.spacing_bps) * qt.BP
        else:
            a = self.p.autopilot
            d = qt.dgrid_delta(ctx.view.sigma_1h(), a.target_fills_per_hour, k_delta=a.k_delta,
                               delta_min=a.delta_min_bps * qt.BP, delta_max=a.delta_max_bps * qt.BP,
                               maker_fee=float(ctx.market.maker_fee), tick_frac=self.tick_frac(ctx, mid),
                               venue=ctx.venue, sigma_1s=ctx.view.vol_1s.sigma())
        if ctx.off_hours:
            d *= self.p.off_hours.spacing_mult
        return d

    def level_count(self, ctx: StrategyContext, mid: float, q_usd: float) -> int:
        cap = 5 if ctx.venue is Venue.LIGHTER_RH else 12
        if self.levels_override is not None:
            return max(1, min(cap, self.levels_override))
        if self.p.levels_per_side != "auto":
            return max(1, min(cap, int(self.p.levels_per_side)))
        return max(1, min(cap, int(self.p.inventory_cap_usd // max(q_usd, 1e-9))))

    # ---------------------------------------------------------------- state
    def reset(self, ctx: StrategyContext, mid: float) -> None:
        self.center = mid
        self.delta = self.spacing(ctx, mid)
        q = self.q_usd(ctx, mid, 3)
        self.n = self.level_count(ctx, mid, q)
        self.points = {j: Side.BUY for j in range(-self.n, 0)} | {j: Side.SELL for j in range(1, self.n + 1)}
        self.out_of_range_since = None

    def price(self, j: int) -> float:
        assert self.center is not None
        return self.center * (1 + self.delta) ** j

    def on_fill(self, ctx: StrategyContext, fill: Fill) -> None:
        super().on_fill(ctx, fill)
        tag = fill.tag or ""
        if not tag.startswith("g") or not tag[1:].lstrip("-").isdigit():
            return
        j = int(tag[1:])
        if fill.side is Side.BUY and self.points.get(j) is Side.BUY:
            self.points.pop(j, None)
            if j + 1 <= self.n:
                self.points[j + 1] = Side.SELL
        elif fill.side is Side.SELL and self.points.get(j) is Side.SELL:
            self.points.pop(j, None)
            if j - 1 >= -self.n:
                self.points[j - 1] = Side.BUY

    def check_recentre(self, ctx: StrategyContext, mid: float) -> str | None:
        assert self.center is not None
        dev = abs(mid - self.center) / self.center
        if dev <= self.p.reset_threshold_pct / 100:
            self.out_of_range_since = None
            return None
        if self.out_of_range_since is None:
            self.out_of_range_since = ctx.now_us
            return None
        if (ctx.now_us - self.out_of_range_since) / 1e6 >= self.p.recentre_after_s:
            self.recentres += 1
            old = self.center
            self.reset(ctx, mid)
            self.unwind_active = self.p.recentre_inventory == "maker_unwind" and ctx.inventory != 0
            self.skew_active = self.p.recentre_inventory == "skew_exit" and ctx.inventory != 0
            return f"re-centre #{self.recentres}: {old:.4f} -> {mid:.4f} (|dev| {dev:.3%} > R)"
        return None

    # ---------------------------------------------------------------- tick
    def on_tick(self, ctx: StrategyContext) -> StrategyOutput:
        why = self.blocked(ctx)
        if why:
            return self.exit_book(ctx, why)
        mid, bbo = self.mid(ctx), self.bbo(ctx)
        if mid is None or bbo is None:
            return self.exit_book(ctx, "no book")
        if self.center is None:
            self.reset(ctx, mid)
        note = self.check_recentre(ctx, mid) or ""
        inv_usd = self.inventory_usd(ctx, mid)
        q_base = qt.base_for_usd(self.q_usd(ctx, mid, self.n), mid, ctx.market)
        if self.skew_active and ctx.inventory == 0:
            self.skew_active = False
        u = self.u(ctx, mid) if self.skew_active else 0.0
        min_q = qt.base_for_usd(self.venue_min_usd(ctx, mid) * 1.01, mid, ctx.market)
        qb, qa = qt.skewed_sizes(q_base, u, min_q) if u else (q_base, q_base)
        no_buys, no_sells = self.caps(ctx, mid, max(qb, qa))
        cap_buys = no_buys or (self.unwind_active and ctx.inventory > 0)
        cap_sells = no_sells or (self.unwind_active and ctx.inventory < 0)
        bb, ba = bbo
        tick = float(ctx.market.tick_size)
        orders: list[DesiredOrder] = []
        min_base = float(ctx.market.min_size)
        for j, side in sorted(self.points.items()):
            px = self.price(j)
            if side is Side.BUY:
                if cap_buys or qb < min_base:
                    continue
                px = min(px, ba - tick)
                d = qt.to_desired(ctx.market, Side.BUY, px, qb, f"g{j}")
            else:
                if cap_sells or qa < min_base:
                    continue
                px = max(px, bb + tick)
                d = qt.to_desired(ctx.market, Side.SELL, px, qa, f"g{j}")
            if d is not None:
                orders.append(d)
        out = StrategyOutput(reason=(f"grid C={self.center:.4f} delta={self.delta / qt.BP:.1f}bp N={self.n} "
                                     f"inv=${inv_usd:.2f} " + note).strip(),
                             half_spread_ticks=self.delta * mid / tick)
        if self.unwind_active:
            ex = self.exit_book(ctx, "maker unwind after re-centre")
            orders += ex.desired.get((ctx.venue, ctx.market.base), [])
            if ctx.inventory == 0:
                self.unwind_active = False
        if note and self.p.recentre_inventory == "hedge_other_venue" and ctx.inventory != 0 and ctx.other_market:
            out.hedge_intents.append(self.hedge_intent(ctx, mid))
        out.set(ctx.venue, ctx.market.base, orders)
        out.metrics = {"delta_bps": self.delta / qt.BP, "levels": float(self.n), "inv_usd": inv_usd, "u": u}
        return out

    def hedge_intent(self, ctx: StrategyContext, mid: float) -> OrderRequest:
        assert ctx.other_market is not None and ctx.other_view is not None
        om = ctx.other_market
        omid = ctx.other_view.mid_f() or mid
        side = Side.SELL if ctx.inventory > 0 else Side.BUY
        slip = 10 * qt.BP
        px = omid * (1 - slip) if side is Side.SELL else omid * (1 + slip)
        size = Decimal(str(qt.base_for_usd(abs(float(ctx.inventory)) * mid, omid, om)))
        return OrderRequest(om.venue, om.base, side, om.round_price(Decimal(str(px)), is_bid=side is Side.BUY), size,
                            TIF.IOC, tag="hedge", reason="grid re-centre: hedge inventory on the other venue")
