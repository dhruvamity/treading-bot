"""Backtest the overnight and swing strategies (swing_rules.py) and their variants, on the longest history there is and
on minute prices since 2020, at Lighter's and Arcus's costs and funding.

    daytrade research swing [spy] [qqq] [xau] [check] [tables]

The main versions (first in each list, `MAIN`) were fixed before any result was seen. The variants change one rule at
a time. They are ranked on everything before 15 Oct 2025 and the best are judged on the twelve months after.
Writes reports/swing/: summary.csv (every run and period), trades_<market>_<version>.csv (main versions), TABLES.md.
"""

from __future__ import annotations

import csv
import datetime as dt
import itertools
import math
import sys
import time

import numpy as np

from . import PERIODS, REPORTS, WARMUP_START, WINDOW_START
from . import bars as B
from . import swing as S
from .swing_rules import (
    AsianBreak,
    BuyHold,
    CumRsi,
    DailyEmaRsi,
    DailyPullback,
    EmaRsiRecover,
    MaTrend,
    Overnight,
    PercentB,
    Rsi2,
    RsiLevel,
    RsiMomentum,
    SessionWindow,
    Trend,
    TrendPullback,
    TripleRsi,
    ZScale,
)

OUT = REPORTS / "swing"
YEAR = 365.25 * B.DAY


def ts(d: str) -> int:
    return int(dt.datetime.fromisoformat(d).replace(tzinfo=dt.UTC).timestamp())


# ---- versions -------------------------------------------------------------------------------------------------------

# The third list's main versions (#3, #4's six RSI kinds, #8), fixed before any result like the rest.
LIST3 = ["pctb[5,2,<0.2x3,out >0.8,sma200]", "rsi2[<10,out sma5,sma200]", "cum_rsi[2x2<35,out>65,sma200]",
         "triple_rsi[5<30,out>50,sma200]", "rsi[14<30,out >50]", "rsi[2<15,out high]", "rsi_mom[14,in>50,out<50]",
         "ema_rsi[5/20,rsi<30 in 10,out cross|rsi<70]"]
MAIN = {
    "SPY": ["overnight[all]", "overnight[down2,sma200]", "triple_rsi[5<30,out>50,sma200]",
            "ma_trend[sma20>sma50,c>sma200]", "buy_and_hold", *LIST3],
    "QQQ": ["overnight[all]", "overnight[down]", "zscale[z<-1,out ema5,3 legs]",
            "trend_pullback[bb2,out sma20,10d]", "buy_and_hold", *LIST3],
    "XAU": ["trend[H4,ema20/50,adx>20,stop 2atr,trail 3atr]", "daily_pullback[2R,zone 6 bars,break 3 bars]",
            "daily_ema_rsi[5/20,rsi 50,stop 2atr]", "asian_break[stop far,hold 24h,bias sma50]", "overnight[all]",
            "ema_rsi[5/20,rsi<30 in 10,out cross|rsi<70]", "session[wf 12m,<=8h,t>2]",
            "asian_break[stop far,hold 24h,bias sma50,weekdays]"],
}
MAIN = {k: list(dict.fromkeys(v)) for k, v in MAIN.items()}      # Triple RSI is in both lists


def stock_versions(m: str) -> list:
    out: list = [BuyHold(m)]
    for filt, trend, days, hold in itertools.product(
            ("all", "down", "down2", "down3", "up", "low5", "low10", "rsi2<10", "rsi2<30", "ibs<.25"),
            (False, True), ("all", "mon-thu"), ("open", "close")):
        out.append(Overnight(m, filt, trend, days, hold))
    for below, ex, trend in itertools.product((30, 25, 35), (50, 60), (True, False)):
        out.append(TripleRsi(m, below, ex, trend))
    for below, ex, trend in itertools.product((10, 5, 20, 30), ("sma5", "rsi>70"), (True, False)):
        out.append(Rsi2(m, below, ex, trend))
    for z, ex, trend, legs in itertools.product((-1.0, -0.5, -1.5, -2.0), ("ema5", "sma5", "ema10"), (False, True),
                                                (3, 1)):
        out.append(ZScale(m, z, ex, trend, legs))
    for entry, ex, days in itertools.product(("bb2", "bb1.5", "low10", "rsi2<10"), ("sma20", "sma10", "sma5"),
                                             (10, 20)):
        out.append(TrendPullback(m, entry, ex, days))
    for kind in ("c>sma200", "sma50>sma200", "sma20>sma50,c>sma200", "c>sma100"):
        out.append(MaTrend(m, kind))
    for days in ("fri", "mon", "tue-thu"):                  # #5's "specific days of the week"
        out.append(Overnight(m, "all", False, days))
    out += list3_versions(m)
    seen: set[str] = set()
    return [r for r in out if not (r.name in seen or seen.add(r.name))]


