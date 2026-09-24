"""Blend: quote around an external reference instead of the local mid.

Reference = sum(w_i x ref_i) over configured sources: "<venue>:<BASE>" (other venue's mid), "pyth" (Arcus oracle),
"local" (local mid). The persistent basis EWMA(ref - local) is removed so quotes don't lean on a structural gap.
A reference older than stale_ms, or deviating from the local mid by more than dev_bps, widens x2; both -> pause.
Main use: thin Arcus RWA books (e.g. SPY) quoted around Lighter's deep mid.
"""

from __future__ import annotations

import math

from bot.common.config import MMSession
from bot.strategies import quoting as qt
from bot.strategies.base import StrategyContext, StrategyOutput
from bot.strategies.mm_base import MMBase


class BlendStrategy(MMBase):
    name = "blend"

    def __init__(self, params: MMSession) -> None:
        super().__init__(params)
        self.basis: float | None = None
        self.last_us = 0

    def reference(self, ctx: StrategyContext) -> tuple[float | None, float, list[str]]:
        """(reference price, max staleness s, notes)."""
        local = ctx.view.mid_f()
        tot = wsum = 0.0
        max_age = 0.0
        notes = []
        for src, w in zip(self.p.blend.reference, self.p.blend.weights, strict=True):
            px: float | None = None
            age = 0.0
            if src == "pyth":
                px = float(ctx.view.oracle) if ctx.view.oracle is not None else None
                age = (ctx.now_us - ctx.view.price_ts_us) / 1e6 if ctx.view.price_ts_us else math.inf
            elif src == "local":
                px, age = local, ctx.view.age_s(ctx.now_us)
            elif ctx.other_view is not None:
                px = ctx.other_view.mid_f()
                age = ctx.other_view.age_s(ctx.now_us)
            if px is None:
                notes.append(f"{src} missing")
                continue
            tot += w * px
            wsum += w
            max_age = max(max_age, age)
        if wsum == 0:
            return None, math.inf, notes
        return tot / wsum, max_age, notes

    def on_tick(self, ctx: StrategyContext) -> StrategyOutput:
        why = self.blocked(ctx)
        if why:
            return self.exit_book(ctx, why)
        local, bbo = ctx.view.mid_f(), self.bbo(ctx)
        ref, age, notes = self.reference(ctx)
        if local is None or bbo is None or ref is None:
            return self.exit_book(ctx, "blend: no local book or reference " + ",".join(notes))
        dt = (ctx.now_us - self.last_us) / 1e6 if self.last_us else 1.0
        self.last_us = ctx.now_us
        a = 1 - math.exp(-dt * math.log(2) / max(1.0, self.p.blend.basis_ewma_halflife_s))
        b = ref - local
        self.basis = b if self.basis is None else self.basis + a * (b - self.basis)
        center = ref - self.basis
        stale = age * 1000 > self.p.blend.stale_ms
        dev = abs(ref - local) / local / qt.BP > self.p.blend.dev_bps
        if stale and dev:
            return self.exit_book(ctx, f"blend paused: reference stale {age:.1f}s and deviating")
        h = (float(self.p.spacing_bps) if isinstance(self.p.spacing_bps, int | float) else 10.0) * qt.BP
        if ctx.off_hours:
            h *= self.p.off_hours.spacing_mult
        if stale or dev:
            h *= 2
        h *= self.participation(ctx)
        u = self.u(ctx, center)
        r = qt.reservation(center, u, h, self.p.skew_kappa)
        bb, ba = bbo
        tick = float(ctx.market.tick_size)
        off = self.p.offset_bps * qt.BP * center
        bid, ask = qt.post_only_guard(r - h * center - off, r + h * center + off, bb, ba, tick)
        n = 1 if self.p.levels_per_side == "auto" else max(1, min(3, int(self.p.levels_per_side)))
        step = self.p.level_step_bps * qt.BP * center
        levels = [(bid - i * step, ask + i * step, str(i)) for i in range(n)]
        q_base = qt.base_for_usd(self.q_usd(ctx, local, n), local, ctx.market)
        caps = self.caps(ctx, local, q_base)
        orders = self.two_sided(ctx, levels=levels, q_base=q_base, u=u, cap_buys=caps[0],
                                cap_sells=caps[1])
        out = StrategyOutput(reason=(f"blend ref={ref:.4f} basis={self.basis:+.4f} h={h / qt.BP:.1f}bp"
                                     + (" widened(stale)" if stale else "") + (" widened(dev)" if dev else "")),
                             half_spread_ticks=h * center / tick)
        out.set(ctx.venue, ctx.market.base, orders)
        out.metrics = {"ref": ref, "basis": self.basis, "h_bps": h / qt.BP, "ref_age_s": age}
        return out
