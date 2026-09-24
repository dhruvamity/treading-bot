"""DN Layer A: funding / basis carry study on the hourly funding panel (P4 task 2, A6.6).

Uses ONLY information available at decision time ("lagged-signal"): at hour t the predictor is the spread PAID at
t-1 (r_L - r_A); live recorded forecasts (Arcus predictedFunding, Lighter current_funding_rate) replace it once the
recorder has history. Costs are swept 0-40 bps round trip. Hypotheses:
  H1 RWA premium pass-through (in-session spread), H2 off-hours asymmetry (Arcus locked, Lighter free),
  H3 divergence episodes (event study on |lagged spread| > theta), H0 no edge.
Evaluation: per-trade and per-week PnL in bps of leg notional, 4-week windows, 24 h block bootstrap of 30-day PnL.
Funding carry only: basis PnL needs recorded books (next report once the recorder has 2-4 weeks).
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from bot.common.time import NEW_YORK
from bot.research.loaders.read import funding_panel

BP = 1e-4
RWA = {"SPY", "QQQ", "NVDA", "TSLA", "AAPL", "MSFT", "AMZN", "GOOGL", "META", "AMD", "INTC", "MU", "BABA", "CRCL",
       "COIN", "PLTR", "ORCL", "SNDK", "CRWV", "SPCX", "BE", "USAR", "SKHY", "SLV", "USO", "SGOV"}
HORIZONS = (4, 8, 12, 24, 48, 72)
COSTS_BPS = (0, 5, 10, 20, 30, 40)


def ar1_halflife(x: np.ndarray) -> float:
    if len(x) < 10:
        return math.nan
    a, b = x[:-1] - x[:-1].mean(), x[1:] - x[1:].mean()
    phi = float((a * b).sum() / (a * a).sum()) if (a * a).sum() > 0 else 0.0
    if phi <= 0 or phi >= 1:
        return 0.0 if phi <= 0 else math.inf
    return math.log(0.5) / math.log(phi)


def run_lengths(mask: np.ndarray) -> list[int]:
    out, n = [], 0
    for m in mask:
        if m:
            n += 1
        elif n:
            out.append(n)
            n = 0
    if n:
        out.append(n)
    return out


@dataclass
class TradeSim:
    trades: list[tuple[int, float]]  # (entry index, carry bps before costs)


def simulate(spread: np.ndarray, theta: float, H: int) -> TradeSim:
    """Enter at t when |spread[t-1]| > theta in the direction of its sign, hold H hours (non-overlapping)."""
    trades = []
    t = 1
    n = len(spread)
    while t + H <= n:
        s = spread[t - 1]
        if abs(s) > theta:
            d = 1.0 if s > 0 else -1.0
            carry = float(d * spread[t:t + H].sum()) / BP
            trades.append((t, carry))
            t += H
        else:
            t += 1
    return TradeSim(trades)


def block_bootstrap_30d(hourly_pnl_bps: np.ndarray, n_boot: int = 2000, block_h: int = 24, seed: int = 7) -> tuple[float, float]:
    """(P(30-day PnL < 0), 5th percentile) from 24 h blocks."""
    rng = random.Random(seed)
    n = len(hourly_pnl_bps)
    if n < block_h * 2:
        return math.nan, math.nan
    blocks = [hourly_pnl_bps[i:i + block_h] for i in range(0, n - block_h + 1, block_h)]
    k = 30
    sums = []
    for _ in range(n_boot):
        sums.append(sum(float(blocks[rng.randrange(len(blocks))].sum()) for _ in range(k)))
    arr = np.array(sums)
    return float((arr < 0).mean()), float(np.percentile(arr, 5))


def session_of(ts_us: int) -> str:
    et = datetime.fromtimestamp(ts_us / 1e6, tz=UTC).astimezone(NEW_YORK)
    if et.weekday() >= 5:
        return "weekend"
    h = et.hour + et.minute / 60
    return "rth" if 4 <= h < 20 else "off"


def study_market(root: Path, market: str) -> dict[str, Any] | None:
    p = funding_panel(root, market)
    if p.is_empty() or p.height < 48:
        return None
    ts = p["funding_ts_us"].to_numpy()
    ra, rl = p["r_a"].to_numpy(), p["r_l"].to_numpy()
    spread = rl - ra  # LONG Arcus / SHORT Lighter receives (r_L - r_A) per unit notional per hour
    ann = 24 * 365 * 100
    sess = np.array([session_of(int(t)) for t in ts])
    res: dict[str, Any] = {
        "market": market, "hours": len(spread),
        "from": datetime.fromtimestamp(ts[0] / 1e6, tz=UTC).strftime("%Y-%m-%d"),
        "to": datetime.fromtimestamp(ts[-1] / 1e6, tz=UTC).strftime("%Y-%m-%d"),
        "r_a_ann_pct": float(ra.mean() * ann), "r_l_ann_pct": float(rl.mean() * ann),
        "spread_ann_pct": float(spread.mean() * ann), "spread_sd_h_bps": float(spread.std() / BP),
        "frac_spread_pos": float((spread > 0).mean()), "spread_halflife_h": ar1_halflife(spread),
        "abs_spread_p50_bps_h": float(np.median(np.abs(spread)) / BP),
        "abs_spread_p90_bps_h": float(np.percentile(np.abs(spread), 90) / BP),
    }
    by_sess = {}
    for s in ("rth", "off", "weekend"):
        m = sess == s
        if m.sum() > 10:
            by_sess[s] = {"hours": int(m.sum()), "spread_ann_pct": float(spread[m].mean() * ann),
                          "r_a_ann_pct": float(ra[m].mean() * ann), "r_l_ann_pct": float(rl[m].mean() * ann)}
    res["by_session"] = by_sess
    theta = max(np.percentile(np.abs(spread), 75), 0.0000005)
    res["episode_theta_bps_h"] = float(theta / BP)
    rl_ = run_lengths(np.abs(spread) > theta)
    res["episode_len_h_p50"] = float(np.median(rl_)) if rl_ else 0.0
    res["episode_len_h_p90"] = float(np.percentile(rl_, 90)) if rl_ else 0.0
    # H3 event study with the lagged signal, then costs
    grid = []
    for H in HORIZONS:
        sim = simulate(spread, theta, H)
        if not sim.trades:
            continue
        carries = np.array([c for _, c in sim.trades])
        for c in COSTS_BPS:
            net = carries - c
            grid.append({"H": H, "cost_bps": c, "trades": len(net), "mean_net_bps": float(net.mean()),
                         "win_rate": float((net > 0).mean()), "total_bps": float(net.sum())})
    res["event_study"] = grid
    # A "carry-neutral points hedge": hold LONG A / SHORT L permanently (or the better static direction)
    static_dir = 1.0 if spread.mean() >= 0 else -1.0
    hourly = static_dir * spread / BP
    windows = [float(hourly[i:i + 24 * 28].sum()) for i in range(0, len(hourly) - 24 * 28 + 1, 24 * 7)]
    p_loss, p5 = block_bootstrap_30d(hourly)
    res["static_pair"] = {"direction": "long A / short L" if static_dir > 0 else "short A / long L",
                          "bps_per_week": float(hourly.mean() * 24 * 7),
                          "frac_4w_windows_positive": float(np.mean([w > 0 for w in windows])) if windows else math.nan,
                          "p_30d_loss": p_loss, "p5_30d_bps": p5}
    return res


def best_rule(res: dict[str, Any], cost_bps: float) -> dict[str, Any] | None:
    rows = [r for r in res.get("event_study", []) if r["cost_bps"] == cost_bps]
    if not rows:
        return None
    best: dict[str, Any] = max(rows, key=lambda r: r["total_bps"])
    return best


def carry_study(root: Path, markets: list[str], reports: Path) -> Path:
    results = [r for m in markets if (r := study_market(root, m)) is not None]
    day = datetime.now(UTC).strftime("%Y-%m-%d")
    out = reports / "carry" / day
    out.mkdir(parents=True, exist_ok=True)
    pl.DataFrame([{k: v for k, v in r.items() if not isinstance(v, dict | list)} for r in results]).write_parquet(
        out / "summary.parquet")
    L: list[str] = [
        f"# DN carry study (funding only) - {day}", "",
        "Hourly funding history from both venues (Arcus from 2026-06-24, Lighter RH from 2026-06-26; Lighter rates "
        "reconstructed as value / mark-candle price). Signal = the spread paid in the previous hour (lagged, no "
        "look-ahead). Pair = LONG Arcus / SHORT Lighter earns r_L - r_A per hour on its notional; the reverse pair "
        "earns the opposite. Costs are round trip in bps of one leg's notional. **Basis PnL is not included** "
        "(needs recorded books), and points are valued at $0.", "",
        "## Summary", "",
        "| Market | Hours | r_A %/yr | r_L %/yr | Spread %/yr | Spread sd bp/h | Half-life h | |spread| p90 bp/h |"
        " Episode p50/p90 h |", "|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        L.append(f"| {r['market']} | {r['hours']} | {r['r_a_ann_pct']:.2f} | {r['r_l_ann_pct']:.2f} | "
                 f"{r['spread_ann_pct']:+.2f} | {r['spread_sd_h_bps']:.3f} | {r['spread_halflife_h']:.1f} | "
                 f"{r['abs_spread_p90_bps_h']:.3f} | {r['episode_len_h_p50']:.0f}/{r['episode_len_h_p90']:.0f} |")
    L += ["", "## Session split (H1 in-session pass-through, H2 off-hours asymmetry)", "",
          "| Market | Session | Hours | r_A %/yr | r_L %/yr | Spread %/yr |", "|---|---|---|---|---|---|"]
    for r in results:
        for s, v in r["by_session"].items():
            L.append(f"| {r['market']} | {s} | {v['hours']} | {v['r_a_ann_pct']:.2f} | {v['r_l_ann_pct']:.2f} | "
                     f"{v['spread_ann_pct']:+.2f} |")
    L += ["", "## H3 event study: enter when |lagged spread| > p75, hold H hours", "",
          "Best horizon per market at each round-trip cost (mean net bps per trade, trades, win rate):", "",
          "| Market | cost 0 | cost 5 | cost 10 | cost 20 | cost 30 | cost 40 |", "|---|---|---|---|---|---|---|"]
    verdict_edge = []
    for r in results:
        cells = []
        for c in COSTS_BPS:
            b = best_rule(r, c)
            cells.append("-" if b is None else f"{b['mean_net_bps']:+.1f} (H{b['H']}, n={b['trades']}, "
                                               f"{b['win_rate']:.0%})")
            if c == 10 and b is not None and b["mean_net_bps"] > 0 and b["win_rate"] >= 0.6 and b["trades"] >= 10:
                verdict_edge.append(r["market"])
        L.append(f"| {r['market']} | " + " | ".join(cells) + " |")
    L += ["", "## Static pair (points hedge cost)", "",
          "Holding the better static direction for the whole sample (no timing), funding only:", "",
          "| Market | Direction | bps/week | 4-week windows > 0 | P(30-day loss) | 5th pct 30-day bps |",
          "|---|---|---|---|---|---|"]
    for r in results:
        s = r["static_pair"]
        L.append(f"| {r['market']} | {s['direction']} | {s['bps_per_week']:+.2f} | {s['frac_4w_windows_positive']:.0%} | "
                 f"{s['p_30d_loss']:.0%} | {s['p5_30d_bps']:+.1f} |")
    L += ["", "## Reading", "",
          "- A timing rule counts as an edge candidate only if, at a realistic 10 bps round trip, the best horizon is "
          "positive per trade with >= 60% winners over >= 10 trades.",
          f"- Candidates at 10 bps: **{', '.join(verdict_edge) if verdict_edge else 'none (H0 holds on funding alone)'}**.",
          "- This uses rates PAID (lagged); recorded predicted rates should sharpen entries. Re-run after 2-4 weeks "
          "of recording with basis PnL and the go/no-go gates (`bot gonogo`).",
          "- Static-pair rows are the cost (negative) or carry (positive) of holding a hedged pair for Lighter OI, "
          "before basis and execution costs: the 'points-hedge cost' in the spec's no-edge fallback.", ""]
    p = out / "CARRY_STUDY.md"
    p.write_text("\n".join(L))
    return p
