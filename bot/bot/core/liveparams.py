"""[LIVE] venue parameters (A2.5): fetched at start-up and hourly, diffed, every change logged and written to
data/param_changes/ (Parquet via the recorder writer when available, JSONL otherwise).

Tracked fields per market: status, tick/step, tiers, minimums, margins, fees, OI cap, RTH, max leverage.
Prices, funding and OI are NOT params (they are market data) and are excluded from the diff.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import orjson

from bot.common.logging import Log
from bot.common.time import now_us, utc_date_str
from bot.venues.arcus.models import base_fee_tier
from bot.venues.arcus.models import parse_market as parse_arcus_market
from bot.venues.arcus.rest import ArcusRest
from bot.venues.base import Market, Venue
from bot.venues.lighter_rh.models import parse_market as parse_lighter_market
from bot.venues.lighter_rh.rest import LighterRest
from bot.venues.symbols import SymbolMap

log = Log("liveparams")

PARAM_FIELDS = ("status", "tick_size", "tick_tiers", "step_size", "min_notional", "min_size", "max_size", "imf",
                "mmf", "offhours_imf", "close_out_mf", "rth", "maker_fee", "taker_fee", "oi_cap_usd",
                "size_decimals", "price_decimals", "multiplier", "liquidation_fee")


@dataclass(frozen=True, slots=True)
class ParamChange:
    ts_us: int
    venue: str
    market: str
    field: str
    old_value: str
    new_value: str


def diff_markets(old: dict[str, Market], new: dict[str, Market], ts_us: int) -> list[ParamChange]:
    out: list[ParamChange] = []
    for base, m in new.items():
        o = old.get(base)
        if o is None:
            out.append(ParamChange(ts_us, m.venue.value, base, "__listed__", "", m.venue_symbol))
            continue
        for f in PARAM_FIELDS:
            a, b = getattr(o, f), getattr(m, f)
            if a != b:
                out.append(ParamChange(ts_us, m.venue.value, base, f, str(a), str(b)))
    for base, o in old.items():
        if base not in new:
            out.append(ParamChange(ts_us, o.venue.value, base, "__delisted__", o.venue_symbol, ""))
    return out


@dataclass
class LiveParams:
    arcus: ArcusRest | None = None
    lighter: LighterRest | None = None
    out_dir: Path = Path("data/param_changes")
    refresh_s: float = 3600.0
    markets: dict[Venue, dict[str, Market]] = field(default_factory=dict)
    arcus_feetiers: dict[str, Any] = field(default_factory=dict)
    symbol_map: SymbolMap = field(default_factory=SymbolMap)
    listeners: list[Callable[[list[ParamChange]], None]] = field(default_factory=list)
    last_refresh_us: int = 0
    perps_only: bool = True

    async def refresh(self) -> list[ParamChange]:
        ts = now_us()
        changes: list[ParamChange] = []
        if self.arcus is not None:
            new_a = await self._fetch_arcus()
            changes += diff_markets(self.markets.get(Venue.ARCUS, {}), new_a, ts)
            self.markets[Venue.ARCUS] = new_a
        if self.lighter is not None:
            new_l = await self._fetch_lighter()
            changes += diff_markets(self.markets.get(Venue.LIGHTER_RH, {}), new_l, ts)
            self.markets[Venue.LIGHTER_RH] = new_l
        self.symbol_map = SymbolMap.build([m for per in self.markets.values() for m in per.values()])
        self.last_refresh_us = ts
        if changes:
            self._persist(changes)
            for fn in self.listeners:
                fn(changes)
            initial = sum(1 for c in changes if c.field == "__listed__")
            log.info("param_changes", data={"n": len(changes), "initial_listings": initial,
                                            "non_listing": [asdict(c) for c in changes if c.field != "__listed__"][:50]})
        return changes

    async def _fetch_arcus(self) -> dict[str, Market]:
        assert self.arcus is not None
        fees = await self.arcus.feetiers()
        self.arcus_feetiers = fees
        maker, taker = base_fee_tier(fees)
        out = {}
        for m in await self.arcus.markets():
            if m.get("type", "PERPETUAL") != "PERPETUAL":
                continue
            mk = parse_arcus_market(m, maker_fee=maker, taker_fee=taker)
            out[mk.base] = mk
        return out

    async def _fetch_lighter(self) -> dict[str, Market]:
        assert self.lighter is not None
        obs = await self.lighter.order_books()
        details = {int(d["market_id"]): d for d in await self.lighter.order_book_details()}
        out = {}
        for ob in obs:
            if self.perps_only and ob.get("market_type") != "perp":
                continue
            mk = parse_lighter_market(ob, details.get(int(ob["market_id"])))
            out[mk.base] = mk
        return out

    def _persist(self, changes: Sequence[ParamChange]) -> None:
        d = self.out_dir / f"date={utc_date_str(changes[0].ts_us)}"
        d.mkdir(parents=True, exist_ok=True)
        with (d / "changes.jsonl").open("ab") as fh:
            for c in changes:
                fh.write(orjson.dumps(asdict(c)) + b"\n")

    def get(self, venue: Venue, base: str) -> Market:
        return self.markets[venue][base.upper()]

    async def run_forever(self) -> None:
        while True:
            try:
                await self.refresh()
            except Exception as e:  # keep the last good snapshot; retry sooner
                log.error("liveparams_refresh_failed", reason=type(e).__name__, data={"err": str(e)[:200]})
                await asyncio.sleep(60)
                continue
            await asyncio.sleep(self.refresh_s)
