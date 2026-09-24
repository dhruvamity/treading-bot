"""Autopilot parameter rules (spec "Step 4").

| delta or h      | DGrid formula from sigma_1h and target fills/hour F (F capped by the venue order budget) | 5-100 bps |
| levels N        | floor(I_cap / (q x P)), capped by venue limits (Arcus <= 12, Lighter <= 5 per side)        |           |
| size q          | max(1.2 x venue minimum, capital x target leverage / (2N))                                 |           |
| skew kappa      | rises when markouts turn negative                                                           | 0.5-2     |
| reset R         | 0.5 x sigma_1h                                                                              | 0.125-1%  |
| SL / TP         | margin-based, Tread DGrid defaults                                                          | 10% / 10% |
| Arcus RWA off-h | delta x 2, size x 0.25, no Mid, never price past the next trading bound                     |           |
"""

from __future__ import annotations

from dataclasses import dataclass

from bot.autopilot.features import Features
from bot.common.config import AutopilotCfg
from bot.strategies import quoting as qt
from bot.venues.base import Venue


@dataclass(frozen=True, slots=True)
class ParamSet:
    delta_bps: float
    levels: int
    size_usd: float
    kappa: float
    reset_pct: float
    stop_loss_pct: float
    take_profit_pct: float | None
    fills_per_hour: float


def fills_cap_from_budget(venue: Venue, *, arcus_pool_remaining: int | None, hours_left: float,
                          lighter_tx_per_min: int = 60) -> float:
    """Each fill costs ~2 actions (the fill's re-list + a requote); keep actions/hour within budget."""
    if venue is Venue.LIGHTER_RH:
        return lighter_tx_per_min * 60 * 0.3 / 2  # never more than 30% of the tx budget on requotes
    if arcus_pool_remaining is None:
        return 60.0
    return max(1.0, arcus_pool_remaining / max(hours_left, 1.0) / 2)


def choose_params(f: Features, cfg: AutopilotCfg, *, venue: Venue, capital_usd: float, leverage: float,
                  inventory_cap_usd: float, venue_min_usd: float, price: float, maker_fee: float, tick_frac: float,
                  off_hours: bool, fills_cap: float) -> ParamSet:
    F = min(cfg.target_fills_per_hour, fills_cap)
    delta = qt.dgrid_delta(f.sigma_1h, F, k_delta=cfg.k_delta, delta_min=cfg.delta_min_bps * qt.BP,
                           delta_max=cfg.delta_max_bps * qt.BP, maker_fee=maker_fee, tick_frac=tick_frac, venue=venue,
                           sigma_1s=f.sigma_1m / 60 ** 0.5)
    cap = 5 if venue is Venue.LIGHTER_RH else 12
    # Target leverage is implied by the inventory cap (I_cap / capital), so q = max(1.2 x min, I_cap / 2N).
    q = max(1.2 * venue_min_usd, inventory_cap_usd / (2 * cap))
    n = max(1, min(cap, int(inventory_cap_usd // q)))
    q = max(1.2 * venue_min_usd, inventory_cap_usd / (2 * n))
    kappa = 1.0
    if f.markout_60s_bps is not None and f.markout_60s_bps < 0:
        kappa = min(2.0, 1.0 + abs(f.markout_60s_bps) / 2)
    kappa = max(0.5, kappa)
    reset = min(1.0, max(0.125, 0.5 * f.sigma_1h * 100))
    if off_hours:
        delta *= 2
        q = max(1.2 * venue_min_usd, q * 0.25)
    return ParamSet(delta_bps=delta / qt.BP, levels=n, size_usd=q, kappa=kappa, reset_pct=reset, stop_loss_pct=10.0,
                    take_profit_pct=10.0, fills_per_hour=F)
