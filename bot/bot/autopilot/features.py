"""Autopilot features (spec Step 2, A6.5). Computed every minute from a MarketView with identical code in live,
paper and research.

    ER (Kaufman)   = |P_t - P_{t-n}| / sum |P_i - P_{i-1}|, n = 60 one-minute bars
    trend_z        = (EMA20 - EMA60) / (P x sigma_1h)
    VarianceRatio  = Var(15-min returns) / (15 x Var(1-min returns))
    OER(delta, W)  = (# delta-level crossings that reverse within W) / (net |excursion| / delta over W)
    markout(tau)   = s x (Mid_{t+tau} - p_fill) / p_fill
    HHI (takers)   = sum share_i^2 over Arcus takerAddress volume shares
"""

from __future__ import annotations

import itertools
import math
from collections import defaultdict, deque
from dataclasses import dataclass, field

from bot.core.marketdata import MarketView
from bot.strategies.quoting import ema

BP = 1e-4


@dataclass
class Features:
    ts_us: int
    sigma_1m: float = 0.0
    sigma_1h: float = 0.0
    er: float = 0.0
    trend_z: float = 0.0
    variance_ratio: float = 1.0
    oer: float = 0.0
    oer_delta_bps: float = 0.0
    spread_bps: float | None = None
    spread_median_bps: float | None = None
    depth_q_usd: float = 0.0
    depth_5q_usd: float = 0.0
    markout_30s_bps: float | None = None
    markout_60s_bps: float | None = None
    taker_hhi: float | None = None
    trades_per_min: float = 0.0
    volume_per_min_usd: float = 0.0
    funding_a: float | None = None
    funding_l: float | None = None
    basis_bps: float | None = None
    basis_z: float | None = None
    session: str = "crypto"
    inventory_usd: float = 0.0
    budget_mode: str = "normal"
    n_bars: int = 0
    extra: dict[str, float] = field(default_factory=dict)


def efficiency_ratio(closes: list[float], n: int = 60) -> float:
    if len(closes) < n + 1:
        return 0.0
    w = closes[-n - 1:]
    path = sum(abs(b - a) for a, b in itertools.pairwise(w))
    return abs(w[-1] - w[0]) / path if path > 0 else 0.0


def variance_ratio(closes: list[float], k: int = 15) -> float:
    if len(closes) < 4 * k + 1:
        return 1.0
    r1 = [math.log(b / a) for a, b in itertools.pairwise(closes) if a > 0 and b > 0]
    rk = [math.log(closes[i + k] / closes[i]) for i in range(0, len(closes) - k, k) if closes[i] > 0]
    if len(r1) < 2 or len(rk) < 2:
        return 1.0
    v1 = _var(r1)
    vk = _var(rk)
    return vk / (k * v1) if v1 > 0 else 1.0


def _var(x: list[float]) -> float:
    m = sum(x) / len(x)
    return sum((v - m) ** 2 for v in x) / (len(x) - 1)


def oer(closes: list[float], delta: float) -> float:
    """Level crossings that reversed / net excursion in levels, on a geometric grid of spacing delta."""
    if len(closes) < 3 or delta <= 0 or closes[0] <= 0:
        return 0.0
    lg = math.log(1 + delta)
    levels = [math.floor(math.log(p / closes[0]) / lg) for p in closes if p > 0]
    crossings = sum(abs(b - a) for a, b in itertools.pairwise(levels))
    net = abs(levels[-1] - levels[0])
    return (crossings - net) / max(net, 1)


def trend_z(closes: list[float], sigma_1h: float) -> float:
    e20, e60 = ema(closes[-120:], 20), ema(closes[-180:], 60)
    if e20 is None or e60 is None or sigma_1h <= 0 or not closes:
        return 0.0
    return (e20 - e60) / (closes[-1] * sigma_1h)


