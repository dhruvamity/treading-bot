"""Public, read-only checks against the real venues. Excluded by default; run with `pytest -m network`.

No credentials, no writes. These catch venue-side drift (renamed fields, changed tick sizes, WS format changes)
that the offline fixtures cannot."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from bot.common.config import load_arcus_config, load_lighter_config
from bot.core.liveparams import LiveParams
from bot.venues.arcus.rest import ArcusRest
from bot.venues.arcus.ws import ArcusWS
from bot.venues.base import Venue
from bot.venues.lighter_rh.rest import LighterRest
from bot.venues.lighter_rh.ws import LighterWS

pytestmark = pytest.mark.network
ROOT = Path(__file__).parents[2]


@pytest.fixture
def cfgs() -> tuple[object, object]:
    a = load_arcus_config(ROOT / "config/venues/arcus.yaml")
    lt = load_lighter_config(ROOT / "config/venues/lighter_rh.yaml")
    a.env = lt.env = "mainnet"
    return a, lt


async def test_liveparams_against_both_venues(cfgs: tuple[object, object], tmp_path: Path) -> None:
    a, lt = cfgs
    ar, lr = ArcusRest(a.rest_url()), LighterRest(lt.rest_url())  # type: ignore[attr-defined]
    try:
        lp = LiveParams(arcus=ar, lighter=lr, out_dir=tmp_path)
        await lp.refresh()
        btc_a, btc_l = lp.get(Venue.ARCUS, "BTC"), lp.get(Venue.LIGHTER_RH, "BTC")
        assert btc_a.tick_size > 0 and btc_a.step_size > 0 and btc_a.min_notional > 0
        assert btc_l.tick_size > 0 and btc_l.min_size > 0
        assert lp.symbol_map.has("SPY", Venue.ARCUS) and lp.symbol_map.has("SPY", Venue.LIGHTER_RH)
    finally:
        await ar.close()
        await lr.close()


async def test_ws_books_sync(cfgs: tuple[object, object]) -> None:
    a, lt = cfgs
    aw, lw = ArcusWS(a.ws_url(), n_levels=20), LighterWS(lt.ws_url(), readonly=True)  # type: ignore[attr-defined]
    await aw.subscribe_market("BTC-USD")
    await lw.subscribe_market(1, "BTC")
    aw.start()
    lw.start()
    try:
        for _ in range(100):
            if aw.books["BTC-USD"].book.mid() is not None and lw.books[1].book.mid() is not None:
                break
            await asyncio.sleep(0.1)
        ma, ml = aw.books["BTC-USD"].book.mid(), lw.books[1].book.mid()
        assert ma is not None and ml is not None
        assert abs(ma - ml) / ml < 0.01  # same asset within 1 %
    finally:
        await aw.stop()
        await lw.stop()
