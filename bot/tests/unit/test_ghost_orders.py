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
