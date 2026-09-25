"""Paper-trade the farm menu on a recorded tape window, market by market, and summarise it.

For every market, leverage and menu setting it runs the scout simulator (the same fill model, latency, order budget
and stops as the scout) over [start, end) and keeps:

- a summary row: volume (maker and taker), fills, PnL, cost per $1M, turnover (volume / capital per hour), the
  worst peak-to-trough drawdown and the worst hour in % of the capital, stops, and a risk label;
- the per-minute series (equity change, position, cumulative volume and fills): the paper-trading "candles";
- every paper fill.

It also writes 1-minute candles of the market itself (mid OHLC, spread, trade volume and count, taker buy share).

The simulation is causal: at each second it only reads the book up to that second, and orders go live 150 ms after
they are sent. Replaying the tape as it is recorded is therefore the same as paper trading it live with this fill
model; `bot farm run` does exactly that, hour by hour.
"""

from __future__ import annotations

import csv
import gzip
import json
import math
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np

from bot.common.sizing import Pct, bucket, min_capital, venue_min_usd
from bot.farm.menu import BY_NAME, FARM_MENU
from bot.scout.scan import ALIVE_MARKET, LEV_CAPS, liquidity, load_holidays, session_mask
from bot.scout.sim import MarketInfo, Risk, S, Sim, SimParams, Window
from bot.scout.tape import TapeStore

FARM_PCT = Pct(position_stop=5.0, daily_stop=10.0, kill=20.0)   # Tread users run SL 5-25% of margin per run
FARM_LEVERAGES = (5.0, 10.0, 20.0)
STATE_CODE = {"normal": 0, "exit_pos": 1, "exit_day": 2, "day_stopped": 3, "cooldown": 4, "killed": 5}
MINUTE_COLS = ("t", "eq", "pos_usd", "maker_usd", "maker_fills", "taker_usd", "fees", "state")
CANDLE_COLS = ("t", "open", "high", "low", "close", "spread_bps", "trades", "volume_usd", "buy_usd", "vwap",
               "last")


# ------------------------------------------------------------------------------------------------ risk label
def risk_label(cpm: float | None, max_dd_pct: float, killed: bool, day_loss_pct: float) -> str:
    """R1 low ... R4 extreme, the same thresholds as the shortlist (research/01_strategy_shortlist.md), applied to
    the paper results: cost per $1M, the worst drawdown and the projected loss per day, all in % of the capital."""
    if killed or cpm is None:
        return "R4" if killed else "U"
    if cpm <= 100 and max_dd_pct <= 3 and day_loss_pct <= 3:
        return "R1"
    if cpm <= 300 and max_dd_pct <= 7 and day_loss_pct <= 10:
        return "R2"
    if cpm <= 1000 and max_dd_pct <= 20 and day_loss_pct <= 30:
        return "R3"
    return "R4"


def day_loss(pnl: float, used: float, hours: float, day_stops: int) -> float:
    """Projected loss per day in % of the capital: the loss scaled to 24 hours, except that a run which hit its
    daily stop loses at most that day's loss per UTC day it spanned (it stops quoting until 00:00 UTC)."""
    loss = max(0.0, -pnl) / used * 100
    if day_stops > 0:
        return loss / max(day_stops, math.ceil(hours / 24 - 1e-9), 1)   # each stop is a different UTC day
    return loss * 24 / max(hours, 1e-9)


