"""The three strategies, written out as exact rules (the owner's notes, 15 Oct 2026, describe them in words).

SPY  `orb`      Previous day's high/low (PDH/PDL) + the 15-minute opening range (ORB). Above PDH and a close above the
                ORB high arms a long; a later bar that dips to the ORB high and closes above it (the retest holds) is
                the entry, at market. The stop is under that bar's low; the target 2R. Below PDL, the same short.
QQQ  `ema`      PDH/PDL + EMAs 13/48/200. Above PDH with EMA13 over EMA48, a bar that dips to EMA13 and closes back
                above it is a long entry; the stop is under that bar's low. Half comes off at 1R (the stop then goes to
                the entry); the rest rides until a bar closes back under EMA48. Below PDL, the same short.
     `vwap`     QQQ's third variant: from 10:00, above VWAP and EMA200, a dip to VWAP that closes above it; 2R.
XAU  `sweep`    Gold, London morning. The 4-hour market structure sets the side (the last break of a swing). From
                07:00 to 10:00 London: price runs the Asian range's low (for a long; its high for a short), then
                closes above the last swing high before that low (the change of character, CHoCH). The fair value gap
                (FVG) left in that move is where a limit order waits; the stop is beyond the swept low; the target 3R.

Every rule reads only closed bars. Times are on the market's own clock (New York for SPY/QQQ, London for gold).
"""

from __future__ import annotations

import math

import numpy as np

from . import bars as B
from .core import Action, Cancel, Day, Exit, Order, Position, Strategy

HOUR = 3600
RTH_OPEN = 9 * HOUR + 30 * 60
RTH_CLOSE = 16 * HOUR


def hm(text: str) -> int:
    h, m = text.split(":")
    return int(h) * HOUR + int(m) * 60


def _nan_except(values: np.ndarray, mask: np.ndarray) -> np.ndarray:
    out = np.full(len(mask), np.nan)
    out[mask] = values[mask]
    return out


# ---- New York cash session (SPY, QQQ) -----------------------------------------------------------------------------

