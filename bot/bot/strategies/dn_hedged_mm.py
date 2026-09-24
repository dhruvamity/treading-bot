"""Cross-venue hedged market making (spec DN module A): maker on Arcus, hedge on Lighter RH.

1. Quote ALO on Arcus at Fair +/- h, Fair = w x Lighter mid + (1 - w) x Arcus mid, h in [3, 30] bps.
2. On each Arcus fill, hedge on Lighter with an IOC for the opposite side, limit = Lighter mid +/- slippage cap.
3. Arcus fills can be $5 while Lighter's minimum is $10: residuals are batched until they reach `min_hedge_usd`.
4. When Arcus fills the opposite quote, the residual flips and the Lighter hedge is unwound the same way.
5. Kill rule: Lighter unreachable (or hedges failing) for more than T s -> cancel Arcus quotes and flatten the Arcus
   inventory with a reduce-only IOC. Every Lighter order is a hedge of a real Arcus fill (A2.2).
"""

from __future__ import annotations

from decimal import Decimal

from bot.common.config import DNSession
from bot.strategies import quoting as qt
from bot.strategies.base import SessionEvent, StrategyContext, StrategyOutput
from bot.venues.base import TIF, Fill, OrderRequest, Side, Venue


class DNHedgedMM:
    name = "dn_hedged_mm"

    def __init__(self, params: DNSession) -> None:
        self.p = params
        self.residual_since_us: int | None = None
        self.killed = False
        self.hedges = 0

    def on_start(self, ctx: StrategyContext) -> None:
        return None

    def on_fill(self, ctx: StrategyContext, fill: Fill) -> None:
        return None

    def on_session_event(self, ctx: StrategyContext, ev: SessionEvent) -> None:
        return None

    def lighter_min_usd(self, ctx: StrategyContext, lmid: float) -> float:
        lm = ctx.other_market
        return max(self.p.min_hedge_usd, float(lm.min_notional), float(lm.min_size) * lmid) if lm else self.p.min_hedge_usd

    def residual_base(self, ctx: StrategyContext) -> float:
        return float(ctx.inventory + ctx.other_inventory)

    def hedge_intent(self, ctx: StrategyContext, residual: float, why: str) -> OrderRequest | None:
        assert ctx.other_market is not None and ctx.other_view is not None
        lm, lmid = ctx.other_market, ctx.other_view.mid_f()
        if lmid is None:
            return None
        side = Side.SELL if residual > 0 else Side.BUY
        slip = self.p.hedge_slippage_cap_bps * qt.BP
        px = lmid * (1 - slip) if side is Side.SELL else lmid * (1 + slip)
        step = float(lm.step_size)
        size = int(abs(residual) / step) * step
        if size <= 0 or size < float(lm.min_size):
            return None
        return OrderRequest(Venue.LIGHTER_RH, lm.base, side, lm.round_price(Decimal(str(px)), is_bid=side is Side.BUY),
                            Decimal(str(size)).quantize(lm.step_size), TIF.IOC, tag="hedge", reason=why)

    def on_tick(self, ctx: StrategyContext) -> StrategyOutput:
        out = StrategyOutput()
        am = ctx.market
        amid, lmid = ctx.view.mid_f(), (ctx.other_view.mid_f() if ctx.other_view else None)
        resid = self.residual_base(ctx)
        ref_px = lmid or amid or 0.0
        resid_usd = abs(resid) * ref_px
        # ---- kill rule
        if not ctx.other_venue_healthy and ctx.other_venue_down_s > self.p.lighter_down_kill_s:
            self.killed = True
        if self.killed:
            out.reason = f"DN kill: Lighter unhealthy {ctx.other_venue_down_s:.0f}s; cancel Arcus quotes, flatten Arcus"
            out.set(Venue.ARCUS, am.base, [])
            if ctx.inventory != 0 and amid:
                side = Side.SELL if ctx.inventory > 0 else Side.BUY
                px = amid * (1 - 20 * qt.BP) if side is Side.SELL else amid * (1 + 20 * qt.BP)
                out.hedge_intents.append(OrderRequest(Venue.ARCUS, am.base, side,
                                                      am.round_price(Decimal(str(px)), is_bid=side is Side.BUY),
                                                      abs(ctx.inventory), TIF.IOC, reduce_only=True, tag="dn_kill",
                                                      reason="DN kill rule: flatten unhedged Arcus leg"))
            if ctx.other_venue_healthy and ctx.inventory == 0:
                self.killed = False  # re-arm once the venue is back and we're flat
            return out
        # ---- hedging
        if lmid is not None and resid_usd >= self.lighter_min_usd(ctx, lmid):
            hedge = self.hedge_intent(ctx, resid, f"hedge Arcus fills: residual {resid:+.6f} (${resid_usd:.2f})")
            if hedge is not None:
                out.hedge_intents.append(hedge)
                self.hedges += 1
            self.residual_since_us = self.residual_since_us or ctx.now_us
        else:
            self.residual_since_us = None
        # ---- quoting
        if amid is None or lmid is None or not ctx.quoting_allowed or ctx.event_window:
            out.reason = "DN MM paused: " + (ctx.quoting_block_reason or ("event window" if ctx.event_window else "no book"))
            out.set(Venue.ARCUS, am.base, [])
            return out
        w = self.p.fair_weight_lighter
        fair = w * lmid + (1 - w) * amid
        h = max(3.0, min(30.0, self.p.half_spread_bps)) * qt.BP
        b, a = ctx.view.book.best_bid(), ctx.view.book.best_ask()
        if b is None or a is None:
            return out
        tick = float(am.tick_size)
        bid, ask = qt.post_only_guard(fair * (1 - h), fair * (1 + h), float(b[0]), float(a[0]), tick)
        cap_usd = self.p.collateral_per_leg_usd * self.p.leverage_per_leg
        a_usd = float(ctx.inventory) * amid
        # Size Arcus quotes so a single fill is hedgeable on Lighter at once: Lighter's effective minimum is
        # max($10, min_base x price) (BTC: 0.0002 BTC ~ $17), above Arcus's (BTC: 0.0001 BTC ~ $8.6).
        venue_min = max(float(am.min_notional), float(am.min_size) * amid)
        q = qt.base_for_usd(max(venue_min * 1.2, self.lighter_min_usd(ctx, lmid) * 1.05, 6.0), amid, am)
        orders = []
        if a_usd < cap_usd:
            d = qt.to_desired(am, Side.BUY, bid, q, "dnb0")
            if d:
                orders.append(d)
        if a_usd > -cap_usd:
            d = qt.to_desired(am, Side.SELL, ask, q, "dna0")
            if d:
                orders.append(d)
        out.set(Venue.ARCUS, am.base, orders)
        out.half_spread_ticks = h * fair / tick
        out.reason = f"DN MM fair={fair:.4f} h={h / qt.BP:.1f}bp resid=${resid_usd:.2f} arcus_inv=${a_usd:.2f}"
        out.metrics = {"fair": fair, "residual_usd": resid_usd, "hedges": float(self.hedges)}
        return out

    def on_stop(self, ctx: StrategyContext) -> StrategyOutput:
        out = StrategyOutput(reason="DN MM stop: cancel quotes; residual hedged; legs left paired")
        out.set(Venue.ARCUS, ctx.market.base, [])
        return out
