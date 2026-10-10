"""The funding arbitrage replayed on history, hour by hour, with the rules the live bot uses (rank.plan to enter,
rank.exit_reason to leave, the plan's own stop).

Data (`arbitrage history` writes it to arbitrage/data/history; `--data` reads another folder):
    <venue>_<SYM>_funding.csv   ts, rate_per_hour       a payment at time ts, + = longs pay
    lighter_<SYM>_px.csv        ts, open, high, low, close   the Lighter perp, the hour starting at ts
    arcus_<SYM>_perp.csv        the Arcus perp from its recorded trades (falls back to arcus_<SYM>_px.csv, its oracle)
    markets.json                both venues' market details (margin, minimum order, volume) as they are today

What it assumes, and where that flatters or hurts:
- one decision an hour, at the payment time, at that hour's closing prices on each venue;
- the next payment's rate is guessed as the last one paid (live, Arcus publishes its own estimate);
- an entry or exit costs `fill_cost_bp` per leg; a stop costs Arcus's taker fee and `cross_slip_bp` on both legs;
- a stop is checked against each hour's high and low on the leg's own venue; both legs are then closed at the same
  relative move, so a stop costs its fees and no more (the gap between the venues at that moment is not known);
- margin and minimum sizes are today's values for the whole period; the 24-hour volume the entry rule checks is the
  one each venue's hourly candles give for the 24 hours before (today's figure where the price files have no volume
  column: a download from before 10 Oct 2026);
- each venue keeps its own money: a leg's gain stays on its venue, so the smaller balance sets the next position.
  A stop leaves most of the money on one venue; the replay moves it back to half and half while flat (the owner's
  transfer between the venues) and counts how often, or never moves it (`rebalance_below=0`).
"""

from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass, field, replace
from pathlib import Path

from arbitrage.hours import open_hours
from arbitrage.rank import HOURS_YEAR, Leg, Plan, Settings, exit_reason, plan
from arbitrage.venues import arcus_leg, lighter_leg, sigma_day

H = 3600
VENUES = ("arcus", "lighter")


@dataclass
class Series:
    symbol: str
    legs: dict[str, Leg]
    rate: dict[str, dict[int, float]]               # venue -> {payment time: rate}
    px: dict[str, dict[int, tuple[float, float, float]]]   # venue -> {hour start: (high, low, close)}
    hours: list[int] = field(default_factory=list)  # payment times both venues have a rate for
    vol: dict[str, dict[int, float]] = field(default_factory=dict)   # venue -> {hour start: dollars traded}; may be {}


@dataclass
class Trade:
    symbol: str
    short_venue: str
    opened: int
    closed: int
    notional: float
    leverage: float
    funding: float
    price: float            # the two legs' price result together (the venues drifting apart)
    cost: float
    why: str

    @property
    def net(self) -> float:
        return self.funding + self.price - self.cost

    @property
    def hours(self) -> float:
        return (self.closed - self.opened) / H


@dataclass
class Result:
    trades: list[Trade]
    equity: dict[str, float]
    start: float
    first: int
    last: int
    curve: list[tuple[int, float]]
    rebalances: int = 0

    @property
    def net(self) -> float:
        return sum(self.equity.values()) - self.start

    @property
    def days(self) -> float:
        return max(1e-9, (self.last - self.first) / 86400)

    def summary(self) -> dict[str, float]:
        peak, dd = -math.inf, 0.0
        for _, e in self.curve:
            peak = max(peak, e)
            dd = max(dd, peak - e)
        held = sum(t.hours for t in self.trades)
        return {"net": self.net, "per_day": self.net / self.days, "apr_pct": self.net / self.start / self.days * 36500,
                "funding": sum(t.funding for t in self.trades), "price": sum(t.price for t in self.trades),
                "cost": sum(t.cost for t in self.trades), "trades": len(self.trades),
                "stops": sum(t.why.startswith("stop") for t in self.trades), "days": self.days,
                "in_market_pct": held / (self.days * 24) * 100, "max_drawdown": dd, "rebalances": self.rebalances,
                "arcus": self.equity["arcus"], "lighter": self.equity["lighter"]}