def list3_versions(m: str) -> list:
    """#3 %b, #4 RSI and #8 EMA + RSI: the main version of each first, then one or two rules changed at a time."""
    out: list = [PercentB(m), Rsi2(m), CumRsi(m), TripleRsi(m), RsiLevel(m), RsiLevel(m, 2, 15, exit="high"),
                 RsiMomentum(m), EmaRsiRecover(m)]
    for n, below, days, (ex, above), trend in itertools.product(
            (5, 20, 30), (0.2, 0.0), (1, 3), (("pctb", 0.8), ("pctb", 0.5), ("sma5", 0.8)), (True, False)):
        out.append(PercentB(m, n, 2.0, below, days, above, trend, exit=ex))
    out += [PercentB(m, scale=True), PercentB(m, vol=True), PercentB(m, 30, 2.0, 0.15, 1, 0.8, True, vol=True)]
    for (days, below), above, trend in itertools.product(((2, 35), (2, 25), (2, 45), (3, 50)), (65, 70),
                                                         (True, False)):
        out.append(CumRsi(m, days, below, above, trend))
    for (n, below, above, ex), trend in itertools.product(
            ((14, 30, 50, "rsi"), (14, 30, 70, "rsi"), (14, 35, 50, "rsi"), (5, 30, 50, "rsi"), (5, 35, 70, "rsi"),
             (2, 15, 0, "high"), (2, 10, 0, "high"), (2, 25, 0, "high")), (False, True)):
        out.append(RsiLevel(m, n, below, above, trend, ex))
    for (n, up, down), trend in itertools.product(((14, 50, 50), (14, 55, 45), (14, 60, 40), (9, 50, 50),
                                                   (21, 50, 50)), (False, True)):
        out.append(RsiMomentum(m, n, up, down, trend))
    out += ema_rsi_versions(m)
    seen: set[str] = set()
    return [r for r in out if not (r.name in seen or seen.add(r.name))]


def ema_rsi_versions(m: str) -> list:
    out = [EmaRsiRecover(m)]
    for (f, s), (os_, within), rx in itertools.product(((5, 20), (9, 21), (10, 30)),
                                                       ((30, 10), (30, 5), (30, 20), (40, 10), (30, 0)), (True, False)):
        out.append(EmaRsiRecover(m, f, s, os_, within, 70, rx))
    return out


