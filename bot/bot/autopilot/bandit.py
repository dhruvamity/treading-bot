"""Autopilot v2: Thompson-sampling bandit over PRE-TESTED parameter sets ("arms") per mode (P7 task 1).

Context bucket = ER tercile x volatility tercile x session. Reward per hour = net PnL in bps of volume
+ lambda x points proxy - mu x drawdown contribution. Guardrails:
- an arm is eligible only if it passed the P4 gates under pessimistic fills (`eligible=True` set by research);
- exploration uses at most 10% of capital (the runner sizes the exploration slice);
- an arm is dropped after 3 sessions losing more than the threshold;
- no exploration in event windows or during Arcus RWA off-hours.
Modes: "offline" (train on simulator results), "shadow" (propose and log, rules act), "live" (exploration slice only).
Posterior: Normal with known observation variance per arm (conjugate), seeded prior N(0, prior_var).
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field


@dataclass
class Arm:
    arm_id: str
    mode: str
    params: dict[str, float]
    eligible: bool = False
    n: int = 0
    mean: float = 0.0
    prior_var: float = 25.0  # (bps)^2
    obs_var: float = 100.0
    losing_sessions: int = 0
    dropped: bool = False

    def posterior(self) -> tuple[float, float]:
        prec = 1 / self.prior_var + self.n / self.obs_var
        mu = (self.mean * self.n / self.obs_var) / prec
        return mu, 1 / prec

    def update(self, reward: float) -> None:
        self.mean = (self.mean * self.n + reward) / (self.n + 1)
        self.n += 1


def bucket(er: float, sigma_1h: float, session: str, er_edges: tuple[float, float] = (0.2, 0.45),
           vol_edges: tuple[float, float] = (0.003, 0.008)) -> str:
    e = 0 if er < er_edges[0] else 1 if er < er_edges[1] else 2
    v = 0 if sigma_1h < vol_edges[0] else 1 if sigma_1h < vol_edges[1] else 2
    return f"er{e}-vol{v}-{session}"


@dataclass
class ThompsonBandit:
    arms: dict[str, dict[str, Arm]] = field(default_factory=dict)  # bucket -> arm_id -> Arm
    template: list[Arm] = field(default_factory=list)
    mode: str = "shadow"
    lose_threshold_bps: float = -5.0
    max_losing_sessions: int = 3
    seed: int = 7
    _rng: random.Random = field(default_factory=random.Random)
    proposals: list[tuple[str, str]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._rng.seed(self.seed)

    def _bucket_arms(self, b: str) -> dict[str, Arm]:
        if b not in self.arms:
            self.arms[b] = {a.arm_id: Arm(a.arm_id, a.mode, dict(a.params), a.eligible, prior_var=a.prior_var,
                                          obs_var=a.obs_var) for a in self.template}
        return self.arms[b]

    def propose(self, b: str, *, mode: str, blocked: bool = False) -> Arm | None:
        """None when exploration is not allowed (event window / off-hours) or no eligible arm exists."""
        if blocked:
            return None
        cands = [a for a in self._bucket_arms(b).values() if a.mode == mode and a.eligible and not a.dropped]
        if not cands:
            return None
        best, best_draw = None, -math.inf
        for a in cands:
            mu, var = a.posterior()
            draw = self._rng.gauss(mu, math.sqrt(var))
            if draw > best_draw:
                best, best_draw = a, draw
        assert best is not None
        self.proposals.append((b, best.arm_id))
        return best

    def reward(self, b: str, arm_id: str, *, pnl_bps_of_volume: float, points_proxy: float = 0.0, lam: float = 0.0,
               drawdown_bps: float = 0.0, mu: float = 1.0) -> float:
        r = pnl_bps_of_volume + lam * points_proxy - mu * drawdown_bps
        self._bucket_arms(b)[arm_id].update(r)
        return r

    def end_session(self, b: str, arm_id: str, session_pnl_bps: float) -> bool:
        a = self._bucket_arms(b)[arm_id]
        if session_pnl_bps < self.lose_threshold_bps:
            a.losing_sessions += 1
            if a.losing_sessions >= self.max_losing_sessions:
                a.dropped = True
        return a.dropped