def relabel(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Recompute the projected loss per day and the risk label of stored rows (after a rule change)."""
    for r in rows:
        r["day_loss_pct"] = round(day_loss(r["pnl"], r["used"], r["hours"], r["day_stops"]), 3)
        r["risk"] = risk_label(r["cpm"], r["max_dd_pct"], r["killed"], r["day_loss_pct"])
    return rows


def leverages_for(meta: dict[str, Any], levs: tuple[float, ...] = FARM_LEVERAGES) -> list[tuple[float, float]]:
    imf = float(meta.get("initialMarginFraction") or 0.2)
    off = float(meta.get("offHoursInitialMarginFraction") or imf)
    top = min(1 / imf, LEV_CAPS.get(meta["marketDisplayName"], math.inf))
    top_off = min(1 / off, top)
    out = [(x, min(x, top_off)) for x in levs if x <= top + 1e-9]
    if top > max(levs) + 1e-9:   # also the market's maximum ("max leverage", S04/S38)
        out.append((round(top, 2), round(top_off, 2)))
    return out


def market_info(meta: dict[str, Any]) -> MarketInfo:
    return MarketInfo(float(meta["tickSize"]), float(meta["stepSize"]), float(meta.get("minOrderNotional") or 5),
                      float(meta.get("minOrderSize") or meta["stepSize"]),
                      mmf=float(meta.get("maintenanceMarginFraction") or 0))


# ------------------------------------------------------------------------------------------------ candles
def candles(w: Window) -> list[tuple[Any, ...]]:
    """1-minute candles of the market from the per-second book and the trades."""
    if not w.n:
        return []
    t0 = int(w.t[0])
    first = (w.start_us - t0) // S
    m0 = first + (-(t0 // S + first) % 60)             # the first whole minute at or after the window start
    n_min = (w.n - m0) // 60
    if n_min <= 0:
        return []
    mid = w.mid[m0:m0 + n_min * 60].reshape(n_min, 60)
    spr = w.spread_bps[m0:m0 + n_min * 60].reshape(n_min, 60)
    ts0 = int(w.t[m0])
    tts = w.tts
    k = np.searchsorted(tts, ts0 + np.arange(n_min + 1, dtype=np.int64) * 60 * S)
    out = []
    for j in range(n_min):
        row = mid[j]
        good = row[~np.isnan(row)]
        if not len(good):
            continue
        a, b = int(k[j]), int(k[j + 1])
        px, sz, buy = w.tpx[a:b], w.tsz[a:b], w.tbuy[a:b]
        notional = px * sz
        vol = float(notional.sum())
        s = spr[j][~np.isnan(spr[j])]
        out.append((ts0 // S + j * 60, float(good[0]), float(good.max()), float(good.min()), float(good[-1]),
                    round(float(s.mean()), 3) if len(s) else "", b - a, round(vol, 2),
                    round(float(notional[buy].sum()), 2), float(notional.sum() / sz.sum()) if b > a else "",
                    float(px[-1]) if b > a else ""))
    return out


def write_csv_gz(path: Path, cols: tuple[str, ...], rows: list[tuple[Any, ...]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with gzip.open(tmp, "wt", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(cols)
        wr.writerows(rows)
    tmp.replace(path)


# ------------------------------------------------------------------------------------------------ one run
def summarise(res: dict[str, Any], minutes: list[tuple[Any, ...]], capital: float, used: float) -> dict[str, Any]:
    vol = res["maker_usd"] + res["taker_usd"]
    hours = max(res["hours"], 1e-9)
    cpm = -res["pnl"] / vol * 1e6 if vol > 0 else None
    eq = np.array([m[1] for m in minutes]) if minutes else np.zeros(1)
    peak = np.maximum.accumulate(np.concatenate([[0.0], eq]))[1:]
    max_dd = float((peak - eq).max()) if len(eq) else 0.0
    worst_hour = 0.0
    hour_pnl: list[float] = []
    if len(minutes) >= 2:
        ts = np.array([m[0] for m in minutes], np.int64)
        hr = (ts - ts[0]) // (3600 * S)
        last = 0.0
        for h in range(int(hr[-1]) + 1):
            sel = np.flatnonzero(hr == h)
            if len(sel):
                v = float(eq[sel[-1]])
                hour_pnl.append(v - last)
                last = v
        worst_hour = min(hour_pnl) if hour_pnl else 0.0
    t100 = next((round((m[0] - minutes[0][0]) / 60e6) for m in minutes if m[3] + m[5] >= 100 * used), None) \
        if minutes else None
    day_loss_pct = day_loss(res["pnl"], used, res["hours"], res["day_stops"])
    out = {
        "market": res["market"], "setting": res["config"], "leverage": res.get("leverage"),
        "family": BY_NAME[res["config"]].family if res["config"] in BY_NAME else "",
        "capital": capital, "used": round(used, 2), "hours": round(res["hours"], 2),
        "maker_usd": round(res["maker_usd"], 2), "taker_usd": round(res["taker_usd"], 2),
        "volume_usd": round(vol, 2), "maker_fills": res["maker_fills"], "taker_fills": res["taker_fills"],
        "fills_per_h": round((res["maker_fills"] + res["taker_fills"]) / hours, 1),
        "turnover_per_h": round(vol / used / hours, 2),
        "pnl": round(res["pnl"], 4), "pnl_pct": round(res["pnl"] / used * 100, 3),
        "cpm": round(cpm, 1) if cpm is not None else None,
        "fees": round(res["fees"], 4), "max_dd_pct": round(max_dd / used * 100, 3),
        "worst_hour_pct": round(worst_hour / used * 100, 3),
        "hours_positive": sum(1 for x in hour_pnl if x > 0), "hours_counted": len(hour_pnl),
        "day_loss_pct": round(day_loss_pct, 3), "pos_stops": res["pos_stops"], "day_stops": res["day_stops"],
        "killed": res["killed"], "liquidated": res["liquidated"], "max_pos_usd": round(res["max_pos_usd"], 2),
        "end_pos_usd": round(res["end_pos_usd"], 2), "minutes_to_100x": t100, "rejects": res["rejects"],
    }
    out["risk"] = risk_label(cpm, out["max_dd_pct"], res["killed"], day_loss_pct)
    return out


def run_market(args: dict[str, Any]) -> dict[str, Any]:
    """Every (leverage, setting) on one market's window. Writes candles, series and fills under out/; returns the
    summary rows."""
    import bot.farm.policies  # noqa: F401  (spawned workers: register the Tread-mode policies)

    tape_root, market, start, end = Path(args["tape_root"]), args["market"], args["start"], args["end"]
    meta, out, capital = args["meta"], Path(args["out"]), float(args["capital"])
    names = args.get("settings") or [e.cfg.name for e in FARM_MENU]
    sp = SimParams(**args.get("sim", {}))
    warm = int(args.get("warmup_s", 1800))
    store = TapeStore(tape_root)
    tape = store.load_range(market, start - warm * S, end)
    alive_market = args.get("alive_market", ALIVE_MARKET)
    alive = store.load_range(alive_market, start - warm * S, end).bbo["ts"] if alive_market != market else None
    holidays = load_holidays(full_only=True)
    w = Window(tape, start, end, warmup_s=warm, alive_ts=alive,
               rth=session_mask(meta.get("regularTradingHours"), load_holidays()), holidays=holidays)
    if not w.n or not w.ok.any():
        return {"market": market, "rows": [], "note": "no data"}
    tag = args.get("tag", "")
    if args.get("candles", True):
        write_csv_gz(out / "candles" / f"{market}{tag}.csv.gz", CANDLE_COLS, candles(w))
    mi = market_info(meta)
    liq = liquidity(tape.trades)
    px = float(np.nanmedian(w.mid)) if w.ok.any() else 0.0
    vmin = venue_min_usd(mi.min_notional, mi.min_size, px)
    order_max = max(bucket(liq["p99"]) or 0.0, bucket(2 * 1.2 * vmin) or 0.0) or None
    rows, series, fills = [], {}, []
    for lev, lev_off in leverages_for(meta, tuple(args.get("leverages", FARM_LEVERAGES))):
        risk = Risk.for_capital(capital, lev, lev_off, pct=FARM_PCT, order_max=order_max,
                                min_capital=round(min_capital(vmin, lev_off), 2))
        if capital < risk.min_capital_usd:
            continue
        for n in names:
            cfg = BY_NAME[n].cfg
            trace: dict[str, list[Any]] = {"fills": [], "minutes": []}
            r = Sim(cfg, risk, mi, sp).run(w, trace=trace).as_dict()
            r["leverage"] = lev
            row = summarise(r, trace["minutes"], capital, risk.used)
            row["order_usd"] = round(risk.order_usd, 2)
            row["taker_orders"] = liq["takers"]
            row["market_volume_usd"] = round(liq["volume"], 2)
            rows.append(row)
            key = f"{n} @ {lev:g}x"
            series[key] = trace["minutes"]
            fills += [(key, *f) for f in trace["fills"]]
    if args.get("series", True) and rows:
        keys = sorted(series)
        write_series(out / "paper" / f"{market}{tag}.npz", keys, series, fills)
    return {"market": market, "rows": rows, "liq": liq, "order_max": order_max,
            "hours": float(w.ok[w.t >= start].sum()) / 3600}


def write_series(path: Path, keys: list[str], series: dict[str, list[tuple[Any, ...]]],
                 fills: list[tuple[Any, ...]]) -> None:
    """Per-minute paper series and every fill, compactly:
    minute_t [key, minute] int64 (UTC us, 0 = padding) and minutes [key, minute, column] float32 with the columns of
    MINUTE_COLS after `t` (NaN padded); fills as fill_t int64, fill_key int16, fill_side int8, fill_px float64,
    fill_qty float64, fill_maker bool, fill_tag."""
    path.parent.mkdir(parents=True, exist_ok=True)
    n = max((len(v) for v in series.values()), default=0)
    mt = np.zeros((len(keys), n), np.int64)
    arr = np.full((len(keys), n, len(MINUTE_COLS) - 1), np.nan, np.float32)
    for i, k in enumerate(keys):
        for j, m in enumerate(series[k]):
            mt[i, j] = m[0]
            arr[i, j] = (*m[1:7], STATE_CODE.get(m[7], -1))
    kid = {k: i for i, k in enumerate(keys)}
    tmp = path.with_name(path.name + ".tmp.npz")
    np.savez_compressed(tmp, keys=np.array(keys), minute_cols=np.array(MINUTE_COLS[1:]), minute_t=mt, minutes=arr,
                        fill_t=np.array([f[1] for f in fills], np.int64),
                        fill_key=np.array([kid[f[0]] for f in fills], np.int16),
                        fill_side=np.array([f[2] for f in fills], np.int8),
                        fill_px=np.array([f[3] for f in fills], np.float64),
                        fill_qty=np.array([f[4] for f in fills], np.float64),
                        fill_maker=np.array([bool(f[5]) for f in fills], bool),
                        fill_tag=np.array([f[6] for f in fills], dtype="U12"))
    tmp.replace(path)


# ------------------------------------------------------------------------------------------------ tables
def best_rows(rows: list[dict[str, Any]], *, max_day_loss_pct: float = 5.0) -> list[dict[str, Any]]:
    """Near breakeven (projected loss at most max_day_loss_pct of the capital per day, not killed), ranked by
    turnover per hour."""
    ok = [r for r in rows if not r["killed"] and r["day_loss_pct"] <= max_day_loss_pct and r["volume_usd"] > 0]
    return sorted(ok, key=lambda r: (-r["turnover_per_h"], -r["pnl"]))


def aggregate(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per (setting, leverage) across markets: the average market and how many markets it was near breakeven on."""
    by: dict[tuple[str, float], list[dict[str, Any]]] = {}
    for r in rows:
        by.setdefault((r["setting"], r["leverage"]), []).append(r)
    out = []
    for (name, lev), rs in by.items():
        vol = sum(r["volume_usd"] for r in rs)
        pnl = sum(r["pnl"] for r in rs)
        out.append({"setting": name, "leverage": lev, "markets": len(rs),
                    "family": rs[0]["family"],
                    "turnover_per_h": round(float(np.mean([r["turnover_per_h"] for r in rs])), 2),
                    "volume_usd": round(vol, 2), "pnl": round(pnl, 3),
                    "cpm": round(-pnl / vol * 1e6, 1) if vol > 0 else None,
                    "median_cpm": round(float(np.median([r["cpm"] for r in rs if r["cpm"] is not None])), 1)
                    if any(r["cpm"] is not None for r in rs) else None,
                    "markets_profitable": sum(1 for r in rs if r["pnl"] > 0),
                    "markets_near_breakeven": sum(1 for r in rs if r["day_loss_pct"] <= 5 and not r["killed"]
                                                  and r["volume_usd"] > 0),
                    "worst_dd_pct": round(max(r["max_dd_pct"] for r in rs), 2),
                    "kills": sum(1 for r in rs if r["killed"]),
                    "risk_mix": {k: sum(1 for r in rs if r["risk"] == k) for k in ("R1", "R2", "R3", "R4")}})
    return sorted(out, key=lambda r: (-(r["markets_near_breakeven"] / max(1, r["markets"])), r["cpm"] or 0))


def fmt_table(rows: list[dict[str, Any]], cols: list[tuple[str, str]], limit: int = 30) -> str:
    head = "| " + " | ".join(h for _, h in cols) + " |"
    sep = "|" + "|".join("---" for _ in cols) + "|"
    body = []
    for r in rows[:limit]:
        cells = []
        for k, _ in cols:
            v = r.get(k)
            if isinstance(v, float):
                v = f"{v:,.2f}" if abs(v) < 1e4 else f"{v:,.0f}"
            elif isinstance(v, dict):
                v = " ".join(f"{a}:{b}" for a, b in v.items() if b)
            cells.append("" if v is None else str(v))
        body.append("| " + " | ".join(cells) + " |")
    return "\n".join([head, sep, *body])


def leaderboard(rows: list[dict[str, Any]], title: str, meta: dict[str, Any]) -> str:
    """The markdown leaderboard written after each analysis."""
    agg = aggregate(rows)
    best = best_rows(rows)
    lines = [f"# {title}", "", *(f"- {k}: {v}" for k, v in meta.items()), "",
             "Near breakeven = projected loss at most 5% of the capital per day and never killed. Turnover = volume "
             "(maker + taker) / capital per hour. CPM = dollars lost per $1M traded (negative = profit). Risk labels: "
             "research/01_strategy_shortlist.md 1.2.", "",
             "## Best single runs (near breakeven, by turnover)", "",
             fmt_table(best, [("market", "market"), ("setting", "setting"), ("leverage", "lev"),
                              ("turnover_per_h", "turnover/h"), ("volume_usd", "volume $"),
                              ("fills_per_h", "fills/h"), ("pnl", "PnL $"), ("cpm", "CPM"),
                              ("max_dd_pct", "max DD %"), ("worst_hour_pct", "worst h %"), ("risk", "risk")], 40),
             "", "## Each setting across markets", "",
             fmt_table(agg, [("setting", "setting"), ("leverage", "lev"), ("family", "family"),
                             ("markets", "markets"), ("markets_near_breakeven", "near BE"),
                             ("markets_profitable", "profitable"), ("turnover_per_h", "avg turnover/h"),
                             ("volume_usd", "volume $"), ("pnl", "PnL $"), ("cpm", "CPM"),
                             ("median_cpm", "median CPM"), ("worst_dd_pct", "worst DD %"), ("kills", "kills"),
                             ("risk_mix", "risk mix")], 200)]
    return "\n".join(lines) + "\n"


def save_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, indent=0, default=str))


__all__ = ["FARM_PCT", "aggregate", "asdict", "best_rows", "candles", "leaderboard", "risk_label", "run_market",
           "save_rows", "summarise"]
