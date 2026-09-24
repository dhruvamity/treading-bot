"""Points overlay (spec DN module C): hold the hedged pair for hours instead of flipping it, because Lighter
appears to reward open interest and hold time over churn (community reports, unverified).

Differences from dn_carry: a minimum hold time (`target_hold_h`, default 12 h) before EV-based exits, and an entry
threshold relaxed by a carry budget so a near-neutral pair can be held for OI. Tracks OI-hours, hold time and a churn
penalty for the weekly points fit (owner enters points with `bot points add`). Points are valued at $0 in
backtests: the pair must stand on its own PnL (spec design rule 5).
"""

from __future__ import annotations

from bot.common.config import DNSession
from bot.strategies.base import StrategyContext, StrategyOutput
from bot.strategies.dn_carry import DNCarry


class PointsOverlay(DNCarry):
    name = "points_overlay"

    def __init__(self, params: DNSession, *, carry_budget_bps: float = 3.0) -> None:
        super().__init__(params)
        self.min_hold_h = params.target_hold_h
        self.entry_relax_bps = carry_budget_bps
        self.oi_hours_usd = 0.0
        self.entries = 0
        self._last_us = 0

    def on_tick(self, ctx: StrategyContext) -> StrategyOutput:
        was_flat = self.phase == "flat"
        out = super().on_tick(ctx)
        if was_flat and self.phase == "entering":
            self.entries += 1
        amid = ctx.view.mid_f() or 0.0
        if self._last_us:
            self.oi_hours_usd += abs(float(ctx.inventory)) * amid * (ctx.now_us - self._last_us) / 3.6e9
        self._last_us = ctx.now_us
        churn = self.entries * self.p.churn_penalty
        out.metrics.update({"oi_hours_usd": self.oi_hours_usd, "entries": float(self.entries), "churn_penalty": churn})
        return out
