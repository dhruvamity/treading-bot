"""Run metrics (Appendix E7 "Metrics"): PnL terms, volume, CPM, OI-hours, drawdowns, liquidations, fill rate,
post-only reject rate, markouts, actions per filled dollar, mode time shares, DN hedge stats."""

from __future__ import annotations

import math
from typing import Any

from bot.research.sim.engine import SimResult


def max_drawdown(curve: list[tuple[int, float]]) -> tuple[float, float]:
    """(max drawdown in $, longest drawdown in hours)."""
    peak, peak_t = -math.inf, 0
    mdd, longest = 0.0, 0.0
    for t, v in curve:
        if v >= peak:
            peak, peak_t = v, t
        else:
            mdd = max(mdd, peak - v)
            longest = max(longest, (t - peak_t) / 3.6e9)
    return mdd, longest


def markouts(res: SimResult, mids: dict[str, list[tuple[int, float]]], horizons_s: tuple[int, ...] = (1, 5, 30, 60, 300)) -> dict[int, float]:
    """Mean signed markout in bps per horizon from a {venue:base: [(ts, mid)]} series."""
    import bisect

    out: dict[int, list[float]] = {h: [] for h in horizons_s}
    for f in res.fills:
        if not f.is_maker:
            continue
        series = mids.get(f"{f.venue.value}:{f.base}")
        if not series:
            continue
        ts = [t for t, _ in series]
        for h in horizons_s:
            i = bisect.bisect_left(ts, f.ts_us + h * 1_000_000)
            if i < len(series):
                m = series[i][1]
                out[h].append(f.side.sign * (m - float(f.price)) / float(f.price) * 1e4)
    return {h: (sum(v) / len(v) if v else math.nan) for h, v in out.items()}


def summarize(res: SimResult, capital: float) -> dict[str, Any]:
    t = res.total
    mdd, longest = max_drawdown(res.equity_curve)
    fills = len(res.fills)
    vol = float(t.volume)
    actions = sum(s.get("actions", 0) for s in res.stats.values())
    total_mode = sum(res.mode_time_s.values()) or 1.0
    return {
        "net_usd": float(t.net), "spread_capture": float(t.spread_capture), "inventory_mtm": float(t.inventory_mtm),
        "funding": float(t.funding), "fees": float(t.fees), "hedge_cost": float(t.hedge_cost),
        "liquidation_loss": float(t.liquidation_loss), "volume_usd": vol, "maker_volume_usd": float(t.maker_volume),
        "cpm_usd_per_1m": float(t.cpm) if t.cpm is not None else None,
        "cost_bps_of_volume": (-float(t.net) / vol * 1e4) if vol else None,
        "oi_hours_usd": float(t.oi_hours_usd), "fills": fills,
        "max_drawdown_usd": mdd, "max_drawdown_pct": mdd / capital * 100 if capital else None,
        "longest_drawdown_h": longest, "liquidations": len(res.liquidations),
        "min_liq_distance_sigma": res.min_liq_distance_sigma,
        "post_only_reject_rate": res.post_only_rejects / max(1, res.orders_placed),
        "fill_rate": fills / max(1, res.orders_placed), "actions": actions,
        "actions_per_filled_usd": actions / vol if vol else None,
        "mode_share": {k: round(v / total_mode, 3) for k, v in res.mode_time_s.items()},
        "risk_events": len(res.risk_events),
    }
