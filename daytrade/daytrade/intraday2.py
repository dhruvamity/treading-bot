"""Two more intraday QQQ strategies from the owner's second list (15 Oct 2026), backtested like the first three.

#2 `vwapreb`  QQQ VWAP rebound (QuantifiedStrategies): after a weak previous day, a small rebound while price is below
              the session's VWAP; long, out at VWAP or at the close. Their exact thresholds are members-only, so the
              main version below is our reading of the public description, fixed before any result was seen.
#7 `atrbo`    QQQ intraday volatility breakout (SystematicPeter): bands at today's open +- the daily ATR; a 5-minute
              close beyond one is a trade that way, a small stop, out at the close. Also its best-documented public
              form, `noise` (Zarattini, Aziz & Barbon 2024, "Beat the Market"): bands from the average move away
              from the open at that minute over 14 days, checked every 30 minutes, VWAP as the trailing exit.

Fills and costs are those of the first three (sim.py). Only the last two years count (the owner's rule,
`daytrade.PERIODS`): versions are ranked on the first of them and judged on the last twelve months.

    daytrade research intraday2            # every version on QQQ and SPY -> reports/intraday2/
    daytrade research intraday2 tables     # the report's tables from reports/intraday2/summary.csv
    daytrade research intraday2 check      # the main versions on Lighter's own prices against the reference
"""

from __future__ import annotations

import csv
import itertools
import math
import sys
import time

import numpy as np

from . import PERIODS, REPORTS, WARMUP_START, WINDOW_START
from . import bars as B
from .core import MARKETS, Day, Exit, Order, Position, Strategy
from .sim import Trade, run
from .strategies import RTH_OPEN, _Stock, hm, stock_days

OUT = REPORTS / "intraday2"
DISASTER = 0.03                       # "no stop": a stop 3% away, only so that a crash day cannot run on

# ---- the days, with what the two strategies need on top of the first study's ------------------------------------

