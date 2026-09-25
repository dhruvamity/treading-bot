"""Balance history: the Arcus account's value over time, kept in state/balances.jsonl.

Written by the scout before each scan (every 30 min), by the live bot every 5 minutes, and by Telegram's /balance.
Each row: ts (UTC seconds), source, account (subaccount), equity, free (free collateral) and net_deposits (Arcus's
lifetime deposits minus withdrawals). Trading PnL is equity - net_deposits, so money moved in or out is never
mistaken for profit or loss.

Appends are one short line each (atomic on macOS and Linux), so the scout, the bot and Telegram can share the file.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DAY = 86_400


@dataclass
class BalanceLog:
    path: Path
    _last: dict[str, float] = field(default_factory=dict)   # source -> last write, this process

    def record(self, *, source: str, equity: float, account_index: int = 0, free: float | None = None,
               net_deposits: float | None = None, ts: float | None = None, min_interval_s: float = 0.0) -> bool:
        """Append one row; with min_interval_s, skip it if this source wrote less than that long ago."""
        ts = time.time() if ts is None else ts
        if min_interval_s and ts - self._last.get(source, -min_interval_s) < min_interval_s:
            return False
        row = {"ts": round(ts, 3), "source": source, "account": account_index, "equity": round(float(equity), 6),
               "free": None if free is None else round(float(free), 6),
               "net_deposits": None if net_deposits is None else round(float(net_deposits), 6)}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as f:
            f.write(json.dumps(row) + "\n")
        self._last[source] = ts
        return True

    def rows(self, since: float | None = None) -> list[dict[str, Any]]:
        try:
            lines = self.path.read_text().splitlines()
        except OSError:
            return []
        out = []
        for ln in lines:
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            if since is None or r["ts"] >= since:
                out.append(r)
        return out

    def latest(self) -> dict[str, Any] | None:
        try:
            with self.path.open("rb") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - 4096))
                tail = f.read().decode(errors="ignore").splitlines()
        except OSError:
            return None
        for ln in reversed(tail):
            try:
                r: dict[str, Any] = json.loads(ln)
                return r
            except ValueError:
                continue
        return None

    def summary(self, now: float | None = None) -> dict[str, Any]:
        """The latest balance and how equity and trading PnL moved over 1, 7 and 30 days."""
        now = time.time() if now is None else now
        rows = self.rows(since=now - 31 * DAY)
        if not rows:
            return {}
        last = rows[-1]
        out: dict[str, Any] = {"latest": last, "rows_30d": len(rows)}
        for label, days in (("1d", 1), ("7d", 7), ("30d", 30)):
            then = next((r for r in rows if r["ts"] >= now - days * DAY), None)
            if then is None or then is last:
                continue
            p_last, p_then = pnl(last), pnl(then)
            out[label] = {"from": then, "equity_change": last["equity"] - then["equity"],
                          "pnl_change": p_last - p_then if p_last is not None and p_then is not None else None,
                          "deposits_change": (last["net_deposits"] - then["net_deposits"])
                          if last.get("net_deposits") is not None and then.get("net_deposits") is not None else None}
        return out


def pnl(row: dict[str, Any]) -> float | None:
    """Lifetime trading PnL: equity minus net deposits (None when Arcus did not say)."""
    nd = row.get("net_deposits")
    return None if nd is None else float(row["equity"]) - float(nd)
