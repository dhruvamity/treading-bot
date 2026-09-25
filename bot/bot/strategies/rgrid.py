"""RGrid (trailing grid): N = 1-3 per side centred on an EMA of mid.

When mid moves more than R from the centre, the centre jumps to mid and the inventory left on the wrong side of the
move is CUT: a reduce-only maker order at the touch, then an IOC after T_cut (emitted as an intent the runner sends).
This caps a trend's loss at about one level plus R instead of the quadratic grid loss, at the cost of some taker
fills (2.25 bps on Arcus).
"""

from __future__ import annotations

import math
from decimal import Decimal

from bot.common.config import MMSession
from bot.strategies import quoting as qt
from bot.strategies.base import StrategyContext, StrategyOutput
from bot.strategies.mm_base import MMBase
from bot.venues.base import TIF, OrderRequest, Side


class RGridStrategy(MMBase):
    name = "rgrid"

    def __init__(self, params: MMSession, *, delta: float | None = None, levels: int | None = None) -> None:
        super().__init__(params)
        self.delta_override = delta
        self.levels_override = levels
        self.ema: float | None = None
        self.center: float | None = None
        self.last_us = 0
        self.cut_side: Side | None = None
        self.cut_since_us: int | None = None
        self.jumps = 0

    def spacing(self, ctx: StrategyContext, mid: float) -> float:
        if self.delta_override is not None:
            return self.delta_override * (self.p.off_hours.spacing_mult if ctx.off_hours else 1.0)
        return self.grid_spacing(ctx, mid)

    def on_tick(self, ctx: StrategyContext) -> StrategyOutput:
        why = self.blocked(ctx)
        if why:
            return self.exit_book(ctx, why)
        mid, bbo = self.mid(ctx), self.bbo(ctx)
        if mid is None or bbo is None:
            return self.exit_book(ctx, "no book")
        dt = (ctx.now_us - self.last_us) / 1e6 if self.last_us else 1.0
        self.last_us = ctx.now_us
        a = 1 - math.exp(-dt / max(1.0, self.p.rgrid_ema_s))
        self.ema = mid if self.ema is None else self.ema + a * (mid - self.ema)
        if self.center is None:
            self.center = self.ema
        note = ""
        R = self.p.reset_threshold_pct / 100
        delta_now = self.spacing(ctx, mid)
        q_level = qt.base_for_usd(self.q_usd(ctx, mid, 1), mid, ctx.market)
        if abs(mid - self.center) / self.center > R:
            self.center = mid
            self.jumps += 1
            note = f"jump #{self.jumps} to {mid:.4f}"
        elif self.cut_side is None:
            self.center = self.ema
        # Cut rule: once price has moved more than one level against the inventory's average entry, the inventory
        # beyond one level is cut (maker at the touch, IOC after T_cut). Keeps a trend's loss near one level + R.
        entry = float(ctx.entry_price) if ctx.entry_price else mid
        adverse = (mid - entry) / entry * (-1 if ctx.inventory > 0 else 1) if ctx.inventory != 0 else 0.0
        excess = abs(float(ctx.inventory)) - 1.5 * q_level
        if self.cut_side is None and excess > 0 and adverse > delta_now:
            self.cut_side = Side.BUY if ctx.inventory < 0 else Side.SELL
            self.cut_since_us = ctx.now_us
            note += " + cut wrong-side inventory"
        delta = self.spacing(ctx, mid)
        n = self.levels_override or (1 if self.p.levels_per_side == "auto" else int(self.p.levels_per_side))
        n = max(1, min(3, n))
        u = self.u(ctx, mid)
        bb, ba = bbo
        tick = float(ctx.market.tick_size)
        levels = []
        for k in range(1, n + 1):
            bp, ap = qt.post_only_guard(self.center * (1 - k * delta), self.center * (1 + k * delta), bb, ba, tick)
            levels.append((bp, ap, str(k)))
        q_base = qt.base_for_usd(self.q_usd(ctx, mid, n), mid, ctx.market)
        caps = self.caps(ctx, mid, q_base)
        orders = self.two_sided(ctx, levels=levels, q_base=q_base, u=u,
                                cap_buys=caps[0] or self.cut_side is Side.SELL,
                                cap_sells=caps[1] or self.cut_side is Side.BUY)
        out = StrategyOutput(reason=f"rgrid centre={self.center:.4f} delta={delta / qt.BP:.1f}bp n={n} {note}".strip(),
                             half_spread_ticks=delta * mid / tick)
        if self.cut_side is not None:
            if abs(float(ctx.inventory)) <= 1.5 * q_level or (self.cut_side is Side.SELL and ctx.inventory < 0) or \
                    (self.cut_side is Side.BUY and ctx.inventory > 0):
                self.cut_side, self.cut_since_us = None, None
            else:
                ex = self.exit_book(ctx, "rgrid cut")
                orders += ex.desired.get((ctx.venue, ctx.market.base), [])
                assert self.cut_since_us is not None
                if (ctx.now_us - self.cut_since_us) / 1e6 >= self.p.rgrid_cut_after_s:
                    out.ioc_intents.append(self.cut_ioc(ctx, mid))
                    out.reason += " | cut escalated to IOC"
                    self.cut_since_us = ctx.now_us  # re-arm: one IOC per T_cut
        out.set(ctx.venue, ctx.market.base, orders)
        out.metrics = {"delta_bps": delta / qt.BP, "jumps": float(self.jumps), "u": u}
        return out

    def cut_ioc(self, ctx: StrategyContext, mid: float) -> OrderRequest:
        m = ctx.market
        side = Side.SELL if ctx.inventory > 0 else Side.BUY
        px = mid * (1 - 20 * qt.BP) if side is Side.SELL else mid * (1 + 20 * qt.BP)
        keep = Decimal(str(qt.base_for_usd(self.q_usd(ctx, mid, 1), mid, m)))
        size = max(abs(ctx.inventory) - keep, m.min_size)
        size = (size / m.step_size).to_integral_value() * m.step_size
        return OrderRequest(m.venue, m.base, side, m.round_price(Decimal(str(px)), is_bid=side is Side.BUY),
                            min(size, abs(ctx.inventory)), TIF.IOC, reduce_only=True, tag="cut",
                            reason="rgrid: wrong-side inventory not cut by maker within T_cut")
