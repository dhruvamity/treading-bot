"""Ghost orders: orders the bot believes rest but Arcus does not have. The live run on 2026-09-25 kept four of them
(their placement errored, so no ack ever came), modified them by clientId every second ("Rejected Modification ...
Order could not be found" in the Arcus UI, ~4 requests/s) and never placed a real quote again."""

from __future__ import annotations

import asyncio
import time
from decimal import Decimal as D
from pathlib import Path
from typing import Any

from bot.core.state import PENDING_GRACE_S, StateStore
from bot.venues.arcus.adapter import ArcusAdapter
from bot.venues.base import TIF, OrderRequest, OrderState, OrderStatus, Side, Venue
from tests.helpers import fixture_markets


def _req(cid: str, side: Side = Side.BUY, px: str = "86000.0") -> OrderRequest:
    return OrderRequest(Venue.ARCUS, "BTC", side, D(px), D("0.0002"), TIF.POST_ONLY, client_id=cid, tag="b0")


def test_reconcile_clears_an_unacknowledged_order_after_the_grace(tmp_path: Path) -> None:
    st = StateStore(tmp_path / "s.sqlite")
    st.on_intent(_req("old"))
    st.on_intent(_req("new"))
    st.orders["old"].created_us = st.orders["old"].updated_us = int((time.time() - PENDING_GRACE_S - 5) * 1e6)
    rep = st.reconcile(Venue.ARCUS, [], [])
    assert rep.missing_local == ["old"]                                  # sent 15 s ago, never acked, not listed
    assert st.orders["old"].status is OrderStatus.CANCELED and st.orders["old"].reject_reason == "reconcile_missing"
    assert st.orders["new"].status is OrderStatus.PENDING_NEW            # just sent: may still land


def test_reconcile_confirms_an_unacknowledged_order_the_venue_lists(tmp_path: Path) -> None:
    st = StateStore(tmp_path / "s.sqlite")
    st.on_intent(_req("a1"))
    st.orders["a1"].created_us = int((time.time() - 60) * 1e6)
    rep = st.reconcile(Venue.ARCUS, [OrderState("a1", "o1", OrderStatus.OPEN, D(0), None, None, 0, Venue.ARCUS)], [])
    assert rep.clean and st.orders["a1"].status is OrderStatus.OPEN and st.orders["a1"].venue_order_id == "o1"


class _Rest:
    def __init__(self, place_rows: list[dict[str, Any]], open_rows: list[dict[str, Any]] | None = None) -> None:
        self.place_rows, self.open_rows = place_rows, open_rows or []

    async def batch_place(self, account_index: int, chunk: Any) -> dict[str, Any]:
        return {"responses": self.place_rows}

    async def place_order(self, account_index: int, f: Any, cid: str | None) -> dict[str, Any]:
        return self.place_rows[0]

    async def open_orders(self, address: str, account_index: int) -> list[dict[str, Any]]:
        return self.open_rows


def _adapter(rest: _Rest) -> ArcusAdapter:
    mk = fixture_markets()[Venue.ARCUS]
    return ArcusAdapter(rest, None, address="0x" + "ab" * 20, account_index=0, markets=mk)  # type: ignore[arg-type]


def test_batch_replies_are_matched_by_client_id_not_row_order() -> None:
    mid = fixture_markets()[Venue.ARCUS]["BTC"].venue_market_id
    rows = [{"orderId": "id-b", "clientId": "b", "status": "ACK", "marketId": mid},   # the venue's order, not ours
            {"orderId": "id-a", "clientId": "a", "status": "ACK", "marketId": mid}]
    ad = _adapter(_Rest(rows))
    asyncio.run(ad.place([_req("a"), _req("b", Side.SELL, "86100.0")]))
    assert ad._live["a"].order_id == "id-a" and ad._live["b"].order_id == "id-b"


def test_reconciliation_teaches_the_adapter_an_order_id_it_missed() -> None:
    mid = fixture_markets()[Venue.ARCUS]["BTC"].venue_market_id
    ad = _adapter(_Rest([{"status": "ACK"}], [{"orderId": "id-a", "clientId": "a", "status": "OPEN", "marketId": mid,
                                                "side": "BUY", "price": "86000.0", "originalSize": "0.0002",
                                                "remainingSize": "0.0002", "timeInForce": "ALO"}]))
    asyncio.run(ad.place([_req("a")]))
    assert ad._live["a"].order_id is None                               # the reply carried no id (an errored place)
    asyncio.run(ad.open_orders())
    assert ad._live["a"].order_id == "id-a"                              # modifies now go by orderId


# ------------------------------------------------------------------------------------------------ requote by cancel + place
def test_arcus_requotes_with_cancel_and_place_by_default() -> None:
    """Every live modify was refused (CannotModifyImmutableFieldTif) on 2026-09-25: Arcus requotes go out as cancel +
    place until a testnet selftest shows modifies land."""
    from bot.core.order_manager import ActionKind, BBOTicks, DesiredOrder, LiveOrderView, PlanParams, plan

    assert _adapter(_Rest([])).use_modify is False
    m = fixture_markets()[Venue.ARCUS]["BTC"]
    live = [LiveOrderView("a", Side.BUY, 859_950, 12_000, "b0", False, False)]
    want = [DesiredOrder(Side.BUY, 859_900, 12_000, "b0")]
    acts = plan(want, live, m, BBOTicks(860_000, 860_001), PlanParams(tol_ticks=2, allow_modify=False))
    assert [a.kind for a in acts] == [ActionKind.CANCEL, ActionKind.PLACE] and acts[0].client_id == "a"


