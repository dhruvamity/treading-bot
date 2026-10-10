"""`arbitrage fills`: what the two legs' orders cost, measured on recorded order books (the tape the Arcus and Lighter
bots' recorders write: arcus/data/scout/tape and lighter/data/tape).

The bot's own way in and out is a limit order at the best price on Arcus (no fee) and, for whatever that has filled, a
taker order on Lighter (no fee either). Three things then decide the cost:

1. how long the Arcus order waits. An order is counted as filled when trades from the other side print through its
   price, or at its price once what was ahead of it in the queue has traded; it is moved when the best price has left
   it behind for `requote_s`.
2. what the fill is worth a few seconds later. An order at the best price is filled when the market comes to it, so
   the price is usually a little worse for it right after (the market it is hedged in has moved the same way).
3. what crossing costs: half the spread and the depth eaten on Lighter; on Arcus the same plus its taker fee.

`pair()` replays both legs together on the days both books were recorded. `arcus_leg()` needs only Arcus's book.
`near_stop()` asks what the bot's `stop_early` setting needs to know: a position closed with a limit order on Arcus
from some share of the way to its stop, is it done before the price reaches the stop itself?
Nothing here trades or reads a key.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from arbitrage.cycle import open_hours, weekend

US = 1_000_000
WAITS = (15, 30, 60, 120, 300, 600)
SIZES = (1000.0, 5000.0, 25000.0)
ARCUS_TAKER_BP = 2.25
# the bot's stop at the venues' highest leverage, as a share of the price: (stock market open, closed), 10 Oct 2026
NEAR_STOPS = {"SPY": (0.003333, 0.008333), "QQQ": (0.006667, 0.016667)}
NEAR_SHARES = (0.6, 0.7, 0.8, 0.9)


def part(t_us: int) -> str:
    t = t_us // US
    if weekend(t):
        return "weekend"
    if not open_hours(t):
        return "nights, Monday to Friday"
    d = dt.datetime.fromtimestamp(t, dt.UTC).astimezone(__import__("zoneinfo").ZoneInfo("America/New_York"))
    return "cash session 09:30-16:00" if 570 <= d.hour * 60 + d.minute < 960 else "early and late 04:00-20:00"


PARTS = ("cash session 09:30-16:00", "early and late 04:00-20:00", "nights, Monday to Friday", "weekend")


class Book:
    """One market's recorded best prices, depth and trades over some days."""

    def __init__(self, root: Path, days: list[str]) -> None:
        import numpy as np

        self.np = np
        self.bbo = self._cat(root, days, "bbo", ("ts", "bid", "ask", "bid_sz", "ask_sz"))
        self.depth = self._cat(root, days, "depth", ("ts", "bp", "bs", "ap", "as_"))
        tr = self._cat(root, days, "trades", ("ts", "px", "sz", "buy", "tid"))
        if len(tr["ts"]):                              # a refill repeats trades the stream already had
            _, i = np.unique(tr["tid"], return_index=True)
            i.sort()
            tr = {k: v[i] for k, v in tr.items()}
        self.tr = tr

    def _cat(self, root: Path, days: list[str], kind: str, fields: tuple[str, ...]) -> dict[str, Any]:
        np = self.np
        cols: dict[str, list[Any]] = {k: [] for k in fields}
        for day in days:
            for f in sorted((root / day).glob(f"{kind}-*.npz")):
                if f.name.endswith(".tmp.npz"):
                    continue
                with np.load(f) as z:
                    for k in fields:
                        cols[k].append(z[k])
        if not cols["ts"]:
            return {k: np.array([]) for k in fields}
        out = {k: np.concatenate(v) for k, v in cols.items()}
        o = np.argsort(out["ts"], kind="stable")
        return {k: v[o] for k, v in out.items()}

    @property
    def ok(self) -> bool:
        return len(self.bbo["ts"]) > 100 and len(self.depth["ts"]) > 100

    def top(self, t: int) -> tuple[float, float, float, float] | None:
        b = self.bbo
        i = int(self.np.searchsorted(b["ts"], t, side="right")) - 1
        if i < 0 or t - b["ts"][i] > 30 * US:
            return None
        return float(b["bid"][i]), float(b["ask"][i]), float(b["bid_sz"][i]), float(b["ask_sz"][i])

    def mid(self, t: int) -> float | None:
        x = self.top(t)
        return None if x is None else (x[0] + x[1]) / 2

    def sweep(self, t: int, buy: bool, units: float) -> float | None:
        """The average price of taking `units` from the last depth snapshot before t (None when none is near)."""
        d = self.depth
        i = int(self.np.searchsorted(d["ts"], t, side="right")) - 1
        if i < 0 or t - d["ts"][i] > 30 * US:
            return None
        px, sz = (d["ap"][i], d["as_"][i]) if buy else (d["bp"][i], d["bs"][i])
        left, cost, last = units, 0.0, 0.0
        for p, s in zip(px, sz, strict=True):
            if p <= 0 or s <= 0:
                continue
            take = min(left, float(s))
            cost += take * float(p)
            left -= take
            last = float(p)
            if left <= 1e-12:
                break
        if last <= 0:
            return None
        return (cost + left * last) / units           # deeper than the snapshot: the rest at its last price

    def passive(self, t0: int, buy: bool, units: float, wait_s: float,
                requote_s: float = 3.0) -> tuple[list[tuple[int, float, float]], float]:
        """A limit order kept at the best price on our side from t0. ([(time, price, units)], what is left)."""
        top = self.top(t0)
        if top is None:
            return [], units
        bid, ask, bsz, asz = top
        p, q = (bid, bsz) if buy else (ask, asz)
        left, fills = units, []
        b, tr = self.bbo, self.tr
        i = int(self.np.searchsorted(b["ts"], t0, side="right"))
        j = int(self.np.searchsorted(tr["ts"], t0, side="right"))
        end, behind = t0 + int(wait_s * US), 0
        nb, nt = len(b["ts"]), len(tr["ts"])
        while left > 1e-12:
            tb = int(b["ts"][i]) if i < nb else 2**62
            tt = int(tr["ts"][j]) if j < nt else 2**62
            t = min(tb, tt)
            if t > end:
                break
            if tt <= tb:                                   # a trade
                px, sz, taker_buys = float(tr["px"][j]), float(tr["sz"][j]), bool(tr["buy"][j])
                j += 1
                if taker_buys == buy:
                    continue                               # the same side as ours: it does not fill us
                if (px < p - 1e-9) if buy else (px > p + 1e-9):
                    fills.append((t, p, left))             # traded through our price
                    left = 0.0
                elif abs(px - p) < 1e-9:
                    q -= sz
                    if q < 0:
                        f = min(left, -q)
                        fills.append((t, p, f))
                        left -= f
                        q = 0.0
            else:                                          # the book changed
                best, size, other = ((float(b["bid"][i]), float(b["bid_sz"][i]), float(b["ask"][i])) if buy else
                                     (float(b["ask"][i]), float(b["ask_sz"][i]), float(b["bid"][i])))
                i += 1
                if (other <= p + 1e-9) if buy else (other >= p - 1e-9):
                    fills.append((t, p, left))             # the other side came to our price
                    left = 0.0
                elif (best > p + 1e-9) if buy else (best < p - 1e-9):
                    behind = behind or t
                    if t - behind >= requote_s * US:       # moved to the new best price, behind what is there
                        p, q, behind = best, size, 0
                else:
                    behind = 0
                    if (best < p - 1e-9) if buy else (best > p + 1e-9):
                        q = 0.0                            # alone at the best price
        return fills, left


