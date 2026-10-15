"""The paper day-trading bot's commands as the trading bot's ONE Telegram bot understands them (treading-bot/arcus).

There is no daytrade Telegram bot of its own: the one bot receives `/dt_<name>` (or `/dt <name>`) and calls this
module. Every command only reads. The paper bot sends no orders; it is started and stopped on the server
(`tbot up daytrade`, `tbot down daytrade`).

    /dt_status   what each strategy holds now, and the paper results (the same as `daytrade status`)
    /dt_trades   the last 10 paper trades
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path

from . import STATE
from .bot import summary

LAST = 10


def trades(n: int = LAST, state_dir: Path | None = None, mode: str = "paper") -> str:
    """The last `n` trades of state/<mode>_trades.jsonl, two short lines each (a phone is narrow)."""
    p = (state_dir or STATE) / f"{mode}_trades.jsonl"
    rows: list[dict] = []
    if p.exists():
        for line in p.read_text().splitlines():
            with contextlib.suppress(ValueError):
                rows.append(json.loads(line))
    if not rows:
        return f"no {mode} trades yet"
    out = [f"last {min(n, len(rows))} of {len(rows)} {mode} trades, newest last, after costs (R = the trade's risk; "
           "bp = 0.01% of the position)"]
    for r in rows[-n:]:
        market = str(r.get("slot", "?")).split("-", 1)[0].upper()
        try:
            res = f"{float(r['r']):+.2f}R" if r.get("stop_bp") or "bp" not in r else f"{float(r['bp']):+.1f} bp"
        except (KeyError, TypeError, ValueError):
            res = "?"
        out.append(f"{r.get('entry_utc', '?')} UTC {market} {r.get('side', '?')}")
        out.append(f"  {r.get('entry', '?')} -> {r.get('exit', '?')} {r.get('reason', '')} {res}")
    return "\n".join(out)


def reply(name: str, state_dir: Path | None = None) -> str | None:
    """What /dt_<name> answers, or None when it is not one of ours."""
    if name == "status":
        return summary(state_dir=state_dir or STATE)
    if name == "trades":
        return trades(state_dir=state_dir)
    return None
