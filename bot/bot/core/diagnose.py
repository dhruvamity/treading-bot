"""`bot diagnose`: why a run filled what it filled, over a time window. Read-only.

Sources, all on the machine the bot runs on:
- the run's state database (state/<mode>.sqlite): every order intent and every venue update with its time, and fills;
- its decision log (logs/decisions.jsonl*): pauses, stops, and orders its own pre-trade checks refused;
- the scout's recorded tape of the same market (data/scout/tape): the best bid and ask when each order was placed,
  and every taker trade in the window.

With `setup` (--replay): the scout's backtest of the run's own setup on the same recorded window, next to what the
run did, under each fill model: the gap between what the backtest assumes and what happened.

It answers, in order: were orders sent and acknowledged; were any rejected (why); how long a buy and a sell rested;
where they rested against the best price; what blocked quoting (pauses, and orders the bot's own checks refused
before sending); and how many taker trades went through a price the bot was resting at (fills the backtest would
count) or went by while it had no order on that side.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from itertools import pairwise
from pathlib import Path
from typing import Any

import numpy as np

from bot.common.time import US_PER_S
from bot.scout.tape import TapeStore

TERMINAL = ("FILLED", "CANCELED", "REJECTED", "EXPIRED")


def _intervals_union(iv: list[tuple[int, int]]) -> int:
    """Total µs covered by a list of [start, end) intervals."""
    tot, cur_s, cur_e = 0, None, None
    for s, e in sorted(iv):
        if cur_e is None or s > cur_e:
            if cur_e is not None and cur_s is not None:
                tot += cur_e - cur_s
            cur_s, cur_e = s, e
        else:
            cur_e = max(cur_e, e)
    if cur_e is not None and cur_s is not None:
        tot += cur_e - cur_s
    return tot


def _dur(us: float) -> str:
    s = int(us / US_PER_S)
    return f"{s // 3600}h{s % 3600 // 60:02d}m" if s >= 3600 else f"{s // 60}m{s % 60:02d}s" if s >= 60 else f"{s}s"


def load_orders(db: Path, start_us: int, end_us: int, base: str | None) -> list[dict[str, Any]]:
    """Orders placed in the window, each with the times the venue acknowledged and closed it (from the events)."""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        cols = [c[1] for c in con.execute("PRAGMA table_info(orders)")]
        rows = [dict(zip(cols, r, strict=True)) for r in con.execute(
            "SELECT * FROM orders WHERE created_us >= ? AND created_us < ?", (start_us, end_us))]
        if base:
            rows = [r for r in rows if r["base"] == base]
        first: dict[tuple[str, str], int] = {}
        for cid, ts, payload in con.execute(
                "SELECT client_id, ts_us, payload FROM events WHERE kind = 'update' AND ts_us >= ? ORDER BY ts_us",
                (start_us,)):
            st = (json.loads(payload or "{}") or {}).get("status", "")
            first.setdefault((cid, st), ts)
        fills = [dict(zip(("side", "price", "size", "is_maker", "ts_us", "base"), r, strict=True)) for r in con.execute(
            "SELECT side, price, size, is_maker, ts_us, base FROM fills WHERE ts_us >= ? AND ts_us < ?",
            (start_us, end_us))]
    finally:
        con.close()
    for r in rows:
        cid = r["client_id"]
        r["acked_us"] = min((first[(cid, s)] for s in ("OPEN", "PARTIALLY_FILLED", "FILLED") if (cid, s) in first),
                            default=None)
        r["closed_us"] = min((first[(cid, s)] for s in TERMINAL if (cid, s) in first), default=None) or \
            (r["updated_us"] if r["status"] in TERMINAL else None)
    return rows + [{"_fill": True, **f} for f in fills if not base or f["base"] == base]


def load_decisions(logs: Path, start_us: int, end_us: int, base: str | None) -> list[dict[str, Any]]:
    out = []
    for p in sorted(logs.glob("decisions.jsonl*")):
        try:
            lines = p.read_text().splitlines()
        except OSError:
            continue
        for ln in lines:
            try:
                d = json.loads(ln)
            except ValueError:
                continue
            if start_us <= int(d.get("ts") or 0) < end_us and (not base or d.get("market") in (None, base)):
                out.append(d)
    return sorted(out, key=lambda d: d["ts"])


def diagnose(*, db: Path, logs: Path, tape_root: Path, markets_json: Path | None, start_us: int, end_us: int,
             base: str | None = None, mode: str = "live", setup: dict[str, Any] | None = None) -> str:
    got = load_orders(db, start_us, end_us, base)
    orders = [r for r in got if not r.get("_fill")]
    fills = [r for r in got if r.get("_fill")]
    base = base or (Counter(r["base"] for r in orders).most_common(1) or [(None, 0)])[0][0]
    market = f"{base}-USD" if base else None
    span = end_us - start_us
    out = [f"{market or 'all markets'} · {mode.upper()} · {_utc(start_us)} → {_utc(end_us)} UTC ({_dur(span)})"]
    if not orders:
        out.append("Orders    none placed in this window: the bot never quoted. See the blocks below.")
    quotes = [r for r in orders if not r["reduce_only"]]
    # ---- orders
    if orders:
        st = Counter(r["status"] for r in orders)
        acked = [r for r in orders if r["acked_us"]]
        lat = sorted((r["acked_us"] - r["created_us"]) / 1000 for r in acked)
        life = sorted(((r["closed_us"] or end_us) - r["created_us"]) / US_PER_S for r in quotes)
        out.append(f"Orders    {len(orders)} sent ({sum(r['side'] == 'buy' for r in orders)} buy, "
                   f"{sum(r['side'] == 'sell' for r in orders)} sell) · acknowledged {len(acked)}"
                   + (f" (median {lat[len(lat) // 2]:.0f} ms)" if lat else "")
                   + (f" · median {life[len(life) // 2]:.0f} s on the book" if life else "")
                   + f" · {len(orders) / max(1, span / 60 / US_PER_S):.1f}/min")
        out.append("          " + " · ".join(f"{k.lower()} {v}" for k, v in st.most_common()))
        rej = Counter((r["reject_reason"] or "?") for r in orders if r["status"] == "REJECTED")
        if rej:
            out.append("Rejected  " + " · ".join(f"{k} ×{v}" for k, v in rej.most_common(5)))
        never = [r for r in orders if not r["acked_us"] and r["status"] not in ("REJECTED",)]
        if never:
            out.append(f"⚠️ {len(never)} orders were never acknowledged by the venue")
    # ---- time on the book, and where against the best price
    tape = TapeStore(tape_root).load_range(market, start_us - 60 * US_PER_S, end_us) if market else None
    tick = _tick(markets_json, market)
    if quotes:
        for side in ("buy", "sell"):
            iv = [(max(start_us, r["acked_us"]), min(end_us, r["closed_us"] or end_us))
                  for r in quotes if r["side"] == side and r["acked_us"]]   # only what reached the book
            on = _intervals_union([(a, b) for a, b in iv if b > a])
            out.append(f"On book   a {side} rested {_dur(on)} of {_dur(span)} ({on / span * 100:.0f}%)")
        if tape is not None and len(tape.bbo["ts"]) and tick:
            behind = []
            for r in quotes:
                k = int(np.searchsorted(tape.bbo["ts"], r["created_us"], side="right")) - 1
                if k < 0:
                    continue
                px = float(r["price"])
                best = float(tape.bbo["bid"][k] if r["side"] == "buy" else tape.bbo["ask"][k])
                behind.append((best - px if r["side"] == "buy" else px - best) / tick)
            if behind:
                b = np.array(behind)
                out.append(f"Placement at or inside the best price {np.mean(b <= 0) * 100:.0f}% · 1-2 ticks behind "
                           f"{np.mean((b > 0) & (b <= 2)) * 100:.0f}% · median {np.median(b):.1f} ticks behind "
                           f"(tick {tick:g})")
    # ---- what blocked quoting
    dec = load_decisions(logs, start_us, end_us, base)
    blocks = Counter(str(d.get("event")) for d in dec if str(d.get("event", "")).startswith("risk:")
                     or d.get("event") in ("ioc_rejected", "resize", "mode"))
    if blocks:
        out.append("Decisions " + " · ".join(f"{k} ×{v}" for k, v in blocks.most_common(6)))
        for d in [d for d in dec if str(d.get("event", "")).startswith("risk:")][:4]:
            out.append(f"          {_utc(int(d['ts']))} {d['event']}: {str(d.get('reason'))[:110]}")
    # ---- orders the bot's own pre-trade checks refused: never sent, so not in the state database
    refused = [d for d in dec if d.get("event") == "reject_pretrade"]
    if refused:
        by: dict[str, list[dict[str, Any]]] = {}
        for d in refused:
            by.setdefault(str(d.get("reason") or "?").split(":")[0], []).append(d)
        out.append(f"Refused   {len(refused):,} orders by the bot's own checks (never sent to the venue)")
        for check, ds in sorted(by.items(), key=lambda kv: -len(kv[1]))[:4]:
            ts = [int(d["ts"]) for d in ds]
            sides = Counter(_side(d) for d in ds)
            out.append(f"          {check} ×{len(ds):,} ("
                       + ", ".join(f"{s} {n:,}" for s, n in sides.most_common())
                       + f") · {_utc(ts[0])} → {_utc(ts[-1])}, refusing for {_dur(_busy(ts))} · last: "
                       + str(ds[-1].get("reason"))[len(check) + 2:][:80])
    # ---- fills against the market's takers
    vol = sum(float(f["price"]) * float(f["size"]) for f in fills)
    out.append(f"Fills     {len(fills)} · ${vol:,.0f} ({sum(1 for f in fills if f['is_maker'])} maker)")
    if tape is not None and len(tape.trades["ts"]):
        tr = tape.trades
        sel = (tr["ts"] >= start_us) & (tr["ts"] < end_us)
        tts, tpx, tsz, tbuy = tr["ts"][sel], tr["px"][sel], tr["sz"][sel], tr["buy"][sel]
        through = idle = 0
        idle_usd = 0.0
        for t, p, q, taker_buy in zip(tts.tolist(), tpx.tolist(), tsz.tolist(), tbuy.tolist(), strict=True):
            side = "sell" if taker_buy else "buy"     # a taker buy lifts asks: our sell side
            live = [float(r["price"]) for r in quotes if r["side"] == side and r["acked_us"]
                    and r["acked_us"] <= t < (r["closed_us"] or end_us)]
            if not live:
                idle += 1
                idle_usd += p * q
            elif (side == "sell" and p > min(live)) or (side == "buy" and p < max(live)):
                through += 1
        out.append(f"Market    {len(tts)} taker trades, ${float(np.sum(tpx * tsz)):,.0f} · {through} went through a "
                   f"price you rested at (the backtest counts those as fills) · {idle} (${idle_usd:,.0f}) traded "
                   "while you had no order on that side")
    elif market:
        out.append(f"Market    no recorded tape for {market} in this window (is the scout recording on this machine?)")
    if setup is not None and market:
        out += replay(setup, fills, dec, tape_root, markets_json, market, start_us, end_us)
    return "\n".join(out)


def find_setup(events: Path, market: str, before_us: int) -> dict[str, Any] | None:
    """The setup of the last run the pilot deployed on `market` before `before_us` (state/pilot_events.jsonl)."""
    try:
        lines = events.read_text().splitlines()
    except OSError:
        return None
    best = None
    for ln in lines:
        try:
            e = json.loads(ln)
        except ValueError:
            continue
        s = e.get("setup") or {}
        if e.get("kind") == "deployed" and s.get("market") == market and e.get("ts", 0) * US_PER_S < before_us:
            best = s
    return best


def replay(setup: dict[str, Any], fills: list[dict[str, Any]], dec: list[dict[str, Any]], tape_root: Path,
           markets_json: Path | None, market: str, start_us: int, end_us: int) -> list[str]:
    """The run's setup backtested on the same tape window, beside the run: volume, fills, PnL, the first daily stop.
    Sizes are the ones the engine traded (its `resize` decision), else the deployed candidate's."""
    from dataclasses import replace

    from bot.scout.scan import BY_NAME, load_holidays, load_markets, market_meta, session_mask
    from bot.scout.sim import Risk, Sim, SimParams, Window

    cfg = BY_NAME.get(str(setup.get("setting")))
    if cfg is None or not setup.get("risk") or markets_json is None:
        return [f"Replay    cannot: setting {setup.get('setting')!r} or its sizes are unknown"]
    risk = Risk(**setup["risk"]).with_stops(cfg.stops)
    sized = next((d.get("data") or {} for d in reversed(dec) if d.get("event") == "resize"
                  and (d.get("data") or {}).get("order")), None)
    if sized:
        risk = replace(risk, capital_usd=float(sized["capital"]), used_usd=float(sized["capital"]),
                       order_usd=float(sized["order"]), cap_usd=float(sized["cap"]),
                       cap_off_usd=float(sized.get("cap_off") or sized["cap"]), pos_stop_usd=float(sized["pos_stop"]),
                       daily_stop_usd=float(sized["daily_stop"]), kill_usd=float(sized["kill"]))
    lim = float(setup.get("max_loss_usd") or 0)
    if lim:   # the run's loss limit (sl=) lifts the daily stop and the kill to itself, as in the engine
        risk = replace(risk, daily_stop_usd=max(risk.daily_stop_usd, lim), kill_usd=max(risk.kill_usd, lim))
    mi = load_markets(markets_json).get(market)
    meta = market_meta(markets_json).get(market, {})
    store = TapeStore(tape_root)
    tape = store.load_range(market, start_us - 2 * 3600 * US_PER_S, end_us)
    if mi is None or not len(tape.bbo["ts"]):
        return [f"Replay    no market data or tape for {market}"]
    hol = load_holidays(full_only=True)
    w = Window(tape, start_us, end_us, rth=session_mask(meta.get("regularTradingHours"), hol), holidays=hol)
    live_vol = sum(float(f["price"]) * float(f["size"]) for f in fills)
    sign = {"buy": 1.0, "sell": -1.0}
    pos = sum(sign.get(str(f["side"]), 0.0) * float(f["size"]) for f in fills)
    cash = -sum(sign.get(str(f["side"]), 0.0) * float(f["price"]) * float(f["size"]) for f in fills)
    fees = 0.0   # the fills table's fee is not loaded here; maker fills pay none on Arcus
    k = int(np.searchsorted(tape.bbo["ts"], end_us, side="right")) - 1
    mid_end = float((tape.bbo["bid"][k] + tape.bbo["ask"][k]) / 2) if k >= 0 else 0.0
    live_pnl = cash + pos * mid_end - fees
    stop = next((int(d["ts"]) for d in dec if str(d.get("event")) == "risk:stop_venue_day"), None)
    out = [f"Replay    {setup.get('setting')} @ {float(setup.get('leverage') or 0):g}x: order ${risk.order_usd:,.0f}, cap "
           f"${risk.cap_usd:,.0f}, stops ${risk.pos_stop_usd:.2f}/${risk.daily_stop_usd:.2f}/${risk.kill_usd:.2f}"
           + (" (the engine's sizes)" if sized else " (the deployed sizes)") + (f", sl=${lim:g}" if lim else ""),
           f"  the run      volume ${live_vol:>9,.0f}  fills {len(fills):4d}  PnL {live_pnl:+7.2f} (marked at the end"
           f"{', starting flat' if fills else ''})  daily stop {_utc(stop) if stop else '-'}"]
    for name, sp in (("through", SimParams()), ("queue (scan)", SimParams(queue=True)),
                     ("front", SimParams(front_of_queue=True))):
        r = Sim(cfg, risk, mi, sp).run(w)
        vol = r.maker_usd + r.taker_usd
        out.append(f"  backtest {name:13s} ${vol:>9,.0f}  fills {r.maker_fills + r.taker_fills:4d}  PnL {r.pnl:+7.2f}"
                   f"  daily stop {_utc(r.first_day_stop_us) if r.first_day_stop_us else '-'}"
                   + (f"  ({vol / live_vol:.1f}x the run's volume)" if live_vol else ""))
    return out


def _side(d: dict[str, Any]) -> str:
    """buy/sell of a logged order: its `side`, or for older logs the market maker's tag (b0 = bid, a0 = ask)."""
    data = d.get("data") or {}
    side = data.get("side") or {"b": "buy", "a": "sell"}.get(str(data.get("tag") or "?")[:1])
    return str(side or "?")


def _busy(ts: list[int], gap_s: int = 10) -> int:
    """µs covered by a run of timestamps, counting a gap longer than gap_s as a break."""
    return sum(min(b - a, gap_s * US_PER_S) for a, b in pairwise(ts))


def _tick(markets_json: Path | None, market: str | None) -> float:
    if not markets_json or not market:
        return 0.0
    try:
        data = json.loads(markets_json.read_text())
    except (OSError, ValueError):
        return 0.0
    m = next((x for x in data.get("markets", data) if x.get("marketDisplayName") == market), None)
    return float(m["tickSize"]) if m and m.get("tickSize") else 0.0


def _utc(us: int) -> str:
    import datetime as dt

    return dt.datetime.fromtimestamp(us / US_PER_S, dt.UTC).strftime("%m-%d %H:%M:%S")
