"""`arbitrage cyclecost`: one market held at the venues' highest leverage and closed and reopened on a clock, replayed
on the hourly history (`arbitrage history`). It answers what that costs and how often it breaks, per cycle length and
per leverage, for a market alone (SPY and QQQ carry the most leverage of the stocks, indices and commodities).

What a cycle is: every `every_h` hours the position is closed and opened again, on the side the last funding payment
says (short where the rate is higher). Each cycle is four fills, so it makes volume of four times the position, and
it costs what four fills cost; the funding goes on being collected in between.

The rules replayed:
- leverage: the highest both venues allow at that hour, or a number. Arcus asks 1.5 times the margin while the
  underlying is closed (Monday to Friday 04:00-20:00 New York is open), so a position opened then is smaller; one
  opened earlier is kept as it is (Arcus does not raise the maintenance margin). Lighter's margin does not change.
- each venue holds half the money. The position is `margin_use` x leverage x the smaller balance. A leg's liquidation
  is its venue's balance less the maintenance margin away. The stop is where the live bot places it
  (`rank.max_leverage`): `stop_frac` of 1 / leverage less the maintenance margin, the same distance on both legs in
  both directions. That is nearer than half the way to the liquidation price, because the bot counts as if all the
  money were margin (SPY at 50x: stop 0.33%, liquidation 0.89%).
- a stop is checked against each venue's own hourly high and low. It closes both legs by crossing (`stop_bp`); when
  the same hour's range also reached the liquidation price, that is counted: the stop order may not have been filled
  before it.
- a closed position leaves its gain on one venue and its loss on the other. When the smaller balance is under
  `rebalance_share` of the money the replay stops, waits `transfer_h` hours for the owner to move it, and starts
  again with half on each (counted as a transfer).

The account is kept at the same size throughout (what a cycle or a stop costs is taken off the result, not off the
next position), so the dollars a day are those of an account of that size, not of one that has shrunk. An hourly bar
whose high is more than 1.25 times its low, or an Arcus bar more than 10% from Lighter's of the same hour, is left
out: that is a broken price feed, not a market (Arcus's QQQ oracle stood at 449 against 726 on 2 and 3 Jul 2026).

What it cannot know: the order of prices inside an hour, the two venues' prices drifting apart between an entry and
an exit (it nets to nothing over many cycles), and what the fills really cost: `round_trip_bp` is a number you give
it (`arbitrage fills` measures it on recorded order books).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

from arbitrage import backtest as bt
from arbitrage.hours import NEW_YORK, open_hours, weekend
from arbitrage.rank import HOURS_YEAR

H = 3600
__all__ = ["NEW_YORK", "open_hours", "weekend"]       # kept here for the modules that grew up importing them
# stop_early and the share of those closes filled as maker before the stop: Arcus SPY, 29 Sep - 3 Oct 2026, the cash
# session, $5,000, the average of the leg that gains and the leg that loses (README 2a; `arbitrage fills`)
EARLY = ((0.0, 0.0), (0.9, 0.84), (0.8, 0.96), (0.7, 0.975), (0.6, 0.985))
BROKEN = 1.25          # a bar whose high is this many times its low is a broken feed ...
APART = 0.10           # ... and so is one that closes this far from the other venue's close of the same hour


def bar(s: bt.Series, venue: str, t: int) -> tuple[float, float, float] | None:
    """The venue's bar for the hour starting at t: (high, low, close), or None when there is none worth trusting."""
    b = s.px[venue].get(t)
    if b is None or b[1] <= 0 or b[0] / b[1] > BROKEN:
        return None
    other = s.px["lighter" if venue == "arcus" else "arcus"].get(t)
    if other is not None and other[2] > 0 and abs(b[2] / other[2] - 1) > APART:
        return None if venue == "arcus" else b          # Arcus's is its oracle wherever its perp did not trade
    return b


def top_leverage(s: bt.Series, t: int) -> float:
    """The highest leverage both venues allow at hour t."""
    a, b = s.legs["arcus"], s.legs["lighter"]
    imf = max(a.imf if open_hours(t) else a.imf_off, b.imf)
    return 1.0 / imf if imf > 0 else 1.0


def stop_distance(s: bt.Series, lev: float, stop_frac: float = 0.5) -> float:
    """Where the live bot puts the stop at that leverage (rank.max_leverage), as a share of the price."""
    return stop_frac * max(0.0, 1.0 / lev - max(leg.mmf for leg in s.legs.values()))


