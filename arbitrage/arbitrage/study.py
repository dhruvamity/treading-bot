"""`arbitrage study`: how long a funding arbitrage position should be held, measured on the history both venues
publish (arbitrage/data/history; `arbitrage history --update` refreshes it in minutes).

Both venues pay funding EVERY HOUR, so "the next payment" is never more than an hour away and one payment is small.
The questions this answers, each from the same data the backtest uses:

1. forward():   after the bot's own entry rule says go, how much funding the next H hours paid, against what getting
                in and out costs. Every hour of every market is a sample, so this is the widest evidence here.
2. holds():     the whole rule replayed (one position at a time, sizes, stops, money on two venues) with the position
                held exactly H hours, or at least H hours. Few positions in a hundred days, and each choice changes
                every later one, so a week more of data can reorder the rows: read "funding less fills" (what was
                paid, less every fill and stop), not "with prices". The prices column is the two legs' closing prices
                against their opening ones: it nets to nothing over many positions and is noise over twenty.
3. weekdays():  the same funding by the day the position was opened (Arcus's stock funding stops at weekends).
4. leverage():  the stop's distance and the leverage it allows: more leverage means a nearer stop.
5. drift():     closing a position once it has moved a share of the money from one venue to the other.

Nothing here trades or reads a key.
"""

from __future__ import annotations

import datetime as dt
import statistics as stats
from dataclasses import replace
from typing import Any

from arbitrage import backtest as bt
from arbitrage.rank import CRYPTO_CLASSES, HOURS_YEAR, Settings, conservative_edge

H = 3600
HORIZONS = (1, 3, 6, 12, 24, 48, 72, 120, 168, 336)
TRIPS_BP = (4.0, 8.0, 16.0)          # in and out on both legs at 1, 2 and 4 bp a fill


def rwa(series: dict[str, bt.Series]) -> dict[str, bt.Series]:
    """Stocks, indices and commodities: every market Arcus does not call crypto."""
    return {k: s for k, s in series.items() if s.legs["arcus"].category.upper() not in CRYPTO_CLASSES}


def _signals(s: bt.Series, st: Settings) -> list[tuple[int, int]]:
    """(index into s.hours, direction) for every hour the entry rule would open this market: the last payment, the
    last 24 h and the last 7 days agree on the side and the smallest pays at least min_edge_apr."""
    d = [s.rate["arcus"][t] - s.rate["lighter"][t] for t in s.hours]
    pre = [0.0]
    for x in d:
        pre.append(pre[-1] + x)
    floor = st.min_edge_apr / 100 / HOURS_YEAR
    out = []
    for i in range(168, len(d)):
        e = conservative_edge(d[i - 1], (pre[i] - pre[i - 24]) / 24, (pre[i] - pre[i - 168]) / 168)
        if abs(e) >= floor:
            out.append((i, 1 if e > 0 else -1))
    return out


