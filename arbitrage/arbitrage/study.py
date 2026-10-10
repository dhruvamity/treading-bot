"""`arbitrage study`: how long a funding arbitrage position should be held, measured on the history both venues
publish (arbitrage/data/history; `arbitrage history --update` refreshes it in minutes).

Both venues pay funding EVERY HOUR, so "the next payment" is never more than an hour away and one payment is small.
The questions this answers, each from the same data the backtest uses:

0. sources():   what the files hold, hour by hour, and whether ProFunding's record of the same rates agrees.
1. forward():   after the bot's own entry rule says go, how much funding the next H hours paid, against what getting
                in and out costs. Every hour of every market is a sample, so this is the widest evidence here.
   ranked():    the same question for the BEST market of each hour only (and the best three): the bot holds one
                position, in the market that would pay most, so that is the market whose future matters. top_forward()
                is forward() on those; lives() is how long the best one went on paying; top_leverage() gives each of
                them a stop at every leverage and follows the hourly highs and lows of both venues.
2. holds():     the whole rule replayed (one position at a time, sizes, stops, money on two venues) with the position
                held exactly H hours, or at least H hours. Few positions in a hundred days, and each choice changes
                every later one, so a week more of data can reorder the rows: read "funding less fills" (what was
                paid, less every fill and stop), not "with prices". The prices column is the two legs' closing prices
                against their opening ones: it nets to nothing over many positions and is noise over twenty.
3. weekdays():  the same funding by the day the position was opened (Arcus's stock funding stops at weekends).
4. leverage():  the stop's distance and the leverage it allows: more leverage means a nearer stop.
5. drift():     closing a position once it has moved a share of the money from one venue to the other.
6. floors():    the volume a market must have traded before it is opened. The replay checks the volume of the 24 hours
                before each hour, as the bot does; until 10 Oct 2026 it used today's volume for the whole history, which
                let it open markets that were too thin at the time and roughly doubled what it reported.

Nothing here trades or reads a key.
"""

from __future__ import annotations

import datetime as dt
import statistics as stats
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from arbitrage import backtest as bt
from arbitrage.rank import CRYPTO_CLASSES, HOURS_YEAR, Settings, conservative_edge, exit_reason

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
    return _forward(series, ((sym, i, sign) for sym, s in series.items() for i, sign in _signals(s, st)), horizons)


def _forward(series: dict[str, bt.Series], picks: Iterable[tuple[str, int, int]],
             horizons: tuple[int, ...] = HORIZONS) -> list[dict[str, float]]:
    """picks: (market, index into its hours of the first payment still to come, direction)."""
    got: dict[int, list[float]] = {h: [] for h in horizons}
    sums: dict[str, list[float]] = {}
    for sym, i, sign in picks:
        s = series[sym]
        pre = sums.get(sym)
        if pre is None:
            pre = sums[sym] = [0.0]
            for t in s.hours:
                pre.append(pre[-1] + s.rate["arcus"][t] - s.rate["lighter"][t])
        for h in horizons:
            if i + h <= len(s.hours) and s.hours[i + h - 1] - s.hours[i] == (h - 1) * H:      # no gap in the hours
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


def weekdays(series: dict[str, bt.Series], st: Settings | None = None, hours: int = 72,
             picks: Iterable[tuple[str, int, int]] | None = None) -> list[dict[str, Any]]:
    """The same, by the UTC weekday the position was opened on (picks: as for _forward; default every go signal)."""
    st = st or Settings()
    if picks is None:
        picks = [(sym, i, sign) for sym, s in series.items() for i, sign in _signals(s, st)]
    got: dict[int, list[float]] = {k: [] for k in range(7)}
    sums: dict[str, list[float]] = {}
    for sym, i, sign in picks:
        s = series[sym]
        pre = sums.get(sym)
        if pre is None:
            pre = sums[sym] = [0.0]
            for t in s.hours:
                pre.append(pre[-1] + s.rate["arcus"][t] - s.rate["lighter"][t])
        if i + hours <= len(s.hours) and s.hours[i + hours - 1] - s.hours[i] == (hours - 1) * H:
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


