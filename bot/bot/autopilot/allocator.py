"""Capital allocator (spec Step 5 last paragraph, P4 task 5).

Score = expected net PnL per $ per day + lambda x points per $ - risk penalty. At $100 total: one market-making
market on Arcus and at most one DN pair. Candidates come from research (diagnostics / backtests) or live estimates.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Candidate:
    venue: str
    market: str
    mode: str
    exp_pnl_per_usd_day: float
    points_per_usd_day: float = 0.0
    risk_penalty: float = 0.0
    kind: str = "mm"  # mm | dn

    def score(self, lam: float) -> float:
        return self.exp_pnl_per_usd_day + lam * self.points_per_usd_day - self.risk_penalty


def allocate(cands: list[Candidate], *, lam: float = 0.0, max_mm_per_venue: int = 1, max_dn: int = 1,
             min_score: float = 0.0) -> list[Candidate]:
    """Top candidates by score, respecting slot limits. `lam` = 0 values points at $0 (backtest default)."""
    chosen: list[Candidate] = []
    mm_count: dict[str, int] = {}
    dn = 0
    for c in sorted(cands, key=lambda c: c.score(lam), reverse=True):
        if c.score(lam) < min_score:
            break
        if c.kind == "dn":
            if dn >= max_dn:
                continue
            dn += 1
        else:
            if mm_count.get(c.venue, 0) >= max_mm_per_venue:
                continue
            mm_count[c.venue] = mm_count.get(c.venue, 0) + 1
        chosen.append(c)
    return chosen
