"""#1 of the owner's third list (15 Oct 2026): monthly momentum rotation (QuantifiedStrategies). At each month's last
close, rank the ETFs by their return over the month (`months` months in the variants) and hold the best one through
the next month; stay if it is still the best.

Held two ways:
- "etf": the ETFs themselves, as posted. Dividends included, about 1 bp a side, no funding, no leverage.
- "perp": a perpetual on Lighter, what this repository can trade. The price only (a perpetual pays no dividend),
  Lighter's costs and its average funding for every day held, at 1x to 3x with the liquidation price checked on each
  day's low. Neither Lighter nor Arcus lists TLT or EEM (Oct 2026), so the perpetual version rotates what they do
  list: SPY, QQQ and gold (GLD's price for gold: the ETF since Nov 2004, which follows spot gold less 0.4% a year).

Only the last two years count (the owner's rule, `daytrade.PERIODS`): the first hold begins at the first month end
inside them; the ranking may look back up to twelve months before it.

    python -m daytrade.rotation        -> reports/rotation/summary.csv (REPORT.md is written from it by hand)
"""

from __future__ import annotations

import csv
import datetime as dt
import itertools
import math
import sys
from dataclasses import dataclass

import numpy as np

from . import DATA, REPORTS, SPLIT, WINDOW_START

OUT = REPORTS / "rotation"
DAILY = DATA / "daily"
def _day(t: int) -> str:
    return dt.datetime.fromtimestamp(t, dt.UTC).date().isoformat()


START, LAST12 = _day(WINDOW_START), _day(SPLIT)

ETF_COST_BP = {"SPY": 0.5, "QQQ": 0.5, "TLT": 1.0, "IEF": 1.0, "SHY": 1.0, "EEM": 2.0, "GLD": 1.0}
# Lighter, one side, and a long's average funding (bp an hour, 26 Jun-15 Oct 2026): as in swing.VENUES; GLD = XAU.
PERP = {"SPY": (0.65, 0.050), "QQQ": (0.75, 0.061), "GLD": (0.55, 0.096)}
PERP_MM = {"SPY": 0.012, "QQQ": 0.012, "GLD": 0.024}


@dataclass
class Series:
    days: np.ndarray        # dates as numpy datetime64[D]
    c: np.ndarray           # [asset, day] close
    lo: np.ndarray          # [asset, day] low
    tr: np.ndarray          # [asset, day] total-return index (dividends reinvested)


def load(syms: tuple[str, ...]) -> Series:
    """The days every one of `syms` traded, from data/daily/yahoo_<sym>.csv (`daytrade fetch daily`)."""
    tables = []
    for s in syms:
        rows = list(csv.reader((DAILY / f"yahoo_{s}.csv").open()))[1:]
        tables.append({r[0]: (float(r[3]), float(r[4]), float(r[5])) for r in rows})
    today = dt.date.today().isoformat()
    common = sorted(set.intersection(*(set(t) for t in tables)) - {today})
    c = np.array([[t[d][1] for d in common] for t in tables])
    lo = np.array([[t[d][0] for d in common] for t in tables])
    div = np.array([[t[d][2] for d in common] for t in tables])
    gross = np.ones_like(c)
    gross[:, 1:] = (c[:, 1:] + div[:, 1:]) / c[:, :-1]
    return Series(np.array(common, dtype="datetime64[D]"), c, lo, np.cumprod(gross, axis=1))


@dataclass(frozen=True)
class Version:
    universe: tuple[str, ...]
    months: int = 1                 # the ranking's look-back
    hold: str = "etf"               # "etf" or "perp"
    flat_below: str = ""            # "": always hold the best; "zero": flat when the best fell; "SHY": hold SHY
                                    # when the best did worse than SHY (absolute momentum)
    lev: float = 1.0

    @property
    def name(self) -> str:
        return (f"rotate[{'/'.join(self.universe)},{self.months}m,{self.hold}"
                + (f",flat<{self.flat_below}" if self.flat_below else "") + (f",{self.lev:g}x" if self.lev != 1 else "")
                + "]")


@dataclass
class Result:
    days: np.ndarray                # the day of each equity point
    equity: np.ndarray              # the account at each day's close ($1,000 at the start)
    holds: list[tuple[str, str, float]]   # (month end the hold began, asset or "-", the hold's result)
    switches: int
    liquidations: int


def month_ends(days: np.ndarray) -> np.ndarray:
    m = days.astype("datetime64[M]")
    return np.flatnonzero(np.r_[m[1:] != m[:-1], True])


