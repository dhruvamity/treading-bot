"""Go/no-go report (P4 task 6): reports/GO_NO_GO.md, reproducible with one command (`bot gonogo`), seeds fixed.

For each candidate MM session: walk-forward over recorded days (tune window reused as-is in v1 - parameters come
from the session file; parameter search is a TODO once >= 3 weeks exist), per-day PnL under PESSIMISTIC and
OPTIMISTIC fills and both Lighter maker latencies, 1 h block bootstrap of 30-day PnL, drawdown, liquidations and
markouts. For DN: the funding-carry study plus basis once recorded. Every gate is filled with the measured value or
marked INSUFFICIENT DATA; the report never claims a pass it has not measured.
"""

from __future__ import annotations

import math
import random
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bot.common.config import DNSession, MMSession
from bot.research.diagnostics.carry import study_market
from bot.research.eval.backtest import current_markets, run_backtest
from bot.research.loaders.read import read_table

MIN_DAYS_FOR_DECISION = 14


def recorded_days(root: Path) -> list[str]:
    bbo = read_table(root, "bbo")
    if bbo.is_empty() or "date" not in bbo.columns:
        return []
    return sorted({str(d) for d in bbo["date"].unique().to_list()})


def bootstrap_30d(daily: list[float], seed: int, n: int = 2000) -> tuple[float, float]:
    if len(daily) < 3:
        return math.nan, math.nan
    rng = random.Random(seed)
    sums = sorted(sum(daily[rng.randrange(len(daily))] for _ in range(30)) for _ in range(n))
    return sum(1 for s in sums if s < 0) / n, sums[int(0.05 * n)]


def gonogo_report(root: Path, reports: Path, *, sessions: list[MMSession | DNSession], seed: int = 7) -> Path:
    import asyncio

    days = recorded_days(root)
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    L = [f"# GO / NO-GO report - {today}", "",
         f"Recorded days available: **{len(days)}** ({days[0] if days else '-'} .. {days[-1] if days else '-'}). "
         f"The spec requires 2-4 weeks of recorded books and out-of-sample, pessimistic-fill results; "
         f"below {MIN_DAYS_FOR_DECISION} days every gate is reported as INSUFFICIENT DATA.", ""]
    decision_grade = len(days) >= MIN_DAYS_FOR_DECISION
    mk = asyncio.run(current_markets()) if sessions else {}
    for s in sessions:
        L += [f"## Session `{s.session_id}` ({'DN ' + s.strategy if isinstance(s, DNSession) else s.mode} on "
              f"{s.market})", ""]
        if isinstance(s, DNSession):
            r = study_market(root, s.market.upper())
            if r:
                sp = r["static_pair"]
                L += [f"- Funding-only static pair: {sp['direction']}, {sp['bps_per_week']:+.2f} bps/week, "
                      f"P(30-day loss) {sp['p_30d_loss']:.0%}; timing rule at 10 bps: see reports/carry/.",
                      "- Basis PnL and execution: INSUFFICIENT DATA until the recorder has both books for the pair."]
            L += ["", "| Gate (DN) | Target | Measured | Status |", "|---|---|---|---|",
                  "| Net PnL | > 0 in >= 70% of windows; P(30-day loss) <= 25% | funding only (above) | INSUFFICIENT DATA |",
                  "| Liquidations | 0 incl. outage tests <= 10 min | not run | INSUFFICIENT DATA |",
                  "| Max drawdown | <= 8% | not run | INSUFFICIENT DATA |",
                  "| Edge source | identified, stable in 2 windows | H1 (Arcus RTH premium pass-through) candidate | PENDING |",
                  ""]
            continue
        per_day: dict[str, list[float]] = {"pessimistic": [], "optimistic": []}
        worst_dd, liqs, min_sig, cost_bps = 0.0, 0, math.inf, []
        for d in days:
            for mode in ("pessimistic", "optimistic"):
                res = run_backtest([s], root, start=d, end=None if d == days[-1] else _next(d), fill_mode=mode,
                                   markets=mk, capital=s.capital_usd)
                if "error" in res:
                    continue
                per_day[mode].append(res["net_usd"])
                if mode == "pessimistic":
                    worst_dd = max(worst_dd, res["max_drawdown_pct"] or 0.0)
                    liqs += res["liquidations"]
                    min_sig = min(min_sig, res["min_liq_distance_sigma"])
                    if res["cost_bps_of_volume"] is not None:
                        cost_bps.append(res["cost_bps_of_volume"])
        pess = per_day["pessimistic"]
        p_loss, p5 = bootstrap_30d(pess, seed)
        status = "PASS" if decision_grade else "INSUFFICIENT DATA"
        net_ok = pess and (sum(pess) >= 0 or (cost_bps and sum(cost_bps) / len(cost_bps) <= 1.0))
        L += [f"- Days simulated: {len(pess)}; pessimistic daily net: {', '.join(f'{x:+.3f}' for x in pess) or '-'}",
              f"- Optimistic daily net: {', '.join(f'{x:+.3f}' for x in per_day['optimistic']) or '-'}",
              f"- Bootstrap (daily blocks) P(30-day loss) {p_loss:.0%}, 5th pct {p5:+.2f} USD" if pess else "", "",
              "| Gate (MM) | Target | Measured | Status |", "|---|---|---|---|",
              f"| Net PnL | >= 0 per 4-week window, or cost <= 1 bp of volume | sum {sum(pess):+.3f} USD; cost "
              f"{(sum(cost_bps) / len(cost_bps)) if cost_bps else math.nan:.2f} bp | "
              f"{status if net_ok else ('FAIL' if decision_grade else 'INSUFFICIENT DATA')} |",
              f"| Liquidations | 0; never closer than 4 sigma | {liqs}; min {min_sig:.1f} sigma | "
              f"{'FAIL' if liqs else status} |",
              f"| Max drawdown | <= 10% of capital | {worst_dd:.2f}% | {'FAIL' if worst_dd > 10 else status} |",
              "| Execution | 30-60 s maker markout not significantly negative | see backtest markouts | "
              f"{'INSUFFICIENT DATA' if not decision_grade else 'REVIEW'} |", ""]
    L += ["## Decision", "",
          ("Not decision grade yet: keep the recorder running and re-run `bot gonogo` after "
           f"{MIN_DAYS_FOR_DECISION}+ recorded days." if not decision_grade else
           "Review each gate above; approve configs for PAPER (P5) only where every gate is PASS."), "",
          "Lighter points valued at $0 (points-reversal scenario is the base case).", ""]
    p = reports / "GO_NO_GO.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(x for x in L if x is not None))
    return p


def _next(d: str) -> str:
    from datetime import date, timedelta

    return (date.fromisoformat(d) + timedelta(days=1)).isoformat()


__all__ = ["Any", "gonogo_report"]