# ------------------------------------------------------------------------------------------------ the best of each hour
@dataclass(frozen=True)
class Pick:
    """One market the entry rule would have opened at payment hour t, and where it stood among that hour's."""
    symbol: str
    t: int
    k: int                      # index of t in the market's hours
    sign: int                   # +1 = short Arcus, long Lighter
    rank: int                   # 0 = the one the bot opens (the most income a day)
    leverage: float
    stop: float                 # the stop's distance, a fraction of the price
    edge_h: float
    entry: tuple[float, float]  # the Arcus and the Lighter price it would have been opened at


def ranked(series: dict[str, bt.Series], capital: float, st: Settings | None = None) -> tuple[list[list[Pick]], int]:
    """(the picks of every hour that had one, best first; the number of hours looked at). The same plan() the bot and
    the replay use, with half the capital on each venue; best = most income a day, as the bot chooses."""
    st = st or Settings()
    money = {"arcus": capital / 2, "lighter": capital / 2}
    mk = {sym: bt._Market(s) for sym, s in series.items()}
    at = {sym: {t: i for i, t in enumerate(s.hours)} for sym, s in series.items()}
    times = sorted({t for s in series.values() for t in s.hours})
    out: list[list[Pick]] = []
    for t in times:
        plans = []
        for m in mk.values():
            if m.step(t):
                pl = m.plan(t, money, st)
                if pl is not None and pl.go:
                    plans.append((pl, m))
        plans.sort(key=lambda x: -x[0].income_day)
        if plans:
            out.append([Pick(pl.symbol, t, at[pl.symbol][t], 1 if pl.short_venue == "arcus" else -1, r, pl.leverage,
                             pl.stop_dist, pl.edge_h, (m.last["arcus"], m.last["lighter"]))
                        for r, (pl, m) in enumerate(plans)])
    return out, len(times)


def top_forward(series: dict[str, bt.Series], picks: list[list[Pick]], top: int = 1,
                horizons: tuple[int, ...] = HORIZONS) -> list[dict[str, float]]:
    """forward() for the best `top` markets of each hour only."""
    return _forward(series, ((x.symbol, x.k + 1, x.sign) for hour in picks for x in hour[:top]), horizons)


def starts(picks: list[list[Pick]]) -> list[Pick]:
    """The hours at which the best market changed: each is one separate opportunity, not one more hour of the same."""
    out: list[Pick] = []
    last: tuple[str, int, int] | None = None
    for hour in picks:
        x = hour[0]
        if last is None or (x.symbol, x.sign) != last[:2] or x.t - last[2] > H:
            out.append(x)
        last = (x.symbol, x.sign, x.t)
    return out


def life(s: bt.Series, x: Pick, st: Settings, *, stop: float = 0.0, cap_h: int = 720) -> tuple[int, float, bool]:
    """(hours held, funding collected in bp of the position, stopped) for a position opened at x and closed by the
    bot's own exit rule, or by a stop `stop` from the entry on either venue's hourly high or low (0 = no stop)."""
    got = 0.0
    d = [s.rate["arcus"][t] - s.rate["lighter"][t] for t in s.hours[max(0, x.k - 23):x.k + cap_h + 1]]
    off = x.k - max(0, x.k - 23)
    hours = 0
    for j in range(x.k + 1, min(len(s.hours), x.k + cap_h + 1)):
        t = s.hours[j]
        i = j - x.k + off
        got += x.sign * d[i] * 1e4
        hours = (t - x.t) // H
        if stop > 0:
            for v, e in zip(bt.VENUES, x.entry, strict=True):
                bar = s.px[v].get(t - H)
                if bar is not None and e > 0 and (bar[1] <= e * (1 - stop) or bar[0] >= e * (1 + stop)):
                    return hours, got, True
        last24 = d[max(0, i - 23):i + 1]
        if exit_reason(hours, x.sign * d[i], x.sign * sum(last24) / len(last24), st):
            break
    return hours, got, False


