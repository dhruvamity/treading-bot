"""`arcus recommend`: what to run, as commands you can paste. Reads the last scan of each bot (the lists the scout
made, here or on a bigger machine from a tape brought home) and prints, per list, the best setup with the exact
Telegram line that starts it. Nothing is started and no key is read.

    arcus recommend                 the three lists of each bot, top 3 each
    arcus recommend SPY QQQ         the best setup of each list on those markets only
    arcus recommend SPY --scan      scan first (both bots, as of the end of the tape), then print
"""

from __future__ import annotations

import datetime as dt
import json
import math
import time
from pathlib import Path
from typing import Any

from arcus.scout import profiles
from arcus.scout.pilot import setup_of

LIST_NAMES = {"volume": "most", "cheapest": "cheapest", "max": "max"}     # Arcus list -> Lighter list
TITLES = {"volume": "🚀 Most Volume", "cheapest": "💎 Cheapest", "max": "🔥 Max Volume"}
LIMITS = "add sl=<dollars> to cap the run's loss, tp=<dollars> to stop once up, vol=<dollars> to stop after that volume"


def _load(p: Path) -> dict[str, Any] | None:
    try:
        d = json.loads(p.read_text())
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def _when(t: float | None) -> str:
    if not t:
        return "unknown"
    ago = max(0.0, time.time() - t)
    age = f"{ago / 86400:.1f} days ago" if ago >= 86400 * 2 else f"{ago / 3600:.1f} h ago" if ago >= 3600 else \
        f"{ago / 60:.0f} min ago"
    return f"{dt.datetime.fromtimestamp(t, dt.UTC):%Y-%m-%d %H:%M} UTC ({age})"


def _num(x: float) -> str:
    return f"${x:,.0f}" if math.isfinite(x) else "-"


def arcus_pick(scan: dict[str, Any], key: str, budget: float, markets: list[str], n: int) -> list[dict[str, Any]]:
    """The profile's best setup per market (all markets, or the named ones), most promising first."""
    keep = {m.upper() for m in markets}
    rows = [c for c in scan.get("all") or [] if not keep or c["market"].removesuffix("-USD").upper() in keep]
    return profiles.top({**scan, "all": rows}, key, budget, n=n if not keep else max(n, len(keep)))


def arcus_line(c: dict[str, Any], rank: int, stop_usd: float) -> list[str]:
    s = setup_of(c)
    lev = float(c.get("leverage") or 0)
    sym = c["market"].removesuffix("-USD")
    cost = profiles.cost_1k(c)
    run = f"/run {sym} {s.name.lower()} {lev:g}x"
    return [f"  {rank}. {sym} · {s.label} · {lev:g}x   {_num(float(c.get('volume_day') or 0))}/day, "
            f"cost ${max(0.0, cost or 0.0):.2f} per $1,000, {float(c.get('fills_day') or 0):,.0f} fills/day",
            f"       paper:  {run} paper",
            f"       live:   {run} live sl={max(1, round(stop_usd))}"]


def lighter_pick(scan: dict[str, Any], key: str, markets: list[str], n: int) -> list[dict[str, Any]]:
    keep = {m.upper() for m in markets}
    name = LIST_NAMES[key]
    if not keep:
        return list((scan.get("lists") or {}).get(name) or [])[:n]
    budget = float(scan.get("volume_cost") or 0.1)
    rows = [r for r in scan.get("table") or [] if r["market"].upper() in keep and not r.get("why")]
    if key == "volume":
        rows = [r for r in rows if r.get("cost_1k") is not None and r["cost_1k"] <= budget]
    if key == "cheapest":
        rows = [r for r in rows if float(r.get("x_capital") or 0) >= profiles.CHEAP_MIN_TURNOVER]
        rows.sort(key=lambda r: (r.get("cost_1k") or 0.0, -float(r["volume_d"])))
    else:
        rows.sort(key=lambda r: -float(r["volume_d"]))
    return rows[:max(n, len(keep))]


def lighter_line(r: dict[str, Any], rank: int, stop_usd: float) -> list[str]:
    lev = float(r.get("leverage") or 0)
    run = f"/l_run {r['market']} {str(r['setup']).lower()} {lev:g}x"
    return [f"  {rank}. {r['market']} · {r['setup']} · {lev:g}x   {_num(float(r.get('volume_d') or 0))}/day, "
            f"cost ${max(0.0, float(r.get('cost_1k') or 0)):.3f} per $1,000, {float(r.get('fills_d') or 0):,.0f} fills/day",
            f"       paper:  {run} paper",
            f"       live:   {run} live sl={max(1, round(stop_usd))}"]


def report(root: Path, markets: list[str], lists: list[str], n: int, budget: float,
           lighter_root: Path | None = None) -> str:
    """The text `arcus recommend` prints."""
    out: list[str] = []
    a = _load(root / "data" / "scout" / "latest.json")
    lt = _load((lighter_root or root.parent / "lighter") / "data" / "scout" / "latest.json")
    if a is None and lt is None:
        return ("No scan yet. With a tape on this machine: arcus recommend --scan. Otherwise tbot import first "
                "(or arcus scout scan if the scout has recorded for 3 days).")
    for key in lists:
        out += ["", f"{TITLES[key]}"]
        if a is not None:
            rows = arcus_pick(a, key, budget, markets, n)
            stop = float((a.get("risk") or {}).get("daily_stop_usd") or 0)
            cap = float((a.get("capital") or {}).get("usd") or 0)
            out.append(f" ARCUS  (scan as of {_when(a['ts_us'] / 1e6)}, at ${cap:,.0f} of capital)")
            for i, c in enumerate(rows, 1):
                out += arcus_line(c, i, stop)
            if not rows:
                near = profiles.nearest({**a, "all": [c for c in a.get("all") or [] if not markets or
                                                      c["market"].removesuffix("-USD").upper() in
                                                      {m.upper() for m in markets}]}, key, budget, n)
                out.append("       nothing passes every check" + (" within the cost budget; closest by cost:"
                                                                    if near and key == "volume" else ""))
                if key == "volume":
                    for i, c in enumerate(near, 1):
                        out += arcus_line(c, i, stop)
        if lt is not None:
            rows = lighter_pick(lt, key, markets, n)
            stops = lt.get("stops") or [0, 0, 0]
            stop = float(lt.get("capital") or 0) * float(stops[1]) / 100 if len(stops) > 1 else 0.0
            asof = lt.get("as_of") or lt.get("t")
            out.append(f" LIGHTER  (scan as of {_when(asof)}, at ${float(lt.get('capital') or 0):,.0f} of capital)")
            for i, r in enumerate(rows, 1):
                out += lighter_line(r, i, stop)
            if not rows:
                out.append("       nothing passes every check")
    out += ["", f"Limits: {LIMITS}.",
            "Paste a line into the trader's Telegram chat. The run is sized from that account's own money, whatever the "
            "scan assumed; `live` needs the live switch on and a code typed back."]
    return "\n".join(out)
