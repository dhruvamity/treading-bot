"""Synthetic markets for tests and sanity runs (E1 paths): range-bound sine + noise, linear trend, jump, gap.

Generates, per step: a book snapshot around the path price (depth ladder), and taker trades that print at the touch
with Poisson-ish frequency so passive quotes near the touch get filled by queue decrements, plus occasional
trade-throughs on jumps. Deterministic for a given seed.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable
from decimal import Decimal

from bot.research.sim.events import Event, ev
from bot.venues.base import PublicTrade, Side, Venue

Path = Callable[[float], float]  # t in seconds -> price


def sine_path(p0: float, amp_frac: float, period_s: float, noise_frac: float, seed: int = 1) -> Path:
    rng = random.Random(seed)
    cache: dict[int, float] = {}

    def f(t: float) -> float:
        k = int(t)
        if k not in cache:
            cache[k] = rng.gauss(0, noise_frac)
        return p0 * (1 + amp_frac * math.sin(2 * math.pi * t / period_s) + cache[k])

    return f


def trend_path(p0: float, drift_frac_per_h: float, noise_frac: float = 0.0, seed: int = 2) -> Path:
    rng = random.Random(seed)
    cache: dict[int, float] = {}

    def f(t: float) -> float:
        k = int(t)
        if k not in cache:
            cache[k] = rng.gauss(0, noise_frac)
        return p0 * (1 + drift_frac_per_h * t / 3600 + cache[k])

    return f


def jump_path(p0: float, at_s: float, jump_frac: float) -> Path:
    return lambda t: p0 * (1 + (jump_frac if t >= at_s else 0.0))


def gap_path(p0: float, gap_start_s: float, gap_end_s: float, gap_frac: float) -> Path:
    """Flat, then no data between gap_start and gap_end (caller skips), reopening at a different level."""
    return lambda t: p0 * (1 + (gap_frac if t >= gap_end_s else 0.0))


def book_events(venue: Venue, base: str, path: Path, *, start_us: int, seconds: int, tick: Decimal,
                step: Decimal, half_spread_ticks: int = 1, levels: int = 20, level_size: Decimal = Decimal("0.5"),
                trades_per_s: float = 2.0, trade_size: Decimal = Decimal("0.05"), seed: int = 3,
                skip: tuple[float, float] | None = None, oracle: bool = True, funding_every_s: int = 3600,
                funding_rate_h: float = 0.0000125) -> list[Event]:
    rng = random.Random(seed)
    out: list[Event] = []
    prev_mid: Decimal | None = None
    for s in range(seconds):
        if skip and skip[0] <= s < skip[1]:
            continue
        t_us = start_us + s * 1_000_000
        px = Decimal(str(path(float(s))))
        mid_ticks = int(px / tick)
        mid = mid_ticks * tick
        bids = [((mid_ticks - half_spread_ticks - i) * tick, level_size) for i in range(levels)]
        asks = [((mid_ticks + half_spread_ticks + i) * tick, level_size) for i in range(levels)]
        out.append(ev(t_us, venue, base, "book_snapshot", (bids, asks), s))
        if oracle:
            out.append(ev(t_us + 1, venue, base, "price", {"mark": mid, "oracle": mid, "index": mid}))
        n = int(trades_per_s) + (1 if rng.random() < trades_per_s - int(trades_per_s) else 0)
        best_bid, best_ask = bids[0][0], asks[0][0]
        for k in range(n):
            taker = Side.BUY if rng.random() < 0.5 else Side.SELL
            price = best_ask if taker is Side.BUY else best_bid
            sz = (trade_size * Decimal(str(0.5 + rng.random()))).quantize(step)
            out.append(ev(t_us + 100 + k, venue, base, "trade",
                          PublicTrade(venue, base, t_us + 100 + k, price, sz, taker, f"syn-{venue.value}-{s}-{k}")))
        if prev_mid is not None and abs(mid - prev_mid) > 3 * tick:
            # a jump: takers sweep through the old touch
            taker = Side.BUY if mid > prev_mid else Side.SELL
            out.append(ev(t_us + 500, venue, base, "trade",
                          PublicTrade(venue, base, t_us + 500, mid, trade_size * 4, taker, f"syn-{venue.value}-{s}-j")))
        prev_mid = mid
        if funding_every_s and s > 0 and s % funding_every_s == 0:
            out.append(ev(t_us + 2, venue, base, "funding_paid", {"rate_h": funding_rate_h, "pay_price": mid}))
    return out
