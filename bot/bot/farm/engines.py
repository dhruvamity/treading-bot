"""Collect the live paper engines' results (the homes `prepare_scout` wrote, run by scripts/paper_engines.sh).

Each engine keeps its ledger in its own state database (state/paper.sqlite): the `status` record has net PnL split
into spread capture and inventory mark-to-market, fees, volume, fills, and how long its quotes sat at the best price.
`collect` reads every home into one table (SUMMARY.md, summary.json), appends a snapshot per engine to
snapshots.jsonl (the hour-by-hour record), and exports every fill to fills/<home>.csv.gz.

    bot farm engines ../research/runs/<run>             once
    bot farm engines ../research/runs/<run> --loop 60   every 60 minutes, committing the results
"""

from __future__ import annotations

import csv
import gzip
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from bot.farm.analyze import fmt_table

S = 1_000_000


def _f(x: Any) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def read_home(home: Path) -> dict[str, Any] | None:
    db = home / "state" / "paper.sqlite"
    if not db.exists():
        return None
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
    try:
        row = con.execute("select v from kv where k='status'").fetchone()
        fills = con.execute("select ts_us, side, price, size, fee, is_maker, tag from fills order by ts_us").fetchall()
    finally:
        con.close()
    if not row:
        return None
    st = json.loads(row[0])
    m = (st.get("markets") or [{}])[0]
    ses = (st.get("sessions") or [{}])[0]
    q = ses.get("quotes") or {}
    now_us = int(st.get("ts_us") or time.time() * S)
    hours = max((now_us - int(st.get("started_us") or now_us)) / 3.6e9, 1e-9)
    capital = _f(ses.get("capital")) or 100.0
    vol, net = _f(m.get("volume")), _f(m.get("net"))
    pos, mark = _f(m.get("position")), _f(m.get("mark"))
    eq = (st.get("account") or {}).get("arcus", {}).get("equity")
    runs = json.loads((home.parent / "runs.json").read_text()) if (home.parent / "runs.json").exists() else []
    meta: dict[str, Any] = next((r for r in runs if Path(r["home"]).name == home.name), {})
    max_pos = 0.0
    p = 0.0
    for _, side, _px, sz, *_ in fills:
        p += _f(sz) if side == "buy" else -_f(sz)
        max_pos = max(max_pos, abs(p))
    secs = _f(q.get("seconds")) or 1e-9
    out = {
        "market": f"{m.get('market') or meta.get('market', home.name)}", "setting": meta.get("setting", ""),
        "leverage": meta.get("leverage"), "hours": round(hours, 2), "capital": capital,
        "order_usd": meta.get("order_usd"), "volume_usd": round(vol, 2), "maker_usd": round(_f(m.get("maker_volume")), 2),
        "fills": int(m.get("fills") or len(fills)), "fills_per_h": round(len(fills) / hours, 1),
        "turnover_per_h": round(vol / capital / hours, 2), "net_pnl": round(net, 4),
        "pnl_pct": round(net / capital * 100, 3), "spread_capture": round(_f(m.get("spread_capture")), 4),
        "inventory_mtm": round(_f(m.get("inventory_mtm")), 4), "fees": round(_f(m.get("fees")), 4),
        "funding": round(_f(m.get("funding")), 4),
        "cpm": round(-net / vol * 1e6, 1) if vol > 0 else None,
        "day_loss_pct": round(max(0.0, -net) / capital * 100 * 24 / hours, 2),
        "position_usd": round(pos * mark, 2), "max_position_usd": round(max_pos * mark, 2),
        "equity": round(_f(eq), 4) if eq is not None else None,
        "quoting_pct": round(_f(q.get("quoting")) / secs * 100, 1),
        "bid_at_touch_pct": round(_f(q.get("bid_touch")) / secs * 100, 1),
        "ask_at_touch_pct": round(_f(q.get("ask_touch")) / secs * 100, 1),
        "blocked": q.get("blocked") or {}, "why_not_quoting": m.get("why") or "",
        "stops": ses.get("stops"), "risk": st.get("risk"), "rejects": ses.get("rejects"), "errors": ses.get("errors"),
        "ts_us": now_us, "home": home.name,
    }
    out["_fills"] = fills
    return out


def collect(engine_dir: Path) -> list[dict[str, Any]]:
    rows = []
    for home in sorted(p for p in engine_dir.iterdir() if p.is_dir() and (p / "config").exists()):
        r = read_home(home)
        if r is None:
            continue
        fills = r.pop("_fills")
        path = engine_dir / "fills" / f"{home.name}.csv.gz"
        path.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(path, "wt", newline="") as f:
            w = csv.writer(f)
            w.writerow(("ts_us", "side", "price", "size", "fee", "is_maker", "tag"))
            w.writerows(fills)
        rows.append(r)
    (engine_dir / "summary.json").write_text(json.dumps(rows, indent=1, default=str))
    with open(engine_dir / "snapshots.jsonl", "a") as f:
        for r in rows:
            f.write(json.dumps({k: r[k] for k in ("ts_us", "market", "setting", "leverage", "hours", "volume_usd",
                                                  "fills", "net_pnl", "spread_capture", "inventory_mtm",
                                                  "position_usd", "quoting_pct")}) + "\n")
    rows.sort(key=lambda r: -r["turnover_per_h"])
    head = ["# Live paper engines", "",
            f"Updated {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}. Each row is the bot's real engine in paper "
            "mode (live Arcus data, simulated orders, the paper venue's queue-aware fills) running one session, the "
            "one Telegram's `/run MARKET SETTING max` deploys: $100 capital at the market's maximum leverage, stops "
            "1% / 2% / 10% of the capital. Net = spread capture + inventory mark-to-market − fees + funding. CPM = "
            "dollars lost per $1M traded (negative = profit). At touch = share of quoting time the bid (ask) sat at the "
            "best price.", "",
            fmt_table(rows, [("market", "market"), ("setting", "setting"), ("leverage", "lev"), ("hours", "hours"),
                             ("volume_usd", "volume $"), ("turnover_per_h", "turnover/h"), ("fills_per_h", "fills/h"),
                             ("net_pnl", "net $"), ("pnl_pct", "net %"), ("cpm", "CPM"),
                             ("spread_capture", "spread $"), ("inventory_mtm", "inventory $"),
                             ("position_usd", "position $"), ("max_position_usd", "max pos $"),
                             ("quoting_pct", "quoting %"), ("bid_at_touch_pct", "bid at touch %"),
                             ("ask_at_touch_pct", "ask at touch %"), ("why_not_quoting", "not quoting")], 100)]
    (engine_dir / "SUMMARY.md").write_text("\n".join(head) + "\n")
    return rows


def commit_paths(engine_dir: Path) -> list[Path]:
    return [engine_dir / "SUMMARY.md", engine_dir / "summary.json", engine_dir / "snapshots.jsonl",
            engine_dir / "runs.json", engine_dir / "fills", *sorted(engine_dir.glob("*/config/sessions/xcheck.yaml"))]
