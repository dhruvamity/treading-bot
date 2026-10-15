"""Backtest every strategy, its variants, on 1-, 5- and 15-minute signal bars and on both venues' costs.

    python -m daytrade.research [spy] [qqq] [xau] [venues]

The main version of each strategy (the first in each list below) was fixed before any result was seen. The other
versions change one or two rules at a time. Choosing among them is done on the years before the last twelve months
("earlier"), and the choice is then judged on the last twelve months ("last 12m"), which no choice has seen.
Writes reports/research/: summary.csv (every run), trades_<name>.csv (the main versions), REPORT.md.
"""

from __future__ import annotations

import csv
import datetime as dt
import itertools
import math
import sys
import time
from dataclasses import dataclass

import numpy as np

from . import REPORTS
from . import bars as B
from .core import MARKETS
from .sim import Trade, run
from .strategies import EmaPullback, LondonSweep, OpeningRange, VwapTrend

NOW = int(time.time())
LAST12 = int(dt.datetime(2025, 10, 15, tzinfo=dt.UTC).timestamp())
OUT = REPORTS / "research"


def ts(d: str) -> int:
    return int(dt.datetime.fromisoformat(d).replace(tzinfo=dt.UTC).timestamp())


# ---- the versions tried ------------------------------------------------------------------------------------------

def spy_versions(market: str = "SPY") -> list:
    out = [OpeningRange(market, tf) for tf in (1, 5, 15)]                       # the main version, each timeframe
    for tf, orb, rr, until, levels, stop in itertools.product((1, 5, 15), (15, 5), (2.0, 1.0, 3.0, None),
                                                              ("11:30", "15:00"), (True, False), ("bar", "mid")):
        if tf == 15 and orb == 5:
            continue
        s = OpeningRange(market, tf, orb, rr, until, levels, stop)
        if s.name not in {x.name for x in out}:
            out.append(s)
    return out


def qqq_versions(market: str = "QQQ") -> list:
    out = [EmaPullback(market, tf) for tf in (1, 5, 15)]
    for tf, (partial, trail, rr), slow, levels, stop in itertools.product(
            (1, 5, 15),
            ((1.0, "ema48", None), (1.0, "ema13", None), (None, "ema48", None), (None, None, 2.0), (1.0, None, 2.0),
             (None, None, 1.0)),
            (False, True), (True, False), ("bar", "ema48")):
        s = EmaPullback(market, tf, partial_r=partial, trail=trail, rr=rr, slow=slow, levels=levels, stop=stop)
        if s.name not in {x.name for x in out}:
            out.append(s)
    for tf, rr in itertools.product((1, 5, 15), (2.0, 1.0, 3.0)):
        out.append(VwapTrend(market, tf, rr=rr))
    for tf, orb, rr, levels in itertools.product((1, 5, 15), (5, 15), (2.0, 1.0, None), (True, False)):
        if tf == 15 and orb == 5:
            continue
        out.append(OpeningRange(market, tf, orb, rr, levels=levels))
    return out


def xau_versions() -> list:
    out = [LondonSweep(tf=tf) for tf in (1, 5, 15)]
    for tf, level, rr, fvg, bias, choch, kill, flat in itertools.product(
            (1, 5, 15), ("asia", "pdhl"), (3.0, 2.0), ("edge", "mid"), ("h4", "none"), (True, False),
            ("07:00-10:00", "07:00-12:00"), ("20:00", "16:00")):
        deadline = "12:00" if kill == "07:00-10:00" else "14:00"
        s = LondonSweep(tf=tf, rr=rr, kill=kill, deadline=deadline, flat=flat, level=level, fvg=fvg, bias=bias,
                        choch=choch)
        if s.name not in {x.name for x in out}:
            out.append(s)
    return out


# ---- statistics --------------------------------------------------------------------------------------------------

@dataclass
class Stats:
    n: int
    win: float
    avg_r: float
    total_r: float
    t: float
    pf: float
    max_dd_r: float
    per_month: float
    gross_r: float
    usd: float           # $1,000, 1% risk a trade, compounded, leverage capped at the venue's
    usd_dd: float        # worst fall from a high, % of the account


def equity(trades: list[Trade], lev: float, start: float = 1000.0, risk: float = 0.01, use: float = 0.9):
    e, peak, worst = start, start, 0.0
    for tr in sorted(trades, key=lambda x: x.t_entry):
        risk_usd = risk * e
        notional = min(risk_usd / max(tr.dist, 1e-9), lev * use * e)
        e += tr.r * notional * tr.dist
        e = max(e, 0.0)
        peak = max(peak, e)
        worst = max(worst, 1 - e / peak if peak > 0 else 1)
    return e, worst


