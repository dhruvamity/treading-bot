"""A paper record of funding arbitrage positions: what one would have earned, from the venues' published rates and
prices. No order is sent; the entry is taken at each venue's mid when it is opened, less the assumed cost of the fills.

state/paper.json holds the open positions, state/paper_history.jsonl the closed ones.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from arbitrage.rank import Plan, Settings


@dataclass
class Position:
    symbol: str
    short_venue: str
    long_venue: str
    size: float
    notional: float
    opened_at: float
    entry: dict[str, float]        # venue -> mid at the open
    stop_dist: float
    fill_cost_bp: float


def load(path: Path) -> dict[str, Position]:
    try:
        return {k: Position(**v) for k, v in json.loads(path.read_text()).items()}
    except (OSError, ValueError, TypeError):
        return {}


def save(path: Path, book: dict[str, Position]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({k: asdict(v) for k, v in book.items()}, indent=1))


def open_position(p: Plan, mids: dict[str, float], s: Settings, now: float | None = None) -> Position:
    return Position(symbol=p.symbol, short_venue=p.short_venue, long_venue=p.long_venue, size=p.size,
                    notional=p.notional, opened_at=time.time() if now is None else now, entry=dict(mids),
                    stop_dist=p.stop_dist, fill_cost_bp=s.fill_cost_bp)


def funding_earned(pos: Position, hist: dict[str, dict[int, float]], now: float) -> tuple[float, int]:
    """(dollars, payments): every hourly payment since the open that both venues have published. A payment at hour H
    counts when the position was open before H."""
    short, long_ = hist[pos.short_venue], hist[pos.long_venue]
    hours = [h for h in sorted(set(short) & set(long_)) if pos.opened_at < h <= now]
    return sum((short[h] - long_[h]) * pos.notional for h in hours), len(hours)


def mark(pos: Position, mids: dict[str, float], hist: dict[str, dict[int, float]], now: float,
         closing: bool = False) -> dict[str, float]:
    """The position's result so far: funding, the two legs' price result (their sum is the basis move), fill costs."""
    funding, n = funding_earned(pos, hist, now)
    long_pnl = pos.size * (mids[pos.long_venue] - pos.entry[pos.long_venue])
    short_pnl = pos.size * (pos.entry[pos.short_venue] - mids[pos.short_venue])
    fills = 4 if closing else 2
    cost = pos.notional * fills * pos.fill_cost_bp / 1e4
    move = max(abs(mids[v] / pos.entry[v] - 1) for v in mids)
    return {"funding": funding, "payments": n, "basis": long_pnl + short_pnl, "long_leg": long_pnl,
            "short_leg": short_pnl, "cost": cost, "net": funding + long_pnl + short_pnl - cost,
            "hours": (now - pos.opened_at) / 3600, "move": move, "stop_hit": float(move >= pos.stop_dist)}


def log_close(path: Path, pos: Position, result: dict[str, float], now: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        fh.write(json.dumps({**asdict(pos), "closed_at": now, **result}) + "\n")