def gold_versions() -> list[tuple[str, object]]:
    """(the bars it decides on, the rule)."""
    out: list[tuple[str, object]] = []
    exits = ((3.0, None), (None, None), (None, 3.0), (2.0, None))
    for tf, kind, adx_min, stop, (trail, tgt), sides in itertools.product(
            ("H4", "D"), ("ema20/50", "ema50/200", "ema9/21", "donchian20/10", "donchian55/20"), (20, None),
            (2.0, 1.5, 3.0), exits, ("both", "long")):
        out.append((tf, Trend(kind, adx_min, stop, trail, tgt, sides, tf)))
    for tgt, look, trig, trail in itertools.product((2.0, 3.0, None), (6, 12), (3, 6), (None, 3.0)):
        if tgt is None and trail is None:
            continue
        out.append(("H4", DailyPullback(tgt, look, trig, 60, trail)))
    for (f, s), stop, sides in itertools.product(((5, 20), (10, 30), (20, 50)), (2.0, 3.0), ("both", "long")):
        out.append(("D", DailyEmaRsi(f, s, 50, stop, sides)))
    for stop, hold, tgt, bias, days in itertools.product(("far", "mid"), (24, 48, 8), (None, 2.0), ("sma50", None),
                                                         ("all", "weekdays")):
        out.append(("H1", AsianBreak(stop, hold, tgt, bias, days)))
    for filt, trend, days in itertools.product(("all", "down", "up"), (False, True), ("all", "mon-thu")):
        out.append(("session", Overnight("XAU", filt, trend, days)))
    for tf in ("D", "H4", "H1"):                            # #8 on gold, on three bar sizes
        out += [(tf, r) for r in ema_rsi_versions("XAU")]
    out.append(("H1", SessionWindow()))                     # #6: the main version first, then the fixed windows
    for w, side in itertools.product(("asia", "london", "ny-am", "ny-pm"), (1, -1)):
        out.append(("H1", SessionWindow(w, side)))
    for months, max_len, t_min, sides in itertools.product((12, 6), (8, 4), (2.0, 1.0), ("both", "long")):
        out.append(("H1", SessionWindow("wf", 1, months, max_len, t_min, sides)))
    seen: set[tuple[str, str]] = set()
    out = [x for x in out if not ((x[0], x[1].name) in seen or seen.add((x[0], x[1].name)))]
    out.sort(key=lambda x: (x[1].name not in MAIN["XAU"],))
    return out


# ---- data -----------------------------------------------------------------------------------------------------------

def stock_data(m: str) -> dict[str, S.Coarse]:
    """Daily and minute prices from WARMUP_START: the owner's two-year rule plus the indicators' warm-up."""
    return {"daily": S.yahoo(m, since=WARMUP_START),
            "minute": S.sessions(B.load(f"alpaca_{m}").between(WARMUP_START, 2**40), f"alpaca_{m}")}


def gold_hours(name: str) -> B.Bars:
    b = B.load(name).between(WARMUP_START, 2**40)
    if name.endswith("_1h"):
        return b
    return B.resample(b, 60)[0]


def gold_data(name: str = "duka_XAUUSD_1h") -> dict[str, S.Coarse]:
    h = gold_hours(name)
    return {"H4": S.by_hours(h, 4, name), "D": S.by_hours(h, 24, name), "H1": S.hourly(h, name),
            "session": S.sessions(h, name, open_at="10:00", close_at="16:00")}


# ---- statistics -----------------------------------------------------------------------------------------------------

def stats(trades: list[S.STrade], venue: S.Venue, t0: int, t1: int, sized_by_risk: bool) -> dict:
    tr = [x for x in trades if t0 <= x.t_in < t1]
    years = max(1e-9, (t1 - t0) / YEAR)
    n = len(tr)
    d = {"trades": n, "per_year": n / years}
    if not n:
        return d
    net = np.array([x.net for x in tr])
    sd = float(net.std(ddof=1)) if n > 1 else math.nan
    d.update(won=float((net > 0).mean()), net_bp=float(net.mean() * 1e4),
             gross_bp=float(np.mean([x.gross for x in tr]) * 1e4), cost_bp=float(np.mean([x.cost for x in tr]) * 1e4),
             fund_bp=float(np.mean([x.fund for x in tr]) * 1e4), hours=float(np.mean([x.hours for x in tr])),
             in_market=float(sum(x.hours for x in tr) * 3600 / (t1 - t0)),
             t=float(net.mean() / sd * math.sqrt(n)) if n > 1 and sd > 0 else math.nan)
    for lev in (1, 3, 10, venue.max_lev):
        a = S.equity(tr, venue, lev=lev)
        d[f"x{lev:g}"], d[f"x{lev:g}_dd"], d[f"x{lev:g}_liq"] = a.final, a.worst_dd, a.liquidations
    if sized_by_risk:
        r = np.array([x.net / x.stop_dist for x in tr if x.stop_dist and not math.isnan(x.stop_dist)])
        if len(r):
            d["avg_r"] = float(r.mean())
            a = S.equity(tr, venue, risk=0.01)
            d["risk1"], d["risk1_dd"], d["risk1_liq"] = a.final, a.worst_dd, a.liquidations
    return d