def lives(series: dict[str, bt.Series], picks: list[list[Pick]], st: Settings | None = None) -> dict[str, Any]:
    """How long the best market of the hour went on paying once it became the best: held from then until the last
    24 h and the next payment both pay nothing (the bot's exit rule with no minimum hold and no stop)."""
    st = replace(st or Settings(), min_hold_h=0.0, max_hold_h=0.0)
    rows = [(life(series[x.symbol], x, st), x) for x in starts(picks)]
    hrs = sorted(r[0][0] for r in rows)
    if not hrs:
        return {"n": 0}
    q = lambda f: hrs[min(len(hrs) - 1, int(len(hrs) * f))]      # noqa: E731
    return {"n": len(hrs), "q1": q(0.25), "median": q(0.5), "q3": q(0.75), "mean": stats.fmean(hrs),
            "mean_bp": stats.fmean(r[0][1] for r in rows),
            **{f"over_{h}": sum(x >= h for x in hrs) / len(hrs) * 100 for h in (24, 48, 72, 168)},
            **{f"paid_{c:g}": sum(r[0][1] > c for r in rows) / len(rows) * 100 for c in TRIPS_BP},
            "markets": Counter(x.symbol for _, x in rows).most_common()}


FILLS_BP = (1.0, 2.0, 4.0)      # what one fill is charged in the tables below


def top_leverage(series: dict[str, bt.Series], capital: float, st: Settings | None = None,
                 top: int = 1) -> list[dict[str, Any]]:
    """Every hour's best market opened at each leverage, with the stop that leverage allows (stop_frac of the way to
    liquidation), and held by the bot's exit rule. The stop is checked against each venue's own hourly high and low.
    A stopped position pays the entry's two fills, Arcus's taker fee and the crossing; and the money it moved from
    one venue to the other has to be sent back by hand before the next position is as big."""
    st = st or Settings()
    now = " (the bot now)"
    highest = st.stop_sigmas <= 0 and st.max_leverage >= 50
    variants = [(f"stop {sg:g} daily moves away" + (now if sg == st.stop_sigmas else ""), replace(st, stop_sigmas=sg))
                for sg in sorted({4.0, 3.0, 2.0, 1.5, 1.0} | ({st.stop_sigmas} if st.stop_sigmas > 0 else set()),
                                 reverse=True)]
    variants += [(f"{lv:g}x on every market that allows it"
                  + (now if st.stop_sigmas <= 0 and st.max_leverage == lv else ""),
                  replace(st, max_leverage=lv, stop_sigmas=0.0)) for lv in (1.0, 2.0, 3.0, 5.0, 7.5, 10.0, 20.0)]
    variants.append(("the venues' maximum leverage" + (now if highest else ""),
                     replace(st, max_leverage=1000.0, stop_sigmas=0.0)))
    rows = []
    for name, sv in variants:
        hours, total = ranked(series, capital, sv)
        xs = [x for hour in hours for x in hour[:top]]
        if not xs:
            continue
        held = fund = 0.0
        stopped = 0
        net = dict.fromkeys(FILLS_BP, 0.0)
        for x in xs:
            h, bp, hit = life(series[x.symbol], x, sv, stop=x.stop)
            held += h
            fund += bp * x.leverage
            stopped += hit
            for fc in FILLS_BP:
                cost = 2 * fc + sv.arcus_taker_bp + 2 * sv.cross_slip_bp if hit else 4 * fc
                net[fc] += (bp - cost) * x.leverage
        # a leg's notional is margin_use x leverage x half the capital: bp of the notional -> % a year of the capital
        scale = sv.margin_use / 2 / 1e4 * 100 / max(1e-9, held) * HOURS_YEAR
        rows.append({"name": name, "leverage": stats.fmean(x.leverage for x in xs),
                     "stop_pct": stats.fmean(x.stop for x in xs) * 100, "n": len(xs), "held_h": held / len(xs),
                     "stopped_pct": stopped / len(xs) * 100, "stops_30d": stopped / max(1e-9, held) * 720,
                     "signal_hours": len(hours) / max(1, total) * 100, "funding_apr": fund * scale,
                     **{f"apr_{fc:g}": net[fc] * scale for fc in FILLS_BP}})
    return rows