def stats(trades: list[Trade], lev: float, months: float) -> Stats:
    if not trades:
        return Stats(0, 0, 0, 0, 0, 0, 0, 0, 0, 1000.0, 0.0)
    r = np.array([x.r for x in trades])
    cum = np.cumsum(r)
    dd = float(np.max(np.maximum.accumulate(np.r_[0, cum])[1:] - cum)) if len(cum) else 0.0
    wins, losses = r[r > 0].sum(), -r[r < 0].sum()
    t = float(r.mean() / (r.std(ddof=1) / math.sqrt(len(r)))) if len(r) > 1 and r.std() > 0 else 0.0
    usd, usd_dd = equity(trades, lev)
    return Stats(len(r), float(np.mean(r > 0)), float(r.mean()), float(r.sum()), t,
                 float(wins / losses) if losses > 0 else math.inf, dd, float(r.sum() / max(months, 1e-9)),
                 float(np.mean([x.r_gross for x in trades])), usd, usd_dd * 100)


def months_between(a: int, b: int) -> float:
    return (b - a) / (30.44 * 86400)


# ---- running -----------------------------------------------------------------------------------------------------

_DAYS: dict = {}


def days_for(s, bars, key: str):
    k = (key, type(s).__name__, s.tf, getattr(s, "flat_label", None))
    if k not in _DAYS:
        _DAYS[k] = s.days(bars)
    return _DAYS[k]


def evaluate(versions: list, data: str, venues: list[str], rows: list[dict], keep: dict, first: int | None = None):
    bars = B.load(data)
    if not len(bars):
        print(f"no data: {data}")
        return
    first = first or int(bars.t[0])
    last = int(bars.t[-1])
    for i, s in enumerate(versions):
        m = MARKETS[s.market]
        days = days_for(s, bars, data)
        for vname in venues:
            v = m.venues[vname]
            trades = run(s, days, v, m.tick)
            if i < 3:
                keep[(s.name, vname, data)] = trades
            for period, a, b in (("all", first, last), ("earlier", first, LAST12), ("last 12m", LAST12, last)):
                tr = [x for x in trades if a <= x.t_entry < b]
                st = stats(tr, v.max_lev, months_between(a, b))
                rows.append({"strategy": s.name, "market": s.market, "tf": s.tf, "venue": vname, "data": data,
                             "main": i < 3, "period": period, **st.__dict__})
        if i % 25 == 0:
            print(f"{time.strftime('%H:%M:%S')} {data} {i + 1}/{len(versions)} {s.name}", flush=True)


def write(rows: list[dict], keep: dict, tag: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / f"summary_{tag}.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    for (name, venue, data), trades in keep.items():
        with (OUT / f"trades_{name}_{venue}_{data}.csv").open("w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["day", "side", "tag", "signal_utc", "entry_utc", "entry", "stop", "target", "exit_utc", "exit",
                        "reason", "r_gross", "r_cost", "r", "stop_bp"])
            f = lambda s: time.strftime("%Y-%m-%d %H:%M", time.gmtime(s))    # noqa: E731
            for x in trades:
                w.writerow([B.day_str(x.day), x.side, x.tag, f(x.t_signal), f(x.t_entry), round(x.entry, 4),
                            round(x.stop0, 4), round(x.target, 4) if x.target else "", f(x.t_exit),
                            round(x.exit, 4), x.reason, round(x.r_gross, 4), round(x.r_cost, 4), round(x.r, 4),
                            round(x.dist * 1e4, 2)])


def main(argv: list[str]) -> None:
    what = argv or ["spy", "qqq", "xau"]
    rows: list[dict] = []
    keep: dict = {}
    if "spy" in what:
        evaluate(spy_versions(), "alpaca_SPY", ["lighter", "arcus"], rows, keep)
    if "qqq" in what:
        evaluate(qqq_versions(), "alpaca_QQQ", ["lighter", "arcus"], rows, keep)
    if "xau" in what:
        for data in [d for d in ("binance_PAXGUSDT", "duka_XAUUSD", "binance_XAUUSDT_perp") if len(B.load(d))]:
            evaluate(xau_versions(), data, ["lighter"], rows, keep)
    if rows:
        write(rows, keep, "_".join(what))
        print(f"wrote {OUT}", flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
