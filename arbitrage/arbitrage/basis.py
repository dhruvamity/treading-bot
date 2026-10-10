"""`arbitrage basis`: how far the same market's price on Arcus is from its price on Lighter, minute by minute, and what
happens to that gap over a weekend and at the Monday reopen.

Why it matters: the two legs of a position are opened and closed at each venue's own price. A gap that is the same at
the exit as at the entry costs nothing. A gap that opens while the position is held is a loss on paper on one venue
(and a gain on the other, where it cannot be used as margin), and a loss in cash if the position has to be closed then.

The two venues price a closed stock differently (their documentation, read 10 Oct 2026):
- Arcus keeps the mark and all trading inside a band around Friday's closing price: the initial margin times 0.5, then
  1, 2 and 4, each step after an hour of pressure (SPY: 1%, 2%, 4%, 8%). Funding is a fixed rate, margin for new
  positions is 1.5 times.
- Lighter has no band on SPY or QQQ since 10 Jul 2026: its price is its own order book's (a 2-minute average), and
  "as soon as an external price is available, the internal price is going to instantly converge to it". Leverage and
  margin do not change.

Data (arbitrage/data/history, written by `arbitrage history --minutes SPY QQQ`):
    lighter_<SYM>_px1m.csv     Lighter's one-minute candles
    arcus_<SYM>_perp1m.csv     the Arcus perp's own trades by the minute (its candles are the oracle's price)
A minute counts when both venues traded in it; the gap is then taken over five minutes (the middle value), because two
last trades of one minute can be most of a minute apart.
"""

from __future__ import annotations

import datetime as dt
import statistics as stats
from pathlib import Path
from typing import Any

from arbitrage.cycle import NEW_YORK, open_hours, weekend
from arbitrage.history import read_rows

Minute = tuple[float, float, float]      # high, low, close


def minutes(path: Path) -> dict[int, Minute]:
    """A minute file as {minute start: (high, low, close)}; minutes in which nothing traded are left out."""
    out = {}
    for r in read_rows(path):
        if len(r) > 5 and float(r[5]) <= 0:
            continue
        out[int(r[0])] = (float(r[2]), float(r[3]), float(r[4]))
    return out


