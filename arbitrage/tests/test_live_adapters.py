"""The live adapters with the network replaced: what they would send to Arcus and to Lighter, and how they read the
answers. No request leaves the machine; the signatures are made with throwaway keys."""

from __future__ import annotations

import json
import time
from decimal import Decimal as D
from pathlib import Path
from typing import Any

import pytest

from arbitrage.exec.arcus import ArcusTrade, tpsl_orders
from arbitrage.exec.lighter import LighterTrade, order_view
from arbitrage.exec.venue import BUY, SELL, VenueDown

# ------------------------------------------------------------------------------------------------ Arcus
ADDR = "0x" + "ab" * 20


def arcus() -> tuple[ArcusTrade, Any]:
    from arcus.common.ids import ClientIdFactory
    from arcus.venues.arcus.adapter import ArcusAdapter
    from arcus.venues.arcus.rest import ArcusRest
    from arcus.venues.arcus.signing import ArcusSigner
    from arcus.venues.base import Market, Venue

    m = Market(venue=Venue.ARCUS, base="BABA", venue_symbol="BABA-USD", venue_market_id=17, status="ONLINE",
               category="EQUITIES", tick_size=D("0.01"), tick_tiers=(), step_size=D("0.0000001"),
               min_notional=D("5"), min_size=D("0.01"), max_size=None, imf=D("0.1"), mmf=D("0.066667"),
               offhours_imf=D("0.15"), rth=None, maker_fee=D("0"), taker_fee=D("0.000225"), oi_cap_usd=None,
               max_leverage=D("10"))
    rest = ArcusRest("https://offline.invalid", signer=ArcusSigner("11" * 32), address=ADDR, writes_allowed=True)
    posted: list[tuple[str, dict[str, Any]]] = []
    answers: dict[str, Any] = {}

    async def post(path: str, body: dict[str, Any], ts: int, sig: str, client_id: str | None = None) -> Any:
        posted.append((path, body))
        a = answers.get(path, {"orderId": f"o{len(posted)}", "status": "OPEN"})
        if isinstance(a, Exception):
            raise a
        return a(body) if callable(a) else a

    async def get(path: str, params: dict[str, Any] | None = None, **kw: Any) -> Any:
        return answers.get(path, {"order": {"orderId": path.rsplit("/", 1)[-1], "status": "OPEN", "filledSize": "0"}})

    rest._post_signed = post                                              # type: ignore[method-assign]
    rest._get = get                                                       # type: ignore[method-assign]
    t = ArcusTrade(Path("/nonexistent"))
    t.rest, t.address, t.account, t.markets = rest, ADDR, 0, {"BABA": m}
    t.adapter = ArcusAdapter(rest, None, address=ADDR, account_index=0, markets=t.markets)
    t._ids = ClientIdFactory("manual", 1)
    t.posted, t.answers = posted, answers                                 # type: ignore[attr-defined]
    return t, rest


async def test_arcus_orders_are_post_only_or_ioc_on_the_tick_and_step() -> None:
    t, _ = arcus()
    cid = await t.maker("BABA", SELL, 5.18514321999, 106.4249, False)
    path, body = t.posted[0]                                              # type: ignore[attr-defined]
    assert path == "/v1/placeOrder" and body["orderSide"] == "SELL" and body["timeInForce"] == "ALO"
    assert body["price"] == "106.43" and body["quantity"] == "5.1851432"   # an ask rounds up to the tick; size down
    assert "reduceOnly" not in body and body["clientId"] == cid and body["marketId"] == 17
    info = await t.order("BABA", cid)                                     # its state is read by Arcus's order id
    assert info is not None and info.open and info.side == SELL and info.price == 106.43
    t.answers["/v1/order/o1"] = {"orderId": "o1", "status": "PARTIALLY_FILLED", "filledSize": "1.5",  # type: ignore[attr-defined]
                                 "avgFillPrice": "106.43"}
    info = await t.order("BABA", cid)
    assert info is not None and info.open and info.filled == 1.5
    gone = {"order": {"orderId": "o1", "status": "CANCELED", "filledSize": "1.5"}}
    t.answers["/v1/order/o1"] = gone                                      # type: ignore[attr-defined]
    info = await t.order("BABA", cid)
    assert info is not None and not info.open and info.filled == 1.5      # cancelled with a part filled: counted
    t.answers["/v1/placeOrder"] = {"orderId": "o9", "status": "FILLED", "filledSize": "2", "avgFillPrice": "106.1"}  # type: ignore[attr-defined]
    tid = await t.taker("BABA", BUY, 2.0, 106.99, True)
    _, body = t.posted[-1]                                                # type: ignore[attr-defined]
    assert (body["timeInForce"], body["price"], body["reduceOnly"]) == ("IOC", "106.99", True)
    done = await t.order("BABA", tid)
    assert done is not None and not done.open and done.filled == 2.0 and done.avg_px == 106.1


