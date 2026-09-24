"""Autopilot v1 rule pipeline (spec "Step 1-3", P4 task 5).

    eligibility (market online; region ok; min notional fits; depth at touch >= 3 x q; OI headroom; no event window;
                 venue healthy: feed lag < 2 s, error rate < 5%)
    -> hard stops (event window, safety pause, markout < -2 bps, Arcus band in expansion zone, spread > 3x median)
         -> PAUSE
    -> local book thin and other venue deep -> BLEND
    -> trending (ER > 0.5 or |trend_z| > 2) -> vol > max -> PAUSE, else RGRID with tilt
    -> ranging (ER < 0.3 and OER > 1.5) -> Arcus tight & calm -> MID, else GRID (DGrid spacing)
    -> otherwise -> SIGNAL or passive GRID
Hysteresis: a new mode must win `confirm_evals` consecutive evaluations and the old mode must have run
`min_dwell_min`, unless a hard stop fires. Every decision carries its reason for the decision log.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from bot.autopilot.features import Features
from bot.common.config import AutopilotCfg
from bot.venues.base import Venue

MODES = ("pause", "blend", "rgrid", "mid", "grid", "signal")


@dataclass(frozen=True, slots=True)
class Eligibility:
    ok: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ModeChoice:
    mode: str
    reason: str
    hard_stop: bool = False
    regime: str = "neutral"


def eligibility(f: Features, *, venue: Venue, online: bool, region_ok: bool, min_notional_usd: float,
                capital_usd: float, q_usd: float, oi_headroom_ok: bool, event_window: bool, feed_lag_s: float,
                error_rate: float) -> Eligibility:
    r = []
    if not online:
        r.append("market not online")
    if not region_ok:
        r.append("region check failed")
    if min_notional_usd * 1.2 > capital_usd:
        r.append("minimum notional does not fit capital")
    if f.depth_q_usd and f.depth_q_usd < 3 * q_usd:
        r.append(f"touch depth ${f.depth_q_usd:.0f} < 3x order ${q_usd:.0f}")
    if not oi_headroom_ok:
        r.append("no OI-cap headroom")
    if event_window:
        r.append("event window")
    if feed_lag_s > 2.0:
        r.append(f"feed lag {feed_lag_s:.1f}s")
    if error_rate > 0.05:
        r.append(f"order error rate {error_rate:.0%}")
    return Eligibility(not r, tuple(r))


def choose_mode(f: Features, cfg: AutopilotCfg, *, venue: Venue, other_depth_usd: float | None, safety_paused: bool,
                band_zone: bool, event_window: bool) -> ModeChoice:
    # ---- hard stops
    hs = []
    if event_window:
        hs.append("event window")
    if safety_paused:
        hs.append("safety pause")
    if f.markout_60s_bps is not None and f.markout_60s_bps < cfg.markout_stop_bps:
        hs.append(f"markout {f.markout_60s_bps:.1f} bps < {cfg.markout_stop_bps}")
    if band_zone:
        hs.append("Arcus band in expansion zone")
    if f.spread_bps is not None and f.spread_median_bps and f.spread_bps > 3 * f.spread_median_bps:
        hs.append(f"spread {f.spread_bps:.1f} > 3x median {f.spread_median_bps:.1f}")
    if hs:
        return ModeChoice("pause", "hard stop: " + "; ".join(hs), hard_stop=True, regime="stop")
    # ---- thin local book, deep other venue -> Blend
    if other_depth_usd is not None and f.depth_5q_usd < cfg.thin_depth_usd and other_depth_usd > cfg.deep_depth_usd:
        return ModeChoice("blend", f"local depth ${f.depth_5q_usd:.0f} thin, other venue ${other_depth_usd:.0f} deep",
                          regime="thin")
    trending = f.er > cfg.er_trend or abs(f.trend_z) > cfg.trend_z
    if trending:
        if f.sigma_1h > cfg.vol_max_1h:
            return ModeChoice("pause", f"trend with sigma_1h {f.sigma_1h:.4f} > max {cfg.vol_max_1h}", regime="trend")
        return ModeChoice("rgrid", f"trending (ER {f.er:.2f}, z {f.trend_z:+.2f})", regime="trend")
    ranging = f.er < cfg.er_range and f.oer > cfg.oer_min
    if ranging:
        tight = f.spread_bps is not None and f.spread_bps <= cfg.tight_spread_bps
        calm = f.variance_ratio <= 1.0
        if venue is Venue.ARCUS and tight and calm:
            return ModeChoice("mid", f"ranging (ER {f.er:.2f}, OER {f.oer:.2f}); Arcus book tight & calm", regime="range")
        return ModeChoice("grid", f"ranging (ER {f.er:.2f}, OER {f.oer:.2f})", regime="range")
    return ModeChoice("signal" if f.n_bars >= 60 else "grid",
                      f"neither trending nor ranging (ER {f.er:.2f}, OER {f.oer:.2f})", regime="neutral")


@dataclass
class ModeSwitcher:
    cfg: AutopilotCfg
    current: str = "pause"
    since_us: int = 0
    candidate: str | None = None
    streak: int = 0
    history: list[tuple[int, str, str]] = field(default_factory=list)

    def update(self, choice: ModeChoice, now_us: int) -> tuple[str, str | None]:
        """Returns (active mode, switch reason or None)."""
        if choice.mode == self.current:
            self.candidate, self.streak = None, 0
            return self.current, None
        if choice.hard_stop:
            return self._switch(choice, now_us, "hard stop overrides hysteresis")
        if choice.mode == self.candidate:
            self.streak += 1
        else:
            self.candidate, self.streak = choice.mode, 1
        dwell_ok = self.current == "pause" or (now_us - self.since_us) / 60e6 >= self.cfg.min_dwell_min
        if self.streak >= self.cfg.confirm_evals and dwell_ok:
            return self._switch(choice, now_us, f"won {self.streak} evaluations")
        return self.current, None

    def _switch(self, choice: ModeChoice, now_us: int, why: str) -> tuple[str, str]:
        old = self.current
        self.current, self.since_us = choice.mode, now_us
        self.candidate, self.streak = None, 0
        self.history.append((now_us, choice.mode, choice.reason))
        return self.current, f"{old} -> {choice.mode}: {choice.reason} ({why})"