def gaps(arcus: dict[int, Minute], lighter: dict[int, Minute], width: int = 300) -> list[tuple[int, float]]:
    """(time, Arcus over Lighter in bp) per five minutes in which both traded: the middle of the minutes' gaps."""
    buckets: dict[int, list[float]] = {}
    for t, a in arcus.items():
        b = lighter.get(t)
        if b is not None and b[2] > 0:
            buckets.setdefault(t // width * width, []).append((a[2] / b[2] - 1) * 1e4)
    return [(t, stats.median(x)) for t, x in sorted(buckets.items())]


def part(t: int) -> str:
    return "weekend" if weekend(t) else "stock market open" if open_hours(t) else "nights, Monday to Friday"


def by_part(g: list[tuple[int, float]]) -> list[dict[str, Any]]:
    rows = []
    for name in ("stock market open", "nights, Monday to Friday", "weekend"):
        x = [b for t, b in g if part(t) == name]
        if len(x) < 20:
            continue
        a = sorted(abs(b) for b in x)
        rows.append({"when": name, "n": len(x), "mean": stats.fmean(x), "median_abs": a[len(a) // 2],
                     "p90": a[int(len(a) * 0.9)], "p99": a[int(len(a) * 0.99)], "max": a[-1],
                     **{f"over_{k}": sum(v > k for v in a) / len(a) * 100 for k in (10, 25, 50, 100)}})
    return rows


def changes(g: list[tuple[int, float]], hours: tuple[float, ...] = (1, 3, 6, 24)) -> list[dict[str, float]]:
    """How much the gap changed between an entry and an exit `hours` later: what a position closed on a clock won or
    lost from the two venues' prices alone, in bp of a leg (as often a gain as a loss)."""
    at = dict(g)
    rows = []
    for h in hours:
        d = sorted(abs(at[t + int(h * 3600)] - b) for t, b in g if t + int(h * 3600) in at)
        if len(d) >= 50:
            rows.append({"hours": h, "n": len(d), "median": d[len(d) // 2], "p90": d[int(len(d) * 0.9)],
                         "p99": d[int(len(d) * 0.99)]})
    return rows


def _ny(t: int) -> dt.datetime:
    return dt.datetime.fromtimestamp(t, NEW_YORK)


def weekends(arcus: dict[int, Minute], lighter: dict[int, Minute]) -> list[dict[str, Any]]:
    """One row per weekend (Friday 20:00 to Monday 04:00 New York) that both venues traded through."""
    g = gaps(arcus, lighter)
    if not g:
        return []
    rows = []
    day = _ny(g[0][0]).date()
    last = _ny(g[-1][0]).date()
    while day <= last:
        if day.weekday() == 4:
            at = lambda d, h, m=0: int(dt.datetime(d.year, d.month, d.day, h, m, tzinfo=NEW_YORK).timestamp())  # noqa: E731
            mon = day + dt.timedelta(days=3)
            start, end = at(day, 20), at(mon, 4)
            inside = [(t, b) for t, b in g if start <= t < end]
            if len(inside) >= 12:
                mid = lambda lo, hi: [b for t, b in g if lo <= t < hi]                 # noqa: E731
                med = lambda x: stats.median(x) if x else None                        # noqa: E731
                anchor = {}
                far = {}
                for name, v in (("arcus", arcus), ("lighter", lighter)):
                    before = [v[t][2] for t in range(start - 1800, start, 60) if t in v]
                    px = [v[t][2] for t in range(start, end, 60) if t in v]
                    anchor[name] = stats.median(before) if before else (px[0] if px else 0.0)
                    far[name] = max((abs(p / anchor[name] - 1) for p in px), default=0.0) * 100 if anchor[name] else 0
                widest = max(inside, key=lambda r: abs(r[1]))
                rows.append({"friday": day.isoformat(), "n": len(inside),
                             "before": med(mid(start - 3600, start)), "median": med([b for _, b in inside]),
                             "widest": widest[1], "widest_at": _ny(widest[0]).strftime("%a %H:%M"),
                             "last_hour": med(mid(end - 3600, end)), "reopen": med(mid(end, end + 3600)),
                             "cash_open": med(mid(at(mon, 9, 30), at(mon, 10, 30))),
                             "arcus_far": far["arcus"], "lighter_far": far["lighter"]})
        day += dt.timedelta(days=1)
    return rows


def report(data: Path, symbols: list[str]) -> str:
    out = ["ARCUS AGAINST LIGHTER, MINUTE BY MINUTE (+ = Arcus is the dearer), in bp of the price", ""]
    f = lambda x: "     -" if x is None else f"{x:>+6.1f}"                               # noqa: E731
    for sym in symbols:
        a, b = minutes(data / f"arcus_{sym}_perp1m.csv"), minutes(data / f"lighter_{sym}_px1m.csv")
        g = gaps(a, b)
        if len(g) < 100:
            out += [f"{sym}: no minute files for both venues (`arbitrage history --minutes {sym}` fetches them)", ""]
            continue
        day = lambda t: dt.datetime.fromtimestamp(t, dt.UTC).strftime("%d %b %Y")       # noqa: E731
        out += [f"{sym}: {len(g):,} five-minute steps in which both venues traded, {day(g[0][0])} to {day(g[-1][0])}",
                f"  {'':<26} {'steps':>7} {'average':>8} {'middle size':>11} {'1 in 10':>8} {'1 in 100':>9} "
                f"{'widest':>7} | over 10 bp / 25 / 50 / 100"]
        for r in by_part(g):
            out.append(f"  {r['when']:<26} {r['n']:>7,} {r['mean']:>+8.1f} {r['median_abs']:>11.1f} {r['p90']:>8.1f} "
                       f"{r['p99']:>9.1f} {r['max']:>7.1f} | {r['over_10']:>4.0f}% / {r['over_25']:.0f}% / "
                       f"{r['over_50']:.1f}% / {r['over_100']:.1f}%")
        ch = changes(g)
        if ch:
            out += ["", "  How much the gap changed between an entry and an exit some hours later (a gain as often "
                        "as a loss):", "  " + "   ".join(f"{r['hours']:g} h: {r['median']:.1f} in the middle, "
                                                         f"{r['p90']:.1f} one time in ten" for r in ch)]
        wk = weekends(a, b)
        out += ["", "  Each weekend (Friday 20:00 to Monday 04:00 New York)",
                f"  {'Friday':<11} {'steps':>5} {'before':>7} {'middle':>7} {'widest':>7} {'when':<10} "
                f"{'last hour':>9} {'reopened':>9} {'cash open':>9} | furthest from Friday's close: Arcus, Lighter"]
        for r in wk:
            out.append(f"  {r['friday']:<11} {r['n']:>5} {f(r['before']):>7} {f(r['median']):>7} {f(r['widest']):>7} "
                       f"{r['widest_at']:<10} {f(r['last_hour']):>9} {f(r['reopen']):>9} {f(r['cash_open']):>9} | "
                       f"{r['arcus_far']:.2f}%, {r['lighter_far']:.2f}%")
        full = [r for r in wk if r["cash_open"] is not None and r["last_hour"] is not None]
        if full:
            w, h, c = (stats.median([abs(r[k]) for r in full]) for k in ("widest", "last_hour", "cash_open"))
            out.append(f"  over {len(full)} weekends, the middle size of the gap: widest {w:.0f} bp, in the last "
                       f"hour {h:.0f}, at the Monday cash open {c:.0f}")
        out.append("")
    out += ["before = the hour before Friday 20:00; reopened = Monday 04:00 to 05:00; cash open = Monday 09:30 to "
            "10:30 (all New York).", "A step is five minutes; its gap is the middle one of its minutes."]
    return "\n".join(out)