def simulate(v: Version, s: Series, syms: tuple[str, ...]) -> Result:
    """Rank at each month end on the closes up to it, hold from that close to the next month end's close."""
    idx = [syms.index(a) for a in v.universe]
    rank_px = s.tr if v.hold == "etf" else s.c
    me = month_ends(s.days)
    k0 = max(v.months, int(np.searchsorted(s.days[me], np.datetime64(START))))
    e = 1000.0
    eq_days, eq = [s.days[me[k0]]], [e]
    holds: list[tuple[str, str, float]] = []
    cur: int | None = None
    switches = liqs = 0
    for k in range(k0, len(me) - 1):
        i0, i1 = me[k], me[k + 1]
        score = {j: rank_px[j, i0] / rank_px[j, me[k - v.months]] - 1 for j in idx}
        pick: int | None = max(score, key=score.get)
        if v.flat_below == "zero" and score[pick] < 0:
            pick = None
        elif v.flat_below == "SHY":
            j_shy = syms.index("SHY")
            if score[pick] < rank_px[j_shy, i0] / rank_px[j_shy, me[k - v.months]] - 1:
                pick = j_shy
        cost = 0.0
        if pick != cur:
            switches += 1
            for j in (cur, pick):
                if j is not None:
                    cost += (ETF_COST_BP[syms[j]] if v.hold == "etf" else PERP[syms[j]][0]) * 1e-4 * v.lev
            cur = pick
        e0 = e * (1 - cost)
        if pick is None:
            path = np.full(i1 - i0, e0)
        else:
            px = rank_px[pick, i0 + 1:i1 + 1] / rank_px[pick, i0] - 1
            if v.hold == "perp":
                days_held = (s.days[i0 + 1:i1 + 1] - s.days[i0]).astype(int)
                fund = PERP[syms[pick]][1] * 1e-4 * 24 * days_held
                path = e0 * (1 + v.lev * (px - fund))
                worst = s.lo[pick, i0 + 1:i1 + 1] / s.c[pick, i0] - 1
                hit = np.flatnonzero(-worst >= 1 / v.lev - PERP_MM[syms[pick]])
                if len(hit):                      # liquidated: half the maintenance margin left (fees, discount)
                    liqs += 1
                    path[hit[0]:] = e0 * v.lev * PERP_MM[syms[pick]] * 0.5
                    cur = None
            else:
                path = e0 * (1 + px)
        holds.append((str(s.days[i0]), syms[pick] if pick is not None else "-", float(path[-1] / e - 1)))
        e = float(max(path[-1], 0.0))
        eq_days += list(s.days[i0 + 1:i1 + 1])
        eq += list(np.maximum(path, 0.0))
        if e <= 0:
            break
    return Result(np.array(eq_days), np.array(eq), holds, switches, liqs)


def buy_hold(asset: str, s: Series, syms: tuple[str, ...], hold: str, start: np.datetime64) -> Result:
    j = syms.index(asset)
    i0 = int(np.searchsorted(s.days, start))
    if hold == "etf":
        px = s.tr[j, i0:] / s.tr[j, i0]
        eq = 1000 * (1 - ETF_COST_BP[asset] * 1e-4) * px
    else:
        days_held = (s.days[i0:] - s.days[i0]).astype(int)          # funding is paid on the value as it grows
        eq = 1000 * (1 - PERP[asset][0] * 1e-4) * s.c[j, i0:] / s.c[j, i0] * np.exp(-PERP[asset][1] * 1e-4 * 24
                                                                                      * days_held)
    return Result(s.days[i0:], eq, [], 1, 0)


# ---- statistics ----------------------------------------------------------------------------------------------------

def stats(r: Result, a: str, b: str) -> dict:
    """The account between days a and b (b excluded): growth a year, worst fall, and the months held."""
    i, j = np.searchsorted(r.days, np.datetime64(a)), np.searchsorted(r.days, np.datetime64(b))
    if j - i < 20:
        return {}
    eq = r.equity[i:j]
    years = (r.days[j - 1] - r.days[i]).astype(int) / 365.25
    peak = np.maximum.accumulate(eq)
    dd = float(np.max(1 - eq / np.where(peak > 0, peak, 1)))
    holds = [h for h in r.holds if a <= h[0] < b]
    m = np.array([h[2] for h in holds]) if holds else np.zeros(0)
    return {"start": str(r.days[i]), "end": str(r.days[j - 1]), "years": years,
            "growth": float(eq[-1] / eq[0]), "cagr": float((eq[-1] / eq[0]) ** (1 / years) - 1) if eq[-1] > 0 else -1.0,
            "worst_fall": dd, "months": len(m), "months_won": float((m > 0).mean()) if len(m) else math.nan,
            "sharpe": float(m.mean() / m.std(ddof=1) * math.sqrt(12)) if len(m) > 2 and m.std() > 0 else math.nan,
            "in": " ".join(f"{a}:{n}" for a, n in sorted(_count(holds).items()))}


