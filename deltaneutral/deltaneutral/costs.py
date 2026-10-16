"""What a fill costs on each venue: half the spread plus the levels behind it for an order of that size, in bp of the
mid, before fees (fees are each market's own, in plan.Rules).

Where the numbers come from: `deltaneutral costs` reads both venues' live books a few times and keeps the median under
the session it is measured in: "rth" (09:30-16:00 New York), "ext" (the rest of Arcus's 04:00-20:00) or "off" (the
stock market closed); data/costs.json holds them and replaces the figures below.
The figures below are the Arcus bot's recorded order book (arcus/data/scout/tape, ten days, 21 Sep - 3 Oct 2026, one
snapshot a minute) and Lighter's live book on 16 Oct. Arcus traded about ten times as much from early October and its
books narrowed (ORCL: about 6 bp wide on the tape, 1.5 bp on 16 Oct), so these are the dear case.
"""

from __future__ import annotations

import json
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import DATA
from .series import cash_session

SESSIONS = ("rth", "ext", "off")

# bp to take $500 (and $1,000) at once, half the round trip: (rth, ext, off)
ARCUS_500 = {"AMD": (3.43, 3.42, 3.03), "INTC": (3.22, 2.97, 3.43), "SKHY": (3.01, 3.62, 3.61),
             "COIN": (9.10, 13.13, 10.33), "PLTR": (3.50, 3.43, 4.22), "USO": (4.05, 3.28, 4.33),
             "ORCL": (3.43, 3.17, 3.66), "CRWV": (3.68, 3.44, 3.70)}
ARCUS_1000 = {"AMD": (3.69, 3.73, 3.72), "INTC": (3.68, 3.47, 4.10), "SKHY": (3.15, 3.89, 4.00),
              "COIN": (11.74, 17.59, 16.07), "PLTR": (3.89, 3.74, 4.61), "USO": (4.05, 3.32, 4.35),
              "ORCL": (3.80, 3.65, 4.26), "CRWV": (4.12, 3.86, 4.43)}
LIGHTER_500 = {"AMD": 1.88, "INTC": 1.58, "SKHY": 1.67, "COIN": 1.38, "PLTR": 0.97, "USO": 2.87, "ORCL": 0.75,
               "CRWV": 1.46}
LIGHTER_1000 = {"AMD": 1.88, "INTC": 1.58, "SKHY": 1.71, "COIN": 1.40, "PLTR": 0.97, "USO": 2.87, "ORCL": 0.85,
                "CRWV": 1.46}
UNKNOWN_BP = 5.0


@dataclass
class Costs:
    """{venue: {symbol: {session: (bp at $500, bp at $1,000)}}}; sizes in between are interpolated, above $1,000
    the $1,000 figure grows in proportion (a cautious guess: deeper levels cost more)."""
    table: dict[str, dict[str, dict[str, tuple[float, float]]]] = field(default_factory=dict)
    scale: float = 1.0               # every cost times this (a sensitivity: 1.5 = half again as dear)
    top_usd: dict[str, dict[str, dict[str, float]]] = field(default_factory=dict)   # dollars at the best price
    fill: dict[str, dict[str, dict[str, float]]] = field(default_factory=dict)   # venue -> symbol -> session ->
                                     # the share of resting orders that fill before the bot gives up on them

    def maker_fill(self, symbol: str, session: str, venue: str = "arcus") -> float:
        """The share of resting orders on `venue` that fill in time (1 when not measured)."""
        by = self.fill.get(venue, {}).get(symbol, {})
        return by.get(session, next(iter(by.values()), 1.0) if by else 1.0)

    def take_bp(self, venue: str, symbol: str, session: str, usd: float) -> float:
        row = self.table.get(venue, {}).get(symbol, {}).get(session)
        if row is None:
            return UNKNOWN_BP * self.scale
        a, b = row
        if usd <= 500:
            bp = a
        elif usd <= 1000:
            bp = a + (b - a) * (usd - 500) / 500
        else:
            bp = b * (1 + 0.25 * (usd / 1000 - 1))
        return bp * self.scale


def defaults() -> Costs:
    t: dict[str, dict[str, dict[str, tuple[float, float]]]] = {"arcus": {}, "lighter": {}}
    for s, row in ARCUS_500.items():
        t["arcus"][s] = {sess: (row[k], ARCUS_1000[s][k]) for k, sess in enumerate(SESSIONS)}
    for s, x in LIGHTER_500.items():
        t["lighter"][s] = {sess: (x, LIGHTER_1000[s]) for sess in SESSIONS}
    return Costs(t)