def taker_hhi(view: MarketView, now_us: int, window_s: float = 3600) -> float | None:
    vol: dict[str, float] = defaultdict(float)
    cutoff = now_us - int(window_s * 1e6)
    for t in view.trades:
        if t.ts_us >= cutoff and t.taker_address:
            vol[t.taker_address] += float(t.price * t.size)
    tot = sum(vol.values())
    if tot <= 0:
        return None
    return sum((v / tot) ** 2 for v in vol.values())


@dataclass
class MarkoutTracker:
    """Signed markouts of our (or simulated) maker fills at 1/5/30/60/300 s, in bps."""

    horizons_s: tuple[int, ...] = (1, 5, 30, 60, 300)
    pending: deque[tuple[int, int, float, int]] = field(default_factory=deque)  # (ts, sign, price, idx)
    results: dict[int, deque[float]] = field(default_factory=lambda: defaultdict(lambda: deque(maxlen=500)))

    def add_fill(self, ts_us: int, side_sign: int, price: float) -> None:
        for h in self.horizons_s:
            self.pending.append((ts_us + h * 1_000_000, side_sign, price, h))

    def update(self, now_us: int, mid: float | None) -> None:
        if mid is None:
            return
        keep: deque[tuple[int, int, float, int]] = deque()
        while self.pending:
            due, s, p, h = self.pending.popleft()
            if now_us >= due:
                self.results[h].append(s * (mid - p) / p / BP)
            else:
                keep.append((due, s, p, h))
        self.pending = keep

    def mean(self, h: int) -> float | None:
        r = self.results.get(h)
        return sum(r) / len(r) if r else None


class FeatureEngine:
    def __init__(self, oer_delta_bps: float = 10.0) -> None:
        self.oer_delta_bps = oer_delta_bps
        self.markouts: dict[tuple[str, str], MarkoutTracker] = defaultdict(MarkoutTracker)
        self.basis_hist: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=24 * 60))

    def compute(self, view: MarketView, now_us: int, *, other: MarketView | None = None, q_usd: float = 6.0,
                session: str = "crypto", inventory_usd: float = 0.0, budget_mode: str = "normal") -> Features:
        closes = [b.c for b in view.closed_bars()]
        s1h = view.sigma_1h()
        f = Features(ts_us=now_us, sigma_1m=view.vol_1m.sigma(), sigma_1h=s1h, session=session,
                     inventory_usd=inventory_usd, budget_mode=budget_mode, n_bars=len(closes))
        f.er = efficiency_ratio(closes, 60)
        f.trend_z = trend_z(closes, s1h)
        f.variance_ratio = variance_ratio(closes[-240:], 15)
        f.oer_delta_bps = self.oer_delta_bps
        f.oer = oer(closes[-60:], self.oer_delta_bps * BP)
        f.spread_bps = view.book.spread_bps()
        f.spread_median_bps = view.spread_med_1h.median()
        mid = view.mid_f()
        if mid:
            f.depth_q_usd = float(min(view.book.depth_notional(True, levels=1), view.book.depth_notional(False, levels=1)))
            f.depth_5q_usd = float(min(view.book.depth_notional(True, within_bps=10),
                                       view.book.depth_notional(False, within_bps=10)))
        mk = self.markouts[(view.venue.value, view.base)]
        mk.update(now_us, mid)
        f.markout_30s_bps, f.markout_60s_bps = mk.mean(30), mk.mean(60)
        f.taker_hhi = taker_hhi(view, now_us)
        recent = [t for t in view.trades if t.ts_us >= now_us - 60_000_000]
        f.trades_per_min = float(len(recent))
        f.volume_per_min_usd = sum(float(t.price * t.size) for t in recent)
        f.funding_a = view.predicted_funding_h
        if other is not None:
            f.funding_l = other.predicted_funding_h
            om = other.mid_f()
            if mid and om:
                b = (mid - om) / om / BP
                f.basis_bps = b
                h = self.basis_hist[view.base]
                h.append(b)
                if len(h) > 30:
                    mu = sum(h) / len(h)
                    sd = math.sqrt(_var(list(h))) if len(h) > 1 else 0.0
                    f.basis_z = (b - mu) / sd if sd > 0 else 0.0
        return f