def near_stop(a: Book, usd: float, stops: tuple[float, float], hold_s: int = 3 * 3600, step_s: int = 300,
              wait_s: int = 600) -> list[dict[str, Any]]:
    """A position opened every `step_s` and held `hold_s`. Once Arcus's mid is a share of the way to the stop, its leg
    is closed with a limit order at the best price; whatever has not filled when the mid reaches the stop (or after
    `wait_s`) would have gone with a taker order. One row per part of the week, share and leg: the leg that gains (a
    long when the price rose: it sells into buyers) and the leg that loses (it buys while the price runs away)."""
    np = a.np
    ts, mid = a.bbo["ts"], (a.bbo["bid"] + a.bbo["ask"]) / 2
    grid = np.arange(int(ts[0]) // US + 1, int(ts[-1]) // US)
    at = np.clip(np.searchsorted(ts, grid * US, side="right") - 1, 0, len(ts) - 1)
    gm, stale = mid[at], grid * US - ts[at] > 30 * US
    got: dict[tuple[str, float, str], list[tuple[float, float | None, bool]]] = {}
    entries: dict[str, list[int]] = {}
    for i0 in range(0, len(grid) - hold_s, step_s):
        if stale[i0]:
            continue
        when = "stock market open" if open_hours(int(grid[i0])) else "stock market closed"
        stop = stops[0] if when == "stock market open" else stops[1]
        rel = gm[i0:i0 + hold_s] / gm[i0] - 1
        entries.setdefault(when, []).append(int(bool((np.abs(rel) >= stop).any())))
        for share in NEAR_SHARES:
            near = np.flatnonzero(np.abs(rel) >= share * stop)
            if not len(near):
                continue
            k1 = int(near[0])
            after = np.flatnonzero(np.abs(rel[k1:]) >= stop)
            wait = min(wait_s, int(after[0])) if len(after) else wait_s
            t1, units = int(grid[i0 + k1]) * US, usd / float(gm[i0 + k1])
            for buy in (True, False):
                fills, left = a.passive(t1, buy, units, max(wait, 1))
                secs = (fills[-1][0] - t1) / US if fills and left <= 1e-12 else None
                leg = "gains" if (rel[k1] > 0) != buy else "loses"
                got.setdefault((when, share, leg), []).append((1 - left / units, secs, bool(len(after))))
    rows = []
    for (when, share, leg), r in sorted(got.items()):
        n = entries[when]
        waits = sorted(x[1] for x in r if x[1] is not None)
        rows.append({"when": when, "share": share, "leg": leg, "entries": len(n), "stopped": sum(n) / len(n) * 100,
                     "closes": len(r), "of_entries": len(r) / len(n) * 100,
                     "done": sum(x[0] >= 0.999 for x in r) / len(r) * 100,
                     "filled": sum(x[0] for x in r) / len(r) * 100,
                     "median_wait": waits[len(waits) // 2] if waits else 0.0,
                     "stop_followed": sum(x[2] for x in r) / len(r) * 100})
    return rows


def days_of(root: Path) -> list[str]:
    return sorted(d.name for d in root.iterdir() if d.is_dir()) if root.is_dir() else []


def _times(b: Book, step_s: int) -> list[int]:
    lo, hi = int(b.bbo["ts"][0]) + 120 * US, int(b.bbo["ts"][-1]) - (WAITS[-1] + 100) * US
    return list(range(lo, hi, step_s * US))


def _mean(rows: list[tuple[float | None, ...]], i: int) -> float:
    v = [r[i] for r in rows if r[i] is not None]
    return sum(v) / len(v) if v else 0.0        # type: ignore[arg-type]


def arcus_leg(a: Book, usd: float, step_s: int = 120) -> list[dict[str, Any]]:
    """Per part of the week: a limit order of `usd` at Arcus's best price, both sides, every `step_s`."""
    got: dict[str, list[tuple[float | None, float | None, float | None, float | None]]] = {}
    for t0 in _times(a, step_s):
        top = a.top(t0)
        if top is None:
            continue
        m0 = (top[0] + top[1]) / 2
        units = usd / m0
        for buy in (True, False):
            fills, left = a.passive(t0, buy, units, WAITS[-1])
            done = (fills[-1][0] - t0) / US if left <= 1e-12 and fills else None
            later5 = later60 = None
            if fills:
                tf, p = fills[0][0], fills[0][1]
                m5, m60 = a.mid(tf + 5 * US), a.mid(tf + 60 * US)
                if m5:
                    later5 = ((m5 - p) if buy else (p - m5)) / p * 1e4
                if m60:
                    later60 = ((m60 - p) if buy else (p - m60)) / p * 1e4
            sw = a.sweep(t0, buy, units)
            cross = None if sw is None else ((sw - m0) if buy else (m0 - sw)) / m0 * 1e4 + ARCUS_TAKER_BP
            got.setdefault(part(t0), []).append((done, later5, later60, cross))
    rows = []
    for name in ("all", *PARTS):
        x = [r for k, v in got.items() if name in ("all", k) for r in v]
        if len(x) < 30:
            continue
        waits = sorted(r[0] if r[0] is not None else 1e9 for r in x)
        rows.append({"when": name, "n": len(x), "median_wait": waits[len(waits) // 2],
                     **{f"in_{w}": sum(v <= w for v in waits) / len(waits) * 100 for w in WAITS},
                     "later5": _mean(x, 1), "later60": _mean(x, 2), "cross": _mean(x, 3)})
    return rows


def pair(a: Book, b: Book, usd: float, *, wait_s: float = 120.0, lag_s: float = 0.5,
         step_s: int = 60) -> list[dict[str, Any]]:
    """Both legs together: the limit order on Arcus, and each of its fills taken on Lighter `lag_s` later. What is not
    filled on Arcus after `wait_s` is crossed there (its fee included). Cost in bp against both mids at the start."""
    lo = max(int(a.bbo["ts"][0]), int(b.bbo["ts"][0]), int(a.depth["ts"][0]), int(b.depth["ts"][0])) + 60 * US
    hi = min(int(a.bbo["ts"][-1]), int(b.bbo["ts"][-1])) - int(wait_s + 100) * US
    got: dict[str, list[tuple[float, bool]]] = {}
    for t0 in range(lo, hi, step_s * US):
        ma, mb = a.mid(t0), b.mid(t0)
        if not ma or not mb:
            continue
        units = usd / ma
        for buy in (True, False):                          # buy on Arcus, sell on Lighter; and the reverse
            fills, left = a.passive(t0, buy, units, wait_s)
            cost, ok = 0.0, True
            todo = [(tf, p, f, 0.0) for tf, p, f in fills]
            if left > 1e-12:
                te = t0 + int(wait_s * US)
                px = a.sweep(te, buy, left)
                if px is None:
                    continue
                todo.append((te, px, left, ARCUS_TAKER_BP))
            for tf, p, f, fee in todo:
                hp = b.sweep(tf + int(lag_s * US), not buy, f)
                if hp is None:
                    ok = False
                    break
                cost += f * ((p - ma) if buy else (ma - p)) + f * ma * fee / 1e4
                cost += f * ((mb - hp) if buy else (hp - mb)) * ma / mb
            if ok:
                got.setdefault(part(t0), []).append((cost / usd * 1e4, left > 1e-12))
    rows = []
    for name in ("all", *PARTS):
        x = [r for k, v in got.items() if name in ("all", k) for r in v]
        if len(x) < 30:
            continue
        c = sorted(r[0] for r in x)
        made = [r[0] for r in x if not r[1]]
        rows.append({"when": name, "n": len(x), "mean": sum(c) / len(c), "median": c[len(c) // 2],
                     "p90": c[int(len(c) * 0.9)], "crossed": sum(r[1] for r in x) / len(x) * 100,
                     "maker_only": sum(made) / len(made) if made else 0.0})
    return rows


def report(arcus_tape: Path, lighter_tape: Path, symbols: list[str], days: int = 5) -> str:
    out = ["WHAT THE FILLS COST ON RECORDED ORDER BOOKS (bp of the order; + = a cost)", ""]
    for sym in symbols:
        ad, ld = days_of(arcus_tape / f"{sym}-USD"), days_of(lighter_tape / sym)
        if not ad:
            out += [f"{sym}: no Arcus tape on this machine ({arcus_tape})", ""]
            continue
        use = ad[-days:]
        a = Book(arcus_tape / f"{sym}-USD", use)
        if not a.ok:
            out += [f"{sym}: the Arcus tape of {use[0]} to {use[-1]} has no order book", ""]
            continue
        np = a.np
        b, d = a.bbo, a.depth
        spread = (b["ask"] - b["bid"]) / b["bid"] * 1e4
        out += [f"{sym} on Arcus, {use[0]} to {use[-1]}: {len(a.tr['ts']):,} trades, "
                f"${float((a.tr['px'] * a.tr['sz']).sum()):,.0f}; spread {float(np.median(spread)):.2f} bp in the "
                f"middle, {float(np.percentile(spread, 90)):.2f} one time in ten; "
                f"${float(np.median(b['bid_sz'] * b['bid'])):,.0f} at the best bid, "
                f"${float(np.median((d['bs'] * d['bp']).sum(1))):,.0f} in the {d['bp'].shape[1]} best bids",
                f"  {'a limit order at the best price':<40} {'cases':>6} | filled within "
                + " ".join(f"{w:>4}s" for w in WAITS) + f" | {'middle wait':>11} | {'worth 5 s later':>15} "
                f"{'60 s later':>10} | {'crossing instead':>16}"]
        for usd in SIZES:
            for r in arcus_leg(a, usd):
                wait = f"{r['median_wait']:.0f} s" if r["median_wait"] < 1e8 else f"over {WAITS[-1]} s"
                name = f"${usd:,.0f} " + ("in all" if r["when"] == "all" else "  " + r["when"])
                out.append(f"  {name:<40} {r['n']:>6} |               "
                           + " ".join(f"{r[f'in_{w}']:>4.0f}%" for w in WAITS)
                           + f" | {wait:>11} | {r['later5']:>+15.2f} {r['later60']:>+10.2f} | {r['cross']:>16.2f}")
        if sym in NEAR_STOPS:
            lo, hi = NEAR_STOPS[sym]
            out += ["", "  Closed with a limit order from a share of the way to the stop "
                        f"({lo * 100:.2f}% from the entry while the stock market is open, {hi * 100:.2f}% while it is "
                        f"closed; ${SIZES[1]:,.0f}, held 3 h)",
                    f"  {'':<22} {'from':>5} {'leg that':>9} {'closes':>7} {'of entries':>10} | "
                    f"{'done before the stop':>20} {'filled':>7} {'middle wait':>11} | {'the stop followed':>17}"]
            for r in near_stop(a, SIZES[1], NEAR_STOPS[sym]):
                out.append(f"  {r['when']:<22} {r['share']:>5.0%} {r['leg']:>9} {r['closes']:>7} "
                           f"{r['of_entries']:>9.0f}% | "
                           f"{r['done']:>19.0f}% {r['filled']:>6.0f}% {r['median_wait']:>9.0f} s | "
                           f"{r['stop_followed']:>16.0f}%")
        both = [x for x in ad if x in ld]
        books = [(x, Book(arcus_tape / f"{sym}-USD", [x]), Book(lighter_tape / sym, [x])) for x in both]
        books = [(x, p, q) for x, p, q in books if p.ok and q.ok]
        if not books:
            out += ["  (no day with both venues' order books: the Lighter leg's cost is not measured here)", ""]
            continue
        for day, p, q in books:
            ls = (q.bbo["ask"] - q.bbo["bid"]) / q.bbo["bid"] * 1e4
            out += ["", f"  Both legs on {day}, the one kind of day both books were recorded: Lighter's spread "
                        f"{float(np.median(ls)):.2f} bp, ${float(np.median(q.bbo['bid_sz'] * q.bbo['bid'])):,.0f} at "
                        "its best bid", f"  {'Arcus limit, Lighter taken at once':<40} {'cases':>6} | "
                        f"{'one pair costs: mean':>20} {'middle':>7} {'1 in 10':>8} | "
                        f"{'Arcus crossed after 120 s':>25} | {'when it was not':>15}"]
            for usd in SIZES:
                for r in pair(p, q, usd):
                    name = f"${usd:,.0f} " + ("in all" if r["when"] == "all" else "  " + r["when"])
                    out.append(f"  {name:<40} {r['n']:>6} | {r['mean']:>20.2f} {r['median']:>7.2f} {r['p90']:>8.2f} | "
                               f"{r['crossed']:>24.0f}% | {r['maker_only']:>15.2f}")
        out.append("")
    out += ["done before the stop = the whole order filled as maker before Arcus's mid reached the stop (or 600 s);",
            "the stop followed = the price went on to the stop within the 3 hours;",
            "worth N s later = Arcus's mid then against the fill's price (- = the market went on against the order);",
            "crossing instead = half the spread, the depth eaten and Arcus's 2.25 bp taker fee;",
            "a pair = one leg on each venue; getting in and out is two pairs."]
    return "\n".join(out)