async def test_arcus_refusals_and_outages_are_told_apart() -> None:
    from arcus.common.errors import OrderRejected, VenueError

    t, _ = arcus()
    t.answers["/v1/placeOrder"] = OrderRejected("arcus", "POST_ONLY_WOULD_CROSS", "x")     # type: ignore[attr-defined]
    cid = await t.maker("BABA", BUY, 1.0, 106.40, False)
    info = await t.order("BABA", cid)
    assert info is not None and not info.open and "POST_ONLY_WOULD_CROSS" in info.note     # nothing rests: place again
    t.answers["/v1/placeOrder"] = VenueError("arcus", "502", status=502, retryable=True)    # type: ignore[attr-defined]
    with pytest.raises(VenueDown):
        await t.maker("BABA", BUY, 1.0, 106.40, False)              # it may have landed: the engine checks


async def test_arcus_position_stops_are_one_signed_bundle_that_closes_the_whole_position() -> None:
    from arcus.venues.arcus import signing as sg

    legs = tpsl_orders(ADDR, 0, 17, -5.2, D("113.297"), D("99.5632"), lambda px, down: px.quantize(D("0.01")), 9, 1)
    assert [(x["tpslType"], x["orderSide"], x["quantity"], x["reduceOnly"]) for x in legs] == [
        ("STOP_LOSS", "BUY", "0", True), ("TAKE_PROFIT", "BUY", "0", True)]                # a short is closed by buying
    assert D(legs[0]["price"]) > D(legs[0]["stopPrice"]) and legs[0]["orderType"] == "MARKET"
    long_legs = tpsl_orders(ADDR, 0, 17, 5.2, D("99.56"), D("113.29"), lambda px, down: px.quantize(D("0.01")), 9, 1)
    assert long_legs[0]["orderSide"] == "SELL" and D(long_legs[0]["price"]) < D(long_legs[0]["stopPrice"])

    t, rest = arcus()
    t.answers["/v1/batchPlaceOrders"] = {"responses": [{"orderId": "s1"}, {"orderId": "s2"}]}   # type: ignore[attr-defined]
    assert await t.set_stops("BABA", -5.2, 113.297, 99.5632)
    path, body = t.posted[-1]                                             # type: ignore[attr-defined]
    assert path == "/v1/batchPlaceOrders" and body["grouping"] == "positionTpsl" and len(body["orders"]) == 2
    leg = body["orders"][0]
    f = sg.OrderFields(market_id=17, side="BUY", price=D(leg["price"]), size=D(0), tif="IOC",
                       good_til_us=int(leg["goodTilTime"]), reduce_only=True, tick_size=D("0.01"),
                       step_size=D("0.0000001"))
    payload = sg.place_payload(ADDR, 0, leg["timestamp"], f, None, tpsl=True)
    assert json.loads(payload)["op"] == sg.OP_TPSL                        # signed as a trigger order, not a plain one
    assert leg["signature"] == rest.signer.sign(payload)
    assert t._stops["BABA"] == ["s1", "s2"]
    t.answers["/v1/batchPlaceOrders"] = {"responses": [{"orderId": "s1"}, {"error": "bad stopPrice"}]}  # type: ignore[attr-defined]
    assert not await t.set_stops("BABA", -5.2, 113.297, 99.5632)          # one leg refused: not protected
    assert not await t.set_stops("BABA", 0.0, 1.0, 2.0)


# ------------------------------------------------------------------------------------------------ Lighter
class FakeSigner:
    def __init__(self) -> None:
        self.orders: list[dict[str, Any]] = []
        self.other: list[tuple[str, dict[str, Any]]] = []

    def create_order(self, **kw: Any) -> Any:
        self.orders.append(kw)
        return ("order", kw)

    def cancel_order(self, **kw: Any) -> Any:
        self.other.append(("cancel", kw))
        return ("cancel", kw)

    def cancel_all(self, **kw: Any) -> Any:
        self.other.append(("cancel_all", kw))
        return ("cancel_all", kw)

    def update_leverage(self, **kw: Any) -> Any:
        self.other.append(("leverage", kw))
        return ("leverage", kw)