def periods(trades: list[S.STrade], cb: S.Coarse) -> dict[str, tuple[int, int]]:
    """The owner's two years (`daytrade.PERIODS`), each cut to the data there is."""
    first, end = max(int(cb.t_o[0]), WINDOW_START), int(cb.t_c[-1]) + 1
    names = {"two years": "two years", "first year": "first year", "last 12m": "last12"}
    return {names[n]: (max(a, first), min(b, end)) for n, a, b in PERIODS if min(b, end) - max(a, first) > 20 * B.DAY}


FIELDS = ["market", "data", "venue", "version", "main", "funding", "period", "start", "end", "trades", "per_year",
          "won", "gross_bp", "cost_bp", "fund_bp", "net_bp", "t", "hours", "in_market", "x1", "x1_dd", "x1_liq", "x3",
          "x3_dd", "x3_liq", "x10", "x10_dd", "x10_liq", "x25", "x25_dd", "x25_liq", "x33", "x33_dd", "x33_liq", "x50",
          "x50_dd", "x50_liq", "avg_r", "risk1", "risk1_dd", "risk1_liq"]


def run_one(market: str, data: str, cb: S.Coarse, venue: S.Venue, rule, rows: list, main: set, fscale: float = 1.0,
            keep: dict | None = None) -> list[S.STrade]:
    start = int(np.searchsorted(cb.t_o, WINDOW_START))     # earlier bars only warm the indicators
    tr = S.simulate(rule, cb, venue, S.Funding(venue, fscale), start=start)
    risk = market == "XAU" and any(x.stop_dist == x.stop_dist for x in tr[:5])
    for pname, (a, b) in periods(tr, cb).items():
        d = stats(tr, venue, a, b, risk)
        rows.append({"market": market, "data": data, "venue": venue.name, "version": rule.name,
                     "main": rule.name in main, "funding": fscale, "period": pname,
                     "start": time.strftime("%Y-%m-%d", time.gmtime(a)),
                     "end": time.strftime("%Y-%m-%d", time.gmtime(b)),
                     **d})
    if keep is not None and rule.name in main and fscale == 1.0:
        keep[(market, data, venue.name, rule.name)] = tr
    return tr


def write_trades(keep: dict) -> None:
    for (market, data, venue, name), tr in keep.items():
        safe = "".join(ch if ch.isalnum() else "_" for ch in name).strip("_")
        with (OUT / f"trades_{market}_{data}_{venue}_{safe}.csv").open("w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["side", "in_utc", "out_utc", "entry", "exit", "share", "legs", "gross_bp", "cost_bp", "fund_bp",
                        "net_bp", "worst_bp", "stop_bp", "reason"])
            f = lambda s: time.strftime("%Y-%m-%d %H:%M", time.gmtime(s))     # noqa: E731
            for x in tr:
                w.writerow([x.side, f(x.t_in), f(x.t_out), f"{x.entry:.4f}", f"{x.exit:.4f}", f"{x.frac:.3f}", x.legs,
                            f"{x.gross * 1e4:.2f}", f"{x.cost * 1e4:.2f}", f"{x.fund * 1e4:.2f}", f"{x.net * 1e4:.2f}",
                            f"{x.mae * 1e4:.1f}", f"{x.stop_dist * 1e4:.1f}" if x.stop_dist == x.stop_dist else "",
                            x.reason])


def stocks(m: str, rows: list, keep: dict) -> None:
    data = stock_data(m)
    vs = stock_versions(m)
    main = set(MAIN[m])
    for dname, cb in data.items():
        for vname, venue in S.VENUES[m].items():
            t = time.time()
            for rule in vs:
                run_one(m, dname, cb, venue, rule, rows, main, keep=keep)
                if rule.name in main and vname == "lighter":
                    for fs in (0.0, 2.0):
                        run_one(m, dname, cb, venue, rule, rows, main, fs)
            print(f"{m} {dname} {vname}: {len(vs)} versions in {time.time() - t:.0f} s", flush=True)
    for open_at, close_at in (("09:31", "15:59"), ("09:35", "15:59"), ("10:00", "15:59"), ("09:30", "15:50"),
                              ("09:30", "16:00")):
        cb = S.sessions(B.load(f"alpaca_{m}").between(WARMUP_START, 2**40), f"alpaca_{m}", open_at=open_at,
                        close_at=close_at)
        for rule in (Overnight(m), Overnight(m, "down"), Overnight(m, "down2", True)):
            run_one(m, f"minute {open_at}-{close_at}", cb, S.VENUES[m]["lighter"], rule, rows, main)