@dataclass(frozen=True)
class Rules:
    every_h: float = 3.0            # close and reopen this often; 0 = keep it until a stop
    leverage: float = 0.0           # 0 = the venues' highest at that hour
    margin_use: float = 0.9
    stop_frac: float = 0.5
    round_trip_bp: float = 1.0      # one cycle: out and in again on both legs, in bp of one leg
    stop_bp: float = 3.0            # a stop: both legs crossed (Arcus's taker fee and the slip), in bp of one leg
    rebalance_share: float = 0.40
    transfer_h: float = 2.0         # how long the owner takes to move the money once told
    flip: bool = True               # False = always short Arcus (the usual side)
    stop_early: float = 0.0         # the live bot's setting: closed with limit orders from this share of the way to
    early_fill: float = 0.96        # the stop; this share of those closes is done before the stop (`arbitrage
                                    # fills` measures it), the rest ends as a stop


def run(s: bt.Series, capital: float, r: Rules | None = None) -> dict[str, Any]:
    r = r or Rules()
    eq = {"arcus": capital / 2, "lighter": capital / 2}
    last = {"arcus": 0.0, "lighter": 0.0}
    pos: dict[str, Any] | None = None
    wait_until = 0
    out = {"funding": 0.0, "cycle_cost": 0.0, "stop_cost": 0.0, "volume": 0.0, "oi_hours": 0.0, "cycles": 0,
           "stops": 0, "liq_reached": 0, "transfers": 0, "held_h": 0, "waiting_h": 0, "hours": 0,
           "stops_open": 0, "stops_night": 0, "stops_weekend": 0, "prices": 0.0, "early": 0.0}
    half = r.round_trip_bp / 2 / 1e4
    for t in s.hours:
        bars = {v: bar(s, v, t - H) for v in bt.VENUES}
        for v, b in bars.items():
            if b is not None:
                last[v] = b[2]
        if min(last.values()) <= 0:
            continue
        out["hours"] += 1
        ra, rl = s.rate["arcus"][t], s.rate["lighter"][t]
        if pos is not None:
            sv, lv, n = pos["short"], pos["long"], pos["notional"]
            out["oi_hours"] += n
            out["held_h"] += 1
            rs, rlong = (ra, rl) if sv == "arcus" else (rl, ra)
            out["funding"] += (rs - rlong) * n
            move = 0.0
            near = r.stop_early if r.stop_early > 0 else 1.0
            early = r.early_fill if r.stop_early > 0 else 0.0         # the share closed with limit orders
            for v in (lv, sv):
                b = bars[v]
                if b is None:
                    continue
                e = pos["entry"][v]
                up, down = b[0] / e - 1, 1 - b[1] / e
                if up >= pos["stop"] * near or down >= pos["stop"] * near:
                    reach = pos["stop"] * (early * near + 1 - early)       # where it was closed, on average
                    move = reach if up >= pos["stop"] * near else -reach
                    lost = sv if move > 0 else lv                    # the leg the move went against
                    lost_bar = bars[lost]
                    if lost_bar is not None:
                        e2 = pos["entry"][lost]
                        if (lost_bar[0] / e2 - 1 if move > 0 else 1 - lost_bar[1] / e2) >= pos["liq"][lost]:
                            out["liq_reached"] += 1
                    break
            if move:
                eq[lv] += n * move
                eq[sv] -= n * move
                out["stop_cost"] += n * ((1 - early) * r.stop_bp / 1e4 + early * half)
                out["stops"] += 1 - early
                out["early"] += early
                out["stops_weekend" if weekend(t - H) else "stops_open" if open_hours(t - H) else "stops_night"] += 1
                out["volume"] += 2 * n
            elif r.every_h > 0 and t - pos["opened"] >= r.every_h * H:
                pl = pos["size"] * (last[lv] - pos["entry"][lv])
                ps = pos["size"] * (pos["entry"][sv] - last[sv])
                eq[lv] += pl
                eq[sv] += ps
                out["prices"] += pl + ps             # the two venues' prices drifting apart or together
                out["cycle_cost"] += n * half
                out["volume"] += 2 * n
            else:
                continue
            pos = None
        if t < wait_until:
            out["waiting_h"] += 1
            continue
        total = eq["arcus"] + eq["lighter"]
        if total <= 0:
            break
        if wait_until:                                  # the owner's transfer has arrived
            eq = {"arcus": total / 2, "lighter": total / 2}
            wait_until = 0
        elif min(eq.values()) / total < r.rebalance_share:
            out["transfers"] += 1
            wait_until = t + int(r.transfer_h * H)
            if r.transfer_h > 0:
                out["waiting_h"] += 1
                continue
            eq = {"arcus": total / 2, "lighter": total / 2}
            wait_until = 0
        lev = min(r.leverage, top_leverage(s, t)) if r.leverage > 0 else top_leverage(s, t)
        px = (last["arcus"] + last["lighter"]) / 2
        n = r.margin_use * lev * min(eq.values())
        short = "arcus" if (ra >= rl or not r.flip) else "lighter"
        long_ = "lighter" if short == "arcus" else "arcus"
        liq = {v: max(0.0, eq[v] / n - s.legs[v].mmf) for v in bt.VENUES}
        pos = {"short": short, "long": long_, "notional": n, "size": n / px, "entry": dict(last), "opened": t,
               "liq": liq, "stop": stop_distance(s, lev, r.stop_frac), "lev": lev}
        out["cycle_cost"] += n * half
        out["volume"] += 2 * n
        out["cycles"] += 1
    days = max(1e-9, out["hours"] / 24)
    net = out["funding"] - out["cycle_cost"] - out["stop_cost"]
    return {**out, "days": days, "net": net, "pct_day": net / days / capital * 100, "net_day": net / days,
            "apr": net / days * 365 / capital * 100, "volume_day": out["volume"] / days,
            "oi": out["oi_hours"] / max(1, out["hours"]), "in_market": out["held_h"] / max(1, out["hours"]) * 100,
            "stops_week": out["stops"] / days * 7, "transfers_week": out["transfers"] / days * 7,
            "early_week": out["early"] / days * 7,
            "cycles_day": out["cycles"] / days,
            "per_million": -net / out["volume"] * 1e6 if out["volume"] else 0.0}


