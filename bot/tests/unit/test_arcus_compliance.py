"""Fixes from the 2026-09-25 audit of the Arcus client against docs.arcus.xyz (the source of truth)."""

from __future__ import annotations

import asyncio
from decimal import Decimal as D
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from bot.common.errors import RateLimited
from bot.core.order_manager import BBOTicks, PlanParams
from bot.core.state import StateStore
from bot.venues.base import Side, Venue
from tests.unit.test_ghost_orders import _adapter, _Rest
from tests.unit.test_order_manager import MID, M, _om, _Race, d


class _Throttled(_Race):
    """The order pool is empty: Arcus answers 429 account_empty with retryAfterMs (docs: rate-limits)."""

    async def place(self, reqs: list[object]) -> list[object]:
        from bot.venues.base import OrderStatus

        for r in reqs:          # as ArcusAdapter.place does on a 429: nothing rests, the order is rejected locally
            self.sent.append(f"place {r.client_id}")  # type: ignore[attr-defined]
            self._ack(r.client_id, OrderStatus.REJECTED)  # type: ignore[attr-defined]
        raise RateLimited("arcus", "rate limited", reason="account_empty", retry_after_ms=3000)


async def test_a_429_is_waited_out_not_retried_every_tick(tmp_path: Path) -> None:
    om, ad, clock = _om(tmp_path, _Throttled)
    bbo, p = BBOTicks(MID - 1, MID + 1), PlanParams(tol_ticks=2)
    await om.sync(Venue.ARCUS, M, [d(Side.BUY, MID - 20, "b0")], bbo, p, why="t")  # type: ignore[attr-defined]
    assert len(ad.sent) == 1 and om.backing_off(Venue.ARCUS, "order")  # type: ignore[attr-defined]
    for _ in range(2):                                                     # 2 s later: still inside retryAfterMs
        clock[0] += 1_000_000
        await om.sync(Venue.ARCUS, M, [d(Side.BUY, MID - 21, "b0")], bbo, p, why="t")  # type: ignore[attr-defined]
    assert len(ad.sent) == 1 and not om.backing_off(Venue.ARCUS, "cancel")  # type: ignore[attr-defined]
    clock[0] += 2_000_000                                                   # past the wait
    await om.sync(Venue.ARCUS, M, [d(Side.BUY, MID - 21, "b0")], bbo, p, why="t")  # type: ignore[attr-defined]
    assert len(ad.sent) == 2                                                # and tries again after it


def test_funding_payments_are_read_live_and_counted_once(tmp_path: Path) -> None:
    ad = _adapter(_Rest([]))
    mid = next(iter(ad._markets.values())).venue_market_id
    base = next(iter(ad._markets.values())).base
    row = {"marketId": mid, "marketDisplayName": "X-USD", "fundingRate": "0.0000058", "size": "-0.3",
           "payment": "0.0013", "time": 1790000000000000}
    asyncio.run(ad._on_funding({"isSnapshot": True, "fundingPayments": [row]}, 0, {"type": "subscribed"}))
    assert ad._funding_q.empty()                                           # the snapshot replays history
    asyncio.run(ad._on_funding(dict(row), 0, {"type": "channel_data"}))   # one payment per frame
    fp = ad._funding_q.get_nowait()
    assert (fp.base, fp.payment, fp.rate_h, fp.position_size) == (base, D("0.0013"), D("0.0000058"), D("-0.3"))
    st = StateStore(tmp_path / "s.sqlite")
    assert st.on_funding(fp.venue, fp.base, fp.ts_us, fp.rate_h, fp.position_size, fp.payment) is True
    assert st.on_funding(fp.venue, fp.base, fp.ts_us, fp.rate_h, fp.position_size, fp.payment) is False   # replay


def test_quotes_on_an_off_hours_bound_are_not_sent() -> None:
    """Docs (real-world-assets): fills AT or beyond a trading bound are rejected."""
    from bot.strategies.mm_base import MMBase

    ctx: Any = SimpleNamespace(view=SimpleNamespace(lower_bound=D("700"), upper_bound=D("800")))
    clip = MMBase.clip_to_bounds
    assert clip(None, ctx, Side.BUY, 700.0) is None and clip(None, ctx, Side.BUY, 700.01) == 700.01  # type: ignore[arg-type]
    assert clip(None, ctx, Side.SELL, 800.0) is None and clip(None, ctx, Side.SELL, 799.99) == 799.99  # type: ignore[arg-type]


def test_read_weights_follow_the_documented_tiers() -> None:
    from bot.venues.arcus.rest import WEIGHTS, _weight_key

    assert WEIGHTS[_weight_key("/v1/order/abc123")] == 2 and WEIGHTS[_weight_key("/v1/trade/9")] == 20
    assert WEIGHTS["/v1/account/stats"] == 2 and _weight_key("/v1/bbo/BTC-USD") == "/v1/bbo"


async def test_off_hours_state_comes_from_market_attributes_and_is_not_wiped(tmp_path: Path) -> None:
    """docs: market-data/marketattributes streams every RTH <-> off-hours crossing and band change; the `markets`
    channel does not carry isOutsideRth or the bands today (market-data/markets, "Field coverage"). Nothing consumed
    marketAttributes, and every 5 s markets snapshot reset the bands to None."""
    import dataclasses

    from bot.venues.arcus.ws import ArcusWS
    from bot.venues.lighter_rh.ws import LighterWS
    from tests.helpers import fixture_markets
    from tests.unit.test_telegram import _runner

    runner, _ = await _runner(tmp_path, SimpleNamespace(url="127.0.0.1:9"))  # type: ignore[arg-type]
    mk = dataclasses.replace(fixture_markets()[Venue.ARCUS]["BTC"], is_outside_rth=False, offhours_imf=D("0.1"))
    runner.params = SimpleNamespace(markets={Venue.ARCUS: {"BTC": mk}})  # type: ignore[assignment]
    runner.bases = {"BTC"}
    runner.arcus_ws = ArcusWS("ws://127.0.0.1:9")
    runner.lighter_ws = LighterWS("ws://127.0.0.1:9")
    runner._wire_feeds()
    attrs = {"isSnapshot": False, "entries": [{"marketId": 1, "marketDisplayName": "BTC-USD",
                                               "offHoursInitialMarginFraction": "0.2", "isOutsideRth": True,
                                               "upperTradingBound": "90000", "lowerTradingBound": "80000",
                                               "isUpperInExpansionZone": False, "isLowerInExpansionZone": True}]}
    await runner.arcus_ws._emit("market_attrs", attrs, 0)
    v = runner.hub.view(Venue.ARCUS, "BTC")
    assert v.is_outside_rth and v.upper_bound == D("90000") and v.lower_in_zone
    m2 = runner.params.markets[Venue.ARCUS]["BTC"]
    assert m2.is_outside_rth and m2.offhours_imf == D("0.2")        # the margin check follows the session
    snap = {"1": {"marketId": 1, "marketDisplayName": "BTC-USD", "status": "ONLINE", "fundingRate": "0.00001",
                  "openInterest": "10", "openInterestCap": "2000000"}}   # the channel's own field names
    await runner.arcus_ws._emit("markets", snap, 0)
    assert v.is_outside_rth and v.upper_bound == D("90000")          # not wiped by a snapshot without them
    assert v.oi_cap == D("2000000") and v.status == "ONLINE"
