"""DGrid: no manual settings; picks Grid (range) or RGrid (trend) from the regime and sets spacing from volatility
(A6.4). Uses the Autopilot's regime when provided, else a local classifier on ER and trend_z. Defaults SL/TP 10%.
Switching sub-mode cancels quotes and hands inventory to the new mode's exit/skew logic (the order manager diff
does the cancel because tags differ between modes).
"""

from __future__ import annotations

from bot.common.config import MMSession
from bot.strategies.base import StrategyContext, StrategyOutput
from bot.strategies.grid import GridStrategy
from bot.strategies.mm_base import MMBase
from bot.strategies.rgrid import RGridStrategy
from bot.venues.base import Fill


def local_regime(ctx: StrategyContext, er_trend: float, er_range: float, z_trend: float) -> str:
    f = ctx.features
    if f is None:
        return "range"
    er = getattr(f, "er", 0.0)
    z = abs(getattr(f, "trend_z", 0.0))
    if er > er_trend or z > z_trend:
        return "trend"
    if er < er_range:
        return "range"
    return "neutral"


class DGridStrategy(MMBase):
    name = "dgrid"

    def __init__(self, params: MMSession) -> None:
        super().__init__(params)
        self.grid = GridStrategy(params)
        self.rgrid = RGridStrategy(params)
        self.active: str = "grid"
        self.switches = 0

    def on_fill(self, ctx: StrategyContext, fill: Fill) -> None:
        super().on_fill(ctx, fill)
        (self.grid if self.active == "grid" else self.rgrid).on_fill(ctx, fill)

    def on_tick(self, ctx: StrategyContext) -> StrategyOutput:
        a = self.p.autopilot
        regime = ctx.regime or local_regime(ctx, a.er_trend, a.er_range, a.trend_z)
        want = "rgrid" if regime == "trend" else "grid"
        note = ""
        if want != self.active:
            self.switches += 1
            note = f" | switch {self.active}->{want} (regime {regime})"
            self.active = want
            if want == "grid":
                self.grid = GridStrategy(self.p)  # fresh centre at current mid
        out = (self.grid if self.active == "grid" else self.rgrid).on_tick(ctx)
        out.reason = f"dgrid[{self.active}] " + out.reason + note
        out.metrics["dgrid_switches"] = float(self.switches)
        return out
