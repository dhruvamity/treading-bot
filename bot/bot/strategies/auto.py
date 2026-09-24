"""`mode: auto`: the Autopilot picks the mode and parameters every `eval_every_s` (default 60 s) and delegates to
the chosen mode's strategy. Explicit (non-"auto") session values override the Autopilot's parameter rules, which is
how a Tread-style manual setup runs. Mode switches go through the hysteresis switcher; on a switch the old mode's
quotes are dropped by the order manager (tags differ) and inventory is handled by the new mode's skew/exit logic.
"""

from __future__ import annotations

from typing import Any

from bot.autopilot.features import FeatureEngine, Features
from bot.autopilot.params import ParamSet, choose_params, fills_cap_from_budget
from bot.autopilot.regime import ModeChoice, ModeSwitcher, choose_mode
from bot.common.config import MMSession
from bot.strategies.base import StrategyContext, StrategyOutput
from bot.strategies.blend import BlendStrategy
from bot.strategies.grid import GridStrategy
from bot.strategies.mid import MidStrategy
from bot.strategies.mm_base import MMBase
from bot.strategies.rgrid import RGridStrategy
from bot.strategies.signal import SignalStrategy
from bot.venues.base import Fill, Venue


class AutoStrategy(MMBase):
    name = "auto"

    def __init__(self, params: MMSession, *, eval_every_s: float = 60.0, feature_engine: FeatureEngine | None = None) -> None:
        super().__init__(params)
        self.fe = feature_engine or FeatureEngine()
        self.switcher = ModeSwitcher(params.autopilot)
        self.eval_every_us = int(eval_every_s * 1e6)
        self.last_eval_us = 0
        self.features: Features | None = None
        self.choice: ModeChoice | None = None
        self.params_set: ParamSet | None = None
        self.inner: Any = None  # one of the MMBase subclasses (each defines on_tick)
        self.switch_log: list[str] = []

    def _effective(self, ps: ParamSet) -> MMSession:
        p = self.p.model_copy(deep=True)
        if self.p.spacing_bps == "auto":
            p.spacing_bps = ps.delta_bps
        if self.p.levels_per_side == "auto":
            p.levels_per_side = ps.levels
        if self.p.order_size_usd == "auto":
            p.order_size_usd = ps.size_usd
        p.skew_kappa = ps.kappa
        p.reset_threshold_pct = ps.reset_pct
        return p

    def _make(self, mode: str, p: MMSession) -> MMBase | None:
        cls = {"mid": MidStrategy, "grid": GridStrategy, "rgrid": RGridStrategy, "blend": BlendStrategy,
               "signal": SignalStrategy}.get(mode)
        return cls(p) if cls else None

    def on_fill(self, ctx: StrategyContext, fill: Fill) -> None:
        super().on_fill(ctx, fill)
        self.fe.markouts[(ctx.venue.value, ctx.market.base)].add_fill(fill.ts_us, fill.side.sign, float(fill.price))
        if self.inner is not None:
            self.inner.on_fill(ctx, fill)

    def evaluate(self, ctx: StrategyContext) -> str | None:
        mid = ctx.view.mid_f() or 0.0
        f = self.fe.compute(ctx.view, ctx.now_us, other=ctx.other_view,
                            inventory_usd=float(ctx.inventory) * mid, budget_mode="normal")
        self.features = f
        ctx.features = f
        other_depth = None
        if ctx.other_view is not None and ctx.other_view.mid() is not None:
            other_depth = float(min(ctx.other_view.book.depth_notional(True, within_bps=10),
                                    ctx.other_view.book.depth_notional(False, within_bps=10)))
        choice = choose_mode(f, self.p.autopilot, venue=ctx.venue, other_depth_usd=other_depth,
                             safety_paused=not ctx.quoting_allowed and "safety" in ctx.quoting_block_reason,
                             band_zone=ctx.view.upper_in_zone or ctx.view.lower_in_zone, event_window=ctx.event_window)
        if choice.mode == "mid" and (ctx.venue is Venue.LIGHTER_RH or ctx.off_hours):
            choice = ModeChoice("grid", choice.reason + " (Mid not allowed here -> Grid)", regime=choice.regime)
        self.choice = choice
        mode, switch = self.switcher.update(choice, ctx.now_us)
        vmin = self.venue_min_usd(ctx, mid) if mid else float(ctx.market.min_notional)
        ps = choose_params(f, self.p.autopilot, venue=ctx.venue, capital_usd=self.p.capital_usd,
                           leverage=min(self.p.leverage_max, 5), inventory_cap_usd=self.p.inventory_cap_usd,
                           venue_min_usd=vmin, price=mid, maker_fee=float(ctx.market.maker_fee),
                           tick_frac=self.tick_frac(ctx, mid) if mid else 0.0, off_hours=ctx.off_hours,
                           fills_cap=fills_cap_from_budget(ctx.venue, arcus_pool_remaining=None, hours_left=8))
        self.params_set = ps
        if switch or self.inner is None or getattr(self.inner, "name", "") != mode:
            self.inner = self._make(mode, self._effective(ps))
            if switch:
                self.switch_log.append(switch)
        return switch

    def on_tick(self, ctx: StrategyContext) -> StrategyOutput:
        switch = None
        if ctx.now_us - self.last_eval_us >= self.eval_every_us:
            self.last_eval_us = ctx.now_us
            switch = self.evaluate(ctx)
        ctx.features = self.features
        ctx.regime = self.choice.regime if self.choice else None
        if self.inner is None:
            out = self.exit_book(ctx, f"autopilot pause: {self.choice.reason if self.choice else 'warming up'}")
        else:
            out = self.inner.on_tick(ctx)
            out.reason = f"auto[{self.switcher.current}] {out.reason}"
        if switch:
            out.reason += f" | MODE SWITCH {switch}"
        if self.params_set:
            out.metrics.update({"ap_delta_bps": self.params_set.delta_bps, "ap_levels": float(self.params_set.levels)})
        return out
