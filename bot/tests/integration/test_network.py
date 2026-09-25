"""Public, read-only checks against Arcus. Excluded by default; run with `pytest -m network`.

No credentials, no writes. These catch venue-side drift (renamed fields, changed tick sizes, WS format changes)
that the offline fixtures cannot."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from bot.common.config import ArcusVenueConfig, load_arcus_config
from bot.core.liveparams import LiveParams
from bot.venues.arcus.rest import ArcusRest
from bot.venues.arcus.ws import ArcusWS
from bot.venues.base import Venue

pytestmark = pytest.mark.network
ROOT = Path(__file__).parents[2]


@pytest.fixture
def cfg() -> ArcusVenueConfig:
    a = load_arcus_config(ROOT / "config/venues/arcus.yaml")
    a.env = "mainnet"
    return a


async def test_liveparams_against_arcus(cfg: ArcusVenueConfig, tmp_path: Path) -> None:
    ar = ArcusRest(cfg.rest_url())
    try:
        lp = LiveParams(arcus=ar, out_dir=tmp_path)
        await lp.refresh()
        btc = lp.get(Venue.ARCUS, "BTC")
        assert btc.tick_size > 0 and btc.step_size > 0 and btc.min_notional > 0
        assert lp.symbol_map.has("SPY", Venue.ARCUS)
    finally:
        await ar.close()


async def test_ws_book_syncs(cfg: ArcusVenueConfig) -> None:
    aw = ArcusWS(cfg.ws_url(), n_levels=20)
    await aw.subscribe_market("BTC-USD")
    aw.start()
    try:
        for _ in range(100):
            if aw.books["BTC-USD"].book.mid() is not None:
                break
            await asyncio.sleep(0.1)
        assert aw.books["BTC-USD"].book.mid() is not None
    finally:
        await aw.stop()