def moves(s: bt.Series, hours: tuple[int, ...] = (1, 3, 6, 12, 24)) -> list[dict[str, Any]]:
    """Per holding time and part of the week: how far the price went from where a position would have been opened,
    the further of up and down on the wider of the two venues, from the hourly highs and lows."""
    rows = []
    for name, test in (("stock market open", open_hours),
                       ("nights, Monday to Friday", lambda t: not open_hours(t) and not weekend(t)),
                       ("weekend", weekend)):
        for h in hours:
            far: list[float] = []
            for i in range(len(s.hours) - h):
                t0 = s.hours[i]
                if not test(t0) or s.hours[i + h] - t0 != h * H:
                    continue
                worst = 0.0
                for v in bt.VENUES:
                    first = bar(s, v, t0 - H)
                    if first is None:
                        continue
                    for j in range(i + 1, i + h + 1):
                        b = bar(s, v, s.hours[j] - H)
                        if b is not None:
                            worst = max(worst, b[0] / first[2] - 1, 1 - b[1] / first[2])
                far.append(worst * 100)
            if far:
                far.sort()
                rows.append({"when": name, "hours": h, "n": len(far), "median": far[len(far) // 2],
                             "p90": far[int(len(far) * 0.9)], "p99": far[int(len(far) * 0.99)], "max": far[-1],
                             "far": far})
    return rows


def distances(s: bt.Series, margin_use: float = 0.9) -> dict[str, dict[str, float]]:
    """At the venues' highest leverage: how far the price may go before each venue liquidates its leg."""
    a, b = s.legs["arcus"], s.legs["lighter"]
    out = {}
    for name, imf in (("open", max(a.imf, b.imf)), ("closed", max(a.imf_off, b.imf))):
        lev = 1 / imf
        out[name] = {"leverage": lev, "arcus": (1 / (margin_use * lev) - a.mmf) * 100,
                     "lighter": (1 / (margin_use * lev) - b.mmf) * 100, "stop": stop_distance(s, lev) * 100}
    return out


def _row(name: str, x: dict[str, Any]) -> str:
    return (f"  {name:<34} {x['oi']:>9,.0f} {x['volume_day']:>11,.0f} {x['in_market']:>8.0f}% {x['stops_week']:>11.1f} "
            f"{x['liq_reached']:>9.0f} {x['transfers_week']:>13.1f} {x['funding'] / x['days']:>+9.3f} "
            f"{-x['cycle_cost'] / x['days']:>+8.3f} {-x['stop_cost'] / x['days']:>+8.3f} {x['net_day']:>+9.3f} "
            f"{x['pct_day']:>+8.2f}% {x['per_million']:>12,.0f}")


HEAD = (f"  {'':<34} {'position':>9} {'volume/day':>11} {'in market':>9} {'stops/week':>11} {'liq price':>9} "
        f"{'transfers/week':>13} {'funding':>9} {'cycles':>8} {'stops':>8} {'net $/day':>9} {'of it/day':>9} "
        f"{'$ per $1M':>12}")


def report(series: dict[str, bt.Series], capital: float, round_trip_bp: dict[str, float] | None = None,
           margin_use: float = 0.9) -> str:
    rt = round_trip_bp or {}
    out = [f"CYCLING AT THE HIGHEST LEVERAGE: ${capital:g} in all, half on each venue, {margin_use * 100:.0f}% of it "
           "used as margin", ""]
    for sym, s in series.items():
        c = rt.get(sym, 1.0)
        first, last = s.hours[0], s.hours[-1]
        d = distances(s, margin_use)
        day = lambda t: dt.datetime.fromtimestamp(t, dt.UTC).strftime("%d %b %Y")       # noqa: E731
        diffs = [s.rate["arcus"][t] - s.rate["lighter"][t] for t in s.hours]
        apr = lambda xs: sum(xs) / max(1, len(xs)) * HOURS_YEAR * 100                      # noqa: E731
        op = [x for t, x in zip(s.hours, diffs, strict=True) if open_hours(t)]
        cl = [x for t, x in zip(s.hours, diffs, strict=True) if not open_hours(t)]
        out += [f"{sym}: {day(first)} to {day(last)} ({(last - first) / 86400:.0f} days)",
                f"  Arcus paid {apr(diffs):+.1f}% a year more than Lighter: {apr(op):+.1f}% while the stock market "
                f"is open, {apr(cl):+.1f}% while it is closed; Arcus was the higher in "
                f"{sum(x > 0 for x in diffs) / len(diffs) * 100:.0f}% of hours",
                f"  highest leverage {d['open']['leverage']:.0f}x while open, {d['closed']['leverage']:.1f}x while "
                f"closed. Liquidation is then {d['open']['arcus']:.2f}% away on Arcus and "
                f"{d['open']['lighter']:.2f}% on Lighter (open), {d['closed']['arcus']:.2f}% and "
                f"{d['closed']['lighter']:.2f}% (closed); the bot's stop is {d['open']['stop']:.2f}% away (open) "
                f"and {d['closed']['stop']:.2f}% (closed)", ""]
        out += ["  How far the price went from the entry (the further of up and down, the wider venue), % of the price",
                f"  {'':<26} {'held':>5} {'cases':>6} {'middle':>7} {'1 in 10':>8} {'1 in 100':>9} {'most':>6} | "
                f"reached the stop / the liquidation price at the highest leverage"]
        for m in moves(s):
            k = "open" if m["when"] == "stock market open" else "closed"
            liq = min(d[k]["arcus"], d[k]["lighter"])
            hit = lambda x, far=m["far"]: sum(v >= x for v in far) / len(far) * 100       # noqa: E731
            out.append(f"  {m['when']:<26} {m['hours']:>4}h {m['n']:>6} {m['median']:>7.2f} {m['p90']:>8.2f} "
                       f"{m['p99']:>9.2f} {m['max']:>6.2f} | {hit(d[k]['stop']):>5.0f}% / {hit(liq):.0f}%")
        out += ["", f"  The highest leverage, by how often it is closed and reopened (a cycle costs {c:g} bp, "
                    "a stop 3 bp)", HEAD]
        for h in (1, 3, 6, 12, 24, 0):
            out.append(_row(f"every {h} h" if h else "never: kept until a stop",
                            run(s, capital, Rules(every_h=h, round_trip_bp=c))))
        out += ["", f"  Every 3 hours, by leverage (a cycle costs {c:g} bp)", HEAD]
        for lev in (0.0, 30.0, 20.0, 10.0, 5.0, 3.0):
            out.append(_row("the venues' highest" if not lev else f"{lev:g}x",
                            run(s, capital, Rules(every_h=3, leverage=lev, round_trip_bp=c))))
        out += ["", "  Every 3 hours at the highest leverage, by where the close with limit orders starts "
                    "(`stop_early`; the share done before the stop is Arcus's recorded SPY book's)", HEAD]
        for near, done in EARLY:
            x = run(s, capital, Rules(every_h=3, round_trip_bp=c, stop_early=near, early_fill=done))
            name = f"from {near:.0%} of the way ({done:.0%} done)" if near else "off: taker orders at the stop"
            out.append(_row(name, x) + f"   + {x['early_week']:.1f} limit closes a week")
        out += ["", "  Every 3 hours at 10x, by what a cycle costs", HEAD]
        for cost in (0.0, 0.25, 0.5, 1.0, 2.0, 4.0):
            out.append(_row(f"{cost:g} bp a cycle", run(s, capital, Rules(every_h=3, leverage=10, round_trip_bp=cost))))
        out.append("")
    out += ["position = dollars on each leg, on average over every hour; volume = both venues together;",
            "liq price = stops in whose hour the price also reached the liquidation price (the stop may not have "
            "filled first);", "$ per $1M = what a million dollars of volume cost after the funding collected "
            "(negative = it paid)."]
    return "\n".join(out)
