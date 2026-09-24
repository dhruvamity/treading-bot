"""Market data hub (P2 task 1): one in-memory view per venue/market, fed identically by live WebSocket
callbacks or by the simulator's event stream.

Derived at 1 s: mid, spread (bps), depth at q and 5q, EWMA realised vol at 1 s / 1 min / 1 h (as fractions per
horizon), rolling medians for the safety pause, 1-minute bars for Signal/Autopilot features, and staleness.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal

from bot.core.book import L2Book
from bot.venues.base import PublicTrade, Venue

US = 1_000_000


class EwmaVar:
    """EWMA of squared log returns sampled on a fixed grid; `sigma()` is the per-step volatility."""

    def __init__(self, halflife_steps: float) -> None:
        self.alpha = 1 - math.exp(math.log(0.5) / halflife_steps)
        self.var: float | None = None
        self.n = 0

    def update(self, r: float) -> None:
        self.n += 1
        self.var = r * r if self.var is None else (1 - self.alpha) * self.var + self.alpha * r * r

    def sigma(self) -> float:
        return math.sqrt(self.var) if self.var else 0.0


class RollingMedian:
    """Median over the last `n` samples (small n; O(n log n) query is fine at 1 Hz)."""

    def __init__(self, n: int) -> None:
        self.buf: deque[float] = deque(maxlen=n)

    def add(self, x: float) -> None:
        self.buf.append(x)

    def median(self) -> float | None:
        if not self.buf:
            return None
        s = sorted(self.buf)
        k = len(s) // 2
        return s[k] if len(s) % 2 else 0.5 * (s[k - 1] + s[k])


@dataclass
class Bar:
    t0_us: int
    o: float
    h: float
    l: float  # noqa: E741
    c: float
    vol: float = 0.0
    notional: float = 0.0
    trades: int = 0


@dataclass
class MarketView:
    venue: Venue
    base: str
    book: L2Book = field(default_factory=L2Book)
    book_ts_us: int = 0
    trades: deque[PublicTrade] = field(default_factory=lambda: deque(maxlen=5000))
    mark: Decimal | None = None
    oracle: Decimal | None = None
    index: Decimal | None = None
    price_ts_us: int = 0
    predicted_funding_h: float | None = None
    last_funding_h: float | None = None
    premium: float | None = None
    is_outside_rth: bool = False
    upper_bound: Decimal | None = None
    lower_bound: Decimal | None = None
    upper_in_zone: bool = False
    lower_in_zone: bool = False
    oi: Decimal | None = None
    oi_cap: Decimal | None = None
    status: str | None = None     # Arcus market status from the live markets channel (None: not seen yet)
    # derived
    vol_1s: EwmaVar = field(default_factory=lambda: EwmaVar(60))  # per-second vol, 1-min halflife
    vol_1m: EwmaVar = field(default_factory=lambda: EwmaVar(30))  # per-minute vol, 30-min halflife
    vol_1h: EwmaVar = field(default_factory=lambda: EwmaVar(24))  # per-hour vol, 24-h halflife
    spread_med_1h: RollingMedian = field(default_factory=lambda: RollingMedian(3600))
    depth_med_1h: RollingMedian = field(default_factory=lambda: RollingMedian(3600))
    last_move_1s: float = 0.0
    _last_mid_1s: float | None = None
    _last_mid_1m: float | None = None
    _last_mid_1h: float | None = None
    _last_1m_us: int = 0
    _last_1h_us: int = 0
    bars_1m: deque[Bar] = field(default_factory=lambda: deque(maxlen=24 * 60))
    _bar: Bar | None = None
    vol_5m_market: deque[tuple[int, float]] = field(default_factory=lambda: deque(maxlen=20_000))

    # ------------------------------------------------------------------ reads
    def mid(self) -> Decimal | None:
        return self.book.mid()

    def mid_f(self) -> float | None:
        m = self.book.mid()
        return float(m) if m is not None else None

    def age_s(self, now_us: int) -> float:
        return (now_us - self.book_ts_us) / US if self.book_ts_us else float("inf")

    def stale(self, now_us: int, max_age_s: float = 2.0) -> bool:
        return self.age_s(now_us) > max_age_s

    def market_volume_usd(self, now_us: int, window_s: float = 300) -> float:
        cutoff = now_us - int(window_s * US)
        return sum(n for t, n in self.vol_5m_market if t >= cutoff)

    def sigma_1h(self) -> float:
        """Hourly vol, blended from the 1-minute estimate when the hourly one is still warming up."""
        if self.vol_1h.n >= 24:
            return self.vol_1h.sigma()
        return self.vol_1m.sigma() * math.sqrt(60)

    # ------------------------------------------------------------------ writes
    def on_trade(self, t: PublicTrade) -> None:
        self.trades.append(t)
        px, sz = float(t.price), float(t.size)
        self.vol_5m_market.append((t.ts_us, px * sz))
        self._bar_update(t.ts_us, px, sz)

    def _bar_update(self, ts: int, px: float, sz: float = 0.0) -> None:
        t0 = ts - ts % (60 * US)
        b = self._bar
        if b is None or b.t0_us != t0:
            if b is not None:
                self.bars_1m.append(b)
            self._bar = Bar(t0, px, px, px, px)
            b = self._bar
        b.h, b.l, b.c = max(b.h, px), min(b.l, px), px
        if sz:
            b.vol += sz
            b.notional += px * sz
            b.trades += 1

    def tick_1s(self, now_us: int) -> None:
        """Called once per second by the hub."""
        m = self.mid_f()
        if m is None or m <= 0:
            return
        self._bar_update(now_us, m)
        if self._last_mid_1s:
            r = math.log(m / self._last_mid_1s)
            self.last_move_1s = r
            self.vol_1s.update(r)
        self._last_mid_1s = m
        if now_us - self._last_1m_us >= 60 * US:
            if self._last_mid_1m:
                self.vol_1m.update(math.log(m / self._last_mid_1m))
            self._last_mid_1m, self._last_1m_us = m, now_us
        if now_us - self._last_1h_us >= 3600 * US:
            if self._last_mid_1h:
                self.vol_1h.update(math.log(m / self._last_mid_1h))
            self._last_mid_1h, self._last_1h_us = m, now_us
        sb = self.book.spread_bps()
        if sb is not None:
            self.spread_med_1h.add(sb)
        d = float(self.book.depth_notional(True, within_bps=25) + self.book.depth_notional(False, within_bps=25))
        self.depth_med_1h.add(d)

    def closed_bars(self) -> list[Bar]:
        return list(self.bars_1m)


class MarketDataHub:
    def __init__(self) -> None:
        self.views: dict[tuple[Venue, str], MarketView] = {}

    def view(self, venue: Venue, base: str) -> MarketView:
        k = (venue, base.upper())
        v = self.views.get(k)
        if v is None:
            v = MarketView(venue, base.upper())
            self.views[k] = v
        return v

    def get(self, venue: Venue, base: str) -> MarketView | None:
        return self.views.get((venue, base.upper()))

    def tick_1s(self, now_us: int) -> None:
        for v in self.views.values():
            v.tick_1s(now_us)

    def stale_markets(self, now_us: int, max_age_s: float = 2.0) -> list[tuple[Venue, str]]:
        return [k for k, v in self.views.items() if v.stale(now_us, max_age_s)]