# ------------------------------------------------------------------------------------------------ the data itself
def sources(data: Path, symbols: Iterable[str]) -> list[str]:
    """What the history files hold, and how ProFunding's record of the same hours compares with the venues' own."""
    from arbitrage.history import read_rows

    day = lambda t: dt.datetime.fromtimestamp(t, dt.UTC).strftime("%d %b %Y %H:%M")       # noqa: E731
    out = []
    syms = sorted(symbols)
    for v in bt.VENUES:
        n = missing = files = 0
        first, last = 2**62, 0
        for sym in syms:
            ts = [int(r[0]) for r in read_rows(data / f"{v}_{sym}_funding.csv")]
            if not ts:
                continue
            files += 1
            n += len(ts)
            missing += (ts[-1] - ts[0]) // H + 1 - len(ts)
            first, last = min(first, ts[0]), max(last, ts[-1])
        if files:
            out.append(f"  {v:<8} funding: {files} markets, {n:,} hourly payments, {day(first)} to {day(last)} UTC, "
                       f"{missing} hours missing inside the files")
    for v in bt.VENUES:
        both = close = files = 0
        level: list[tuple[float, str]] = []
        first, last = 2**62, 0
        for sym in syms:
            pf = {int(r[0]): float(r[1]) for r in read_rows(data / f"profunding_{v}_{sym}_funding.csv")}
            own = {int(r[0]): float(r[1]) for r in read_rows(data / f"{v}_{sym}_funding.csv")}
            ts = sorted(set(pf) & set(own))
            if not ts:
                continue
            files += 1
            both += len(ts)
            first, last = min(first, ts[0]), max(last, ts[-1])
            close += sum(abs(pf[t] - own[t]) * HOURS_YEAR * 100 <= 0.5 for t in ts)
            level.append((abs(stats.fmean(pf[t] for t in ts) - stats.fmean(own[t] for t in ts)) * HOURS_YEAR * 100,
                          sym))
        if files:
            out.append(f"  ProFunding's {v} rates: {files} markets, {both:,} hours in both, {day(first)} to "
                       f"{day(last)}; a market's average over those hours differs from the venue's own by "
                       f"{stats.fmean(x for x, _ in level):.2f}% a year (most: {max(level)[1]} {max(level)[0]:.2f}%); "
                       f"hour by hour {close / both * 100:.0f}% are within 0.5% a year")
    if not any("ProFunding" in x for x in out):
        out.append("  ProFunding: no files (`arbitrage history --profunding` fetches its last 30 days)")
    return out


def traded(data: Path, picks: list[list[Pick]]) -> str:
    """What the best market of each hour had traded on each venue in the 24 h before (from the candles' volume)."""
    from arbitrage.history import read_rows

    vol: dict[tuple[str, str], dict[int, float]] = {}
    by: dict[str, list[float]] = {v: [] for v in bt.VENUES}
    for hour in picks:
        x = hour[0]
        for v in bt.VENUES:
            rows = vol.get((v, x.symbol))
            if rows is None:
                rows = vol[(v, x.symbol)] = {int(r[0]): float(r[5]) for r in
                                             read_rows(data / f"{v}_{x.symbol}_px.csv") if len(r) > 5}
            if not rows:
                return ("   traded then: the price files have no volume column, so today's volume was used for every "
                        "hour (`arbitrage history --update` adds the column)")
            by[v].append(sum(rows.get(x.t - i * H, 0.0) for i in range(1, 25)))
    if not by["arcus"]:
        return "   traded then: no hour had a best market"
    cut = lambda xs, f: sorted(xs)[int(len(xs) * f)]       # noqa: E731
    return ("   traded in that market in the 24 h before: Arcus "
            f"${cut(by['arcus'], 0.5):,.0f} in the middle hour and ${cut(by['arcus'], 0.1):,.0f} in the thinnest "
            f"tenth, Lighter ${cut(by['lighter'], 0.5):,.0f} and ${cut(by['lighter'], 0.1):,.0f}")


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