def stock_days(b: B.Bars, tf: int, start: int | None = None, end: int | None = None,
               emas: tuple[int, ...] = (13, 48, 200)) -> list[Day]:
    """One Day per New York trading day: its 09:30-16:00 one-minute bars, and in `ctx` the previous session's high,
    low and close, the signal bars (values only on the minute a signal bar closes; NaN elsewhere), EMAs of the signal
    bars' closes (running across days, cash session only) and the session's VWAP."""
    lt = B.local(b.t, B.NY)
    sod, dkey = lt % B.DAY, lt // B.DAY
    keep = (sod >= RTH_OPEN) & (sod < RTH_CLOSE)
    r, rsod, rday = b.take(keep), sod[keep], dkey[keep]
    if not len(r):
        return []
    key = rday * 10_000 + (rsod - RTH_OPEN) // (tf * 60)
    starts = np.flatnonzero(np.r_[True, key[1:] != key[:-1]])
    ends = np.r_[starts[1:], len(r)]
    grp = np.repeat(np.arange(len(starts)), ends - starts)
    closes = np.zeros(len(r), dtype=bool)
    closes[ends - 1] = True
    last = len(r) - 1                  # the newest bar closes its signal bar only at that bar's last minute
    bucket_end = RTH_OPEN + ((rsod[last] - RTH_OPEN) // (tf * 60) + 1) * tf * 60
    if rsod[last] + 60 < min(bucket_end, RTH_CLOSE):
        closes[last] = False
    tfo, tfc = r.o[starts], r.c[ends - 1]
    tfh, tfl = np.maximum.reduceat(r.h, starts), np.minimum.reduceat(r.l, starts)
    ema_tf = {n: B.ema(tfc, n) for n in emas}
    dstarts = np.flatnonzero(np.r_[True, rday[1:] != rday[:-1]])
    dends = np.r_[dstarts[1:], len(r)]
    tp = (r.h + r.l + r.c) / 3
    out = []
    for i, (a, z) in enumerate(zip(dstarts, dends, strict=True)):
        if i == 0:
            continue
        pa, pz = dstarts[i - 1], dends[i - 1]
        if (start is not None and r.t[a] < start) or (end is not None and r.t[a] >= end):
            continue
        g = grp[a:z]
        cl = closes[a:z]
        vol = r.v[a:z]
        cv = np.cumsum(vol)
        vwap = np.where(cv > 0, np.cumsum(tp[a:z] * vol) / np.maximum(cv, 1e-12), np.nan)
        ctx = {"pdh": float(r.h[pa:pz].max()), "pdl": float(r.l[pa:pz].min()), "pclose": float(r.c[pz - 1]),
               "sod": rsod[a:z], "closes": cl,
               "tfo": _nan_except(tfo[g], cl), "tfh": _nan_except(tfh[g], cl), "tfl": _nan_except(tfl[g], cl),
               "tfc": _nan_except(tfc[g], cl), "vwap": vwap}
        for n, e in ema_tf.items():
            ctx[f"ema{n}"] = _nan_except(e[g], cl)
        out.append(Day(int(rday[a]), r.t[a:z], r.o[a:z], r.h[a:z], r.l[a:z], r.c[a:z], r.v[a:z], ctx))
    return out


class _Stock(Strategy):
    """What SPY's and QQQ's rules share: the clock, the signal bars, the stop's least width."""
    min_stop_bp = 5.0               # a stop closer than this is widened to it (a one-cent stop is not a stop)
    flat = hm("15:55")

    def days(self, bars, start=None, end=None):
        return stock_days(bars, self.tf, start, end)

    def start_day(self, day: Day) -> None:
        super().start_day(day)
        x = day.ctx
        self.sod = x["sod"].tolist()
        self.cl = x["closes"].tolist()
        self.flat_at = B.local_to_utc(day.key, self.flat, B.NY)

    def at(self, sec: int) -> int:
        return B.local_to_utc(self.day.key, sec, B.NY)

    def stop_for(self, side: int, price: float, stop: float, tick: float = 0.01) -> float:
        least = price * self.min_stop_bp / 1e4
        if side > 0:
            return min(stop, price - least)
        return max(stop, price + least)

    def entry(self, side: int, price: float, stop: float, rr: float | None, tag: str, **kw) -> Order:
        stop = self.stop_for(side, price, stop)
        risk = abs(price - stop)
        target = price + side * rr * risk if rr else None
        return Order(side, "market", price, stop, target, expire=self.at(self.sod[0]) + 10 * B.DAY,
                     flat_at=self.flat_at, tag=tag, **kw)


class OpeningRange(_Stock):
    """SPY: PDH/PDL + opening range, enter on a retest that holds."""

    def __init__(self, market: str = "SPY", tf: int = 5, orb_min: int = 15, rr: float | None = 2.0,
                 until: str = "11:30", levels: bool = True, stop: str = "bar", tol_bp: float = 0.0,
                 max_trades: int = 1):
        self.market, self.tf, self.orb_min, self.rr = market, tf, orb_min, rr
        self.until, self.levels, self.stop_mode, self.tol = hm(until), levels, stop, tol_bp / 1e4
        self.max_trades = max_trades
        exit_ = f"rr{rr}" if rr else "eod"
        self.name = (f"{market.lower()}-orb{orb_min}-tf{tf}-{exit_}-to{until.replace(':', '')}"
                     f"{'' if levels else '-nolevels'}{'' if stop == 'bar' else '-' + stop}"
                     f"{'' if max_trades == 1 else f'-x{max_trades}'}")

    def start_day(self, day: Day) -> None:
        super().start_day(day)
        x = day.ctx
        self.pdh, self.pdl = x["pdh"], x["pdl"]
        self.tfh, self.tfl, self.tfc = x["tfh"].tolist(), x["tfl"].tolist(), x["tfc"].tolist()
        self.orb_end = RTH_OPEN + self.orb_min * 60
        n = int(np.searchsorted(x["sod"], self.orb_end))
        first = int(x["sod"][0]) if len(day) else RTH_OPEN
        self.orbh = float(day.h[:n].max()) if n and first < RTH_OPEN + 60 else math.nan
        self.orbl = float(day.l[:n].min()) if n and first < RTH_OPEN + 60 else math.nan
        self.armed = 0

    def on_bar(self, k: int, pos: Position | None, trades_today: int) -> list[Action]:
        if not self.cl[k] or self.sod[k] + 60 < self.orb_end or math.isnan(self.orbh):
            return []
        if pos is not None or trades_today >= self.max_trades or self.sod[k] + 60 > self.until:
            self.armed = 0
            return []
        c, hi, lo = self.tfc[k], self.tfh[k], self.tfl[k]
        bull = not self.levels or c > self.pdh
        bear = not self.levels or c < self.pdl
        if self.armed == 0:
            if c > self.orbh and bull:
                self.armed = 1
            elif c < self.orbl and bear:
                self.armed = -1
            return []
        if self.armed == 1:
            if c < self.orbh:
                self.armed = 0
            elif lo <= self.orbh * (1 + self.tol) and bull:
                self.armed = 0
                stop = (self.orbh + self.orbl) / 2 if self.stop_mode == "mid" else lo - 0.01
                return [self.entry(1, c, stop, self.rr, "orb-long")]
        else:
            if c > self.orbl:
                self.armed = 0
            elif hi >= self.orbl * (1 - self.tol) and bear:
                self.armed = 0
                stop = (self.orbh + self.orbl) / 2 if self.stop_mode == "mid" else hi + 0.01
                return [self.entry(-1, c, stop, self.rr, "orb-short")]
        return []


class EmaPullback(_Stock):
    """QQQ: PDH/PDL + EMA 13/48/200 pullbacks; half off at 1R, the rest until a close through EMA48."""

    def __init__(self, market: str = "QQQ", tf: int = 5, start: str = "09:45", until: str = "15:00",
                 partial_r: float | None = 1.0, trail: str | None = "ema48", rr: float | None = None,
                 slow: bool = False, levels: bool = True, stop: str = "bar", max_trades: int = 1):
        self.market, self.tf, self.start_s, self.until = market, tf, hm(start), hm(until)
        self.partial_r, self.trail, self.rr, self.slow, self.levels = partial_r, trail, rr, slow, levels
        self.stop_mode, self.max_trades = stop, max_trades
        self.name = (f"{market.lower()}-ema-tf{tf}-p{partial_r}-{trail or 'notrail'}-{'rr' + str(rr) if rr else 'run'}"
                     f"{'-ema200' if slow else ''}{'' if levels else '-nolevels'}"
                     f"{'' if stop == 'bar' else '-' + stop}{'' if max_trades == 1 else f'-x{max_trades}'}"
                     f"-{start.replace(':', '')}")

    def start_day(self, day: Day) -> None:
        super().start_day(day)
        x = day.ctx
        self.pdh, self.pdl = x["pdh"], x["pdl"]
        self.tfh, self.tfl, self.tfc = x["tfh"].tolist(), x["tfl"].tolist(), x["tfc"].tolist()
        self.e13, self.e48, self.e200 = x["ema13"].tolist(), x["ema48"].tolist(), x["ema200"].tolist()

    def on_bar(self, k: int, pos: Position | None, trades_today: int) -> list[Action]:
        if not self.cl[k]:
            return []
        c, hi, lo = self.tfc[k], self.tfh[k], self.tfl[k]
        e13, e48, e200 = self.e13[k], self.e48[k], self.e200[k]
        if pos is not None:
            if self.trail == "ema48" and (c - e48) * pos.side < 0:
                return [Exit("ema48")]
            if self.trail == "ema13" and (c - e13) * pos.side < 0:
                return [Exit("ema13")]
            return []
        t_end = self.sod[k] + 60
        if trades_today >= self.max_trades or t_end <= self.start_s or t_end > self.until:
            return []
        if (not self.levels or c > self.pdh) and e13 > e48 and (not self.slow or c > e200) and lo <= e13 < c:
            stop = min(lo, e48) - 0.01 if self.stop_mode == "ema48" else lo - 0.01
            return [self._order(1, c, stop)]
        if (not self.levels or c < self.pdl) and e13 < e48 and (not self.slow or c < e200) and hi >= e13 > c:
            stop = max(hi, e48) + 0.01 if self.stop_mode == "ema48" else hi + 0.01
            return [self._order(-1, c, stop)]
        return []

    def _order(self, side: int, c: float, stop: float) -> Order:
        o = self.entry(side, c, stop, self.rr, "ema-long" if side > 0 else "ema-short")
        if self.partial_r:
            o.partial = c + side * self.partial_r * abs(c - o.stop)
        return o


class VwapTrend(_Stock):
    """QQQ's third variant: from 10:00, with price above VWAP and EMA200, buy a dip to VWAP that closes above it."""

    def __init__(self, market: str = "QQQ", tf: int = 5, start: str = "10:00", until: str = "15:00",
                 rr: float | None = 2.0, max_trades: int = 1):
        self.market, self.tf, self.start_s, self.until, self.rr = market, tf, hm(start), hm(until), rr
        self.max_trades = max_trades
        exit_ = f"rr{rr}" if rr else "eod"
        self.name = f"{market.lower()}-vwap-tf{tf}-{exit_}{'' if max_trades == 1 else f'-x{max_trades}'}"

    def start_day(self, day: Day) -> None:
        super().start_day(day)
        x = day.ctx
        self.tfh, self.tfl, self.tfc = x["tfh"].tolist(), x["tfl"].tolist(), x["tfc"].tolist()
        self.e200, self.vwap = x["ema200"].tolist(), x["vwap"].tolist()

    def on_bar(self, k: int, pos: Position | None, trades_today: int) -> list[Action]:
        if not self.cl[k] or pos is not None or trades_today >= self.max_trades:
            return []
        t_end = self.sod[k] + 60
        if t_end <= self.start_s or t_end > self.until:
            return []
        c, hi, lo, vw, e200 = self.tfc[k], self.tfh[k], self.tfl[k], self.vwap[k], self.e200[k]
        if math.isnan(vw):
            return []
        if c > vw and c > e200 and lo <= vw:
            return [self.entry(1, c, lo - 0.01, self.rr, "vwap-long")]
        if c < vw and c < e200 and hi >= vw:
            return [self.entry(-1, c, hi + 0.01, self.rr, "vwap-short")]
        return []


class FirstCandle(_Stock):
    """The opening-range rule as the published backtests run it (Zarattini & Aziz, 2023, QQQ): the first 5-minute bar
    sets the side (up bar: long, down bar: short, flat: nothing); enter at the next bar's open; the stop is the far
    end of that first bar; out at the target (10R in the paper) or the close."""

    def __init__(self, market: str = "QQQ", orb_min: int = 5, rr: float | None = 10.0):
        self.market, self.tf, self.orb_min, self.rr = market, 1, orb_min, rr
        self.name = f"{market.lower()}-firstcandle{orb_min}-{'rr' + str(rr) if rr else 'eod'}"

    def on_bar(self, k: int, pos: Position | None, trades_today: int) -> list[Action]:
        if trades_today or pos is not None or self.sod[k] + 60 != RTH_OPEN + self.orb_min * 60:
            return []
        if self.sod[0] != RTH_OPEN:
            return []
        o, c = float(self.day.o[0]), float(self.day.c[k])
        hi, lo = float(self.day.h[:k + 1].max()), float(self.day.l[:k + 1].min())
        if c > o:
            return [self.entry(1, c, lo, self.rr, "first-long")]
        if c < o:
            return [self.entry(-1, c, hi, self.rr, "first-short")]
        return []


# ---- London morning (gold) -----------------------------------------------------------------------------------------

def structure_trend(h: np.ndarray, l: np.ndarray, c: np.ndarray, n: int = 2) -> np.ndarray:  # noqa: E741
    """Market structure, bar by bar: +1 after a close above the last confirmed swing high, -1 after a close below
    the last confirmed swing low (a swing is a high above the `n` bars on each side; it is known `n` bars later)."""
    out = np.zeros(len(c), dtype=np.int8)
    hl, ll, cl = h.tolist(), l.tolist(), c.tolist()
    state, sh, sl = 0, math.nan, math.nan
    for j in range(len(cl)):
        i = j - n
        if i >= n:
            if hl[i] > max(hl[i - n:i]) and hl[i] >= max(hl[i + 1:j + 1]):
                sh = hl[i]
            if ll[i] < min(ll[i - n:i]) and ll[i] <= min(ll[i + 1:j + 1]):
                sl = ll[i]
        if cl[j] > sh:
            state = 1
        elif cl[j] < sl:
            state = -1
        out[j] = state
    return out


def htf_bias(b: B.Bars, hours: int = 4, n: int = 2) -> tuple[np.ndarray, np.ndarray]:
    """The structure of `hours`-hour bars (on New York's 17:00 roll, as gold's day is counted): for each such bar
    the second it closed (UTC) and the trend after it."""
    lny = B.local(b.t, B.NY)
    key = (lny - 17 * HOUR) // (hours * HOUR)
    starts = np.flatnonzero(np.r_[True, key[1:] != key[:-1]])
    ends = np.r_[starts[1:], len(b)]
    h, l = np.maximum.reduceat(b.h, starts), np.minimum.reduceat(b.l, starts)  # noqa: E741
    c = b.c[ends - 1]
    # a bar closes at the end of its period on the clock (a quiet last minute does not delay it)
    closes_at = b.t[starts] - (lny[starts] - 17 * HOUR) % (hours * HOUR) + hours * HOUR
    return closes_at, structure_trend(h, l, c, n)


def gold_days(b: B.Bars, tf: int, start: int | None = None, end: int | None = None, asia: str = "00:00-07:00",
              until: str = "20:00", bias_hours: int = 4) -> list[Day]:
    """One Day per London weekday: its bars from 00:00 to `until`, and in `ctx` the Asian range, the previous day's
    high/low, the 4-hour structure as it stood at 07:00 London, and the signal bars (values only where they close)."""
    lt = B.local(b.t, B.LONDON)
    sod, dkey = lt % B.DAY, lt // B.DAY
    bias_t, bias_v = htf_bias(b, bias_hours)
    a0, a1 = (hm(x) for x in asia.split("-"))
    stop_s = hm(until)
    dstarts = np.flatnonzero(np.r_[True, dkey[1:] != dkey[:-1]])
    dends = np.r_[dstarts[1:], len(b)]
    out = []
    prev = None
    for a, z in zip(dstarts, dends, strict=True):
        day = int(dkey[a])
        full = slice(a, z)
        if B.weekday(day) >= 5:
            continue
        if prev is not None and (start is None or b.t[a] >= start) and (end is None or b.t[a] < end):
            s = sod[full]
            win = s < stop_s
            asian = (s >= a0) & (s < a1)
            if asian.sum() >= 60 and win.sum() > 0:
                t = b.t[full][win]
                key = t // (tf * 60)
                cl = np.r_[key[1:] != key[:-1], (int(t[-1]) + 60) % (tf * 60) == 0]
                gs = np.flatnonzero(np.r_[True, key[1:] != key[:-1]])
                ge = np.r_[gs[1:], len(t)]
                hh, ll_ = b.h[full][win], b.l[full][win]
                g = np.repeat(np.arange(len(gs)), ge - gs)
                tfh, tfl = np.maximum.reduceat(hh, gs)[g], np.minimum.reduceat(ll_, gs)[g]
                tfo, tfc = b.o[full][win][gs][g], b.c[full][win][ge - 1][g]
                at7 = B.local_to_utc(day, a1, B.LONDON)
                j = int(np.searchsorted(bias_t, at7, side="right")) - 1
                ctx = {"asia_h": float(b.h[full][asian].max()), "asia_l": float(b.l[full][asian].min()),
                       "pdh": prev[0], "pdl": prev[1], "bias": int(bias_v[j]) if j >= 0 else 0,
                       "sod": s[win], "closes": cl, "tfi": g,
                       "tfo": _nan_except(tfo, cl), "tfh": _nan_except(tfh, cl),
                       "tfl": _nan_except(tfl, cl), "tfc": _nan_except(tfc, cl)}
                out.append(Day(day, t, b.o[full][win], hh, ll_, b.c[full][win], b.v[full][win], ctx))
        prev = (float(b.h[full].max()), float(b.l[full].min()))
    return out


class LondonSweep(Strategy):
    """Gold: HTF bias + London-morning sweep of the Asian range, CHoCH, limit order in the FVG, stop past the sweep."""
    min_stop_bp = 5.0

    def __init__(self, market: str = "XAU", tf: int = 5, rr: float = 3.0, kill: str = "07:00-10:00",
                 deadline: str = "12:00", flat: str = "20:00", level: str = "asia", fvg: str = "edge",
                 bias: str = "h4", choch: bool = True, buffer_bp: float = 2.0, swing_n: int = 2, max_trades: int = 1):
        self.market, self.tf, self.rr = market, tf, rr
        self.k0, self.k1 = (hm(x) for x in kill.split("-"))
        self.deadline, self.flat = hm(deadline), hm(flat)
        self.level, self.fvg, self.bias_mode, self.need_choch = level, fvg, bias, choch
        self.buffer, self.n, self.max_trades = buffer_bp / 1e4, swing_n, max_trades
        self.flat_label = flat
        self.name = (f"xau-sweep-{level}-tf{tf}-rr{rr}-{fvg}-{bias}{'' if choch else '-nochoch'}"
                     f"-k{kill.replace(':', '').replace('-', '_')}-dl{deadline.replace(':', '')}"
                     f"{'' if flat == '20:00' else '-flat' + flat.replace(':', '')}"
                     f"{'' if max_trades == 1 else f'-x{max_trades}'}")

    def days(self, bars, start=None, end=None):
        return gold_days(bars, self.tf, start, end, until=self.flat_label)

    def start_day(self, day: Day) -> None:
        super().start_day(day)
        x = day.ctx
        self.sod, self.cl, self.tfi = x["sod"].tolist(), x["closes"].tolist(), x["tfi"].tolist()
        self.tfh, self.tfl, self.tfc = x["tfh"].tolist(), x["tfl"].tolist(), x["tfc"].tolist()
        self.hi_lvl, self.lo_lvl = (x["asia_h"], x["asia_l"]) if self.level == "asia" else (x["pdh"], x["pdl"])
        self.side = x["bias"] if self.bias_mode != "none" else 0
        self.H: list[float] = []            # today's closed signal bars
        self.L: list[float] = []
        self.C: list[float] = []
        self.swing_h: list[tuple[int, float]] = []
        self.swing_l: list[tuple[int, float]] = []
        self.phase = "wait"                 # wait -> swept -> ordered -> done
        self.dir = 0
        self.ext = math.nan
        self.ext_i = -1
        self.choch_i = -1
        self.at = lambda sec: B.local_to_utc(day.key, sec, B.LONDON)

    def _swings(self) -> None:
        n, j = self.n, len(self.H) - 1
        i = j - n
        if i >= n:
            if self.H[i] > max(self.H[i - n:i]) and self.H[i] >= max(self.H[i + 1:j + 1]):
                self.swing_h.append((i, self.H[i]))
            if self.L[i] < min(self.L[i - n:i]) and self.L[i] <= min(self.L[i + 1:j + 1]):
                self.swing_l.append((i, self.L[i]))

    def _choch_level(self) -> float:
        """The last swing before the swept extreme, on the side a change of character must break."""
        swings = self.swing_h if self.dir > 0 else self.swing_l
        before = [p for i, p in swings if i < self.ext_i]
        if before:
            return before[-1]
        lo = max(0, self.ext_i - 3)
        seg = (self.H if self.dir > 0 else self.L)[lo:self.ext_i]
        if not seg:
            return math.nan
        return max(seg) if self.dir > 0 else min(seg)

    def _find_fvg(self) -> float | None:
        """The newest fair value gap in the move away from the extreme: for a long, a bar whose low is above the
        high two bars earlier. Its near edge (or middle) is the entry."""
        last = len(self.H) - 1
        for j in range(last, self.ext_i + 1, -1):
            if j - 2 < self.ext_i:
                break
            if self.dir > 0 and self.L[j] > self.H[j - 2]:
                return self.L[j] if self.fvg == "edge" else (self.L[j] + self.H[j - 2]) / 2
            if self.dir < 0 and self.H[j] < self.L[j - 2]:
                return self.H[j] if self.fvg == "edge" else (self.H[j] + self.L[j - 2]) / 2
        return None

    def on_bar(self, k: int, pos: Position | None, trades_today: int) -> list[Action]:
        if not self.cl[k]:
            return []
        self.H.append(self.tfh[k])
        self.L.append(self.tfl[k])
        self.C.append(self.tfc[k])
        self._swings()
        j = len(self.H) - 1
        t_end = self.sod[k] + 60
        if self.phase == "done" or pos is not None or trades_today >= self.max_trades:
            if trades_today >= self.max_trades and pos is None and self.phase == "ordered":
                self.phase = "done"
            return []
        if self.phase == "ordered":
            return []
        if self.phase == "wait":
            if t_end <= self.k0 or t_end > self.k1:
                return []
            took_low = self.L[j] < self.lo_lvl and self.side >= 0
            took_high = self.H[j] > self.hi_lvl and self.side <= 0
            if took_low and took_high:
                return []                    # both sides in one bar: no story to trade
            if took_low or took_high:
                self.dir = 1 if took_low else -1
                self.phase = "swept"
                self.ext, self.ext_i = (self.L[j], j) if took_low else (self.H[j], j)
            return []
        # swept: follow the extreme, wait for the change of character, then the gap
        if t_end > self.deadline:
            self.phase = "done"
            return []
        if self.dir > 0 and self.L[j] < self.ext:
            self.ext, self.ext_i, self.choch_i = self.L[j], j, -1
        elif self.dir < 0 and self.H[j] > self.ext:
            self.ext, self.ext_i, self.choch_i = self.H[j], j, -1
        if self.choch_i < 0:
            lvl = self._choch_level()
            if not self.need_choch or (not math.isnan(lvl) and (self.C[j] - lvl) * self.dir > 0):
                self.choch_i = j
            else:
                return []
        entry = self._find_fvg()
        if entry is None:
            return []
        stop = self.ext * (1 - self.dir * self.buffer)
        least = entry * self.min_stop_bp / 1e4
        if (entry - stop) * self.dir < least:
            stop = entry - self.dir * least
        if (self.C[j] - entry) * self.dir <= 0:
            return []                        # already back through the gap: no retest left to wait for
        risk = abs(entry - stop)
        target = entry + self.dir * self.rr * risk
        self.phase = "ordered"
        return [Order(self.dir, "limit", entry, stop, target, expire=self.at(self.deadline),
                      flat_at=self.at(self.flat), tag="sweep-long" if self.dir > 0 else "sweep-short",
                      cancel_beyond=target)]


def cancel_all() -> list[Action]:
    return [Cancel("strategy")]