# ------------------------------------------------------------------------------------------------ loading
def _csv(path: Path) -> list[dict[str, str]]:
    try:
        with path.open() as fh:
            return list(csv.DictReader(fh))
    except OSError:
        return []


def load(data: Path, symbols: list[str] | None = None) -> dict[str, Series]:
    meta = json.loads((data / "markets.json").read_text())
    out: dict[str, Series] = {}
    for sym in sorted(meta):
        if symbols and sym not in symbols:
            continue
        a, b = arcus_leg(meta[sym]["arcus"]), lighter_leg(meta[sym]["lighter"], None, 0)
        if a is None or b is None:
            continue
        rate = {v: {int(r["ts"]): float(r["rate_per_hour"]) for r in _csv(data / f"{v}_{sym}_funding.csv")}
                for v in VENUES}
        px: dict[str, dict[int, tuple[float, float, float]]] = {}
        vol: dict[str, dict[int, float]] = {}
        for v, names in (("arcus", ("perp", "px")), ("lighter", ("px",))):
            rows: dict[int, tuple[float, float, float]] = {}
            for name in reversed(names):                 # the perp's own trades win over the oracle where both exist
                for r in _csv(data / f"{v}_{sym}_{name}.csv"):
                    rows[int(r["ts"])] = (float(r["high"]), float(r["low"]), float(r["close"]))
                    if name == "px" and r.get("volume") not in (None, ""):
                        vol.setdefault(v, {})[int(r["ts"])] = float(r["volume"])
            px[v] = rows
        hours = sorted(set(rate["arcus"]) & set(rate["lighter"]))
        if len(hours) >= 48 and px["arcus"] and px["lighter"]:
            out[sym] = Series(sym, {"arcus": a, "lighter": b}, rate, px, hours, vol if len(vol) == 2 else {})
    return out


# ------------------------------------------------------------------------------------------------ the replay
class _Market:
    """One market's running view: the last price on each venue, the rates so far, the daily move."""

    def __init__(self, s: Series) -> None:
        self.s = s
        self.last = {v: 0.0 for v in VENUES}
        self.bar: dict[str, tuple[float, float, float] | None] = {v: None for v in VENUES}
        self.ra: list[float] = []
        self.rl: list[float] = []
        self.closes: list[float] = []
        self.traded: dict[str, list[float]] = {v: [] for v in VENUES}       # the last 24 hours' dollars, per venue
        self._sigma: tuple[int, float] = (-1, 0.0)

    def step(self, t: int) -> bool:
        """Take in the hour that ends at t. False when this market has no payment at t."""
        for v in VENUES:
            bar = self.s.px[v].get(t - H)
            self.bar[v] = bar
            if bar is not None:
                self.last[v] = bar[2]
            if self.s.vol:
                self.traded[v] = [*self.traded[v][-23:], self.s.vol[v].get(t - H, 0.0)]
        if self.s.px["lighter"].get(t - H) is not None:
            self.closes.append(self.last["lighter"])
        if t not in self.s.rate["arcus"] or t not in self.s.rate["lighter"]:
            return False
        self.ra.append(self.s.rate["arcus"][t])
        self.rl.append(self.s.rate["lighter"][t])
        return True

    def sigma(self, t: int) -> float:
        day = t // 86400
        if self._sigma[0] != day:
            self._sigma = (day, sigma_day(self.closes[-336:]))
        return self._sigma[1]

    def plan(self, t: int, money: dict[str, float], st: Settings) -> Plan | None:
        if len(self.ra) < 24 or min(self.last.values()) <= 0:
            return None
        legs = {v: replace(self.s.legs[v], mark=self.last[v], rate_h=r[-1], next_rate_h=r[-1], next_at=t + H)
                for v, r in (("arcus", self.ra), ("lighter", self.rl))}
        if legs["arcus"].imf_off > legs["arcus"].imf:     # a stock: closed or open at this hour, not at the download
            legs["arcus"] = replace(legs["arcus"], off_hours=not open_hours(t))
        if self.s.vol:                                   # what was traded in the 24 h before, not what is today
            legs = {v: replace(leg, volume_24h=sum(self.traded[v])) for v, leg in legs.items()}
        return plan(legs["arcus"], legs["lighter"], self.ra[-168:], self.rl[-168:], self.sigma(t), money, st)