def forward(series: dict[str, bt.Series], st: Settings | None = None,
            horizons: tuple[int, ...] = HORIZONS) -> list[dict[str, float]]:
    """Per horizon: the funding one leg's notional collected over the next H hours after a go signal, in bp."""
    st = st or Settings()
    got: dict[int, list[float]] = {h: [] for h in horizons}
    for s in series.values():
        d = [s.rate["arcus"][t] - s.rate["lighter"][t] for t in s.hours]
        pre = [0.0]
        for x in d:
            pre.append(pre[-1] + x)
        for i, sign in _signals(s, st):
            for h in horizons:
                if i + h <= len(d) and s.hours[i + h - 1] - s.hours[i] == (h - 1) * H:      # no gap in the hours
                    got[h].append(sign * (pre[i + h] - pre[i]) * 1e4)
    rows = []
    for h in horizons:
        x = sorted(got[h])
        if not x:
            continue
        mean = stats.fmean(x)
        rows.append({"hours": h, "n": len(x), "mean_bp": mean, "median_bp": x[len(x) // 2],
                     "low_quarter_bp": x[len(x) // 4],
                     **{f"net_{c:g}": mean - c for c in TRIPS_BP},
                     **{f"won_{c:g}": sum(v > c for v in x) / len(x) * 100 for c in TRIPS_BP},
                     "breakeven_bp": mean, "per_day_bp": mean / h * 24})
    return rows


def weekdays(series: dict[str, bt.Series], st: Settings | None = None, hours: int = 72) -> list[dict[str, Any]]:
    """The same, by the UTC weekday the position was opened on."""
    st = st or Settings()
    got: dict[int, list[float]] = {k: [] for k in range(7)}
    for s in series.values():
        d = [s.rate["arcus"][t] - s.rate["lighter"][t] for t in s.hours]
        pre = [0.0]
        for x in d:
            pre.append(pre[-1] + x)
        for i, sign in _signals(s, st):
            if i + hours <= len(d) and s.hours[i + hours - 1] - s.hours[i] == (hours - 1) * H:
                day = dt.datetime.fromtimestamp(s.hours[i], dt.UTC).weekday()
                got[day].append(sign * (pre[i + hours] - pre[i]) * 1e4)
    names = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
    return [{"day": names[k], "n": len(v), "mean_bp": stats.fmean(v) if v else 0.0} for k, v in got.items()]


def markets(series: dict[str, bt.Series]) -> list[dict[str, Any]]:
    """Per market: what Arcus paid over Lighter, a year, on weekdays and at weekends, and how often it was positive."""
    rows = []
    for sym, s in series.items():
        d = [(t, s.rate["arcus"][t] - s.rate["lighter"][t]) for t in s.hours]
        wd = [x for t, x in d if dt.datetime.fromtimestamp(t, dt.UTC).weekday() < 5]
        we = [x for t, x in d if dt.datetime.fromtimestamp(t, dt.UTC).weekday() >= 5]
        apr = lambda v: stats.fmean(v) * HOURS_YEAR * 100 if v else 0.0      # noqa: E731
        rows.append({"market": sym, "class": s.legs["arcus"].category, "hours": len(d), "apr": apr([x for _, x in d]),
                     "weekday_apr": apr(wd), "weekend_apr": apr(we),
                     "arcus_higher_pct": sum(x > 0 for _, x in d) / len(d) * 100})
    return sorted(rows, key=lambda r: -abs(r["apr"]))


def _run(series: dict[str, bt.Series], capital: float, st: Settings, name: str) -> dict[str, Any]:
    r = bt.run(series, capital, st)
    x = r.summary()
    n = max(1, len(r.trades))
    return {"name": name, "per_day": x["per_day"], "apr": x["per_day"] * 365 / capital * 100, "trades": x["trades"],
            "carry_day": (x["funding"] - x["cost"]) / max(1e-9, x["days"]),
            "stops": x["stops"], "moved": r.rebalances, "in_market": x["in_market_pct"], "funding": x["funding"],
            "price": x["price"], "cost": x["cost"], "leverage": sum(t.leverage for t in r.trades) / n,
            "hold_h": sum((t.closed - t.opened) / H for t in r.trades) / n}


def holds(series: dict[str, bt.Series], capital: float, st: Settings | None = None) -> list[dict[str, Any]]:
    st = st or Settings()
    rows = [_run(series, capital, st, "the bot's rule now")]
    rows += [_run(series, capital, replace(st, min_hold_h=h, max_hold_h=h), f"exactly {h} h, then pick again")
             for h in (1, 3, 6, 12, 24, 48, 72, 168, 336)]
    rows += [_run(series, capital, replace(st, min_hold_h=h, max_hold_h=0), f"at least {h} h, until it stops paying")
             for h in (0, 24, 72, 168, 336)]
    return rows


def leverage(series: dict[str, bt.Series], capital: float, st: Settings | None = None) -> list[dict[str, Any]]:
    st = st or Settings()
    return [_run(series, capital, replace(st, max_leverage=50, stop_sigmas=sg),
                 "the venues' maximum leverage" if sg < 0.1 else f"stop {sg:g} daily moves away")
            for sg in (4, 3, 2, 1.5, 1, 0.5, 0.01)]


def drift(series: dict[str, bt.Series], capital: float, st: Settings | None = None) -> list[dict[str, Any]]:
    st = st or Settings()
    return [_run(series, capital, replace(st, drift_close_share=x),
                 "off (the stop closes it)" if x == 0 else f"close at {x * 100:.0f}% / {100 - x * 100:.0f}%")
            for x in (0.0, 0.45, 0.40, 0.35, 0.30)]


# ------------------------------------------------------------------------------------------------ the report
def _table(rows: list[dict[str, Any]]) -> list[str]:
    out = [f"  {'':<38} {'funding less fills, $ a day':>27} | {'with prices':>11} {'a year':>8} {'positions':>9} "
           f"{'stops':>5} {'moved':>5} {'in market':>9} {'funding':>9} {'prices':>8} {'fills':>7} {'leverage':>8} "
           f"{'held':>7}"]
    for r in rows:
        out.append(f"  {r['name']:<38} {r['carry_day']:>+27.3f} | {r['per_day']:>+11.3f} {r['apr']:>+7.1f}% "
                   f"{r['trades']:>9.0f} "
                   f"{r['stops']:>5.0f} "
                   f"{r['moved']:>5.0f} {r['in_market']:>8.0f}% {r['funding']:>+9.2f} {r['price']:>+8.2f} "
                   f"{-r['cost']:>+7.2f} {r['leverage']:>7.1f}x {r['hold_h']:>6.0f}h")
    return out


def report(series: dict[str, bt.Series], capital: float, st: Settings | None = None, *, title: str = "") -> str:
    st = st or Settings()
    first = min(s.hours[0] for s in series.values())
    last = max(s.hours[-1] for s in series.values())
    day = lambda t: dt.datetime.fromtimestamp(t, dt.UTC).strftime("%d %b %Y")       # noqa: E731
    out = [f"HOLDING STUDY{title}: {len(series)} markets, {day(first)} to {day(last)} "
           f"({(last - first) / 86400:.0f} days), funding paid every hour on both venues", ""]
    out += ["1. After the entry rule says go: funding one leg collected over the next H hours (bp of the position)",
            f"  {'H':>5} {'samples':>8} {'mean':>7} {'median':>7} {'worst quarter':>13} | mean after fills of "
            f"{' / '.join(f'{c:g} bp' for c in TRIPS_BP)} | share that beat "
            f"{' / '.join(f'{c:g}' for c in TRIPS_BP)} bp"]
    for r in forward(series, st):
        out.append(f"  {r['hours']:>4.0f}h {r['n']:>8.0f} {r['mean_bp']:>7.2f} {r['median_bp']:>7.2f} "
                   f"{r['low_quarter_bp']:>13.2f} | " + " / ".join(f"{r[f'net_{c:g}']:+7.2f}" for c in TRIPS_BP)
                   + "       | " + " / ".join(f"{r[f'won_{c:g}']:4.0f}%" for c in TRIPS_BP))
    out += ["", f"2. The whole rule replayed at ${capital:g} (fills at {st.fill_cost_bp:g} bp each)"]
    out += _table(holds(series, capital, st))
    for fc in (2.0, 4.0):
        if fc != st.fill_cost_bp:
            out += ["", f"   the same with fills at {fc:g} bp each"]
            s2 = replace(st, fill_cost_bp=fc)
            out += _table([_run(series, capital, s2, "the bot's rule now")]
                          + [_run(series, capital, replace(s2, min_hold_h=h, max_hold_h=h),
                                  f"exactly {h} h, then pick again") for h in (3, 24, 72, 168)]
                          + [_run(series, capital, replace(s2, min_hold_h=h), f"at least {h} h, until it stops paying")
                             for h in (72, 168)])
    out += ["", "3. Funding over the next 72 h by the day the position was opened (bp)",
            "  " + "   ".join(f"{r['day']} {r['mean_bp']:.1f} ({r['n']})" for r in weekdays(series, st))]
    out += ["", f"4. The stop's distance and the leverage it allows (fills at 2 bp, ${capital:g})"]
    out += _table(leverage(series, capital, replace(st, fill_cost_bp=2.0)))
    out += ["", "5. Closing once a position has moved a share of the money to one venue (fills at 2 bp)"]
    out += _table(drift(series, capital, replace(st, fill_cost_bp=2.0)))
    out += ["", "6. What Arcus paid over Lighter, % a year on the position (+ = short Arcus, long Lighter)",
            f"  {'market':<8} {'class':<12} {'all':>7} {'weekdays':>9} {'weekends':>9} {'hours Arcus higher':>19}"]
    for m in markets(series):
        out.append(f"  {m['market']:<8} {m['class'].lower():<12} {m['apr']:>+6.1f}% {m['weekday_apr']:>+8.1f}% "
                   f"{m['weekend_apr']:>+8.1f}% {m['arcus_higher_pct']:>18.0f}%")
    return "\n".join(out)