def _count(holds: list) -> dict:
    out: dict[str, int] = {}
    for h in holds:
        out[h[1]] = out.get(h[1], 0) + 1
    return out


PERIODS = (("two years", START, "2100-01-01"), ("first year", START, LAST12), ("last 12 months", LAST12, "2100-01-01"))


# ---- the versions ----------------------------------------------------------------------------------------------------

MAIN = Version(("SPY", "TLT", "EEM"))                       # as posted
MAIN_PERP = Version(("SPY", "QQQ", "GLD"), hold="perp")      # what the venues can trade


def versions() -> list[Version]:
    out = [MAIN, MAIN_PERP]
    for uni, months, flat in itertools.product(
            (("SPY", "TLT", "EEM"), ("SPY", "TLT"), ("SPY", "TLT", "EEM", "GLD"), ("SPY", "QQQ", "GLD"),
             ("SPY", "QQQ", "GLD", "TLT")), (1, 3, 6, 12), ("", "zero", "SHY")):
        out.append(Version(uni, months, "etf", flat))
    for uni, months, flat, lev in itertools.product((("SPY", "QQQ", "GLD"), ("SPY", "QQQ"), ("SPY", "GLD")),
                                                    (1, 3, 6, 12), ("", "zero"), (1.0, 2.0, 3.0)):
        out.append(Version(uni, months, "perp", flat, lev))
    seen: set[str] = set()
    return [v for v in out if not (v.name in seen or seen.add(v.name))]


ALL = ("SPY", "QQQ", "TLT", "EEM", "GLD", "SHY")


def run_all() -> list[dict]:
    s = load(ALL)
    rows = []
    for v in versions():
        r = simulate(v, s, ALL)
        for pname, a, b in PERIODS:
            st = stats(r, a, b)
            if st:
                rows.append({"version": v.name, "main": v in (MAIN, MAIN_PERP), "hold": v.hold,
                             "universe": "/".join(v.universe), "months_back": v.months, "flat_below": v.flat_below,
                             "lev": v.lev, "period": pname, "switches": r.switches, "liquidations": r.liquidations,
                             **st})
    me = month_ends(s.days)
    start = s.days[me[int(np.searchsorted(s.days[me], np.datetime64(START)))]]
    for asset, hold in (("SPY", "etf"), ("SPY", "perp"), ("QQQ", "perp"), ("GLD", "perp"), ("TLT", "etf"),
                        ("EEM", "etf")):
        r = buy_hold(asset, s, ALL, hold, start)
        for pname, a, b in PERIODS:
            st = stats(r, a, b)
            if st:
                rows.append({"version": f"buy_and_hold[{asset},{hold}]", "main": False, "hold": hold,
                             "universe": asset, "months_back": 0, "flat_below": "", "lev": 1.0, "period": pname,
                             "switches": 1, "liquidations": 0, **st})
    return rows


def write(rows: list[dict]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / "summary.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, list(rows[0]))
        w.writeheader()
        for r in rows:
            w.writerow({k: (f"{x:.4f}" if isinstance(x, float) else x) for k, x in r.items()})


def table(rows: list[dict], names: list[str]) -> list[str]:
    get = {(r["version"], r["period"]): r for r in rows}
    out = ["| Version | " + " | ".join(p for p, _, _ in PERIODS) + " | Worst fall, two years | Switches a year |",
           "|---|" + "---|" * (len(PERIODS) + 2)]
    for n in names:
        a = get.get((n, "two years"))
        if not a:
            continue
        cells = [f"{get[(n, p)]['cagr']:+.1%}" if (n, p) in get else "-" for p, _, _ in PERIODS]
        out.append(f"| {n} | " + " | ".join(cells) + f" | {a['worst_fall']:.0%} | {a['switches'] / a['years']:.1f} |")
    return out


def main(argv: list[str]) -> None:
    rows = run_all()
    write(rows)
    print("\n".join(table(rows, [MAIN.name, MAIN_PERP.name, "buy_and_hold[SPY,etf]", "buy_and_hold[SPY,perp]"])))


if __name__ == "__main__":
    main(sys.argv[1:])
