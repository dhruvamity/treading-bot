"""Shared test helpers: real market parameters from the live fixtures, session builders."""

from __future__ import annotations

import json
from pathlib import Path

from bot.common.config import MMSession
from bot.venues.arcus.models import base_fee_tier
from bot.venues.arcus.models import parse_market as parse_arcus
from bot.venues.base import Market, Venue
from bot.venues.lighter_rh.models import parse_market as parse_lighter

FIX = Path(__file__).parent / "fixtures" / "live"


def fixture_markets() -> dict[Venue, dict[str, Market]]:
    fees = json.loads((FIX / "arcus_feetiers.json").read_text())
    mk, tk = base_fee_tier(fees)
    a = {m.base: m for m in (parse_arcus(x, maker_fee=mk, taker_fee=tk)
                             for x in json.loads((FIX / "arcus_markets.json").read_text())["markets"])}
    obs = json.loads((FIX / "lighter_orderbooks.json").read_text())["order_books"]
    det = {d["market_id"]: d for d in json.loads((FIX / "lighter_obd_btc.json").read_text())["order_book_details"]}
    lt = {m.base: m for m in (parse_lighter(o, det.get(o["market_id"])) for o in obs if o["market_type"] == "perp")}
    return {Venue.ARCUS: a, Venue.LIGHTER_RH: lt}


def mm_session(**kw: object) -> MMSession:
    base = dict(session_id="t", venue="arcus", account_index=1, market="BTC", mode="grid", capital_usd=35,
                spacing_bps=10, levels_per_side=3, order_size_usd=6, inventory_cap_usd=30)
    base.update(kw)
    return MMSession.model_validate(base)