def gold(rows: list, keep: dict,
         sources: tuple[str, ...] = ("duka_XAUUSD_1h", "binance_PAXGUSDT", "binance_XAUUSDT_perp")) -> None:
    venue = S.VENUES["XAU"]["lighter"]
    vs = gold_versions()
    main = set(MAIN["XAU"])
    for src in sources:
        if not len(B.load(src)):
            print(f"{src}: no data", flush=True)
            continue
        data = gold_data(src)
        t = time.time()
        for tf, rule in vs:
            run_one("XAU", f"{src}:{tf}", data[tf], venue, rule, rows, main, keep=keep)
            if rule.name in main:
                for fs in (0.0, 2.0):
                    run_one("XAU", f"{src}:{tf}", data[tf], venue, rule, rows, main, fs)
        print(f"XAU {src}: {len(vs)} versions in {time.time() - t:.0f} s", flush=True)
    gld = S.yahoo("GLD", since=WARMUP_START)         # the gold ETF: daily rules and the overnight effect
    for tf, rule in vs:
        if tf in ("D", "session"):
            run_one("XAU", "GLD daily", gld, venue, rule, rows, main, keep=keep)
    print("XAU GLD daily done", flush=True)


def write(rows: list, tag: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"summary_{tag}.csv"
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: (f"{v:.4f}" if isinstance(v, float) else v) for k, v in r.items()})
    print(f"wrote {path} ({len(rows)} rows)", flush=True)


def main(argv: list[str]) -> None:
    what = argv or ["spy", "qqq", "xau", "tables"]
    OUT.mkdir(parents=True, exist_ok=True)
    for m in ("spy", "qqq"):
        if m in what:
            rows: list = []
            keep: dict = {}
            stocks(m.upper(), rows, keep)
            write(rows, m)
            write_trades(keep)
    if "xau" in what:
        rows, keep = [], {}
        gold(rows, keep)
        write(rows, "xau")
        write_trades(keep)
    if "check" in what:
        (OUT / "CHECK.md").write_text("\n".join(check()) + "\n")
        print((OUT / "CHECK.md").read_text())
    if "tables" in what:
        (OUT / "TABLES.md").write_text(tables() + "\n")
        print(f"wrote {OUT / 'TABLES.md'}")


# ---- choosing among the versions, out of sample ---------------------------------------------------------------------

def rank(rows: list[dict], market: str, data: str, venue: str, pick: str, judge: list[str], top: int = 10,
         min_trades: int = 30) -> dict:
    """Rank every version on period `pick` by the t-statistic of its trades (at least `min_trades`), then report how
    the best `top` did in the periods `judge`, which the ranking has not seen."""
    by: dict[str, dict[str, dict]] = {}
    for r in rows:
        if r["market"] == market and r["data"] == data and r["venue"] == venue and float(r["funding"]) == 1.0:
            by.setdefault(r["version"], {})[r["period"]] = r
    f = lambda r, k: float(r[k]) if r and r.get(k) not in ("", None) else math.nan    # noqa: E731
    cand = [(f(p[pick], "t"), v) for v, p in by.items() if pick in p and f(p[pick], "trades") >= min_trades]
    cand = sorted([c for c in cand if not math.isnan(c[0])], reverse=True)[:top]
    out = {"versions": len(by), "positive_pick": sum(f(p.get(pick), "net_bp") > 0 for p in by.values()),
           "best": [v for _, v in cand], "pick_net": float(np.nanmean([f(by[v][pick], "net_bp") for _, v in cand]))}
    for j in judge:
        vals = [f(by[v].get(j), "net_bp") for _, v in cand]
        out[f"{j}_net"] = float(np.nanmean(vals)) if any(not math.isnan(x) for x in vals) else math.nan
        out[f"{j}_x1"] = float(np.nanmean([f(by[v].get(j), "x1") for _, v in cand]))
        out[f"{j}_positive"] = sum(x > 0 for x in vals)
        out[f"positive_{j}"] = sum(f(p.get(j), "net_bp") > 0 for p in by.values())
    return out