def session_days(b: B.Bars, tf: int) -> list[Day]:
    """stock_days (09:30-16:00, signal bars, VWAP, the previous close) plus, in ctx, from earlier sessions only:
    `open` (09:30's open; NaN when the first minute is missing), `atr14`/`atr50` (the mean daily true range of the
    last 14/50 sessions), `prev_ret` (the previous session's close-to-close return) and, per minute, `sigma`: the
    average distance from the open at that minute over the last 14 sessions (the noise band of Zarattini et al.)."""
    days = stock_days(b, tf)
    trs: list[float] = []
    moves: list[np.ndarray] = []
    prev_pclose = math.nan
    for d in days:
        x = d.ctx
        sod = x["sod"]
        o = float(d.o[0]) if len(d) and int(sod[0]) == RTH_OPEN else math.nan
        idx = np.clip((sod - RTH_OPEN) // 60, 0, 389).astype(int)
        x["open"] = o
        x["atr14"] = float(np.mean(trs[-14:])) if len(trs) >= 14 else math.nan
        x["atr50"] = float(np.mean(trs[-50:])) if len(trs) >= 50 else math.nan
        x["prev_ret"] = x["pclose"] / prev_pclose - 1 if prev_pclose > 0 else math.nan
        if len(moves) >= 14:
            m = np.stack(moves[-14:])
            n = np.sum(~np.isnan(m), axis=0)
            sig = np.where(n > 0, np.nansum(m, axis=0) / np.maximum(n, 1), np.nan)
            x["sigma"] = sig[idx]
        else:
            x["sigma"] = np.full(len(d), np.nan)
        pc = x["pclose"]                       # today's own numbers go in only after today's ctx is made
        trs.append(max(float(d.h.max()), pc) - min(float(d.l.min()), pc))
        mv = np.full(390, np.nan)
        if not math.isnan(o):
            mv[idx] = np.abs(d.c / o - 1)
        moves.append(mv)
        prev_pclose = pc
    return days


class _Day2(_Stock):
    def days(self, bars, start=None, end=None):
        return session_days(bars, self.tf)

    def start_day(self, day: Day) -> None:
        super().start_day(day)
        x = day.ctx
        self.tfo, self.tfh, self.tfl, self.tfc = (x[k].tolist() for k in ("tfo", "tfh", "tfl", "tfc"))
        self.vwap = x["vwap"].tolist()

    def order(self, side: int, price: float, stop: float, target: float | None, tag: str) -> Order:
        return Order(side, "market", price, self.stop_for(side, price, stop), target,
                     expire=self.at(self.sod[0]) + 10 * B.DAY, flat_at=self.flat_at, tag=tag)


# ---- #2 VWAP rebound -----------------------------------------------------------------------------------------------

class VwapRebound(_Day2):
    """Long only. `weak`: the previous session closed down ("down"), down 1% or more ("down1"), or any day ("any").
    A signal bar between `start` and `until` that closes `depth_bp` or more under VWAP, right after a down bar, and
    closes above that bar's close ("up") or its high ("engulf") is the entry. Out at VWAP as it was at the signal
    ("vwap") or only at 15:55 ("eod"); the stop is a 3% safety stop ("none") or under the dip's low ("dip")."""

    def __init__(self, market: str = "QQQ", tf: int = 5, weak: str = "down", depth_bp: float = 30.0,
                 rebound: str = "up", exit_: str = "vwap", stop: str = "none", start: str = "10:00",
                 until: str = "15:00"):
        self.market, self.tf, self.weak, self.depth = market, tf, weak, depth_bp / 1e4
        self.rebound, self.exit, self.stop_mode, self.start_s, self.until = rebound, exit_, stop, hm(start), hm(until)
        self.name = f"{market.lower()}-vwapreb-tf{tf}-{weak}-d{depth_bp:g}-{rebound}-{exit_}-{stop}"

    def start_day(self, day: Day) -> None:
        super().start_day(day)
        r = day.ctx["prev_ret"]
        self.ok = self.weak == "any" or (not math.isnan(r) and (r < 0 if self.weak == "down" else r <= -0.01))
        self.prev: tuple[float, float, float, float] | None = None
        self.lows: list[float] = []

    def on_bar(self, k: int, pos: Position | None, trades_today: int) -> list:
        if not self.cl[k]:
            return []
        o, h, lo, c = self.tfo[k], self.tfh[k], self.tfl[k], self.tfc[k]
        prev, self.prev = self.prev, (o, h, lo, c)
        self.lows = [*self.lows[-2:], lo]
        t_end = self.sod[k] + 60
        if pos is not None or trades_today or not self.ok or prev is None or not self.start_s < t_end <= self.until:
            return []
        vw = self.vwap[k]
        if math.isnan(vw) or c > vw * (1 - self.depth) or c >= vw or prev[3] >= prev[0]:
            return []
        if c <= (prev[3] if self.rebound == "up" else prev[1]):
            return []
        stop = min(self.lows) - 0.01 if self.stop_mode == "dip" else c * (1 - DISASTER)
        return [self.order(1, c, stop, vw if self.exit == "vwap" else None, "vwapreb")]


# ---- #7 volatility breakout ----------------------------------------------------------------------------------------

class AtrBreakout(_Day2):
    """Bands at the 09:30 open +- k x ATR(14 days). The first signal bar (to `until`) that closes beyond one is a trade
    that way; the stop is `stop_atr` x ATR back inside the band ("open": at the open). Out at 15:55. `vol`: trade every
    day ("any"), or only when the 14-day ATR is above the 50-day ("rising")."""

    def __init__(self, market: str = "QQQ", tf: int = 5, k: float = 1.0, stop_atr: float | str = 0.25,
                 vol: str = "any", until: str = "15:00"):
        self.market, self.tf, self.k, self.stop_atr, self.vol, self.until = market, tf, k, stop_atr, vol, hm(until)
        self.name = f"{market.lower()}-atrbo-tf{tf}-k{k:g}-s{stop_atr}-{vol}"

    def start_day(self, day: Day) -> None:
        super().start_day(day)
        x = day.ctx
        self.o, self.atr = x["open"], x["atr14"]
        self.ok = not (math.isnan(self.o) or math.isnan(self.atr)) and (
            self.vol == "any" or (not math.isnan(x["atr50"]) and self.atr > x["atr50"]))

    def on_bar(self, k: int, pos: Position | None, trades_today: int) -> list:
        if not self.cl[k] or pos is not None or trades_today or not self.ok or self.sod[k] + 60 > self.until:
            return []
        c, a = self.tfc[k], self.atr
        for side in (1, -1):
            band = self.o + side * self.k * a
            if (c - band) * side > 0:
                stop = self.o if self.stop_atr == "open" else band - side * float(self.stop_atr) * a
                return [self.order(side, c, stop, None, "atrbo-long" if side > 0 else "atrbo-short")]
        return []


class NoiseBreakout(_Day2):
    """Zarattini, Aziz & Barbon (2024): the upper band is max(open, previous close) x (1 + sigma), the lower
    min(open, previous close) x (1 - sigma), sigma the average move from the open at that minute over 14 sessions.
    Checked every `every` minutes from 10:00 (last entry 15:30): a close above the upper band goes long, below the
    lower short. Exit at a check where the close is back under max(upper band, VWAP) for a long (min(lower, VWAP) for
    a short; `trail` "band": the band alone), else at 15:55. Several trades a day; a 3% safety stop only."""

    def __init__(self, market: str = "QQQ", every: int = 30, mult: float = 1.0, trail: str = "vwap"):
        self.market, self.tf, self.every, self.mult, self.trail = market, 1, every, mult, trail
        self.max_trades = 20
        self.name = f"{market.lower()}-noise-every{every}-m{mult:g}-{trail}"

    def start_day(self, day: Day) -> None:
        super().start_day(day)
        x = day.ctx
        o, pc = x["open"], x["pclose"]
        self.ok = not math.isnan(o)
        self.up, self.dn = max(o, pc), min(o, pc)
        self.sig, self.c = x["sigma"].tolist(), day.c.tolist()

    def on_bar(self, k: int, pos: Position | None, trades_today: int) -> list:
        t_end = self.sod[k] + 60
        if not self.ok or t_end < RTH_OPEN + 1800 or (t_end - RTH_OPEN) % (self.every * 60):
            return []
        s, c, vw = self.sig[k], self.c[k], self.vwap[k]
        if math.isnan(s):
            return []
        ub, lb = self.up * (1 + self.mult * s), self.dn * (1 - self.mult * s)
        if pos is not None:
            line = (max(ub, vw) if pos.side > 0 else min(lb, vw)) if self.trail == "vwap" else (ub if pos.side > 0
                                                                                              else lb)
            return [Exit("trail")] if (c - line) * pos.side < 0 else []
        if trades_today >= self.max_trades or t_end > hm("15:30"):
            return []
        if c > ub:
            return [self.order(1, c, c * (1 - DISASTER), None, "noise-long")]
        if c < lb:
            return [self.order(-1, c, c * (1 + DISASTER), None, "noise-short")]
        return []


# ---- the versions tried --------------------------------------------------------------------------------------------

def vwap_versions(market: str) -> list[Strategy]:
    out: list[Strategy] = [VwapRebound(market)]                     # the main version, fixed before any result
    for tf, weak, depth, reb, exit_, stop in itertools.product((5, 15), ("down", "down1", "any"), (30.0, 0.0, 60.0),
                                                               ("up", "engulf"), ("vwap", "eod"), ("none", "dip")):
        s = VwapRebound(market, tf, weak, depth, reb, exit_, stop)
        if s.name not in {x.name for x in out}:
            out.append(s)
    return out


def breakout_versions(market: str) -> list[Strategy]:
    out: list[Strategy] = [AtrBreakout(market), NoiseBreakout(market)]   # the two main versions
    for tf, k, stop, vol in itertools.product((5, 15), (1.0, 0.25, 0.5, 0.75), (0.25, 0.1, 0.5, "open"),
                                              ("any", "rising")):
        s = AtrBreakout(market, tf, k, stop, vol)
        if s.name not in {x.name for x in out}:
            out.append(s)
    for every, trail in itertools.product((30, 15, 60), ("vwap", "band")):
        s = NoiseBreakout(market, every, 1.0, trail)
        if s.name not in {x.name for x in out}:
            out.append(s)
    return out


MAIN = ("vwapreb-tf5-down-d30-up-vwap-none", "atrbo-tf5-k1-s0.25-any", "noise-every30-m1-vwap")


def is_main(name: str) -> bool:
    return name.split("-", 1)[1] in MAIN


# ---- results in basis points of the price (a stop 3% away makes R meaningless) ------------------------------------

def bp(tr: Trade, gross: bool = False) -> float:
    """The trade's result as a share of its entry price, in basis points, after costs (or before)."""
    return (tr.r_gross if gross else tr.r) * tr.dist * 1e4


def grow(net_bp: np.ndarray, lev: float, start: float = 1000.0) -> tuple[float, float]:
    """$1,000 with the whole account in every trade at `lev` times, compounded; and the worst fall from a high (%)."""
    e, peak, worst = start, start, 0.0
    for r in net_bp.tolist():
        e = max(e * (1 + lev * r / 1e4), 0.0)
        peak = max(peak, e)
        worst = max(worst, 1 - e / peak if peak > 0 else 1.0)
    return e, worst * 100


def summarize(trades: list[Trade], a: int, b: int) -> dict:
    sel = [x for x in trades if a <= x.t_entry < b]
    years = (b - a) / (365.25 * B.DAY)
    if not sel:
        return {"n": 0, "per_year": 0.0, "win": 0.0, "avg_bp": 0.0, "gross_bp": 0.0, "t": 0.0, "pf": 0.0,
                "total_pct": 0.0, "usd_1x": 1000.0, "dd_1x": 0.0, "usd_5x": 1000.0, "dd_5x": 0.0}
    net = np.array([bp(x) for x in sel])
    gross = np.array([bp(x, True) for x in sel])
    t = float(net.mean() / (net.std(ddof=1) / math.sqrt(len(net)))) if len(net) > 1 and net.std() > 0 else 0.0
    loss = -net[net < 0].sum()
    u1, d1 = grow(net, 1)
    u5, d5 = grow(net, 5)
    return {"n": len(sel), "per_year": len(sel) / max(years, 1e-9), "win": float(np.mean(net > 0)),
            "avg_bp": float(net.mean()), "gross_bp": float(gross.mean()), "t": t,
            "pf": float(net[net > 0].sum() / loss) if loss > 0 else math.inf, "total_pct": float(net.sum() / 100),
            "usd_1x": u1, "dd_1x": d1, "usd_5x": u5, "dd_5x": d5}


# ---- running -------------------------------------------------------------------------------------------------------

def history(sym: str) -> B.Bars:
    """The ETF's minutes from WARMUP_START (the 14- and 50-session numbers need some weeks before the window)."""
    return B.load(f"alpaca_{sym}").between(WARMUP_START, 2**40)


def evaluate(market: str, versions: list[Strategy], rows: list[dict], keep: dict) -> None:
    bars = history(market)
    if not len(bars):
        print(f"no data for {market}: run `daytrade fetch alpaca`")
        return
    first, last = max(int(bars.t[0]), WINDOW_START), int(bars.t[-1]) + 60
    m = MARKETS[market]
    days: dict[int, list[Day]] = {}
    for i, s in enumerate(versions):
        if s.tf not in days:
            days[s.tf] = session_days(bars, s.tf)
        for vname in ("lighter", "arcus"):
            trades = run(s, days[s.tf], m.venues[vname], m.tick)
            if is_main(s.name):
                keep[(s.name, vname)] = trades
            for period, a, b in PERIODS:
                a, b = max(a, first), min(b, last)
                rows.append({"strategy": s.name, "family": s.name.split("-")[1], "market": market, "venue": vname,
                             "period": period, "from": B.day_str(a // B.DAY), "to": B.day_str((b - 1) // B.DAY),
                             **summarize(trades, a, b)})
        if i % 20 == 0:
            print(f"{time.strftime('%H:%M:%S')} {market} {i + 1}/{len(versions)} {s.name}", flush=True)


def write(rows: list[dict], keep: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / "summary.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    f = lambda s: time.strftime("%Y-%m-%d %H:%M", time.gmtime(s))    # noqa: E731
    for (name, venue), trades in keep.items():
        with (OUT / f"trades_{name}_{venue}.csv").open("w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["day", "side", "tag", "entry_utc", "entry", "stop", "target", "exit_utc", "exit", "reason",
                        "bp_before_costs", "bp_after_costs"])
            for x in trades:
                w.writerow([B.day_str(x.day), x.side, x.tag, f(x.t_entry), round(x.entry, 4), round(x.stop0, 4),
                            round(x.target, 4) if x.target else "", f(x.t_exit), round(x.exit, 4), x.reason,
                            round(bp(x, True), 2), round(bp(x), 2)])


def tables(path=None) -> str:
    """The report's tables from summary.csv: the main versions, and the first year's picks judged on the last 12
    months."""
    rows = list(csv.DictReader((path or OUT / "summary.csv").open()))
    for r in rows:
        for k in ("n", "per_year", "win", "avg_bp", "gross_bp", "t", "pf", "total_pct", "usd_1x", "dd_1x", "usd_5x",
                  "dd_5x"):
            r[k] = float(r[k])
    get = {(r["strategy"], r["venue"], r["period"]): r for r in rows}
    usd = lambda x: f"${x:,.0f}"         # noqa: E731
    out = ["## Main versions (fixed before any result)", "",
           "| Strategy | Market | Costs | Trades a year | Won | Average trade after costs | Before costs | t | "
           "$1,000 at 1x, two years | $1,000 at 5x, two years | Worst fall at 5x | Last 12 months: average | "
           "$1,000 at 5x |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        if r["period"] != "two years" or not is_main(r["strategy"]):
            continue
        z = get[(r["strategy"], r["venue"], "last 12m")]
        out.append(f"| {r['family']} | {r['market']} | {r['venue']} | {r['per_year']:.0f} | {r['win']:.0%} | "
                   f"{r['avg_bp']:+.1f} bp | {r['gross_bp']:+.1f} bp | {r['t']:+.1f} | {usd(r['usd_1x'])} | "
                   f"{usd(r['usd_5x'])} | {r['dd_5x']:.0f}% | {z['avg_bp']:+.1f} bp ({z['n']:.0f}) | "
                   f"{usd(z['usd_5x'])} |")
    out += ["", "## Picked on the first year, judged on the last 12 months (Lighter costs, 15+ first-year trades)",
            "", "| Market | Family | Version | Versions tried | First year: average | t | Last 12m: average | trades | "
            "$1,000 at 5x |", "|---|---|---|---|---|---|---|---|---|"]
    for market in sorted({r["market"] for r in rows}):
        for fam in ("vwapreb", "atrbo", "noise"):
            cand = [r for r in rows if r["market"] == market and r["family"] == fam and r["venue"] == "lighter"
                    and r["period"] == "first year" and r["n"] >= 15]
            cand.sort(key=lambda r: -r["avg_bp"])
            for r in cand[:3]:
                z = get[(r["strategy"], "lighter", "last 12m")]
                out.append(f"| {market} | {fam} | {r['strategy']} | {len(cand)} | {r['avg_bp']:+.1f} bp | "
                           f"{r['t']:+.1f} | {z['avg_bp']:+.1f} bp | {z['n']:.0f} | {usd(z['usd_5x'])} |")
    out += ["", "## How many versions made money after Lighter's costs", "",
            "| Market | Family | Versions | Positive, first year | Positive, last 12 months | Positive in both |",
            "|---|---|---|---|---|---|"]
    for market in sorted({r["market"] for r in rows}):
        for fam in ("vwapreb", "atrbo", "noise"):
            names = {r["strategy"] for r in rows if r["market"] == market and r["family"] == fam}
            pos = lambda n, p: get[(n, "lighter", p)]["avg_bp"] > 0 and get[(n, "lighter", p)]["n"] > 0   # noqa
            a = {n for n in names if pos(n, "first year")}
            b = {n for n in names if pos(n, "last 12m")}
            out.append(f"| {market} | {fam} | {len(names)} | {len(a)} | {len(b)} | {len(a & b)} |")
    return "\n".join(out)


def venue_check() -> str:
    """The main versions on Lighter's own one-minute prices against the reference (Alpaca) on the days both have,
    Lighter's costs: how many trades, the same trades (same minute and side), and the average after costs."""
    out = ["## The main versions on Lighter's own prices (its mark candles) against the reference", ""]
    for market in ("QQQ", "SPY"):
        ref, ven = history(market), B.load(f"lighter_{market}")
        if not len(ven):
            continue
        m = MARKETS[market]
        start = int(ven.t[0]) + 25 * B.DAY                    # the 14-session numbers need about three weeks first
        for s in (VwapRebound(market), AtrBreakout(market), NoiseBreakout(market)):
            got = {}
            for label, b in (("reference", ref), ("Lighter", ven)):
                tr = [x for x in run(s, session_days(b, s.tf), m.venues["lighter"], m.tick) if x.t_entry >= start]
                got[label] = tr
            same = {(x.t_entry, x.side) for x in got["reference"]} & {(x.t_entry, x.side) for x in got["Lighter"]}
            line = ", ".join(f"{k} {len(v)} trades, {np.mean([bp(x) for x in v]) if v else 0:+.1f} bp"
                             for k, v in got.items())
            out.append(f"- {s.name}, from {B.day_str(start // B.DAY)}: {line}; {len(same)} the same trade")
    return "\n".join(out)


def main(argv: list[str]) -> None:
    if argv[:1] == ["check"]:
        print(venue_check())
        return
    if argv[:1] == ["tables"]:
        print(tables())
        return
    rows: list[dict] = []
    keep: dict = {}
    for market in [x.upper() for x in argv] or ["QQQ", "SPY"]:
        evaluate(market, vwap_versions(market) + breakout_versions(market), rows, keep)
    if rows:
        write(rows, keep)
        print(f"wrote {OUT}", flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