def lighter(tmp_path: Path) -> tuple[LighterTrade, FakeSigner, list[Any]]:
    from lighter_bot.venue.market import Market
    from lighter_bot.venue.nonce import ClientIds, Nonces

    t = LighterTrade(tmp_path)
    t.market = Market(market_id=19, symbol="BABA", price_decimals=2, size_decimals=4, min_base=0.04, min_quote=10.0,
                      imf_min=1000, imf_default=5000, mmf=600)
    t.account, t.signer = 12345, FakeSigner()
    t.nonces, t.ids = Nonces(tmp_path / "n.txt"), ClientIds(tmp_path / "c.txt")
    sent: list[Any] = []

    async def send(txs: list[Any]) -> None:
        sent.append(txs)

    t._send = send                                                        # type: ignore[method-assign]
    t._pos_at, t._auth_until = time.time(), time.time() + 3600            # no upkeep calls in these tests
    return t, t.signer, sent


async def test_lighter_orders_stops_and_leverage_as_they_would_be_signed(tmp_path: Path) -> None:
    from lighter_bot.venue import consts as C

    t, sg_, sent = lighter(tmp_path)
    cid = await t.maker("BABA", BUY, 5.18514, 106.4049, False)
    o = sg_.orders[-1]
    assert (o["order_type"], o["tif"], o["is_ask"], o["reduce_only"]) == (C.ORDER_LIMIT, C.TIF_POST_ONLY, False, False)
    assert (o["price"], o["size"], o["trigger_price"]) == (10640, 51851, 0)   # a bid rounds down; 4 size decimals
    assert o["client_index"] == int(cid) and len(sent) == 1
    await t.taker("BABA", SELL, 5.1851, 105.87, True)
    o = sg_.orders[-1]
    assert (o["order_type"], o["tif"], o["is_ask"], o["reduce_only"], o["expiry"]) == (
        C.ORDER_MARKET, C.TIF_IOC, True, True, C.IOC_EXPIRY)
    assert await t.set_stops("BABA", 5.1851, 99.5632, 113.297)            # long: both legs sell
    stop, take = sg_.orders[-2], sg_.orders[-1]
    assert (stop["order_type"], take["order_type"]) == (C.ORDER_STOP_LOSS, C.ORDER_TAKE_PROFIT)
    assert stop["is_ask"] and take["is_ask"] and stop["reduce_only"] and take["reduce_only"]
    assert (stop["trigger_price"], take["trigger_price"], stop["size"]) == (9956, 11329, 51851)
    assert stop["price"] < stop["trigger_price"] and stop["tif"] == C.TIF_IOC  # may run 5% past the trigger, no more
    assert len(sent[-1]) == 2                                             # one request for the pair
    await t.set_stops("BABA", -5.1851, 113.297, 99.5632)                  # short: both legs buy, bound above
    assert not sg_.orders[-1]["is_ask"] and sg_.orders[-2]["price"] > sg_.orders[-2]["trigger_price"]
    assert not await t.set_stops("BABA", 0.0, 1.0, 2.0)
    await t.set_leverage("BABA", 6.2)
    assert sg_.other[-1] == ("leverage", {"market": 19, "fraction": t.market.leverage_fraction(7.0),
                                           "nonce": sg_.other[-1][1]["nonce"]})
    await t.set_leverage("BABA", 40.0)                                    # never over the market's 10x
    assert sg_.other[-1][1]["fraction"] == t.market.leverage_fraction(10.0)
    nonces = [o["nonce"] for o in sg_.orders]
    assert nonces == sorted(set(nonces))                                  # strictly rising


async def test_lighter_reads_fills_and_the_position_from_the_account_stream(tmp_path: Path) -> None:
    t, _sg, _sent = lighter(tmp_path)
    cid = int(await t.maker("BABA", BUY, 5.0, 106.40, False))
    ch = "account_orders:19"
    t._on_msg({"channel": ch, "orders": {"19": [{"client_order_index": cid, "status": "open",
                                                 "initial_base_amount": "5.0000", "remaining_base_amount": "3.0000",
                                                 "filled_base_amount": "2.0000", "filled_quote_amount": "212.80"}]}})
    info = await t.order("BABA", str(cid))
    assert info is not None and info.open and info.filled == 2.0 and info.avg_px == pytest.approx(106.40)
    t._on_msg({"channel": ch, "orders": {"19": [{"client_order_index": cid, "status": "canceled-post-only",
                                                 "initial_base_amount": "5.0000", "remaining_base_amount": "3.0000",
                                                 "filled_base_amount": "2.0000"}]}})
    assert not info.open and info.note == "canceled-post-only" and info.filled == 2.0
    t._on_msg({"channel": "account_all:12345", "positions": {"19": {"position": "2.0000", "sign": -1}}})
    assert await t.position("BABA") == -2.0
    t._on_msg({"channel": "account_all:12345", "positions": {"19": {"position": "0.00001", "sign": 1}}})
    assert await t.position("BABA") == 0.0                                # under half a step: flat
    assert order_view({"status": "filled", "initial_base_amount": "5", "remaining_base_amount": "0"}) == (
        5.0, False, "filled")
    assert await t.order("BABA", "999") is None
