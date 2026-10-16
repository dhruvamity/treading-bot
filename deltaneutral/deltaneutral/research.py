"""`deltaneutral research`: which of the owner's low open-interest markets, and which pairs of them, suit the bot,
and what the last 30 days say it would have done with $100 on each venue. Writes reports/REPORT.md.

What a market is judged on (all from the venues' own data, data.py):
- open interest and the highest leverage on each venue now (Arcus: while the stock market is open and closed);
- what each venue's funding paid over the 30 days, and the difference (what the position can earn);
- what a fill costs on each venue (costs.py), which a daily cycle pays eight times;
- how much it moves in a day.
What a pair is judged on: how far apart its two markets moved in 24 hours (each venue's result is that difference),
how close that came to liquidating a venue at the highest leverage, and the 30-day replay (backtest.py).
A longer look at liquidations uses the arbitrage's hourly history (arbitrage/data/history, from late June 2026).
"""

from __future__ import annotations

import datetime as dt
import itertools
import math
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np

from . import ROOT, TOKENS
from . import backtest as BT
from . import costs as C
from . import plan as P
from . import series as S
from .plan import ARCUS, LIGHTER, VENUES, Pair

DAY = 86_400
YEAR_H = 24 * 365
DAYS = 30
ARB_HISTORY = ROOT.parent / "arbitrage" / "data" / "history"


def data_end(symbols: list[str] | tuple[str, ...] = TOKENS) -> int:
    """The last five-minute bar every market's files hold on both venues (now, when nothing was downloaded)."""
    ends = []
    for s in symbols:
        for name in (f"lighter_{s}_mark5m", f"arcus_{s}_oracle5m"):
            rows = S.read(name)
            if len(rows):
                ends.append(int(rows[-1, 0]))
    return min(ends) if ends else int(time.time()) // S.FIVE * S.FIVE


def window(days: int = DAYS, end: int | None = None, symbols: list[str] | tuple[str, ...] = TOKENS) -> tuple[int,
                                                                                                             int]:
    """The last `days` whole days to the last bar the files hold."""
    e = end or data_end(symbols)
    return e - days * DAY, e


def load_all(symbols: list[str], start: int, end: int) -> dict[str, S.Market]:
    t = S.clock(start - 8 * DAY, end)          # a week before the window: the trailing beta and funding look back
    m = S.load_markets()
    return {s: S.market(s, t, markets=m) for s in symbols}