def load(root: Path | None = None, scale: float = 1.0) -> Costs:
    p = (root or DATA) / "costs.json"
    c = defaults()
    try:
        saved = json.loads(p.read_text())
        for v, syms in saved.get("table", {}).items():
            for s, sess in syms.items():
                c.table.setdefault(v, {}).setdefault(s, {}).update({k: (float(x[0]), float(x[1]))
                                                                    for k, x in sess.items()})
        c.top_usd = saved.get("top_usd", {})
        mf = saved.get("maker_fill", {})
        c.fill = mf if set(mf) <= {"arcus", "lighter"} else {"arcus": mf}       # older files: Arcus's alone
        # a session not measured live yet takes a measured one's figure (the recorded tape is from before Arcus's
        # books narrowed in October, and would overstate it): the stock market's own hours first, then the others
        for v, syms in saved.get("table", {}).items():
            for s, sess in syms.items():
                have = [k for k in ("rth", "ext", "off") if k in sess]
                for k in SESSIONS:
                    if k not in sess and have:
                        c.table[v][s][k] = tuple(sess[have[0]])          # type: ignore[assignment]
    except (OSError, ValueError):
        pass
    c.scale = scale
    return c


def tape() -> Costs:
    """The recorded tape's figures alone (21 Sep - 3 Oct 2026, before Arcus's books narrowed): the dear case."""
    return defaults()


# ------------------------------------------------------------------------------------------------ measuring
def take(levels: list[tuple[float, float]], usd: float) -> float:
    """The average price of taking `usd` dollars from `levels` (price, size), best first; NaN when they run out."""
    left, cost, q = usd, 0.0, 0.0
    for p, s in levels:
        if p <= 0 or s <= 0:
            continue
        v = min(left, p * s)
        cost += v
        q += v / p
        left -= v
        if left <= 1e-9:
            return cost / q
    return float("nan")


def half_round_trip(bids: list[tuple[float, float]], asks: list[tuple[float, float]], usd: float) -> float:
    """bp from the mid, the average of buying and selling `usd` at once."""
    mid = (bids[0][0] + asks[0][0]) / 2
    return ((take(asks, usd) - mid) + (mid - take(bids, usd))) / 2 / mid * 1e4


def arcus_tape(symbol: str, tape: Path) -> dict[str, tuple[float, float]]:
    # (how ARCUS_500 / ARCUS_1000 above were made)
    """{session: (median bp at $500, at $1,000)} from the recorded depth, one snapshot a minute."""
    got: dict[str, list[tuple[float, float]]] = {s: [] for s in SESSIONS}
    for f in sorted((tape / f"{symbol}-USD").glob("*/depth-*.npz")):
        if f.name.endswith(".tmp.npz"):
            continue
        with np.load(f) as z:
            ts, bp, bs, ap, asz = z["ts"], z["bp"], z["bs"], z["ap"], z["as_"]
        last = -1
        for i in range(len(ts)):
            m = int(ts[i]) // 60_000_000
            if m == last or bp[i, 0] <= 0 or ap[i, 0] <= bp[i, 0]:
                continue
            last = m
            bids, asks = list(zip(bp[i], bs[i], strict=True)), list(zip(ap[i], asz[i], strict=True))
            got[cash_session(ts[i] / 1e6)].append((half_round_trip(bids, asks, 500),
                                                   half_round_trip(bids, asks, 1000)))
    return {s: (float(np.nanmedian([a for a, _ in v])), float(np.nanmedian([b for _, b in v])))
            for s, v in got.items() if v}


