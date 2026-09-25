"""`bot diagnose`: why a run filled what it filled, over a time window. Read-only.

Sources, all on the machine the bot runs on:
- the run's state database (state/<mode>.sqlite): every order intent and every venue update with its time, and fills;
- its decision log (logs/decisions.jsonl*): pauses, stops, rejects;
- the scout's recorded tape of the same market (data/scout/tape): the best bid and ask when each order was placed,
  and every taker trade in the window.

It answers, in order: were orders sent and acknowledged; were any rejected (why); how long a buy and a sell rested;
where they rested against the best price; what blocked quoting; and how many taker trades went through a price the
bot was resting at (fills the backtest would count) or went by while it had no order on that side.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
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
             base: str | None = None, mode: str = "live") -> str:
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
    return "\n".join(out)


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