# ---- the venues' own prices -----------------------------------------------------------------------------------------

def check() -> list[str]:
    """Since the venues opened: the night move (15:59 to 09:30 New York) on each venue's own price against the
    reference, and the funding a long actually paid on those nights."""
    lines = ["| Market | Prices | Nights | Night move, venue (bp) | Same nights, reference (bp) | Moves together | "
             "Funding a long paid a night (bp) |", "|---|---|---|---|---|---|---|"]
    pairs = [("SPY", "lighter_SPY", "alpaca_SPY", "lighter"), ("SPY", "arcus_SPY", "alpaca_SPY", "arcus"),
             ("QQQ", "lighter_QQQ", "alpaca_QQQ", "lighter"), ("QQQ", "arcus_QQQ", "alpaca_QQQ", "arcus"),
             ("XAU", "lighter_XAU", "binance_XAUUSDT_perp", "lighter"), ("XAU", "binance_PAXGUSDT",
                                                                         "binance_XAUUSDT_perp", "lighter")]
    for m, vname, rname, venue in pairs:
        v, r = B.load(vname), B.load(rname)
        if not len(v) or not len(r):
            continue
        a = S.sessions(v, vname, end_at="23:59")
        b = S.sessions(r.between(int(v.t[0]), int(v.t[-1]) + 60), rname, end_at="23:59")
        common = np.intersect1d(a.day, b.day)
        ia, ib = np.searchsorted(a.day, common), np.searchsorted(b.day, common)
        k = np.flatnonzero((np.diff(ia) == 1) & (np.diff(ib) == 1))
        if len(k) < 5:
            continue
        na = (a.o[ia[k + 1]] / a.c[ia[k]] - 1) * 1e4
        nb = (b.o[ib[k + 1]] / b.c[ib[k]] - 1) * 1e4
        fu = S.Funding(S.VENUES[m][venue if m != "XAU" else "lighter"])
        paid = np.mean([fu(int(a.t_c[ia[x]]), int(a.t_o[ia[x + 1]])) for x in k]) * 1e4
        lines.append(f"| {m} | {vname} vs {rname} | {len(k)} | {na.mean():+.1f} | {nb.mean():+.1f} | "
                     f"{np.corrcoef(na, nb)[0, 1]:.3f} | {paid:.2f} |")
    return lines


# ---- the report's tables --------------------------------------------------------------------------------------------

def _f(r: dict | None, k: str) -> float:
    try:
        return float(r[k]) if r and r.get(k) not in ("", None) else math.nan
    except ValueError:
        return math.nan


def family(name: str) -> str:
    return name.split("[", 1)[0]


def oos_table(rows: list[dict], market: str, data: str, venue: str = "lighter", top: int = 5,
              pick: str = "first year", judge: tuple[str, ...] = ("last12",), min_trades: int = 5
              ) -> list[str]:
    """Per family: how many versions made money in each period, and the `top` best by t in `pick` (at least
    `min_trades` trades there), judged on the periods after it."""
    by: dict[str, dict[str, dict]] = {}
    for r in rows:
        if r["market"] == market and r["data"] == data and r["venue"] == venue and float(r["funding"]) == 1.0:
            by.setdefault(r["version"], {})[r["period"]] = r
    fams = sorted({family(v) for v in by}, key=lambda x: list(dict.fromkeys(family(v) for v in by)).index(x))
    head = (f"| Family | Versions | Positive in {pick} | " + " | ".join(f"Positive in {j}" for j in judge)
            + f" | Best {top} in {pick}: average there | " + " | ".join(f"same, in {j}" for j in judge) + " |")
    out = [head, "|---|" + "---|" * (2 + 2 * len(judge) + 1)]
    for fam in fams:
        vs = {v: p for v, p in by.items() if family(v) == fam}
        cand = sorted([(_f(p.get(pick), "t"), v) for v, p in vs.items() if _f(p.get(pick), "trades") >= min_trades
                       and not math.isnan(_f(p.get(pick), "t"))], reverse=True)[:top]
        cells = [f"{len(vs)}"]
        for per in (pick, *judge):
            pos = sum(_f(p.get(per), "net_bp") > 0 for p in vs.values())
            has = sum(_f(p.get(per), "trades") > 0 for p in vs.values())
            cells.append(f"{pos} of {has}")
        for per in (pick, *judge):
            vals = [_f(vs[v].get(per), "net_bp") for _, v in cand]
            cells.append(f"{float(np.nanmean(vals)):+.1f} bp" if cand and any(x == x for x in vals) else "-")
        out.append(f"| {fam} | " + " | ".join(cells) + " |")
    return out