def maker_fills(symbols: list[str], venue: str = "arcus", usd: float = 500.0, wait_s: float = 600.0,
                days: float = 7.0, root: Path | None = None) -> dict[str, dict[str, float]]:
    """Estimate, per market and session, the share of resting orders of `usd` at the best price that fill within
    `wait_s` on `venue`: from its trades over the last `days` days (Arcus: data/arcus_<SYM>_perp1m, by the minute;
    Lighter: data/lighter_<SYM>_5m, five minutes) and the dollars measured at its best price (`top_usd`). An order
    fills when half of what trades in the next `wait_s` (the takers on its side) reaches what waits ahead of it plus
    its own size. Rough: it does not know which side each trade took, nor the queue at each moment. Saved into
    data/costs.json under "maker_fill"."""
    from .series import read

    p = (root or DATA) / "costs.json"
    try:
        saved = json.loads(p.read_text())
    except (OSError, ValueError):
        saved = {}
    top = saved.get("top_usd", {}).get(venue, {})
    name, bar, col = (("arcus_{}_perp1m", 60, 5) if venue == "arcus" else ("lighter_{}_5m", 300, 5))
    out: dict[str, dict[str, float]] = {}
    w = max(1, int(wait_s // bar))
    for s in symbols:
        rows = read(name.format(s), root)
        if len(rows) == 0:
            continue
        t0 = int(rows[-1, 0]) - int(days * 86_400)
        rows = rows[rows[:, 0] >= t0]
        slots = np.arange(t0 // bar * bar, int(rows[-1, 0]) + bar, bar)
        dollars = np.zeros(len(slots))
        dollars[np.searchsorted(slots, rows[:, 0].astype(np.int64))] = rows[:, col]
        ahead = np.cumsum(np.concatenate([[0.0], dollars]))
        nxt = ahead[w:] - ahead[:-w]                          # dollars traded in the next wait_s
        got: dict[str, list[bool]] = {}
        for i in range(0, len(nxt), max(1, 300 // bar)):
            sess = cash_session(float(slots[i]))
            queue = float(top.get(s, {}).get(sess) or next(iter(top.get(s, {}).values()), usd))
            got.setdefault(sess, []).append(nxt[i] / 2 >= queue + usd)
        out[s] = {k: float(np.mean(v)) for k, v in got.items() if v}
    mf = saved.get("maker_fill", {})
    if not set(mf) <= {"arcus", "lighter"}:
        mf = {"arcus": mf}
    mf[venue] = {**mf.get(venue, {}), **out}
    saved["maker_fill"] = mf
    p.write_text(json.dumps(saved, indent=1))
    return out


def _get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def lighter_book(market_id: int) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    b = _get(f"https://api.rh.lighter.xyz/api/v1/orderBookOrders?market_id={market_id}&limit=50")

    def levels(rows: list[dict], rev: bool) -> list[tuple[float, float]]:
        agg: dict[float, float] = {}
        for o in rows:
            p = float(o["price"])
            agg[p] = agg.get(p, 0.0) + float(o["remaining_base_amount"])
        return sorted(agg.items(), reverse=rev)

    return levels(b.get("bids") or [], True), levels(b.get("asks") or [], False)


def arcus_book(symbol: str) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    b = _get(f"https://api.arcus.xyz/v1/l2OrderBook/{symbol}-USD?nLevels=20")
    return ([(float(p), float(q)) for p, q in b.get("bids") or []],
            [(float(p), float(q)) for p, q in b.get("asks") or []])


def measure(symbols: list[str], lighter_ids: dict[str, int], snapshots: int = 5, gap_s: float = 30.0,
            root: Path | None = None, say=print) -> Costs:
    """Read both venues' live books `snapshots` times, `gap_s` apart, and keep the median cost of taking $500 and
    $1,000 under the session it is now ("rth", "ext" or "off"; data/costs.json keeps the other sessions' figures).
    Measure in each session once: until then a session uses the figures above."""
    sess = cash_session(time.time())
    reads: dict[tuple[str, str], list[tuple[float, float]]] = {}
    tops: dict[tuple[str, str], list[float]] = {}
    for k in range(snapshots):
        for s in symbols:
            for v, book in (("arcus", lambda s=s: arcus_book(s)),
                            ("lighter", lambda s=s: lighter_book(lighter_ids[s]) if s in lighter_ids else ([], []))):
                try:
                    bids, asks = book()
                except (OSError, ValueError, KeyError) as e:
                    say(f"{v} {s}: {e}")
                    continue
                if bids and asks:
                    reads.setdefault((v, s), []).append((half_round_trip(bids, asks, 500),
                                                         half_round_trip(bids, asks, 1000)))
                    tops.setdefault((v, s), []).append((bids[0][0] * bids[0][1] + asks[0][0] * asks[0][1]) / 2)
                time.sleep(1.1 if v == "lighter" else 0.3)
        if k < snapshots - 1:
            time.sleep(gap_s)
    p = (root or DATA) / "costs.json"
    try:
        saved = json.loads(p.read_text())
    except (OSError, ValueError):
        saved = {}
    table = saved.get("table", {})
    for (v, s), r in reads.items():
        row = [float(np.nanmedian([a for a, _ in r])), float(np.nanmedian([b for _, b in r]))]
        table.setdefault(v, {}).setdefault(s, {})[sess] = row
    top = saved.get("top_usd", {})
    for (v, s), r in tops.items():
        top.setdefault(v, {}).setdefault(s, {})[sess] = float(np.median(r))
    saved.update({"table": table, "top_usd": top, f"measured_{sess}": int(time.time())})
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(saved, indent=1))
    return load(root)