def floors(series: dict[str, bt.Series], capital: float, st: Settings | None = None) -> list[dict[str, Any]]:
    """The volume a market must have traded on each venue in the 24 h before the bot opens it."""
    st = st or Settings()
    return [_run(series, capital, replace(st, min_volume_24h=v),
                 f"${v:,.0f} a day" + (" (the bot now)" if v == st.min_volume_24h else ""))
            for v in sorted({0.0, 25_000.0, 100_000.0, 500_000.0, 2_000_000.0, st.min_volume_24h})]


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


def _forward_table(rows: list[dict[str, float]]) -> list[str]:
    out = [f"  {'H':>5} {'samples':>8} {'mean':>7} {'median':>7} {'worst quarter':>13} "
           f"{'bp a day in this stretch':>25} | mean after fills of "
           f"{' / '.join(f'{c:g} bp' for c in TRIPS_BP)} | share that beat "
           f"{' / '.join(f'{c:g}' for c in TRIPS_BP)} bp"]
    before = (0.0, 0.0)
    for r in rows:
        pace = (r["mean_bp"] - before[1]) / (r["hours"] - before[0]) * 24
        before = (r["hours"], r["mean_bp"])
        out.append(f"  {r['hours']:>4.0f}h {r['n']:>8.0f} {r['mean_bp']:>7.2f} {r['median_bp']:>7.2f} "
                   f"{r['low_quarter_bp']:>13.2f} {pace:>25.2f} | "
                   + " / ".join(f"{r[f'net_{c:g}']:+7.2f}" for c in TRIPS_BP)
                   + "       | " + " / ".join(f"{r[f'won_{c:g}']:4.0f}%" for c in TRIPS_BP))
    return out


def _leverage_table(rows: list[dict[str, Any]]) -> list[str]:
    fills = " / ".join(f"{fc:g}" for fc in FILLS_BP)
    out = [f"  {'':<38} {'leverage':>8} {'stop at':>8} {'held':>6} {'stopped':>8} {'stops in 30 days':>16} "
           f"{'funding':>8} | % a year of the capital while held, after fills of {fills} bp each"]
    for r in rows:
        out.append(f"  {r['name']:<38} {r['leverage']:>7.1f}x {r['stop_pct']:>7.2f}% {r['held_h']:>5.0f}h "
                   f"{r['stopped_pct']:>7.1f}% {r['stops_30d']:>16.1f} {r['funding_apr']:>+7.1f}% | "
                   + " / ".join(f"{r[f'apr_{fc:g}']:+7.1f}" for fc in FILLS_BP))
    return out


