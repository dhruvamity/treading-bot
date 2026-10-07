"""The market's regime now: the last hour's volatility against its usual level at this hour of the week, and shocks.

Research (2026-09-27, 8 weeks of BTC): the cost per dollar of a market-making hour follows the volatility of the
hour before it, whatever the session. BTC Mid 0 costs about 1.1 bp after a calm hour (under 0.75x usual), 1.9-2.0
after a normal one and 2.7 after a wild one (over 2x). A trend bias does not help; waiting for a calm hour does.

- ratio:  realized volatility of the last 60 one-minute mid moves over the median of the same measure at this UTC
          hour on the same kind of day (weekend or weekday, arcus/scout/sessions.py) over the last 28 days;
- bucket: calm < 0.75x <= normal < 1.25x <= busy < 2x <= wild;
- shock:  a one-minute move in the last 15 minutes over 6x the usual one-minute volatility and over 15 bp
          (news, a liquidation cascade): the autopilot stays flat for COOL_S after it.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any

import numpy as np

from arcus.scout import sessions
from arcus.scout.tape import US_DAY, TapeStore

S = 1_000_000
MIN = 60 * S
BUCKETS = ("calm", "normal", "busy", "wild")
EDGES = (0.75, 1.25, 2.0)
SHOCK_SIGMA = 6.0
SHOCK_MIN_BPS = 15.0
SHOCK_LOOK_MIN = 15
COOL_S = 30 * 60
USUAL_DAYS = 28


def bucket(ratio: float) -> str:
    if not math.isfinite(ratio):
        return "normal"
    return BUCKETS[sum(ratio >= e for e in EDGES)]


def minute_mids(bbo: dict[str, np.ndarray], trades: dict[str, np.ndarray], start_us: int, end_us: int) -> np.ndarray:
    """The mid at the end of each minute in [start, end) (the last trade where the book is missing); NaN before any
    data. One value per minute."""
    grid = np.arange(start_us + MIN, end_us + 1, MIN, dtype=np.int64)
    out = np.full(len(grid), np.nan)
    if len(bbo["ts"]):
        i = np.searchsorted(bbo["ts"], grid, side="left") - 1
        ok = i >= 0
        mid = (bbo["bid"] + bbo["ask"]) / 2
        out[ok] = mid[i[ok]]
    if len(trades["ts"]) and np.isnan(out).any():
        j = np.searchsorted(trades["ts"], grid, side="left") - 1
        ok = (j >= 0) & np.isnan(out)
        out[ok] = trades["px"][j[ok]]
    return out


def hour_rv(mids: np.ndarray) -> float:
    """Realized volatility (bp) of a run of one-minute mids: sqrt of the summed squared log moves."""
    r = np.diff(np.log(mids))
    r = r[np.isfinite(r)]
    return float(np.sqrt((r * r).sum()) * 1e4) if len(r) >= 30 else math.nan


def weekend_at(ts_us: int) -> bool:
    return sessions.session_of(ts_us / S) == "weekend"


@dataclass
class Usual:
    """Median hourly volatility per (weekend?, UTC hour) over the last USUAL_DAYS days."""

    rv: dict[tuple[bool, int], float]
    day: str

    def at(self, ts_us: int) -> float:
        h = int(ts_us // (3600 * S) % 24)
        v = self.rv.get((weekend_at(ts_us), h))
        if v is None:   # a new market: any hour of that kind of day
            vs = [x for (w, _), x in self.rv.items() if w == weekend_at(ts_us)] or list(self.rv.values())
            v = float(np.median(vs)) if vs else math.nan
        return v


def usual(store: TapeStore, market: str, now_us: int, days: int = USUAL_DAYS) -> Usual:
    start = now_us - now_us % US_DAY - days * US_DAY
    per: dict[tuple[bool, int], list[float]] = {}
    for d in range(days):
        s0 = start + d * US_DAY
        tape = store.load_range(market, s0, s0 + US_DAY)
        if not len(tape.bbo["ts"]) and not len(tape.trades["ts"]):
            continue
        mids = minute_mids(tape.bbo, tape.trades, s0 - MIN, s0 + US_DAY)
        for h in range(24):
            rv = hour_rv(mids[h * 60:h * 60 + 61])
            if math.isfinite(rv) and rv > 0:
                t = s0 + h * 3600 * S
                per.setdefault((weekend_at(t + 1800 * S), h), []).append(rv)
    return Usual({k: float(np.median(v)) for k, v in per.items()}, time.strftime("%Y-%m-%d", time.gmtime(now_us / S)))


@dataclass
class Regime:
    market: str
    ratio: float          # last hour's volatility over the usual at this hour
    bucket: str
    rv_bps: float
    usual_bps: float
    move_1h_bps: float
    jump_bps: float       # largest one-minute move in the last SHOCK_LOOK_MIN minutes
    shock: bool
    age_s: float          # how old the newest mid is

    @property
    def text(self) -> str:
        if self.shock:
            return f"shock: {self.jump_bps:.0f} bp in a minute"
        return f"{self.bucket} ({self.ratio:.1f}x usual volatility, {self.move_1h_bps:+.0f} bp in 1 h)"

    def as_dict(self) -> dict[str, Any]:
        return {k: (round(v, 3) if isinstance(v, float) and math.isfinite(v) else v) for k, v in self.__dict__.items()}


def of(market: str, mids: np.ndarray, now_us: int, usual_rv: float, age_s: float = 0.0) -> Regime:
    """mids: the last 61+ one-minute mids ending at now."""
    last = mids[-61:]
    rv = hour_rv(last)
    ratio = rv / usual_rv if usual_rv and math.isfinite(rv) and usual_rv > 0 else math.nan
    ok = last[np.isfinite(last)]
    move = float((ok[-1] / ok[0] - 1) * 1e4) if len(ok) >= 2 else 0.0
    r = np.abs(np.diff(np.log(mids[-(SHOCK_LOOK_MIN + 1):]))) * 1e4
    r = r[np.isfinite(r)]
    jump = float(r.max()) if len(r) else 0.0
    sigma_1m = usual_rv / math.sqrt(60) if usual_rv and math.isfinite(usual_rv) else math.inf
    shock = jump > max(SHOCK_SIGMA * sigma_1m, SHOCK_MIN_BPS)
    return Regime(market, ratio, bucket(ratio), rv, usual_rv, move, jump, shock, age_s)


def now(store: TapeStore, market: str, now_us: int, u: Usual, recent: list[tuple[int, float]] | None = None) -> Regime:
    """The regime from the tape on disk, topped up with the recorder's newest minutes (`recent`: [(minute_us, mid)],
    which the recorder keeps in memory between its 5-minute writes)."""
    start = now_us - now_us % MIN - 90 * MIN
    tape = store.load_range(market, start - 10 * MIN, now_us)
    mids = minute_mids(tape.bbo, tape.trades, start, now_us - now_us % MIN)
    last_ts = max([int(tape.bbo["ts"][-1])] if len(tape.bbo["ts"]) else [0])
    if recent:
        grid = np.arange(start + MIN, now_us - now_us % MIN + 1, MIN, dtype=np.int64)
        for m_us, mid in recent:
            k = int(np.searchsorted(grid, m_us))
            if k < len(grid) and grid[k] == m_us:
                mids[k] = mid
        last_ts = max(last_ts, recent[-1][0])
        mids = _ffill(mids)
    # the last 60 minutes are judged against the usual level of the hour they mostly fall in
    return of(market, mids, now_us, u.at(now_us - 30 * MIN), (now_us - last_ts) / S if last_ts else math.inf)


def _ffill(a: np.ndarray) -> np.ndarray:
    out = a.copy()
    for i in range(1, len(out)):
        if not np.isfinite(out[i]):
            out[i] = out[i - 1]
    return out
