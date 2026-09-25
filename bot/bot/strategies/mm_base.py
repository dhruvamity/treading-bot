"""Common market-making plumbing for the Tread-style modes.

Handles: quoting gate (risk/safety/event windows), Arcus RWA off-hours rules (spacing x2, size x0.25, no Mid, never
price past the next trading bound), bias path I*(t), inventory skew, participation cap, and the exit book used when
quoting is blocked (reduce-only maker order at the touch; the runner escalates to IOC after `exit_taker_after_s`).
"""

from __future__ import annotations

from decimal import Decimal

from bot.common.config import MMSession
from bot.core.order_manager import DesiredOrder
from bot.strategies import quoting as qt
from bot.strategies.base import StrategyContext, StrategyOutput
from bot.venues.base import Fill, Side


class MMBase:
    name = "mm"

    def __init__(self, params: MMSession) -> None:
        self.p = params
        self.fills = 0

    # ---------------------------------------------------------------- hooks
    def on_fill(self, ctx: StrategyContext, fill: Fill) -> None:
        self.fills += 1

    def on_stop(self, ctx: StrategyContext) -> StrategyOutput:
        return self.exit_book(ctx, "session end: unwind inventory with a reduce-only maker order")

    # ---------------------------------------------------------------- helpers
    def mid(self, ctx: StrategyContext) -> float | None:
        return ctx.view.mid_f()

    def bbo(self, ctx: StrategyContext) -> tuple[float, float] | None:
        b, a = ctx.view.book.best_bid(), ctx.view.book.best_ask()
        if b is None or a is None:
            return None
        return float(b[0]), float(a[0])

    def tick_frac(self, ctx: StrategyContext, mid: float) -> float:
        return float(ctx.market.tick_size) / mid if mid > 0 else 0.0

    def grid_spacing(self, ctx: StrategyContext, mid: float) -> float:
        """Grid / RGrid step as a fraction of price: spacing_bps, or `auto` from the 1 h volatility (auto_spacing),
        widened by off_hours.spacing_mult outside an Arcus RWA session."""
        if isinstance(self.p.spacing_bps, int | float):
            d = float(self.p.spacing_bps) * qt.BP
        else:
            a = self.p.auto_spacing
            d = qt.vol_spacing(ctx.view.sigma_1h(), a.target_fills_per_hour, k_delta=a.k_delta,
                               delta_min=a.delta_min_bps * qt.BP, delta_max=a.delta_max_bps * qt.BP,
                               maker_fee=float(ctx.market.maker_fee), tick_frac=self.tick_frac(ctx, mid))
        return d * (self.p.off_hours.spacing_mult if ctx.off_hours else 1.0)

    def venue_min_usd(self, ctx: StrategyContext, mid: float) -> float:
        return float(max(ctx.market.min_notional, ctx.market.min_size * Decimal(str(mid))))

    def q_usd(self, ctx: StrategyContext, mid: float, levels: int) -> float:
        q = qt.order_size_usd(self.p.order_size_usd, venue_min_usd=self.venue_min_usd(ctx, mid),
                              inventory_cap_usd=self.p.inventory_cap_usd, levels=levels)
        if ctx.off_hours:
            q = max(self.venue_min_usd(ctx, mid) * 1.2, q * self.p.off_hours.size_mult)
        return q

    def cap_usd(self, ctx: StrategyContext) -> float:
        """Inventory cap now: the off-hours cap outside an Arcus RWA session, when set."""
        off = self.p.inventory_cap_off_usd
        return off if ctx.off_hours and off is not None else self.p.inventory_cap_usd

    def target_inventory_base(self, ctx: StrategyContext, mid: float) -> float:
        usd = qt.bias_target_usd(self.p.bias, ctx.session_progress, self.p.bias_size_usd)
        return usd / mid if mid > 0 else 0.0

    def u(self, ctx: StrategyContext, mid: float, extra_target_base: float = 0.0) -> float:
        return qt.skew_u(float(ctx.inventory), self.target_inventory_base(ctx, mid) + extra_target_base, mid,
                         self.cap_usd(ctx))

    def inventory_usd(self, ctx: StrategyContext, mid: float) -> float:
        return float(ctx.inventory) * mid

    def caps(self, ctx: StrategyContext, mid: float, q_base: float) -> tuple[bool, bool]:
        """(no new buys, no new sells): the PROJECTED inventory after one more fill must stay within 1.2 x I_cap
        (the risk engine's hard cap is 1.25 x I_cap, so it stays a backstop rather than a routine filter)."""
        inv = self.inventory_usd(ctx, mid)
        q = q_base * mid
        cap = self.cap_usd(ctx)
        lim = 1.2 * cap
        return inv + q > lim or inv >= cap, inv - q < -lim or inv <= -cap

    def participation(self, ctx: StrategyContext) -> float:
        return qt.participation_mult(ctx.our_fill_usd_5m, ctx.view.market_volume_usd(ctx.now_us),
                                     self.p.participation_cap_pct)

    def clip_to_bounds(self, ctx: StrategyContext, side: Side, price: float) -> float | None:
        """Arcus off-hours: never price past the current trading bound."""
        v = ctx.view
        # docs (real-world-assets): fills AT or beyond a bound are rejected, so a quote on the bound is pointless too
        if side is Side.BUY and v.lower_bound is not None and price <= float(v.lower_bound):
            return None
        if side is Side.SELL and v.upper_bound is not None and price >= float(v.upper_bound):
            return None
        return price

    def blocked(self, ctx: StrategyContext) -> str | None:
        if not ctx.quoting_allowed:
            return ctx.quoting_block_reason or "quoting blocked"
        if ctx.event_window:
            return "event window: no new quotes"
        return None

    def exit_book(self, ctx: StrategyContext, why: str) -> StrategyOutput:
        """Reduce-only maker exit at the touch (one order). Empty when flat."""
        out = StrategyOutput(reason=why)
        orders: list[DesiredOrder] = []
        bbo = self.bbo(ctx)
        if ctx.inventory != 0 and bbo is not None:
            bid, ask = bbo
            if ctx.inventory > 0:
                d = qt.to_desired(ctx.market, Side.SELL, ask, abs(ctx.inventory), "exit", reduce_only=True)
            else:
                d = qt.to_desired(ctx.market, Side.BUY, bid, abs(ctx.inventory), "exit", reduce_only=True)
            if d is not None:
                orders.append(d)
        out.set(ctx.venue, ctx.market.base, orders)
        return out

    def two_sided(self, ctx: StrategyContext, *, levels: list[tuple[float, float, str]], q_base: float, u: float,
                  cap_buys: bool, cap_sells: bool) -> list[DesiredOrder]:
        """levels: [(bid_px, ask_px, tag_suffix)] -> skewed, capped desired orders."""
        m = ctx.market
        min_base = float(m.min_size)
        qb, qa = qt.skewed_sizes(q_base, u, qt.base_for_usd(self.venue_min_usd(ctx, float(ctx.view.mid_f() or 0)) * 1.01,
                                                             float(ctx.view.mid_f() or 1), m))
        out: list[DesiredOrder] = []
        for bid_px, ask_px, tag in levels:
            if not cap_buys and qb >= min_base:
                bp = self.clip_to_bounds(ctx, Side.BUY, bid_px) if ctx.off_hours else bid_px
                if bp is not None:
                    d = qt.to_desired(m, Side.BUY, bp, qb, f"b{tag}")
                    if d:
                        out.append(d)
            if not cap_sells and qa >= min_base:
                ap = self.clip_to_bounds(ctx, Side.SELL, ask_px) if ctx.off_hours else ask_px
                if ap is not None:
                    d = qt.to_desired(m, Side.SELL, ap, qa, f"a{tag}")
                    if d:
                        out.append(d)
        return out