def test_the_engine_takes_the_venues_modify_choice(tmp_path: Path) -> None:
    from bot.strategies.base import StrategyOutput
    from tests.unit.test_sizing import _engine

    eng, _ = _engine(tmp_path, 100)
    seen: list[bool] = []

    async def fake_sync(v: Any, m: Any, desired: Any, bbo: Any, params: Any, *, why: str) -> Any:
        from bot.core.order_manager import SyncResult

        seen.append(params.allow_modify)
        return SyncResult()

    eng.om.sync = fake_sync  # type: ignore[method-assign]
    eng.hub.view(Venue.ARCUS, "BTC")
    out = StrategyOutput(reason="t")
    out.set(Venue.ARCUS, "BTC", [])
    asyncio.run(eng.apply(out, None, 0))
    eng.om.adapters[Venue.ARCUS].use_modify = False
    asyncio.run(eng.apply(out, None, 0))
    assert seen == [True, False]                     # paper venues modify; an Arcus adapter says no


# ------------------------------------------------------------------------------------------------ account channel shapes
# Shapes as the live `orders` / `userFills` channels sent them (captured 2026-09-25; values made up here).
ORDER_ROW = {"address": "0xabc", "clientId": "a", "createdAt": 1790000000000000, "filledSize": "0",
             "goodTilTime": "1793000000000000", "marketDisplayName": "BTC-USD", "marketId": 1, "orderId": "id-a",
             "originalSize": "0.0002", "price": "86000.0", "remainingSize": "0.0002", "sequenceNumber": 7,
             "side": "BUY", "status": "OPEN", "timeInForce": "ALO", "type": "LIMIT", "updatedAt": 1790000000000001}


def test_a_streaming_order_update_is_one_object_and_is_applied() -> None:
    """The docs (channels#orders) and the live feed send ONE order object per `channel_data` frame. The parser only
    read {"orders": [...]}: every ack, cancel and fill status was dropped, so no order was ever acknowledged."""
    ad = _adapter(_Rest([{"orderId": "id-a", "clientId": "a", "status": "ACK"}]))
    asyncio.run(ad.place([_req("a")]))
    asyncio.run(ad._on_orders(dict(ORDER_ROW), 0, {"type": "channel_data", "channel": "orders"}))
    st = ad._orders_q.get_nowait()
    assert st.client_id == "a" and st.status is OrderStatus.OPEN and st.venue_order_id == "id-a"
    assert ArcusAdapter.order_rows({"orders": [ORDER_ROW]}) == [ORDER_ROW]      # older shapes still read
    assert ArcusAdapter.order_rows([ORDER_ROW]) == [ORDER_ROW]


def test_the_orders_snapshot_applies_open_orders_and_only_our_closed_ones() -> None:
    closed_ours = {**ORDER_ROW, "clientId": "b", "orderId": "id-b", "status": "CANCELED"}
    closed_old = {**ORDER_ROW, "clientId": "old", "orderId": "id-old", "status": "FILLED"}
    snap = {"isSnapshot": True, "lastSequenceId": 9, "openOrders": [ORDER_ROW],
            "recentClosedOrders": [closed_ours, closed_old]}
    rows = ArcusAdapter.order_rows(snap, {"a": object(), "b": object()})
    assert [r["clientId"] for r in rows] == ["a", "b"]   # a closed order we no longer track is not replayed


def test_fill_frames_in_every_documented_shape() -> None:
    live = {"accountIndex": 0, "address": "0xabc", "clientId": "a", "closedPnl": "0", "createdAt": 1790000000000000,
            "fee": "0", "marketDisplayName": "BTC-USD", "marketId": 1, "orderId": "id-a", "originalSize": "0.0002",
            "positionEffect": "OPEN_LONG", "price": "86000.0", "remainingSize": "0", "role": "MAKER",
            "sequenceNumber": 8, "side": "BUY", "size": "0.0002", "tradeId": "t1"}
    assert ArcusAdapter.fill_rows({"isSnapshot": False, "fills": [live]}) == [live]    # as seen live
    docs = {"isSnapshot": False, "tradeId": "t2", "orderId": "ord-99", "market": "BTC-USD", "side": "BUY",
            "fillPrice": "50000.00", "fillSize": "0.01"}                                  # the docs' example
    assert ArcusAdapter.fill_rows(docs) == [docs]
    from bot.venues.arcus.models import parse_fill

    f = parse_fill(docs, {})
    assert (f.base, f.price, f.size, f.trade_id) == ("BTC", D("50000.00"), D("0.01"), "t2")
    g = parse_fill(live, {1: "BTC"})
    assert (g.base, g.is_maker, g.client_id) == ("BTC", True, "a")


def test_a_fills_snapshot_is_not_replayed_as_new_fills() -> None:
    ad = _adapter(_Rest([]))
    asyncio.run(ad._on_fills({"isSnapshot": True, "fills": [{"tradeId": "t1"}]}, 0, {"type": "subscribed"}))
    assert ad._fills_q.empty()