def run(series: dict[str, Series], capital: float, st: Settings | None = None, *, start: int = 0,
        end: int = 2**62, rebalance_below: float = 0.4) -> Result:
    """One position at a time, in the market that pays most; each venue starts with half the capital.
    rebalance_below: while flat, when one venue holds less than this share of the money, it is moved back to half
    and half (the owner's transfer, counted in `rebalances`); 0 = the money is never moved."""
    st = st or Settings()
    moved = 0
    eq = {"arcus": capital / 2, "lighter": capital / 2}
    mk = {sym: _Market(s) for sym, s in series.items()}
    times = sorted({t for s in series.values() for t in s.hours if start <= t <= end})
    trades: list[Trade] = []
    curve: list[tuple[int, float]] = []
    pos: dict | None = None
    for t in times:
        live = {sym for sym, m in mk.items() if m.step(t)}
        if pos is not None and pos["sym"] in live:
            m = mk[pos["sym"]]
            sv, lv = pos["short"], pos["long"]
            sign = 1.0 if sv == "arcus" else -1.0
            diff = sign * (m.ra[-1] - m.rl[-1])
            # the payment at t: the short leg receives its venue's rate, the long leg pays its own
            n_now = pos["size"] * m.last["arcus"]
            rs, rl_ = (m.ra[-1], m.rl[-1]) if sv == "arcus" else (m.rl[-1], m.ra[-1])
            eq[sv] += rs * n_now
            eq[lv] -= rl_ * n_now
            pos["funding"] += (rs - rl_) * n_now
            why = ""
            # stop or take profit: either leg's own venue price reached the distance during the hour
            for v in (lv, sv):
                bar = m.bar[v]
                if bar is None:
                    continue
                hi, lo, _ = bar
                e = pos["entry"][v]
                if lo <= e * (1 - pos["stop"]) or hi >= e * (1 + pos["stop"]):
                    up = hi >= e * (1 + pos["stop"])
                    why = f"stop: {v} {'up' if up else 'down'} {pos['stop'] * 100:.2f}% from the entry"
                    move = pos["stop"] if up else -pos["stop"]
                    break
            if why:
                # both legs close at the same relative move: the price results cancel, the crossing is paid for
                eq[lv] += pos["size"] * pos["entry"][lv] * move
                eq[sv] -= pos["size"] * pos["entry"][sv] * move
                cost = pos["notional"] * (st.arcus_taker_bp + 2 * st.cross_slip_bp) / 1e4
                price = 0.0
            else:
                d24 = sign * sum(a - b for a, b in zip(m.ra[-24:], m.rl[-24:], strict=False)) / min(24, len(m.ra))
                why = exit_reason((t - pos["opened"]) / H, diff, d24, st)
                swing = pos["size"] * (abs(m.last[lv] - pos["entry"][lv]) + abs(m.last[sv] - pos["entry"][sv])) / 2
                if not why and st.drift_close_share > 0 and swing / pos["money0"] >= 0.5 - st.drift_close_share:
                    why = "drift: closed after the payment so the money can be moved"
                if why:
                    pl = pos["size"] * (m.last[lv] - pos["entry"][lv])
                    ps = pos["size"] * (pos["entry"][sv] - m.last[sv])
                    eq[lv] += pl
                    eq[sv] += ps
                    price = pl + ps
                    cost = pos["notional"] * 2 * st.fill_cost_bp / 1e4
            if why:
                eq["arcus"] -= cost / 2
                eq["lighter"] -= cost / 2
                trades.append(Trade(pos["sym"], sv, pos["opened"], t, pos["notional"], pos["lev"], pos["funding"],
                                    price, cost + pos["cost_in"], why))
                pos = None
        if pos is None:
            total = eq["arcus"] + eq["lighter"]
            if rebalance_below > 0 and total > 0 and min(eq.values()) / total < rebalance_below:
                eq = {"arcus": total / 2, "lighter": total / 2}
                moved += 1
            best: Plan | None = None
            for sym in live:
                p = mk[sym].plan(t, eq, st)
                if p is not None and p.go and (best is None or p.income_day > best.income_day):
                    best = p
            if best is not None:
                m = mk[best.symbol]
                cost_in = best.notional * 2 * st.fill_cost_bp / 1e4
                eq["arcus"] -= cost_in / 2
                eq["lighter"] -= cost_in / 2
                pos = {"sym": best.symbol, "short": best.short_venue, "long": best.long_venue, "size": best.size,
                       "notional": best.notional, "lev": best.leverage, "stop": best.stop_dist, "opened": t,
                       "entry": dict(m.last), "funding": 0.0, "cost_in": cost_in,
                       "money0": max(1e-9, eq["arcus"] + eq["lighter"])}
        # the money on both venues: what has been paid and closed. An open position's two legs are not marked: their
        # hourly prices come from different moments on each venue, and the gap would read as a loss that is not one.
        curve.append((t, eq["arcus"] + eq["lighter"]))
    if pos is not None and times:                      # still open at the end: marked, not closed
        m = mk[pos["sym"]]
        pl = pos["size"] * (m.last[pos["long"]] - pos["entry"][pos["long"]])
        ps = pos["size"] * (pos["entry"][pos["short"]] - m.last[pos["short"]])
        eq[pos["long"]] += pl
        eq[pos["short"]] += ps
        trades.append(Trade(pos["sym"], pos["short"], pos["opened"], times[-1], pos["notional"], pos["lev"],
                            pos["funding"], pl + ps, pos["cost_in"], "still open at the end"))
    return Result(trades, eq, capital, times[0] if times else 0, times[-1] if times else 0, curve, moved)


