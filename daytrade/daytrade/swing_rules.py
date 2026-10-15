"""The overnight and swing strategies from the owner's second set of notes (15 Oct 2026), made exact.

SPY and QQQ (daily bars; a "close" is the price at 15:59 New York on minute data, the official close on daily data):
- `Overnight`: buy at the close, sell at the next open. Every day, or only after weakness (a down day, two or three,
  the lowest close of five days, RSI(2) low), with or without the 200-day average, on chosen weekdays.
  `hold="close"` keeps it to the next close instead (close to close).
- `TripleRsi`: Connors' rule. RSI(5) under 30, lower three days running, under 60 three days ago, close above the
  200-day average: buy at the close; sell at the close when RSI(5) is back over 50.
- `Rsi2`: Connors' RSI(2): close above the 200-day average and RSI(2) under 10, sell when the close is over the
  5-day average.
- `ZScale`: z = (close - 5-day average) / 5-day standard deviation. Under -1: buy a third; another third at each
  close under the last buy (three at most); sell everything at a close over the 5-day EMA.
- `TrendPullback`: an uptrend (close over a rising 200-day average) and a deep dip (close under the lower 20-day
  Bollinger band): buy at the close, sell at a close over the 20-day average or after 10 days.
- `MaTrend`: long while a moving-average condition holds (close over the 200-day average; 50 over 200; 20 over 50).
- `BuyHold`: the perpetual held long all along (pays funding all along): the yardstick.

Gold (spot gold by the hour, bars on New York's 17:00 roll):
- `Trend`: four-hour (or daily) trend following. EMA 20 crossing EMA 50 with ADX(14) over 20, entered at the next
  bar's open, stop 2 ATR, trailed 3 ATR from the best close, out (and reversed) on the opposite cross. Variants:
  Donchian breakouts, other averages, targets instead of the trail, long only.
- `DailyEmaRsi`: daily EMA 5 crossing EMA 20 with RSI(14) on the same side of 50, held to the reverse cross.
- `DailyPullback`: daily trend (close over SMA50 over SMA200, or the reverse), a pullback to the daily EMA20, then a
  four-hour close above the last three bars' highs (below their lows for a short): stop under the pullback's low,
  target 2R, out after ten days.
- `AsianBreak`: the Asian range (00:00-07:00 London); the first hourly close beyond it between 07:00 and 16:00 in the
  direction of the daily trend; stop at the far side; held 24 hours (or to 2R).
- `Overnight` on gold: 16:00 to the next 09:30 New York, as for the ETFs.

The owner's third list (15 Oct 2026, systematic accounts; #2 and #7 are intraday, in intraday2.py; #1 is in
rotation.py; #5 is `Overnight` above):
- #3 `PercentB`: Connors' %b. Close above the 200-day average, %b(5, 2) under 0.2 three closes running: buy at the
  close; sell at a close with %b over 0.8.
- #4 RSI on SPY, six kinds: `Rsi2` (above), `CumRsi` (Connors' cumulative RSI), `TripleRsi` (above), `RsiLevel`
  (RSI(14) under 30 out over 50; RSI(2) under 15 out at a close over the previous high), `RsiMomentum` (RSI(14)
  crossing over 50, out under 50).
- #6 `SessionWindow`: gold by the hour of its day: fixed session windows, or every month the window with the best
  record over the previous two years.
- #8 `EmaRsiRecover`: EMA 5 crossing over EMA 20 while RSI(14) recovers from under 30; held while EMA 5 stays over
  EMA 20; out on the cross back or when RSI(14) falls back under 70. Gold and the ETFs.
"""

from __future__ import annotations

import numpy as np

from . import bars as B
from .swing import Add, Coarse, Enter, Exit, Pos, Rule, SetStop

# ---- indicators (NaN until there is enough history) -----------------------------------------------------------------