def report(series: dict[str, bt.Series], capital: float, st: Settings | None = None, *, title: str = "",
           data: Path | None = None) -> str:
    st = st or Settings()
    first = min(s.hours[0] for s in series.values())
    last = max(s.hours[-1] for s in series.values())
    day = lambda t: dt.datetime.fromtimestamp(t, dt.UTC).strftime("%d %b %Y")       # noqa: E731
    out = [f"HOLDING STUDY{title}: {len(series)} markets, {day(first)} to {day(last)} "
           f"({(last - first) / 86400:.0f} days), funding paid every hour on both venues", ""]
    if data is not None:
        out += ["0. The data"] + sources(data, series) + [""]
    picks, total = ranked(series, capital, st)
    began = starts(picks)
    out += ["1. The BEST market of each hour (the one the bot would open): funding one leg collected over the next H "
            "hours, in bp of the position",
            f"   {len(picks)} of {total} hours had a market that passed the entry rule; the best one changed "
            f"{len(began)} times, so these are about {len(began)} separate opportunities seen from every hour of each"]
    out += _forward_table(top_forward(series, picks, 1))
    best = Counter(hour[0].symbol for hour in picks).most_common(8)
    out += ["   the best market was: " + ", ".join(f"{sym} {n / len(picks) * 100:.0f}%" for sym, n in best)
            if picks else "   no hour had one"]
    if data is not None and picks:
        out.append(traded(data, picks))
    out += ["", "   the best THREE of each hour"] + _forward_table(top_forward(series, picks, 3))
    out += ["", "   EVERY market that passed the entry rule (last payment, 24 h and 7 days agree)"]
    out += _forward_table(forward(series, st))
    lv = lives(series, picks, st)
    if lv["n"]:
        out += ["", "2. How long the best market went on paying, from the hour it became the best until the last 24 h "
                "and the next payment both paid nothing",
                f"   {lv['n']} opportunities: a quarter ended within {lv['q1']} h, half within {lv['median']} h, a "
                f"quarter lasted over {lv['q3']} h (mean {lv['mean']:.0f} h); "
                + ", ".join(f"{lv[f'over_{h}']:.0f}% lasted {h} h" for h in (24, 48, 72, 168)),
                f"   funding collected over that life: {lv['mean_bp']:.1f} bp on average; "
                + ", ".join(f"{lv[f'paid_{c:g}']:.0f}% paid for {c:g} bp of fills" for c in TRIPS_BP)]
    out += ["", f"3. The whole rule replayed at ${capital:g}, one position at a time in the best market "
            f"(fills at {st.fill_cost_bp:g} bp each)"]
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
    top1 = [(x.symbol, x.k + 1, x.sign) for hour in picks for x in hour[:1]]
    out += ["", "4. Funding over the next 72 h by the day the best market was opened (bp)",
            "  " + "   ".join(f"{r['day']} {r['mean_bp']:.1f} ({r['n']})" for r in weekdays(series, st, picks=top1))]
    out += ["", "5. Leverage: the best market of each hour opened at each leverage, the stop half-way to liquidation, "
            "held by the bot's exit rule", "   (a stop costs the entry's fills, a taker fee and the crossing, and "
            "leaves the money on one venue: one transfer by hand each, and a smaller next position until it is made)"]
    out += _leverage_table(top_leverage(series, capital, st))
    out += ["", f"   the same as a replay, one position at a time (fills at 2 bp, ${capital:g})"]
    out += _table(leverage(series, capital, replace(st, fill_cost_bp=2.0)))
    out += ["", "6. Closing once a position has moved a share of the money to one venue (fills at 2 bp)"]
    out += _table(drift(series, capital, replace(st, fill_cost_bp=2.0)))
    out += ["", "7. What Arcus paid over Lighter, % a year on the position (+ = short Arcus, long Lighter)",
            f"  {'market':<8} {'class':<12} {'all':>7} {'weekdays':>9} {'weekends':>9} {'hours Arcus higher':>19}"]
    for m in markets(series):
        out.append(f"  {m['market']:<8} {m['class'].lower():<12} {m['apr']:>+6.1f}% {m['weekday_apr']:>+8.1f}% "
                   f"{m['weekend_apr']:>+8.1f}% {m['arcus_higher_pct']:>18.0f}%")
    if all(s.vol for s in series.values()):
        out += ["", "8. The volume floor: what a market must have traded on each venue in the 24 h before (fills at "
                "2 bp). A thin market's fills cost more than this assumes"]
        out += _table(floors(series, capital, replace(st, fill_cost_bp=2.0)))
    return "\n".join(out)