def main_table(rows: list[dict], market: str, data: str, names: list[str], venue: str = "lighter") -> list[str]:
    get = {(r["version"], r["period"]): r for r in rows if r["market"] == market and r["data"] == data
           and r["venue"] == venue and float(r["funding"]) == 1.0}
    pers = [p for p in ("first year", "last12") if any(k[1] == p for k in get)]
    out = ["| Version | Trades a year | Won | Average trade after costs | Funding in it | t | "
           + " | ".join(f"Average, {p}" for p in pers) + " | $1,000 at 1x | worst fall | at 3x | worst fall |",
           "|---|" + "---|" * (5 + len(pers) + 4)]
    for n in names:
        a = get.get((n, "two years"))
        if not a:
            continue
        cells = [f"{_f(a, 'per_year'):.1f}", f"{_f(a, 'won'):.0%}", f"{_f(a, 'net_bp'):+.1f} bp",
                 f"{_f(a, 'fund_bp'):.1f} bp", f"{_f(a, 't'):+.1f}"]
        for p in pers:
            q = get.get((n, p))
            cells.append(f"{_f(q, 'net_bp'):+.1f} ({_f(q, 'trades'):.0f})" if q and _f(q, "trades") > 0 else "-")
        liq3 = f" ({a['x3_liq']} liquidated)" if a.get("x3_liq") not in ("0", "", None) else ""
        cells += [f"${_f(a, 'x1'):,.0f}", f"{_f(a, 'x1_dd'):.0%}", f"${_f(a, 'x3'):,.0f}{liq3}",
                  f"{_f(a, 'x3_dd'):.0%}"]
        out.append(f"| {n} | " + " | ".join(cells) + " |")
    return out


def tables() -> str:
    """The report's tables: the main versions on each market's data, and every version by family, ranked on the first
    of the two years and judged on the last twelve months."""
    out = []
    for m in ("spy", "qqq"):
        p = OUT / f"summary_{m}.csv"
        if not p.exists():
            continue
        rows = list(csv.DictReader(p.open()))
        M = m.upper()
        for data in ("minute", "daily"):
            out += [f"### {M}, {data} prices, Lighter's costs and funding", ""]
            out += [*main_table(rows, M, data, MAIN[M]), ""]
            out += [f"### {M}, {data} prices: every version by family, ranked on the first year, judged on the last "
                    "12 months", ""]
            out += [*oos_table(rows, M, data), ""]
    p = OUT / "summary_xau.csv"
    if p.exists():
        rows = list(csv.DictReader(p.open()))
        datas = list(dict.fromkeys(r["data"] for r in rows))
        for data in datas:
            names = [n for n in MAIN["XAU"] if any(r["version"] == n and r["data"] == data for r in rows)]
            if names:
                out += [f"### Gold, {data}, Lighter's costs and funding", "", *main_table(rows, "XAU", data, names)]
                out += [""]
        for src in ("duka_XAUUSD_1h", "binance_PAXGUSDT"):
            for tf in ("D", "H4", "H1", "session"):
                data = f"{src}:{tf}"
                if data in datas:
                    out += [f"### Gold, {data}: every version by family", ""]
                    out += [*oos_table(rows, "XAU", data), ""]
    return "\n".join(out)


if __name__ == "__main__":
    main(sys.argv[1:])