def sma(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        cs = np.cumsum(np.r_[0.0, x])
        out[n - 1:] = (cs[n:] - cs[:-n]) / n
    return out


def rstd(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        out[n - 1:] = np.lib.stride_tricks.sliding_window_view(x, n).std(axis=1)
    return out


def rsum(x: np.ndarray, n: int) -> np.ndarray:
    """The sum of the last n values (a NaN spoils only the windows that hold it)."""
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        out[n - 1:] = np.lib.stride_tricks.sliding_window_view(x, n).sum(axis=1)
    return out


def rmax(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        out[n - 1:] = np.lib.stride_tricks.sliding_window_view(x, n).max(axis=1)
    return out


def rmin(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        out[n - 1:] = np.lib.stride_tricks.sliding_window_view(x, n).min(axis=1)
    return out


def lag(x: np.ndarray, k: int = 1) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if k < len(x):
        out[k:] = x[:-k]
    return out


def wilder(x: np.ndarray, n: int) -> np.ndarray:
    """Wilder's average (alpha 1/n), seeded with the plain average of the first n values."""
    out = np.full(len(x), np.nan)
    if len(x) < n:
        return out
    m = float(np.mean(x[:n]))
    out[n - 1] = m
    for i in range(n, len(x)):
        m += (x[i] - m) / n
        out[i] = m
    return out


def rsi(c: np.ndarray, n: int) -> np.ndarray:
    d = np.diff(c, prepend=np.nan)[1:]
    up, dn = wilder(np.maximum(d, 0.0), n), wilder(np.maximum(-d, 0.0), n)
    with np.errstate(divide="ignore", invalid="ignore"):
        r = np.where(dn == 0, 100.0, 100.0 - 100.0 / (1.0 + up / dn))
    return np.r_[np.nan, r]


def true_range(h: np.ndarray, lo: np.ndarray, c: np.ndarray) -> np.ndarray:
    c1 = lag(c)
    c1[0] = c[0]
    return np.maximum(h - lo, np.maximum(np.abs(h - c1), np.abs(lo - c1)))


def atr(h: np.ndarray, lo: np.ndarray, c: np.ndarray, n: int = 14) -> np.ndarray:
    return wilder(true_range(h, lo, c), n)


def adx(h: np.ndarray, lo: np.ndarray, c: np.ndarray, n: int = 14) -> np.ndarray:
    up, dn = np.diff(h, prepend=h[0]), -np.diff(lo, prepend=lo[0])
    pdm = np.where((up > dn) & (up > 0), up, 0.0)
    mdm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = wilder(true_range(h, lo, c), n)
    with np.errstate(divide="ignore", invalid="ignore"):
        pdi, mdi = 100 * wilder(pdm, n) / tr, 100 * wilder(mdm, n) / tr
        dx = 100 * np.abs(pdi - mdi) / (pdi + mdi)
    out = np.full(len(c), np.nan)
    ok = ~np.isnan(dx)
    if ok.sum() >= n:
        first = int(np.argmax(ok))
        out[first:] = wilder(np.nan_to_num(dx[first:]), n)
    return out


def ema(x: np.ndarray, n: int) -> np.ndarray:
    return B.ema(x, n)


# ---- SPY and QQQ (daily) --------------------------------------------------------------------------------------------

DAYS = {"all": (0, 1, 2, 3, 4), "mon-thu": (0, 1, 2, 3), "no-fri": (0, 1, 2, 3), "fri": (4,), "mon": (0,),
        "tue-thu": (1, 2, 3)}


def _weakness(cb: Coarse, filt: str) -> np.ndarray:
    c, h, lo = cb.c, cb.h, cb.l
    c1, c2, c3 = lag(c), lag(c, 2), lag(c, 3)
    with np.errstate(invalid="ignore", divide="ignore"):
        return {
            "all": np.ones(len(c), dtype=bool),
            "down": c < c1,
            "up": c > c1,
            "down2": (c < c1) & (c1 < c2),
            "down3": (c < c1) & (c1 < c2) & (c2 < c3),
            "low5": c <= rmin(c, 5),
            "low10": c <= rmin(c, 10),
            "rsi2<10": rsi(c, 2) < 10,
            "rsi2<30": rsi(c, 2) < 30,
            "ibs<.25": (c - lo) < 0.25 * (h - lo),
        }[filt]


class Overnight(Rule):
    """Buy at the close, sell at the next open (or, `hold="close"`, at the next close unless the rule still holds)."""

    def __init__(self, market: str, filt: str = "all", trend: bool = False, days: str = "all", hold: str = "open"):
        self.market, self.filt, self.trend, self.days, self.hold = market, filt, trend, days, hold
        self.name = (f"overnight[{filt}" + (",sma200" if trend else "") + (f",{days}" if days != "all" else "")
                     + (",to-close" if hold == "close" else "") + "]")

    def prepare(self, cb: Coarse) -> None:
        super().prepare(cb)
        ok = _weakness(cb, self.filt)
        if self.trend:
            with np.errstate(invalid="ignore"):
                ok = ok & (cb.c > sma(cb.c, 200))
        wd = np.array([B.weekday(int(d)) for d in cb.day])
        self.ok = ok & np.isin(wd, DAYS[self.days])

    def decide(self, i: int, pos: Pos | None) -> list:
        if self.hold == "open":
            return [Enter(1, "close", tag=self.filt), Exit("open", "open")] if pos is None and self.ok[i] else []
        if pos is None:
            return [Enter(1, "close", tag=self.filt)] if self.ok[i] else []
        return [] if self.ok[i] else [Exit("close", "close")]


class TripleRsi(Rule):
    def __init__(self, market: str, below: float = 30, exit_above: float = 50, trend: bool = True, n: int = 5):
        self.market, self.below, self.exit_above, self.trend, self.n = market, below, exit_above, trend, n
        self.name = f"triple_rsi[{n}<{below:g},out>{exit_above:g}" + (",sma200" if trend else "") + "]"

    def prepare(self, cb: Coarse) -> None:
        super().prepare(cb)
        r = rsi(cb.c, self.n)
        r1, r2, r3 = lag(r), lag(r, 2), lag(r, 3)
        with np.errstate(invalid="ignore"):
            ok = (r < self.below) & (r < r1) & (r1 < r2) & (r2 < r3) & (r3 < 60)
            if self.trend:
                ok &= cb.c > sma(cb.c, 200)
        self.ok, self.r = ok, r

    def decide(self, i: int, pos: Pos | None) -> list:
        if pos is None:
            return [Enter(1, "close")] if self.ok[i] else []
        return [Exit("close", "rsi")] if self.r[i] > self.exit_above else []


class Rsi2(Rule):
    def __init__(self, market: str, below: float = 10, exit: str = "sma5", trend: bool = True):
        self.market, self.below, self.exit, self.trend = market, below, exit, trend
        self.name = f"rsi2[<{below:g},out {exit}" + (",sma200" if trend else "") + "]"

    def prepare(self, cb: Coarse) -> None:
        super().prepare(cb)
        c = cb.c
        self.r = rsi(c, 2)
        with np.errstate(invalid="ignore"):
            self.ok = (self.r < self.below) & ((c > sma(c, 200)) if self.trend else True)
        self.s5 = sma(c, 5)

    def decide(self, i: int, pos: Pos | None) -> list:
        c = self.cb.c[i]
        if pos is None:
            return [Enter(1, "close")] if self.ok[i] else []
        out = c > self.s5[i] if self.exit == "sma5" else self.r[i] > 70
        return [Exit("close", "rule")] if out else []


class ZScale(Rule):
    def __init__(self, market: str, z: float = -1.0, exit: str = "ema5", trend: bool = False, legs: int = 3):
        self.market, self.z, self.exit, self.trend, self.legs = market, z, exit, trend, legs
        self.name = f"zscale[z<{z:g},out {exit},{legs} legs" + (",sma200" if trend else "") + "]"

    def prepare(self, cb: Coarse) -> None:
        super().prepare(cb)
        c = cb.c
        with np.errstate(invalid="ignore", divide="ignore"):
            self.zs = (c - sma(c, 5)) / rstd(c, 5)
        self.out = {"ema5": ema(c, 5), "sma5": sma(c, 5), "ema10": ema(c, 10)}[self.exit]
        self.up = (c > sma(c, 200)) if self.trend else np.ones(len(c), dtype=bool)

    def decide(self, i: int, pos: Pos | None) -> list:
        c = self.cb.c[i]
        if pos is None:
            return [Enter(1, "close", frac=1.0 / self.legs)] if self.zs[i] < self.z and self.up[i] else []
        if c > self.out[i]:
            return [Exit("close", "rule")]
        if len(pos.legs) < self.legs and c < pos.legs[-1][1]:
            return [Add(1.0 / self.legs)]
        return []


class TrendPullback(Rule):
    def __init__(self, market: str, entry: str = "bb2", exit: str = "sma20", max_days: int = 10):
        self.market, self.entry, self.exit, self.max_days = market, entry, exit, max_days
        self.name = f"trend_pullback[{entry},out {exit},{max_days}d]"

    def prepare(self, cb: Coarse) -> None:
        super().prepare(cb)
        c = cb.c
        s200, s20 = sma(c, 200), sma(c, 20)
        with np.errstate(invalid="ignore"):
            up = (c > s200) & (s200 > lag(s200, 20))
            dip = {"bb2": c < s20 - 2 * rstd(c, 20), "bb1.5": c < s20 - 1.5 * rstd(c, 20), "low10": c <= rmin(c, 10),
                   "rsi2<10": rsi(c, 2) < 10}[self.entry]
        self.ok = up & dip
        self.out = {"sma20": s20, "sma10": sma(c, 10), "sma5": sma(c, 5)}[self.exit]

    def decide(self, i: int, pos: Pos | None) -> list:
        if pos is None:
            return [Enter(1, "close")] if self.ok[i] else []
        if self.cb.c[i] > self.out[i] or pos.bars >= self.max_days:
            return [Exit("close", "rule")]
        return []


class MaTrend(Rule):
    def __init__(self, market: str, kind: str = "c>sma200"):
        self.market, self.kind = market, kind
        self.name = f"ma_trend[{kind}]"

    def prepare(self, cb: Coarse) -> None:
        super().prepare(cb)
        c = cb.c
        with np.errstate(invalid="ignore"):
            self.want = {"c>sma200": c > sma(c, 200), "sma50>sma200": sma(c, 50) > sma(c, 200),
                         "sma20>sma50,c>sma200": (sma(c, 20) > sma(c, 50)) & (c > sma(c, 200)),
                         "c>sma100": c > sma(c, 100)}[self.kind]

    def decide(self, i: int, pos: Pos | None) -> list:
        if pos is None:
            return [Enter(1, "close")] if self.want[i] else []
        return [] if self.want[i] else [Exit("close", "rule")]


class BuyHold(Rule):
    """Long all the time. Closed and reopened at the last close of each month (one spread a month), so that every
    period of the report has its own trades."""

    def __init__(self, market: str):
        self.market, self.name = market, "buy_and_hold"

    def prepare(self, cb: Coarse) -> None:
        super().prepare(cb)
        month = np.array([B.day_str(int(d))[:7] for d in cb.day])
        self.last = np.r_[month[1:] != month[:-1], False]

    def decide(self, i: int, pos: Pos | None) -> list:
        if pos is None:
            return [Enter(1, "close")]
        return [Exit("close", "month"), Enter(1, "close")] if self.last[i] else []


# ---- the third list: #3 %b, #4 RSI, #8 EMA + RSI (any market; long only) --------------------------------------------

def pct_b(c: np.ndarray, n: int, k: float) -> np.ndarray:
    """Bollinger %b: where the close sits between the bands, the n-bar average -+ k standard deviations (0 = on the
    lower band, 1 = on the upper)."""
    m, s = sma(c, n), rstd(c, n)
    with np.errstate(invalid="ignore", divide="ignore"):
        return (c - (m - k * s)) / (2 * k * s)


def run_of(ok: np.ndarray, n: int) -> np.ndarray:
    """True where `ok` has held at each of the last n bars."""
    out = ok.copy()
    for k in range(1, n):
        out &= np.r_[np.zeros(k, dtype=bool), ok[:-k]]
    return out


class PercentB(Rule):
    """#3, Connors' %b (High Probability ETF Trading, 2009): %b(n, k) under `below` for `days` closes running, the
    close above the 200-day average: buy at the close; sell at a close with %b over `exit_above` (`exit` "sma5": a
    close over the 5-day average). `scale`: buy half, the other half at a later close with %b under `below` again
    (his aggressive version). `vol`: only when the 10-day volatility is over the 100-day."""

    def __init__(self, market: str, n: int = 5, k: float = 2.0, below: float = 0.2, days: int = 3,
                 exit_above: float = 0.8, trend: bool = True, scale: bool = False, exit: str = "pctb",
                 vol: bool = False):
        self.market, self.n, self.k, self.below, self.days = market, n, k, below, days
        self.exit_above, self.trend, self.scale, self.exit, self.vol = exit_above, trend, scale, exit, vol
        self.name = (f"pctb[{n},{k:g},<{below:g}x{days},out " + (f">{exit_above:g}" if exit == "pctb" else exit)
                     + (",sma200" if trend else "") + (",scale" if scale else "") + (",vol" if vol else "") + "]")

    def prepare(self, cb: Coarse) -> None:
        super().prepare(cb)
        c = cb.c
        self.b = pct_b(c, self.n, self.k)
        with np.errstate(invalid="ignore"):
            self.low = self.b < self.below
            ok = run_of(self.low, self.days)
            if self.trend:
                ok &= c > sma(c, 200)
            if self.vol:
                ret = np.r_[np.nan, np.diff(np.log(c))]
                ok &= rstd(ret, 10) > rstd(ret, 100)
        self.ok, self.s5 = ok, sma(c, 5)

    def decide(self, i: int, pos: Pos | None) -> list:
        if pos is None:
            return [Enter(1, "close", frac=0.5 if self.scale else 1.0)] if self.ok[i] else []
        out = self.b[i] > self.exit_above if self.exit == "pctb" else self.cb.c[i] > self.s5[i]
        if out:
            return [Exit("close", "rule")]
        if self.scale and len(pos.legs) < 2 and self.low[i]:
            return [Add(0.5)]
        return []


class CumRsi(Rule):
    """#4, Connors' cumulative RSI (Short Term Trading Strategies That Work, 2008): RSI(2) added up over the last
    `days` closes under `below`, the close above the 200-day average: buy at the close; sell at a close with RSI(2)
    over `exit_above`."""

    def __init__(self, market: str, days: int = 2, below: float = 35, exit_above: float = 65, trend: bool = True):
        self.market, self.days, self.below, self.exit_above, self.trend = market, days, below, exit_above, trend
        self.name = f"cum_rsi[2x{days}<{below:g},out>{exit_above:g}" + (",sma200" if trend else "") + "]"

    def prepare(self, cb: Coarse) -> None:
        super().prepare(cb)
        c = cb.c
        self.r = rsi(c, 2)
        with np.errstate(invalid="ignore"):
            self.ok = (rsum(self.r, self.days) < self.below) & ((c > sma(c, 200)) if self.trend else True)

    def decide(self, i: int, pos: Pos | None) -> list:
        if pos is None:
            return [Enter(1, "close")] if self.ok[i] else []
        return [Exit("close", "rsi")] if self.r[i] > self.exit_above else []


class RsiLevel(Rule):
    """#4: RSI(n) closing under `below` buys at the close; out at a close with RSI(n) over `exit_above`, or (`exit`
    "high", QuantifiedStrategies' usual exit) at a close over the previous bar's high."""

    def __init__(self, market: str, n: int = 14, below: float = 30, exit_above: float = 50, trend: bool = False,
                 exit: str = "rsi"):
        self.market, self.n, self.below, self.exit_above, self.trend, self.exit = market, n, below, exit_above, \
            trend, exit
        self.name = (f"rsi[{n}<{below:g},out " + (f">{exit_above:g}" if exit == "rsi" else "high")
                     + (",sma200" if trend else "") + "]")

    def prepare(self, cb: Coarse) -> None:
        super().prepare(cb)
        c = cb.c
        self.r = rsi(c, self.n)
        with np.errstate(invalid="ignore"):
            self.ok = (self.r < self.below) & ((c > sma(c, 200)) if self.trend else True)
        self.h1 = lag(cb.h)

    def decide(self, i: int, pos: Pos | None) -> list:
        if pos is None:
            return [Enter(1, "close")] if self.ok[i] else []
        out = self.r[i] > self.exit_above if self.exit == "rsi" else self.cb.c[i] > self.h1[i]
        return [Exit("close", "rule")] if out else []


class RsiMomentum(Rule):
    """#4, the momentum kind: long from a close where RSI(n) crosses over `up`, out at a close under `down`."""

    def __init__(self, market: str, n: int = 14, up: float = 50, down: float = 50, trend: bool = False):
        self.market, self.n, self.up, self.down, self.trend = market, n, up, down, trend
        self.name = f"rsi_mom[{n},in>{up:g},out<{down:g}" + (",sma200" if trend else "") + "]"

    def prepare(self, cb: Coarse) -> None:
        super().prepare(cb)
        c = cb.c
        self.r = rsi(c, self.n)
        with np.errstate(invalid="ignore"):
            self.ok = (self.r > self.up) & (lag(self.r) <= self.up) & ((c > sma(c, 200)) if self.trend else True)

    def decide(self, i: int, pos: Pos | None) -> list:
        if pos is None:
            return [Enter(1, "close")] if self.ok[i] else []
        return [Exit("close", "rsi")] if self.r[i] < self.down else []


class EmaRsiRecover(Rule):
    """#8: EMA `fast` crossing over EMA `slow` while RSI(14) recovers from oversold: under `oversold` at some close of
    the last `within` bars, and not under it now (`within` 0: the cross alone). Long only, at the close; held while
    the fast EMA stays over the slow; out at the close of the cross back, or (`rsi_exit`) of the bar where RSI(14)
    falls back under `overbought` from over it."""

    def __init__(self, market: str, fast: int = 5, slow: int = 20, oversold: float = 30, within: int = 10,
                 overbought: float = 70, rsi_exit: bool = True):
        self.market, self.fast, self.slow, self.oversold, self.within = market, fast, slow, oversold, within
        self.overbought, self.rsi_exit = overbought, rsi_exit
        self.name = (f"ema_rsi[{fast}/{slow}," + (f"rsi<{oversold:g} in {within}" if within else "no rsi")
                     + (f",out cross|rsi<{overbought:g}" if rsi_exit else ",out cross") + "]")

    def prepare(self, cb: Coarse) -> None:
        super().prepare(cb)
        c = cb.c
        f, s, r = ema(c, self.fast), ema(c, self.slow), rsi(c, 14)
        with np.errstate(invalid="ignore"):
            ok = (f > s) & (lag(f) <= lag(s))
            if self.within:
                ok &= (rmin(np.nan_to_num(r, nan=50.0), self.within) < self.oversold) & (r >= self.oversold)
            self.ok = ok
            self.down = f < s
            self.roll = (lag(r) > self.overbought) & (r <= self.overbought)

    def decide(self, i: int, pos: Pos | None) -> list:
        if pos is None:
            return [Enter(1, "close")] if self.ok[i] else []
        if self.down[i] or (self.rsi_exit and self.roll[i]):
            return [Exit("close", "cross" if self.down[i] else "rsi")]
        return []


# ---- gold -----------------------------------------------------------------------------------------------------------

class Trend(Rule):
    """Trend following on four-hour (or daily) bars, long and short."""

    def __init__(self, kind: str = "ema20/50", adx_min: float | None = 20, stop_atr: float = 2.0,
                 trail_atr: float | None = 3.0, target_r: float | None = None, sides: str = "both", tf: str = "H4"):
        self.market = "XAU"
        self.kind, self.adx_min, self.stop_atr, self.trail_atr = kind, adx_min, stop_atr, trail_atr
        self.target_r, self.sides, self.tf = target_r, sides, tf
        self.name = (f"trend[{tf},{kind}" + (f",adx>{adx_min:g}" if adx_min else "") + f",stop {stop_atr:g}atr"
                     + (f",trail {trail_atr:g}atr" if trail_atr else "") + (f",{target_r:g}R" if target_r else "")
                     + (",long only" if sides == "long" else "") + "]")

    def prepare(self, cb: Coarse) -> None:
        super().prepare(cb)
        c, h, lo = cb.c, cb.h, cb.l
        self.atr = atr(h, lo, c, 14)
        self.adx = adx(h, lo, c, 14) if self.adx_min else np.full(len(c), 100.0)
        kind, _, arg = self.kind.partition("/")
        if self.kind.startswith("ema"):
            f, s = ema(c, int(kind[3:])), ema(c, int(arg))
            with np.errstate(invalid="ignore"):
                self.long_in = (f > s) & (lag(f) <= lag(s))
                self.short_in = (f < s) & (lag(f) >= lag(s))
            self.long_out, self.short_out = self.short_in, self.long_in
        else:                                               # "donchian20/10": in on a 20-bar close breakout, out on 10
            n_in, n_out = int(kind[8:]), int(arg)
            with np.errstate(invalid="ignore"):
                self.long_in, self.short_in = c > lag(rmax(h, n_in)), c < lag(rmin(lo, n_in))
                self.long_out, self.short_out = c < lag(rmin(lo, n_out)), c > lag(rmax(h, n_out))

    def _entry(self, i: int) -> list:
        if not (self.adx[i] >= self.adx_min if self.adx_min else True) or not self.atr[i] > 0:
            return []
        d = self.stop_atr * self.atr[i]
        if self.long_in[i]:
            return [Enter(1, "open", stop_dist=d, target_r=self.target_r, tag="long")]
        if self.short_in[i] and self.sides == "both":
            return [Enter(-1, "open", stop_dist=d, target_r=self.target_r, tag="short")]
        return []

    def decide(self, i: int, pos: Pos | None) -> list:
        if pos is None:
            return self._entry(i)
        if (self.long_out[i] if pos.side > 0 else self.short_out[i]):
            return [Exit("open", "reverse")] + self._entry(i)
        if self.trail_atr and self.atr[i] > 0:
            new = pos.best - pos.side * self.trail_atr * self.atr[i]
            if (new - pos.stop) * pos.side > 0:
                return [SetStop(new)]
        return []


def _daily_context(cb: Coarse) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """Daily closes, highs and lows made from the bars of each day, and for every bar the index of the last FINISHED
    day before it (-1 at the start)."""
    starts = np.flatnonzero(np.r_[True, cb.day[1:] != cb.day[:-1]])
    ends = np.r_[starts[1:], len(cb)]
    d = {"c": cb.c[ends - 1], "h": np.maximum.reduceat(cb.h, starts), "l": np.minimum.reduceat(cb.l, starts)}
    which = np.repeat(np.arange(len(starts)), ends - starts) - 1
    return which, d


class DailyEmaRsi(Rule):
    def __init__(self, fast: int = 5, slow: int = 20, rsi_mid: float = 50, stop_atr: float = 2.0, sides: str = "both"):
        self.market, self.fast, self.slow, self.rsi_mid, self.stop_atr, self.sides = "XAU", fast, slow, rsi_mid, \
            stop_atr, sides
        self.name = f"daily_ema_rsi[{fast}/{slow},rsi {rsi_mid:g},stop {stop_atr:g}atr" + \
            (",long only" if sides == "long" else "") + "]"

    def prepare(self, cb: Coarse) -> None:
        super().prepare(cb)
        c = cb.c
        f, s, r = ema(c, self.fast), ema(c, self.slow), rsi(c, 14)
        self.atr = atr(cb.h, cb.l, c, 14)
        with np.errstate(invalid="ignore"):
            self.up = (f > s) & (lag(f) <= lag(s))
            self.dn = (f < s) & (lag(f) >= lag(s))
            self.long_ok, self.short_ok = r > self.rsi_mid, r < self.rsi_mid

    def _entry(self, i: int) -> list:
        d = self.stop_atr * self.atr[i]
        if not d > 0:
            return []
        if self.up[i] and self.long_ok[i]:
            return [Enter(1, "open", stop_dist=d, tag="long")]
        if self.dn[i] and self.short_ok[i] and self.sides == "both":
            return [Enter(-1, "open", stop_dist=d, tag="short")]
        return []

    def decide(self, i: int, pos: Pos | None) -> list:
        if pos is None:
            return self._entry(i)
        if (self.dn[i] if pos.side > 0 else self.up[i]):
            return [Exit("open", "reverse")] + self._entry(i)
        return []


class DailyPullback(Rule):
    """On four-hour bars, with the daily trend of the finished days."""

    def __init__(self, target_r: float | None = 2.0, look: int = 6, trigger: int = 3, max_bars: int = 60,
                 trail_atr: float | None = None):
        self.market, self.target_r, self.look, self.trigger, self.max_bars, self.trail_atr = "XAU", target_r, look, \
            trigger, max_bars, trail_atr
        self.name = (f"daily_pullback[{target_r:g}R" if target_r else "daily_pullback[no target") + \
            f",zone {look} bars,break {trigger} bars" + (f",trail {trail_atr:g}atr" if trail_atr else "") + "]"

    def prepare(self, cb: Coarse) -> None:
        super().prepare(cb)
        which, d = _daily_context(cb)
        dc = d["c"]
        s50, s200, e20 = sma(dc, 50), sma(dc, 200), ema(dc, 20)
        ok = which >= 0
        k = np.where(ok, which, 0)
        with np.errstate(invalid="ignore"):
            self.up = ok & (dc[k] > s50[k]) & (s50[k] > s200[k])
            self.dn = ok & (dc[k] < s50[k]) & (s50[k] < s200[k])
        self.zone = np.where(ok, e20[k], np.nan)
        c, h, lo = cb.c, cb.h, cb.l
        self.lo_n, self.hi_n = rmin(lo, self.look), rmax(h, self.look)
        self.brk_up, self.brk_dn = lag(rmax(h, self.trigger)), lag(rmin(lo, self.trigger))
        self.atr = atr(h, lo, c, 14)

    def decide(self, i: int, pos: Pos | None) -> list:
        c = self.cb.c[i]
        if pos is not None:
            if pos.bars >= self.max_bars:
                return [Exit("open", "time")]
            if self.trail_atr and self.atr[i] > 0:
                new = pos.best - pos.side * self.trail_atr * self.atr[i]
                if (new - pos.stop) * pos.side > 0:
                    return [SetStop(new)]
            return []
        a = self.atr[i]
        if not a > 0:
            return []
        if self.up[i] and self.lo_n[i] <= self.zone[i] and c > self.brk_up[i] and c > self.zone[i]:
            return [Enter(1, "open", stop=self.lo_n[i] - 0.25 * a, target_r=self.target_r, tag="long")]
        if self.dn[i] and self.hi_n[i] >= self.zone[i] and c < self.brk_dn[i] and c < self.zone[i]:
            return [Enter(-1, "open", stop=self.hi_n[i] + 0.25 * a, target_r=self.target_r, tag="short")]
        return []


class AsianBreak(Rule):
    """On hourly bars (London clock): the first close beyond the Asian range in the daily trend's direction.
    `days` "weekdays": entries only on Monday-Friday (London days) and the trend from weekday closes, as on spot gold;
    a position held into a weekend still lives through the weekend's bars where the prices have them (Lighter's gold
    and Binance's trade at weekends)."""

    def __init__(self, stop: str = "far", hold: int = 24, target_r: float | None = None, bias: str = "sma50",
                 days: str = "all"):
        self.market, self.stop, self.hold, self.target_r, self.bias, self.days = "XAU", stop, hold, target_r, bias, days
        self.name = f"asian_break[stop {stop},hold {hold}h" + (f",{target_r:g}R" if target_r else "") + \
            (f",bias {bias}" if bias else ",no bias") + (",weekdays" if days == "weekdays" else "") + "]"

    def prepare(self, cb: Coarse) -> None:
        super().prepare(cb)
        loc = B.local(cb.t_o, B.LONDON)
        self.tod = loc % B.DAY
        day = cb.day
        n = len(cb)
        self.ah, self.al = np.full(n, np.nan), np.full(n, np.nan)
        starts = np.flatnonzero(np.r_[True, day[1:] != day[:-1]])
        ends = np.r_[starts[1:], n]
        incl = np.array([self.days == "all" or B.weekday(int(day[a])) < 5 for a in starts.tolist()], dtype=bool)
        closes = []
        for a, b, use in zip(starts.tolist(), ends.tolist(), incl.tolist(), strict=True):
            if not use:
                continue
            asia = (self.tod[a:b] < 7 * 3600)
            if asia.sum() >= 5:
                hh, ll = cb.h[a:b][asia].max(), cb.l[a:b][asia].min()
                after = np.flatnonzero(self.tod[a:b] >= 7 * 3600) + a
                self.ah[after], self.al[after] = hh, ll
            closes.append(cb.c[b - 1])
        dc = np.array(closes) if closes else np.zeros(1)
        trend = sma(dc, 50)
        prev = np.cumsum(incl) - incl - 1                   # each day: the last finished day the trend counts
        which = np.repeat(prev, ends - starts)
        k = np.where(which >= 0, which, 0)
        with np.errstate(invalid="ignore"):
            self.up = (which >= 0) & ((dc[k] > trend[k]) if self.bias else True)
            self.dn = (which >= 0) & ((dc[k] < trend[k]) if self.bias else True)
        self.done_day = -1

    def decide(self, i: int, pos: Pos | None) -> list:
        if pos is not None:
            return [Exit("open", "time")] if pos.bars >= self.hold else []
        d = int(self.cb.day[i])
        if d == self.done_day or not (7 * 3600 <= self.tod[i] < 16 * 3600) or np.isnan(self.ah[i]):
            return []
        c, hh, ll = self.cb.c[i], self.ah[i], self.al[i]
        mid = (hh + ll) / 2
        if c > hh and self.up[i]:
            self.done_day = d
            return [Enter(1, "open", stop=ll if self.stop == "far" else mid, target_r=self.target_r, tag="long")]
        if c < ll and self.dn[i]:
            self.done_day = d
            return [Enter(-1, "open", stop=hh if self.stop == "far" else mid, target_r=self.target_r, tag="short")]
        return []


# ---- #6: gold by the hour of its day --------------------------------------------------------------------------------

DAY_OPEN = 23                       # gold's day opens at 23:00 London (18:00 New York), after an hour's break
FIXED_WINDOWS = {"asia": (0, 8), "london": (8, 5), "ny-am": (13, 4), "ny-pm": (17, 6)}   # (first hour, hours)


def gold_hour(t: np.ndarray) -> np.ndarray:
    """The hour of gold's day that starts at `t`: 0 = 23:00 London (the open) .. 22 = 21:00 (the last); 23 is the
    daily break (22:00-23:00 London)."""
    return ((B.local(t, B.LONDON) % B.DAY) // 3600 - DAY_OPEN) % 24


class SessionWindow(Rule):
    """#6 on hourly bars: every day, in at the open of the window's first hour, out at the close of its last.

    A fixed `window`: "asia" (23:00-07:00 London), "london" (07:00-12:00), "ny-am" (12:00-16:00, the US data and the
    New York open) or "ny-pm" (16:00-22:00), long or short (`side`). Or "wf" (the main version): at the start of each
    month, every window of 1 to `max_len` hours and both sides (`sides` "long": long only) is scored on the last
    `months` months: the t-statistic of its daily result after `cost_bp` (a round trip) and funding; the best one is
    traded that month if its t is over `t_min`, else nothing. Only hours before the month are looked at. 12 months:
    the owner's rule gives a backtest at most two years of history."""

    def __init__(self, window: str = "wf", side: int = 1, months: int = 12, max_len: int = 8, t_min: float = 2.0,
                 sides: str = "both", cost_bp: float = 1.1, funding_bp_h: float = 0.1):
        self.market, self.window, self.side, self.months, self.max_len = "XAU", window, side, months, max_len
        self.t_min, self.sides, self.cost, self.fund = t_min, sides, cost_bp * 1e-4, funding_bp_h * 1e-4
        self.name = (f"session[{window},{'long' if side > 0 else 'short'}]" if window != "wf" else
                     f"session[wf {months}m,<={max_len}h,t>{t_min:g}" + (",long only" if sides == "long" else "")
                     + "]")

    def prepare(self, cb: Coarse) -> None:
        super().prepare(cb)
        n = len(cb)
        g = gold_hour(cb.t_o)
        ge = gold_hour(cb.t_c)
        self.g_end = np.where(ge == 23, 0, ge)              # the break leads to the next open
        self.plan = np.zeros((n, 3), dtype=np.int64)        # side (0: none), first hour, hours
        self.cur: tuple[int, int, int] | None = None
        if self.window != "wf":
            g0, L = FIXED_WINDOWS[self.window]
            self.plan[:] = (self.side, g0, L)
            return
        gday = (B.local(cb.t_o, B.LONDON) + (24 - DAY_OPEN) * 3600) // B.DAY      # 23:00 starts the next day
        days, di = np.unique(gday, return_inverse=True)
        R = np.zeros((len(days), 23))
        ok = g < 23
        with np.errstate(invalid="ignore", divide="ignore"):
            r = np.nan_to_num(cb.c / cb.o - 1.0)
        R[di[ok], g[ok]] = r[ok]
        cs = np.c_[np.zeros(len(days)), np.cumsum(R, axis=1)]
        ym = np.array([int(B.day_str(int(d))[:4]) * 12 + int(B.day_str(int(d))[5:7]) - 1 for d in days])
        sides = (1, -1) if self.sides == "both" else (1,)
        for m in np.unique(ym):
            rows = np.flatnonzero((ym >= m - self.months) & (ym < m))
            if len(rows) < self.months * 15:                 # at least about 3/4 of the months' trading days
                continue
            sub = cs[rows]
            best = (-np.inf, 0, 0, 0)
            for L in range(1, self.max_len + 1):
                w = sub[:, L:24] - sub[:, 0:24 - L]          # windows starting at hours 0 .. 23-L
                for s in sides:
                    x = s * w - self.cost - s * self.fund * L
                    sd = x.std(axis=0, ddof=1)
                    with np.errstate(divide="ignore", invalid="ignore"):
                        t = np.where(sd > 0, x.mean(axis=0) / sd * np.sqrt(len(rows)), -np.inf)
                    k = int(np.argmax(t))
                    if t[k] > best[0]:
                        best = (float(t[k]), s, k, L)
            if best[0] > self.t_min:
                self.plan[np.isin(di, np.flatnonzero(ym == m))] = best[1:]

    def decide(self, i: int, pos: Pos | None) -> list:
        ge = int(self.g_end[i])
        if pos is not None:
            _, g0, L = self.cur
            end = g0 + L if g0 + L < 23 else 0
            return [Exit("close", "window")] if ge == end or pos.bars >= L else []
        side, g0, L = (int(x) for x in self.plan[i])
        if side and ge == g0:
            self.cur = (side, g0, L)
            return [Enter(side, "open", tag=f"{g0}+{L}h")]
        return []
