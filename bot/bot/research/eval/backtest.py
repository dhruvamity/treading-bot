"""Backtest harness over RECORDED data (P3A/P4): loads the recorder's Parquet for the sessions' markets, replays
through the simulator with pessimistic or optimistic fills and both Lighter maker-latency variants, and returns the
metrics. Walk-forward and bootstrap live in gonogo.py."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from bot.common.config import DNSession, MMSession
from bot.core.liveparams import LiveParams
from bot.research.eval.metrics import summarize
from bot.research.sim.engine import SimConfig, Simulator
from bot.research.sim.events import merge, recorded_events
from bot.research.sim.fills import FillMode
from bot.venues.arcus.rest import ArcusRest
from bot.venues.base import Market, Venue
from bot.venues.lighter_rh.rest import LighterRest
from bot.venues.paper.adapter import LatencyModel


def _us(d: str | None) -> int | None:
    if not d:
        return None
    return int(datetime.fromisoformat(d).replace(tzinfo=UTC).timestamp() * 1e6)


async def current_markets() -> dict[Venue, dict[str, Market]]:
    """Market parameters for the simulator. Uses today's live values; param_changes records history for audits."""
    a, lt = ArcusRest("https://api.arcus.xyz"), LighterRest("https://api.rh.lighter.xyz")
    try:
        lp = LiveParams(arcus=a, lighter=lt, out_dir=Path("/tmp/bot_bt_params"))
        await lp.refresh()
        return lp.markets
    finally:
        await a.close()
        await lt.close()


def run_backtest(sessions: list[MMSession | DNSession], data: Path, *, start: str | None, end: str | None,
                 fill_mode: str = "pessimistic", lighter_maker_ms: float = 200, markets: dict[Venue, dict[str, Market]] | None = None,
                 capital: float | None = None) -> dict[str, Any]:
    mk = markets or asyncio.run(current_markets())
    s_us, e_us = _us(start), _us(end)
    streams = []
    for s in sessions:
        venues = [Venue.ARCUS, Venue.LIGHTER_RH] if isinstance(s, DNSession) or getattr(s, "mode", "") == "blend" \
            else [Venue(s.venue), Venue.LIGHTER_RH if s.venue == "arcus" else Venue.ARCUS]
        for v in venues:
            streams.append(recorded_events(data, v, s.market.upper(), start_us=s_us, end_us=e_us))
    events = list(merge(streams))
    if not events:
        return {"error": "no recorded events in range; run `bot record` first"}
    cfg = SimConfig(fill_mode=FillMode(fill_mode),
                    latency={Venue.ARCUS: LatencyModel.arcus(), Venue.LIGHTER_RH: LatencyModel.lighter(maker_ms=lighter_maker_ms)})
    sim = Simulator(sessions, mk, cfg)
    res = sim.run_sync(events)
    cap = capital or sum(float(getattr(s, "capital_usd", 0) or getattr(s, "collateral_per_leg_usd", 0) * 2) for s in sessions)
    out = summarize(res, cap)
    out.update({"events": len(events), "from_us": events[0].ts_us, "to_us": events[-1].ts_us, "fill_mode": fill_mode,
                "lighter_maker_ms": lighter_maker_ms,
                "breakdown": {k: v.as_dict() for k, v in res.breakdown.items()},
                "equity_final": float(sum((pv.equity() for pv in sim.venues.values()), Decimal(0)))})
    return out