def scalp(series: dict[str, Series], capital: float, st: Settings | None = None) -> dict[str, float]:
    """The other idea, for comparison: open just before a payment and close just after it, whenever the last
    payment's difference would have covered the four fills. Each venue's price is taken as unchanged in between."""
    st = st or Settings()
    trip = 4 * st.fill_cost_bp / 1e4
    net = funding = cost = 0.0
    n = 0
    times = sorted({t for s in series.values() for t in s.hours})
    mk = {sym: _Market(s) for sym, s in series.items()}
    money = {"arcus": capital / 2, "lighter": capital / 2}
    for t in times:
        live = [sym for sym, m in mk.items() if m.step(t)]
        best, best_gain = None, 0.0
        for sym in live:
            m = mk[sym]
            if len(m.ra) < 2:
                continue
            p = m.plan(t, money, st)
            if p is None or p.notional <= 0:
                continue
            guess = abs(m.ra[-2] - m.rl[-2])              # what the payment before this one paid
            if guess > trip and guess * p.notional > best_gain:
                best, best_gain = (sym, p, 1.0 if m.ra[-2] > m.rl[-2] else -1.0), guess * p.notional
        if best is not None:
            sym, p, sign = best
            m = mk[sym]
            got = sign * (m.ra[-1] - m.rl[-1]) * p.notional
            funding += got
            cost += trip * p.notional
            net += got - trip * p.notional
            n += 1
    days = max(1e-9, (times[-1] - times[0]) / 86400) if times else 1.0
    return {"net": net, "per_day": net / days, "funding": funding, "cost": cost, "trades": n, "days": days}


def by_market(series: dict[str, Series], capital: float, st: Settings | None = None) -> list[dict[str, float | str]]:
    """Each market on its own with the whole capital: which ones the rule would have held, and what they paid."""
    rows: list[dict[str, float | str]] = []
    for sym, s in series.items():
        r = run({sym: s}, capital, st)
        x = r.summary()
        diffs = [s.rate["arcus"][t] - s.rate["lighter"][t] for t in s.hours]
        rows.append({"market": sym, "days": x["days"], "net": x["net"], "per_day": x["per_day"],
                     "funding": x["funding"], "price": x["price"], "cost": x["cost"], "trades": x["trades"],
                     "stops": x["stops"], "in_market_pct": x["in_market_pct"],
                     "diff_apr": sum(diffs) / len(diffs) * HOURS_YEAR * 100,
                     "lev": max((t.leverage for t in r.trades), default=0.0)})
    return sorted(rows, key=lambda r: -float(r["per_day"]))
