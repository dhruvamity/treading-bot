"""One-minute bars as plain numpy arrays, stored one file per source and market (data/bars/<name>.npz).

`t` is the bar's opening second (UTC). Everything else (5- and 15-minute bars, sessions, daily levels) is made from
these, the same way in the backtest and in the live bot.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

from . import DATA

NY = ZoneInfo("America/New_York")
LONDON = ZoneInfo("Europe/London")
DAY = 86_400


@dataclass
class Bars:
    t: np.ndarray       # int64, bar open, UTC seconds
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray       # noqa: E741
    c: np.ndarray
    v: np.ndarray

    def __len__(self) -> int:
        return len(self.t)

    def take(self, idx: np.ndarray | slice) -> Bars:
        return Bars(self.t[idx], self.o[idx], self.h[idx], self.l[idx], self.c[idx], self.v[idx])

    def between(self, start: int, end: int) -> Bars:
        """Bars with start <= t < end."""
        i, j = np.searchsorted(self.t, [start, end])
        return self.take(slice(i, j))


def empty() -> Bars:
    z = np.zeros(0)
    return Bars(np.zeros(0, dtype=np.int64), z, z.copy(), z.copy(), z.copy(), z.copy())


def from_rows(rows: list[tuple]) -> Bars:
    """Rows of (t, o, h, l, c, v); sorted and de-duplicated by t (the later row wins)."""
    if not rows:
        return empty()
    a = np.array(rows, dtype=np.float64)
    t = a[:, 0].astype(np.int64)
    order = np.argsort(t, kind="stable")
    a, t = a[order], t[order]
    keep = np.ones(len(t), dtype=bool)
    keep[:-1] = t[1:] != t[:-1]
    a, t = a[keep], t[keep]
    return Bars(t, a[:, 1], a[:, 2], a[:, 3], a[:, 4], a[:, 5])


def concat(a: Bars, b: Bars) -> Bars:
    if not len(a):
        return b
    if not len(b):
        return a
    rows = np.concatenate([np.column_stack([x.t, x.o, x.h, x.l, x.c, x.v]) for x in (a, b)])
    return from_rows(rows.tolist())


def path(name: str) -> Path:
    return DATA / "bars" / f"{name}.npz"


def save(name: str, b: Bars) -> Path:
    p = path(name)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp.npz")
    np.savez_compressed(tmp, t=b.t, o=b.o, h=b.h, l=b.l, c=b.c, v=b.v)
    tmp.replace(p)
    return p


def load(name: str) -> Bars:
    p = path(name)
    if not p.exists():
        return empty()
    z = np.load(p)
    return Bars(z["t"].astype(np.int64), z["o"], z["h"], z["l"], z["c"], z["v"])


# ---- local time ------------------------------------------------------------------------------------------------

_OFFSETS: dict[tuple[str, int], int] = {}


def utc_offset(tz: ZoneInfo, day_index: int) -> int:
    """Seconds to add to UTC for local time on that UTC day (taken at 12:00 UTC: every session here is far from
    the 01:00-07:00 UTC Sunday-morning clock changes)."""
    key = (tz.key, day_index)
    off = _OFFSETS.get(key)
    if off is None:
        noon = dt.datetime.fromtimestamp(day_index * DAY + 43_200, dt.UTC)
        off = int(noon.astimezone(tz).utcoffset().total_seconds())
        _OFFSETS[key] = off
    return off


def local(t: np.ndarray, tz: ZoneInfo) -> np.ndarray:
    """Local wall-clock seconds for each UTC second (a plain number: local day = x // 86400)."""
    days = t // DAY
    uniq = np.unique(days)
    offs = np.array([utc_offset(tz, int(d)) for d in uniq], dtype=np.int64)
    return t + offs[np.searchsorted(uniq, days)]


def local_to_utc(day: int, seconds: int, tz: ZoneInfo) -> int:
    """UTC second of `seconds` past local midnight on local day number `day`."""
    return day * DAY + seconds - utc_offset(tz, day)


def weekday(local_day: int) -> int:
    """Monday 0 .. Sunday 6 for a local day number (1 Jan 1970 was a Thursday)."""
    return (local_day + 3) % 7


def day_str(local_day: int) -> str:
    return (dt.date(1970, 1, 1) + dt.timedelta(days=int(local_day))).isoformat()


# ---- larger bars -----------------------------------------------------------------------------------------------

def resample(b: Bars, minutes: int, anchor: np.ndarray | None = None) -> tuple[Bars, np.ndarray]:
    """N-minute bars from one-minute bars. `anchor` (seconds, per one-minute bar) shifts the grid, so that 5- and
    15-minute bars start at 09:30 New York for stocks. Returns the bars and, for each one-minute bar, the index of the
    larger bar it belongs to. A larger bar's time is its first minute's."""
    step = minutes * 60
    key = (b.t - (anchor if anchor is not None else 0)) // step
    starts = np.flatnonzero(np.r_[True, key[1:] != key[:-1]])
    ends = np.r_[starts[1:], len(b)]
    grp = np.repeat(np.arange(len(starts)), ends - starts)
    out = Bars(b.t[starts], b.o[starts], np.maximum.reduceat(b.h, starts), np.minimum.reduceat(b.l, starts),
               b.c[ends - 1], np.add.reduceat(b.v, starts))
    return out, grp


def ema(x: np.ndarray, n: int) -> np.ndarray:
    """Exponential moving average with alpha 2/(n+1), seeded with the first value."""
    out = np.empty_like(x, dtype=np.float64)
    if not len(x):
        return out
    a = 2.0 / (n + 1)
    m = float(x[0])
    for i, v in enumerate(x.tolist()):
        m += a * (v - m)
        out[i] = m
    return out