# ------------------------------------------------------------------------------------------------ the markets
def daily_move(px: np.ndarray, t: np.ndarray, start: int) -> float:
    """One day's typical move (a fraction): the standard deviation of 24-hour changes, one every hour."""
    i0 = int(np.searchsorted(t, start))
    p = px[i0:]
    step = 3600 // S.FIVE
    a, b = p[:-DAY // S.FIVE:step], p[DAY // S.FIVE::step]
    n = min(len(a), len(b))
    r = np.log(b[:n] / a[:n])
    r = r[np.isfinite(r)]
    return float(np.std(r)) if len(r) > 5 else math.nan


def funding_year(mk: S.Market, v: str, start: int) -> float:
    """What the venue's funding paid a long over the window, as % a year (+ = longs paid)."""
    i0 = int(np.searchsorted(mk.t, start))
    f = mk.funding[v][i0:]
    hours = int(np.count_nonzero(mk.t[i0:] % 3600 == 0))
    return float(f.sum()) / hours * YEAR_H * 100 if hours else math.nan


def basis_bp(mk: S.Market, start: int) -> tuple[float, float]:
    """Arcus's price over Lighter's (bp): the average, and the typical change over 24 hours."""
    i0 = int(np.searchsorted(mk.t, start))
    g = (mk.px[ARCUS][i0:] / mk.px[LIGHTER][i0:] - 1) * 1e4
    d = g[DAY // S.FIVE:] - g[:-DAY // S.FIVE]
    return float(np.nanmean(g)), float(np.nanstd(d))


def token_rows(mks: dict[str, S.Market], costs: C.Costs, start: int) -> list[dict]:
    m = S.load_markets()
    out = []
    for s, mk in mks.items():
        a, lt = m["arcus"][s], m["lighter"][s]
        mark = float(a.get("markPrice") or 0)
        bmean, bmove = basis_bp(mk, start)
        out.append({
            "sym": s,
            "l_oi": float(lt["open_interest"]) * float(lt["mark_price"]),
            "a_oi": float(a.get("openInterest") or 0) * mark,
            "l_lev": mk.rules[LIGHTER].max_leverage(False),
            "a_lev": mk.rules[ARCUS].max_leverage(False),
            "a_lev_off": mk.rules[ARCUS].max_leverage(True),
            "l_vol": float(lt.get("daily_quote_token_volume") or 0),
            "a_vol": float(a.get("volume24hNotional") or 0),
            "l_fund": funding_year(mk, LIGHTER, start),
            "a_fund": funding_year(mk, ARCUS, start),
            "a_cost": costs.take_bp(ARCUS, s, "off", 500) + mk.rules[ARCUS].taker_bp,
            "l_cost": costs.take_bp(LIGHTER, s, "off", 500) + mk.rules[LIGHTER].taker_bp,
            "a_fill": costs.maker_fill(s, "rth", ARCUS),
            "l_fill": costs.maker_fill(s, "rth", LIGHTER),
            "move": daily_move(mk.px[LIGHTER], mk.t, start) * 100,
            "basis": bmean,
            "basis_move": bmove,
        })
    return out


# ------------------------------------------------------------------------------------------------ the pairs
def pair_spread(mx: S.Market, my: S.Market, v: str, start: int) -> tuple[float, float, float]:
    """On venue v's prices: the correlation of hourly returns, the typical 24-hour move of X against Y (equal
    dollars, a fraction), and the largest 24-hour move of X against Y (from any five-minute bar)."""
    i0 = int(np.searchsorted(mx.t, start))
    x, y = np.log(mx.px[v][i0:]), np.log(my.px[v][i0:])
    h = 3600 // S.FIVE
    rx, ry = np.diff(x[::h]), np.diff(y[::h])
    ok = np.isfinite(rx) & np.isfinite(ry)
    corr = float(np.corrcoef(rx[ok], ry[ok])[0, 1]) if ok.sum() > 10 else math.nan
    w = DAY // S.FIVE
    sp = x - y
    worst = 0.0
    for k in range(0, len(sp) - 1, h):           # every hour as an open, the worst point of the next 24 h
        seg = sp[k:k + w + 1] - sp[k]
        seg = seg[np.isfinite(seg)]
        if len(seg):
            worst = max(worst, float(np.max(np.abs(seg))))
    d = sp[w::h] - sp[:-w:h]
    d = d[np.isfinite(d)]
    return corr, float(np.std(d)) if len(d) else math.nan, worst


def liq_distance(mx: S.Market, my: S.Market, margin_use: float = 0.9) -> float:
    """How far X and Y may move apart before the weaker venue is liquidated, at both venues' highest leverage
    (in-session) with `margin_use` of the money as margin: a fraction of X's dollars."""
    eq = {ARCUS: 100.0, LIGHTER: 100.0}
    sz = P.size(eq, mx.rules, my.rules, False, margin_use)
    return min(P.spread_to_liquidation(eq[v], sz.nx, sz.ny, mx.rules[v], my.rules[v]) for v in VENUES)


def distances(mx: S.Market, my: S.Market, share: float = 0.75, margin_use: float = 0.9) -> dict[str, tuple[float,
                                                                                                        float]]:
    """{venue: (how far apart X and Y may move before the bot's guard closes, at `share` of the way, and before the
    venue liquidates)}, a fraction of X's dollars, for $100 a venue at the highest in-session leverage."""
    eq = {ARCUS: 100.0, LIGHTER: 100.0}
    sz = P.size(eq, mx.rules, my.rules, False, margin_use)
    out = {}
    for v in VENUES:
        mm = P.maintenance(sz.nx, sz.ny, mx.rules[v], my.rules[v])
        out[v] = (share * (eq[v] - mm) / sz.nx, (eq[v] - mm) / sz.nx)
    return out


LONG_FROM = int(dt.datetime(2026, 7, 15, tzinfo=dt.UTC).timestamp())   # Arcus's first weeks have bad prints


def long_history(x: str, y: str, dist: dict[str, tuple[float, float]], root: Path | None = None,
                 since: int = LONG_FROM) -> dict[str, Any]:
    """On the hourly history since late June (data.fetch_hours: Lighter's mark, Arcus's candles): of all 24-hour holds
    starting on the hour, the share in which X and Y moved further apart than the guard's distance or the
    liquidation's on either venue, and the largest move seen. A hold counts once whichever venue it was."""
    def series(name: str) -> dict[int, float]:
        rows = S.read(name, root)
        return {int(r[0]): float(r[4]) for r in rows if r[4] > 0}

    sp = {}
    for v, kind in ((LIGHTER, "mark1h"), (ARCUS, "oracle1h")):
        sx = series(f"{v}_{x}_{'mark1h' if v == LIGHTER else 'oracle1h'}")
        sy = series(f"{v}_{y}_{'mark1h' if v == LIGHTER else 'oracle1h'}")
        sp[v] = {h: math.log(sx[h]) - math.log(sy[h]) for h in sx if h in sy}
        _ = kind
    hours = sorted(h for h in set(sp[LIGHTER]) & set(sp[ARCUS]) if h >= since)
    if len(hours) < 48:
        return {"holds": 0, "guard": math.nan, "liquidated": math.nan, "worst": math.nan, "days": 0}
    hh = np.array(hours)
    arr = {v: np.array([sp[v][h] for h in hours]) for v in VENUES}
    n = guard_n = liq_n = 0
    worst = 0.0
    days: dict[str, int] = {}
    for k in range(len(hours)):
        e = int(np.searchsorted(hh, hh[k] + DAY, side="right"))
        if hh[-1] < hh[k] + DAY:
            break
        n += 1
        g = q = False
        for v in VENUES:
            m = float(np.max(np.abs(np.expm1(arr[v][k:e] - arr[v][k]))))
            worst = max(worst, m)
            g = g or m > dist[v][0]
            q = q or m > dist[v][1]
        guard_n += g
        liq_n += q
        if g:
            d = dt.datetime.fromtimestamp(int(hh[k]), S.NEW_YORK).strftime("%d %b")
            days[d] = days.get(d, 0) + 1
    return {"holds": n, "guard": guard_n / n, "liquidated": liq_n / n, "worst": worst,
            "days": (hh[-1] - hh[0]) / DAY, "bad_days": days}


# ------------------------------------------------------------------------------------------------ the report
def money(x: float) -> str:
    return f"-${-x:,.2f}" if x < 0 else f"${x:,.2f}"


def fmt_k(x: float) -> str:
    return f"${x / 1e6:.2f}m" if x >= 1e6 else f"${x / 1e3:.0f}k"


def run_pair(pair: Pair, mks: dict[str, S.Market], costs: C.Costs, st: BT.Settings, start: int, end: int,
             equity: dict[str, float] | None = None) -> BT.Result:
    return BT.run(pair, mks[pair.x], mks[pair.y], costs, st, start, end, equity)


def pairs_of(symbols: list[str]) -> list[Pair]:
    return [Pair(a, b) for a, b in itertools.combinations(symbols, 2)]


def transfers_line(r: BT.Result) -> tuple[int, float, float]:
    """(how many, the typical one, the largest), dollars."""
    amts = [t[1] for t in r.transfers]
    return len(amts), float(np.median(amts)) if amts else 0.0, max(amts, default=0.0)


RECOMMENDED = Pair("USO", "ORCL")          # Arcus's highest leverage and its lowest OI at 10x; both legs' funding pays
EXAMPLE = Pair("ORCL", "CRWV")             # the owner's example
SETTINGS_SHOWN: list[tuple[str, dict[str, Any]]] = [
    ("the bot's settings (10:00 New York, way from funding, limit orders on both venues, stops at 75%)", {}),
    ("only Arcus rests limit orders, Lighter takes", {"execution": "arcus"}),
    ("both venues take at once (no limit orders)", {"execution": "take"}),
    ("no stops resting on the venues (the bot's guard alone)", {"venue_stops": False}),
    ("open 09:30 New York (the cash open)", {"open_ny": 9.5}),
    ("open 15:00 New York", {"open_ny": 15.0}),
    ("open 20:00 New York (stock market closed: Arcus's off-hours margin)", {"open_ny": 20.0}),
    ("cut to Arcus's off-hours size at each close, grow back at the reopen", {"offhours": "cut"}),
    ("weekdays only", {"weekends": False}),
    ("at most 5x", {"max_leverage": 5.0}),
    ("no stops and no guard (the venues liquidate)", {"stop_share": 0.0}),
    ("Y's dollars = beta x X's", {"ratio": "beta"}),
    ("money never moved between the venues", {"transfers": "never"}),
    ("hold 72 h instead of 24 (outside the owner's rule)", {"hold_h": 72.0}),
]


def early(r: BT.Result) -> int:
    """Cycles ended before their time: a stop, the guard or a liquidation."""
    return r.stops + r.guards + r.liquidations


def runs_take_gap(runs: dict[str, BT.Result], mks: dict[str, S.Market], costs: C.Costs, base: BT.Settings,
                  start: int, end: int, pair: Pair) -> float:
    """What resting limit orders on both venues saved on `pair` against taking every fill."""
    taken = run_pair(pair, mks, costs, replace(base, execution="take"), start, end)
    return runs[pair.name].total - taken.total


def report(symbols: list[str] | None = None, days: int = DAYS, out: Path | None = None) -> str:
    syms = list(symbols or TOKENS)
    start, end = window(days, symbols=syms)
    mks = load_all(syms, start, end)
    costs = C.load()
    base = BT.Settings()
    lines: list[str] = []
    w = lines.append
    d0 = dt.datetime.fromtimestamp(start, dt.UTC)
    d1 = dt.datetime.fromtimestamp(end, dt.UTC)
    runs = {p.name: run_pair(p, mks, costs, base, start, end) for p in pairs_of(syms)}

    def row(r: BT.Result) -> str:
        n, typ, big = transfers_line(r)
        return (f"{money(r.total)} | {money(r.pnl)} | {money(r.parts()['funding'])} | {money(r.parts()['costs'])} | "
                f"{fmt_k(r.volume)} | {money(r.avg_oi)} | {r.stops} / {r.guards} / {r.liquidations} | {n}: typically "
                f"{money(typ)}, at most {money(big)}")

    w("# The delta-neutral bot: which markets, and the last 30 days\n")
    w(f"{d0:%d %b %Y %H:%M} to {d1:%d %b %Y %H:%M} UTC ({days} days), $100 on Arcus and $100 on Lighter at the start, "
      "the bot's own rules (one cycle every 24 hours from 10:00 New York, both venues' highest leverage with 90% of "
      "the money as margin, limit orders on both venues that chase the book, stops at 75% of the way to liquidation, "
      "money made even after every close). Written by `deltaneutral research`; prices: Arcus's "
      "candles and Lighter's mark, five-minute bars; funding: each venue's own hourly payments; fills: both venues' "
      "live books measured on 16 Oct and Arcus's own trades (section 6).\n")

    # ---- the answer
    w("## The answer\n")
    best = sorted(runs.values(), key=lambda r: -r.total)
    shown = [runs[RECOMMENDED.name], runs[EXAMPLE.name], best[0], best[1]]
    seen: set[str] = set()
    w("| Pair | $200 became | Made | Funding | Fills | Traded | Open interest held (both venues) | Stops / guard "
      "closes / liquidations | Transfers |")
    w("|---|---|---|---|---|---|---|---|---|")
    for r in shown:
        if r.pair.name in seen:
            continue
        seen.add(r.pair.name)
        tag = " (recommended)" if r.pair == RECOMMENDED else " (your example)" if r.pair == EXAMPLE else ""
        w(f"| {r.pair.name}{tag} | {row(r)} |")
    w("")
    lost = [r.pnl for r in runs.values()]
    cover = {k: r.parts()["funding"] / -r.parts()["costs"] for k, r in runs.items() if r.parts()["costs"] < 0}
    uso = [v for k, v in cover.items() if "USO" in k]
    other = [v for k, v in cover.items() if "USO" not in k]
    w(f"- **No pair made money** with a close and a reopen every 24 hours: all {len(runs)} pairs lost between "
      f"{money(-max(lost))} and {money(-min(lost))} of the $200 in {days} days. Each cycle pays eight fills (two "
      "markets, two venues, in and out) and earns the funding difference: that covered at most "
      f"{max(other) * 100:.0f}% of the fills on the pairs without USO, and {min(uso) * 100:.0f}-{max(uso) * 100:.0f}% "
      "on the pairs with it.")
    w("- **The one real edge is USO's funding.** Arcus paid USO's longs (about 14% a year: its shorts paid) while "
      "Lighter charged them about 4%: long USO on Arcus and short on Lighter earned about 18% a year on the "
      "position, every one of the four weeks. ORCL, AMD, INTC, COIN and CRWV paid the other way round (about 5-7% a "
      "year, short on Arcus). So USO with one of those earns on both legs; it still pays less than the fills.")
    w(f"- **Recommended pair: {RECOMMENDED.name}** (the bot's default). USO is Arcus's highest leverage (20x, the "
      "only market of the eight above 10x) and ORCL Arcus's lowest open interest at 10x, as you asked; both legs' "
      "funding pays. The bot holds Arcus long USO / short ORCL and Lighter short USO / long ORCL (it turns round on "
      "its own when the funding does). Its limit orders fill least often, though: on Lighter about $5,000 waits at "
      f"USO's best price and little trades. {best[0].pair.name} lost least ({money(best[0].total)}): both venues' "
      "limit orders fill most often on AMD (`deltaneutral pair " + " ".join(best[0].pair.symbols()) + "`).")
    w("- **Limit orders on both venues** (chasing the best price, neither venue more than $150 ahead of the other) "
      f"save {money(runs_take_gap(runs, mks, costs, base, start, end, RECOMMENDED))} on {RECOMMENDED.name} and "
      f"{money(runs_take_gap(runs, mks, costs, base, start, end, best[0].pair))} on {best[0].pair.name} in 30 days "
      "against taking every fill (section 4).")
    w("- **Every cycle needs a transfer.** At the highest leverage one venue makes what the other loses, so the money "
      "ends uneven every day; the bot says exactly how much to move and which way after each close.")
    w("")

    # ---- markets
    rows = token_rows(mks, costs, start)
    w("## 1. The eight markets\n")
    w("| Market | Lighter OI | Arcus OI | Lighter leverage | Arcus leverage (market open / closed) | Lighter "
      "funding, a year | Arcus funding, a year | Short on the dearer venue earns, a year | Arcus fill (bp) | "
      "Lighter fill (bp) | Limit order filled in 10 min, Arcus / Lighter | Daily move |")
    w("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in sorted(rows, key=lambda r: r["a_oi"]):
        edge = abs(r["a_fund"] - r["l_fund"])
        dear = "Arcus" if r["a_fund"] > r["l_fund"] else "Lighter"
        w(f"| {r['sym']} | {fmt_k(r['l_oi'])} | {fmt_k(r['a_oi'])} | {r['l_lev']:.0f}x | {r['a_lev']:.0f}x / "
          f"{r['a_lev_off']:.1f}x | {r['l_fund']:+.1f}% | {r['a_fund']:+.1f}% | {edge:.1f}% (short on {dear}) | "
          f"{r['a_cost']:.1f} | {r['l_cost']:.1f} | {r['a_fill'] * 100:.0f}% / {r['l_fill'] * 100:.0f}% | "
          f"{r['move']:.1f}% |")
    w("\nOpen interest and leverage: both venues' market lists, 16 Oct 2026. Funding: + means longs paid, the 30 "
      "days' average. A fill: half the spread and the levels behind it for $500, plus the fee (Arcus 2.25 bp to "
      "take, nothing to rest; Lighter nothing), measured on the live books while the stock market was closed. "
      "Limit order: the share of $500 limit orders at Arcus's best price that would have filled within 10 minutes "
      "while the stock market is open (section 6). Daily move: the typical 24-hour change.\n")
    w("On Arcus, from your Lighter list, by open interest and leverage: COIN has the least open interest but only "
      "5x; then ORCL, CRWV and PLTR at 10x; USO has the most but the highest leverage (20x). On Lighter every one is "
      "10x, and that is what limits the size: Lighter's legs must be as big as Arcus's, so Arcus's 20x on USO only "
      "leaves margin unused there.\n")

    # ---- every pair
    w("## 2. Every pair, the last 30 days\n")
    w("Each venue holds one market long and the other short, so its money moves with the difference between the "
      "two (times about 9x). `Move apart`: the typical and the largest 24-hour move of one against the other. "
      "`Room`: how far apart they may move before the weaker venue is liquidated at the highest leverage. The "
      "bot's guard closes everything earlier, at about two-thirds of that.\n")
    w("| Pair | Correlation | Typical move apart | Largest move apart | Room | $200 became | Funding | Fills | "
      "Traded | OI held | Stops / guard closes / liquidations | Typical transfer |")
    w("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in best:
        p = r.pair
        corr, sd, worst = pair_spread(mks[p.x], mks[p.y], LIGHTER, start)
        dist = liq_distance(mks[p.x], mks[p.y])
        n, typ, _ = transfers_line(r)
        w(f"| {p.name} | {corr:.2f} | {sd * 100:.1f}% | {worst * 100:.1f}% | {dist * 100:.1f}% | {money(r.total)} | "
          f"{money(r.parts()['funding'])} | {money(r.parts()['costs'])} | {fmt_k(r.volume)} | {money(r.avg_oi)} | "
          f"{r.stops} / {r.guards} / {r.liquidations} | {money(typ)} |")
    w("")

    # ---- longer history
    w("## 3. Liquidation risk over a longer stretch (15 Jul - 16 Oct)\n")
    w("The 30 days had few earnings reports. On the venues' hourly prices since mid-July (data.fetch_hours; "
      "both venues' first weeks are left out, Arcus's prices were broken then), every 24-hour hold starting on the "
      "hour, at the highest leverage:\n")
    w("| Pair | Holds | Ended early (75% of the way) | Would have liquidated a venue without it | Largest 24 h "
      "move apart | Worst days |")
    w("|---|---|---|---|---|---|")
    for r in best[:6] + [runs[RECOMMENDED.name], runs[EXAMPLE.name], runs.get("AMD/INTC", best[-1])]:
        p = r.pair
        if f"long:{p.name}" in seen:
            continue
        seen.add(f"long:{p.name}")
        lh = long_history(p.x, p.y, distances(mks[p.x], mks[p.y]))
        if not lh["holds"]:
            continue
        top = ", ".join(k for k, _ in sorted(lh["bad_days"].items(), key=lambda kv: -kv[1])[:4])
        w(f"| {p.name} | {lh['holds']:,} | {lh['guard'] * 100:.0f}% | {lh['liquidated'] * 100:.0f}% | "
          f"{lh['worst'] * 100:.0f}% | {top} |")
    w("\nThe worst days are mostly earnings reports: INTC's on 23 Jul, PLTR's on 3 Aug and AMD's on 4 Aug each moved "
      "8-12% in the hour after the close, and the other market did not. Most of these companies usually report "
      "again between late October and early November (look up the dates: they were not checked here). "
      "`deltaneutral skip DATE` (Telegram `/dn_skip DATE`) keeps the bot flat from 16:00 New York the day before a "
      "report to 16:00 the day after. The recommended pair is the least exposed: USO is an oil fund with no "
      "earnings (it moves on OPEC and inventory days), and ORCL usually reports in mid-December.\n")

    # ---- settings
    w(f"## 4. What the settings change ({RECOMMENDED.name} and {EXAMPLE.name}, the same 30 days)\n")
    w(f"| Setting | {RECOMMENDED.name}: $200 became | traded | stops + guard closes | {EXAMPLE.name}: $200 became | "
      "traded | stops + guard closes |")
    w("|---|---|---|---|---|---|---|")
    for name, kw in SETTINGS_SHOWN:
        st = replace(base, **kw)
        a = run_pair(RECOMMENDED, mks, costs, st, start, end)
        b = run_pair(EXAMPLE, mks, costs, st, start, end)
        w(f"| {name} | {money(a.total)} | {fmt_k(a.volume)} | {early(a)} | {money(b.total)} | {fmt_k(b.volume)} | "
          f"{early(b)} |")
    tape = C.tape()
    tape.fill = costs.fill
    dear = C.Costs(costs.table, 2.0, costs.top_usd, costs.fill)
    for name, cc in (("fills as dear as on Arcus's late-September books", tape), ("every fill twice as dear", dear)):
        a = BT.run(RECOMMENDED, mks[RECOMMENDED.x], mks[RECOMMENDED.y], cc, base, start, end)
        b = BT.run(EXAMPLE, mks[EXAMPLE.x], mks[EXAMPLE.y], cc, base, start, end)
        w(f"| {name} | {money(a.total)} | {fmt_k(a.volume)} | {early(a)} | {money(b.total)} | {fmt_k(b.volume)} | "
          f"{early(b)} |")
    w("\nThe defaults are the rows that lose least for the same volume. Cutting at the stock market's close is what "
      "you described (Lighter's legs follow Arcus's smaller size); it only adds fills, because Arcus does not cut a "
      "position at its close: its docs say maintenance margin does not change off-hours, only opening and adding "
      "need 1.5 times the margin. So the bot keeps the position, opens smaller when a cycle starts with the market "
      "closed, and Lighter always mirrors whatever Arcus holds (`offhours cut` is there if you want it). Weekdays "
      "only and 5x lose less only because they trade less. Holding 72 hours is about break-even: the fills, not "
      "the markets, are what loses. The stops resting on the venues change the result little: they are there for "
      "when the bot is not (a crash, a lost connection).\n")

    # ---- costs
    w("## 5. The transfers\n")
    r = runs[RECOMMENDED.name]
    n, typ, big = transfers_line(r)
    w(f"{RECOMMENDED.name} needed a transfer after {n} of {len(r.cycles)} cycles: typically {money(typ)}, at most "
      f"{money(big)}, {money(sum(t[1] for t in r.transfers))} in the 30 days. Both venues hold USDG on Robinhood "
      "Chain; there is no venue-to-venue transfer: withdraw from one to your wallet, deposit on the other. The bot "
      "names the amount, the venue it comes from and the one it goes to (`/dn_transfer`). It opens the next cycle "
      "without waiting, sized by the poorer venue (`wait_transfer 1` makes it wait instead). Moving nothing at all "
      "keeps shrinking the position: the poorer venue sets the size.\n")
    w("## 6. Where the fill costs come from\n")
    w("Arcus's books on these markets narrowed in October, when its trading grew about tenfold (AMD: about 800 trades "
      "a day in September, 5,000-7,000 in October). Measured on 16 Oct (`deltaneutral costs`), taking $500 costs "
      "0.3-1.5 bp from the mid on Arcus plus its 2.25 bp fee, and 0.8-2.7 bp on Lighter, which charges no fee. The "
      "Arcus bot's recorded books from late September were 3-4 bp (COIN 9-13 bp). The share of limit orders that "
      "fill within 10 minutes comes from each venue's own trades of the last 7 days and the size waiting at its best "
      "price (costs.maker_fills; section 1): a resting fill costs 0.5 bp in the replay (what prices that run on take "
      "back), the rest is taken. While one venue's order waits for the other's a leg is unhedged, by at most "
      "$150 and for at most a minute (`max_gap_usd`, `catchup_s`); that adds noise, not an expected cost, and is "
      "not in the replay. Lighter's book has not been recorded: it was measured only while the stock market was "
      "closed; run `deltaneutral costs` during New York hours to add that session.\n")
    w("## 7. Limits\n")
    w("- 30 days of the venues' own data: a calm month for these markets. Section 3 is the longer view.")
    w("- Fills are estimated from books measured on one day and applied to the whole month.")
    w("- Arcus's prices are its candles (its oracle while the stock market is open); its perp trades around them.")
    w("- Arcus's off-hours price band can refuse a fill at its edge; a close then waits for the band to widen.")
    w("- The bot has not traded live. Live uses the arbitrage's Arcus and Lighter clients (the Lighter one passed "
      "`arbitrage livetest` on the venue; the Arcus one has not been run live).")
    return "\n".join(lines) + "\n"
