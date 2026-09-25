"""Synthetic market tapes for the paper farm: a pipeline test and a mechanics study, NOT evidence of real edge.

The results depend entirely on the assumptions below. They show how each strategy family reacts to chop, trend and
toxic (informed) flow, with the same simulator the live tapes go through. Real-market conclusions need recorded
tapes (`bot farm run`).

Model, one second at a time:
- Efficient price: a random walk. Regimes switch per minute (Markov chain): calm, volatile (3x the volatility) and
  trending (a drift of `trend_bps_min` per minute), plus rare jumps.
- Market makers quote around a lagged copy of it: book mid = EMA(efficient price, `lag_s`) + temporary impact. The
  spread is 1..3 x `spread_ticks` ticks, re-drawn now and then. Each level holds a random depth.
- Noise takers arrive at `noise_per_s` with lognormal sizes. Each is one taker order (one sequence number) that
  walks the book level by level, printing at each price it takes. Its impact is temporary (it decays with a
  `revert_s` half-life): the "sweep and bounce back" flow that pays deep quotes.
- Informed takers trade when the efficient price has moved beyond the touch (the makers' quotes are stale): with
  probability `informed_p` each such second, in the direction of the move. They move the makers' mid to the new
  price at once. This is the flow that picks off quotes at the touch.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np

from bot.scout.tape import DayTape

S = 1_000_000


@dataclass(frozen=True)
class Profile:
    name: str
    p0: float
    tick: float
    step: float
    sigma_1m_bps: float          # calm-regime volatility per minute
    spread_ticks: int            # the narrowest spread; the book draws 1..3x this
    level_usd: float             # median depth per price level
    level_gap_ticks: float       # mean distance between price levels (books are sparse away from the touch)
    noise_per_s: float           # noise taker orders per second
    noise_usd: float             # median noise taker order
    informed_p: float = 0.3      # chance per stale second that an informed taker trades
    lag_s: float = 3.0           # how slowly the makers follow the efficient price
    revert_s: float = 8.0        # half-life of a sweep's temporary impact
    impact_bps_per_10k: float = 1.0   # temporary impact of a $10k sweep
    imf: float = 0.04            # initial margin fraction (max leverage = 1 / imf)
    mmf: float = 0.02
    min_notional: float = 5.0


PROFILES = {
    # an Arcus index perp: thin, a wide-ish book, low volatility (QQQ trades about $1M a day on Arcus)
    "index": Profile("index", 747.0, 0.01, 0.001, 1.5, 8, 1_500, 4.0, 0.012, 1_500, imf=0.04, mmf=0.02),
    # a crypto major: tight book, a lot of flow
    "major": Profile("major", 86_600.0, 0.1, 0.00001, 3.0, 2, 12_000, 2.0, 0.08, 4_000, imf=0.05, mmf=0.025),
    # a mid-cap alt: wider, more volatile, less flow
    "alt": Profile("alt", 118.0, 0.001, 0.01, 6.0, 12, 1_000, 6.0, 0.03, 1_200, imf=0.1, mmf=0.05),
}


@dataclass(frozen=True)
class Regimes:
    """Per-minute Markov chain over calm / volatile / trend-up / trend-down (rows: from, columns: to)."""

    name: str
    p: tuple[tuple[float, ...], ...]
    trend_bps_min: float = 1.5
    vol_x: float = 3.0
    jump_per_h: float = 0.5
    jump_bps: float = 15.0


REGIMES = {
    "chop": Regimes("chop", ((0.98, 0.02, 0.0, 0.0), (0.2, 0.8, 0.0, 0.0), (1.0, 0, 0, 0), (1.0, 0, 0, 0)),
                    jump_per_h=0.2),
    "trend": Regimes("trend", ((0.9, 0.0, 0.05, 0.05), (0.5, 0.5, 0.0, 0.0), (0.02, 0.0, 0.98, 0.0),
                               (0.02, 0.0, 0.0, 0.98)), trend_bps_min=1.5),
    "mixed": Regimes("mixed", ((0.95, 0.02, 0.015, 0.015), (0.3, 0.7, 0.0, 0.0), (0.05, 0.02, 0.93, 0.0),
                               (0.05, 0.02, 0.0, 0.93)), trend_bps_min=1.0, jump_per_h=0.5),
}


@dataclass
class _Book:
    mid: float
    spread: int
    impact: float = 0.0
    depth_bid: list[float] = field(default_factory=list)
    depth_ask: list[float] = field(default_factory=list)


def generate(profile: Profile, regimes: Regimes, *, hours: float, start_us: int, seed: int = 1,
             market: str = "SYN-USD") -> tuple[DayTape, dict[str, Any]]:
    """A tape (best bid/offer rows and trades) and an Arcus-style market record for it."""
    rng = np.random.default_rng(seed)
    pr = profile
    n = int(hours * 3600)
    sig_s = pr.sigma_1m_bps * 1e-4 / math.sqrt(60)
    reg = 0
    x = math.log(pr.p0)
    ema = x
    a_lag = 1 - math.exp(-1 / pr.lag_s)
    decay = 0.5 ** (1 / pr.revert_s)
    tick = pr.tick
    spread = pr.spread_ticks
    impact = 0.0
    bbo: list[tuple[int, float, float, float, float]] = []
    trades: list[tuple[int, float, float, bool, int, int]] = []
    seq = tid = 0
    n_levels = 16
    p_jump = regimes.jump_per_h / 3600
    probs = np.array(regimes.p, float)
    probs = probs / probs.sum(axis=1, keepdims=True)

    def levels() -> list[tuple[int, float]]:
        """(ticks from the touch, depth USD) for each level, the touch first."""
        gaps = np.concatenate([[0], rng.geometric(1 / max(1.0, pr.level_gap_ticks), n_levels - 1)])
        return list(zip(np.cumsum(gaps).tolist(), (pr.level_usd * rng.lognormal(0, 0.8, n_levels)).tolist(),
                        strict=True))

    depth_b, depth_a = levels(), levels()
    last_quote: tuple[float, float] | None = None
    for s in range(n):
        t = start_us + s * S
        if s % 60 == 0:
            reg = int(rng.choice(4, p=probs[reg]))
        vol = sig_s * (regimes.vol_x if reg == 1 else 1.0)
        drift = {2: 1, 3: -1}.get(reg, 0) * regimes.trend_bps_min * 1e-4 / 60
        x += drift + vol * rng.standard_normal()
        if rng.random() < p_jump:
            x += (1 if rng.random() < 0.5 else -1) * regimes.jump_bps * 1e-4
        ema += a_lag * (x - ema)
        impact *= decay
        if rng.random() < 0.02:
            spread = int(pr.spread_ticks * rng.integers(1, 4))
        if rng.random() < 0.05:
            depth_b, depth_a = levels(), levels()
        m = math.exp(ema) * (1 + impact)
        bid = math.floor((m - spread * tick / 2) / tick) * tick
        ask = bid + spread * tick
        # informed taker: the efficient price is beyond the touch
        xp = math.exp(x)
        events: list[tuple[int, bool, float, bool]] = []    # (offset us, taker buys, notional, informed)
        if (xp > ask or xp < bid) and rng.random() < pr.informed_p:
            events.append((int(rng.integers(0, S)), xp > ask, pr.noise_usd * rng.lognormal(0.3, 0.8), True))
        k = rng.poisson(pr.noise_per_s)
        for _ in range(k):
            events.append((int(rng.integers(0, S)), bool(rng.random() < 0.5),
                           pr.noise_usd * rng.lognormal(0, 1.1), False))
        events.sort()
        if last_quote != (bid, ask):
            bbo.append((t, bid, ask, depth_b[0][1] / bid, depth_a[0][1] / ask))
            last_quote = (bid, ask)
        for off, buy, usd, informed in events:
            seq += 1
            ts = t + off
            depth = depth_a if buy else depth_b
            left = usd
            lvl = 0
            px = ask if buy else bid
            while left > 0 and lvl < n_levels:
                gap, size = depth[lvl]
                take = min(left, size)
                p = px + (gap * tick if buy else -gap * tick)
                tid += 1
                trades.append((ts, p, take / p, buy, seq, tid))
                left -= take
                lvl += 1
            if informed:
                ema = x   # the makers catch up with the new price
            else:
                impact += (1 if buy else -1) * pr.impact_bps_per_10k * 1e-4 * usd / 10_000
            m = math.exp(ema) * (1 + impact)
            nb = math.floor((m - spread * tick / 2) / tick) * tick
            if (nb, nb + spread * tick) != (bid, ask):
                bid, ask = nb, nb + spread * tick
                bbo.append((ts + 1, bid, ask, depth_b[0][1] / bid, depth_a[0][1] / ask))
                last_quote = (bid, ask)
    b = np.array(bbo, np.float64)
    order = np.argsort(b[:, 0], kind="stable")
    b = b[order]
    tr = np.array([r[:3] for r in trades], np.float64).reshape(-1, 3)
    tape = DayTape(market, "synthetic",
                   {"ts": b[:, 0].astype(np.int64), "bid": b[:, 1], "ask": b[:, 2], "bid_sz": b[:, 3],
                    "ask_sz": b[:, 4]},
                   {"ts": tr[:, 0].astype(np.int64), "px": tr[:, 1], "sz": tr[:, 2],
                    "buy": np.array([r[3] for r in trades], bool), "seq": np.array([r[4] for r in trades], np.int64),
                    "tid": np.array([r[5] for r in trades], np.int64)})
    meta = {"marketDisplayName": market, "tickSize": str(pr.tick), "stepSize": str(pr.step),
            "minOrderNotional": str(pr.min_notional), "minOrderSize": str(pr.step),
            "initialMarginFraction": str(pr.imf), "offHoursInitialMarginFraction": str(pr.imf),
            "maintenanceMarginFraction": str(pr.mmf), "regularTradingHours": None, "status": "ONLINE",
            "markPrice": str(pr.p0)}
    return tape, meta


def variant(profile: Profile, **kw: Any) -> Profile:
    return replace(profile, **kw)
