"""Venue REST / WebSocket clients and adapters against an in-process fake server (no network).

Checks: Arcus auth headers + Ed25519 signature verifies server-side, IP-weight accounting, 429 reason parsing, typed
rejections; WS subscription replay, dispatch of the LIVE captured frames, gap-driven resubscribe; the adapter's
place / modify / cancel / DMS paths and stream parsing.
"""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal as D
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web
from cryptography.hazmat.primitives.asymmetric import ed25519

from bot.common.errors import AuthError, GeoRestricted, LiveLockError, OrderRejected, RateLimited, VenueError
from bot.common.ratelimit import RollingWindow, TokenBucket
from bot.venues.arcus import signing as sg
from bot.venues.arcus.adapter import ArcusAdapter
from bot.venues.arcus.rest import ArcusRest
from bot.venues.arcus.ws import ArcusWS
from bot.venues.base import TIF, OrderRequest, OrderStatus, Side, Venue
from bot.venues.ws_base import ReconnectingWS
from tests.helpers import fixture_markets

FIX = Path(__file__).parents[1] / "fixtures" / "live"
MK = fixture_markets()
ADDR = "0x" + "ab" * 20


class FakeServer:
    """Records requests; responses configurable per path."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.responses: dict[str, tuple[int, Any]] = {}
        self.ws_msgs: list[dict[str, Any]] = []
        self.ws_script: list[dict[str, Any]] = []
        self.app = web.Application()
        self.app.router.add_route("*", "/ws", self.ws)
        self.app.router.add_route("*", "/{tail:.*}", self.handle)
        self.runner: web.AppRunner | None = None
        self.url = ""

    async def start(self) -> None:
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
        self.url = f"http://127.0.0.1:{port}"

    async def stop(self) -> None:
        if self.runner:
            await self.runner.cleanup()

    async def handle(self, req: web.Request) -> web.StreamResponse:
        body = await req.read()
        rec = {"method": req.method, "path": req.path, "query": dict(req.query), "headers": dict(req.headers),
               "body": body}
        if req.content_type == "application/x-www-form-urlencoded":
            rec["form"] = dict(await req.post())
        self.requests.append(rec)
        status, payload = self.responses.get(req.path, (200, {"ok": True}))
        if callable(payload):
            payload = payload(rec)
        return web.json_response(payload, status=status)

    async def ws(self, req: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(req)
        for m in self.ws_script:
            await ws.send_str(json.dumps(m))
        async for msg in ws:
            if msg.type.name == "TEXT":
                self.ws_msgs.append(json.loads(msg.data))
        return ws


@pytest.fixture
async def server():  # type: ignore[no-untyped-def]
    s = FakeServer()
    await s.start()
    yield s
    await s.stop()


# ------------------------------------------------------------------------------------------------ rate limiting
async def test_token_bucket_and_windows() -> None:
    t = {"now": 0.0}
    b = TokenBucket(10, 5, clock=lambda: t["now"])
    assert b.try_acquire(10) and not b.try_acquire(1)
    assert b.wait_time(5) == pytest.approx(1.0)
    t["now"] = 1.0
    assert b.try_acquire(5)
    b.charge(20)
    assert b.available() < 0
    b.drain(2)
    assert b.wait_time(1) > 2
    with pytest.raises(ValueError):
        await b.acquire(11)
    w = RollingWindow(3, 60, clock=lambda: t["now"])
    assert w.try_take(3) and not w.try_take(1) and w.remaining() == 0
    t["now"] = 62.0
    assert w.remaining() == 3


# ------------------------------------------------------------------------------------------------ Arcus REST
async def test_arcus_rest_signed_place_and_verify(server: FakeServer) -> None:
    priv, pub = sg.ArcusSigner.generate()
    server.responses["/v1/placeOrder"] = (202, {"orderId": "o1", "status": "ACK", "clientId": "alx1",
                                                "rateLimit": {"pool": "order", "remaining": 19999}})
    rest = ArcusRest(server.url, signer=sg.ArcusSigner(priv), address=ADDR, writes_allowed=True)
    m = MK[Venue.ARCUS]["BTC"]
    f = sg.OrderFields(m.venue_market_id, "BUY", D("86000.0"), D("0.00012"), "ALO", 1_793_000_000_000_000, False,
                       m.tick_size, m.step_size)
    resp = await rest.place_order(1, f, "alx1")
    assert resp["orderId"] == "o1" and rest.pool_remaining["order"] == 19999
    r = server.requests[-1]
    body = json.loads(r["body"])
    assert r["query"]["address"] == ADDR and body["timeInForce"] == "ALO" and body["quantity"] == "0.00012"
    ts = int(r["headers"]["X-Timestamp"])
    assert body["timestamp"] == ts and r["headers"]["X-API-Key"] == pub
    payload = sg.place_payload(ADDR, 1, ts, f, "alx1")
    ed25519.Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub)).verify(bytes.fromhex(r["headers"]["X-Signature"]),
                                                                          payload)
    # batch: per-element signatures, header signature present
    server.responses["/v1/batchPlaceOrders"] = (202, {"responses": [{"orderId": "a"}, {"orderId": "b"}]})
    await rest.batch_place(1, [(f, "c1"), (f, "c2")])
    b = json.loads(server.requests[-1]["body"])
    assert len(b["orders"]) == 2 and all(len(o["signature"]) == 128 for o in b["orders"])
    assert server.requests[-1]["headers"]["X-Signature"] == b["orders"][0]["signature"]
    # scheme 2
    await rest.cancel_all(1, 1)
    r = server.requests[-1]
    msg = sg.scheme2_message(int(r["headers"]["X-Timestamp"]), "cancelAllOrders", json.loads(r["body"]))
    ed25519.Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub)).verify(bytes.fromhex(r["headers"]["X-Signature"]), msg)
    await rest.schedule_cancel(1, 123)
    assert json.loads(server.requests[-1]["body"])["time"] == 123
    await rest.schedule_cancel(1, None)
    assert "time" not in json.loads(server.requests[-1]["body"])
    await rest.set_leverage(1, 1, 3, isolated=False)
    await rest.cancel_order(1, 1, client_id="alx1")
    assert json.loads(server.requests[-1]["body"])["kind"] == "clientId"
    await rest.batch_cancel(1, [(1, "oid", None), (1, None, "cid")])
    kinds = [c["kind"] for c in json.loads(server.requests[-1]["body"])["cancels"]]
    assert kinds == ["orderId", "clientId"]
    await rest.modify_order(1, f, order_id="o1", client_id="alx1")
    assert json.loads(server.requests[-1]["body"])["orderId"] == "o1"
    await rest.close()


async def test_arcus_rest_errors_and_weights(server: FakeServer) -> None:
    rest = ArcusRest(server.url, ip_bucket=TokenBucket(100, 100))
    server.responses["/v1/markets"] = (200, {"markets": [{"marketId": 1}]})
    before = rest.ip.available()
    assert await rest.markets() == [{"marketId": 1}]
    assert before - rest.ip.available() >= 19  # weight 20
    server.responses["/v1/fundingRates"] = (200, {"fundingRates": [{"x": i} for i in range(40)]})
    await rest.funding_rates("BTC-USD")
    server.responses["/v1/compliance"] = (403, {"error": "blocked", "code": "GEO_RESTRICTED"})
    with pytest.raises(GeoRestricted):
        await rest.compliance()
    server.responses["/v1/prices"] = (429, {"error": "rate limited"})
    with pytest.raises(RateLimited):
        await rest.prices()
    server.responses["/v1/rateLimit"] = (200, {"accountIndex": 0})
    with pytest.raises(ValueError):
        await rest.rate_limit(ADDR, 2)  # misspelled index silently served 0
    for path, call in (("/v1/l2OrderBook/BTC-USD", rest.l2_orderbook("BTC-USD", 100)),
                       ("/v1/bbo/BTC-USD", rest.bbo("BTC-USD"))):
        server.responses[path] = (200, {"bids": []})
        await call
    await rest.close()
    priv, _ = sg.ArcusSigner.generate()
    w = ArcusRest(server.url, signer=sg.ArcusSigner(priv), address=ADDR, writes_allowed=True)
    f = sg.OrderFields(1, "BUY", D("100.0"), D("0.0001"), "ALO", 1, False, D("0.1"), D("0.00000001"))
    server.responses["/v1/placeOrder"] = (200, {"status": "REJECTED", "rejectionReason": "POST_ONLY_WOULD_CROSS"})
    with pytest.raises(OrderRejected) as e:
        await w.place_order(1, f, "c")
    assert e.value.reason == "POST_ONLY_WOULD_CROSS"
    server.responses["/v1/placeOrder"] = (401, {"error": "bad signature", "errorType": "Unauthorized"})
    with pytest.raises(AuthError):
        await w.place_order(1, f, "c")
    server.responses["/v1/placeOrder"] = (429, {"error": "rate limited", "reason": "account_empty", "retryAfterMs": 850})
    with pytest.raises(RateLimited) as e2:
        await w.place_order(1, f, "c")
    assert e2.value.reason == "account_empty" and e2.value.retry_after_ms == 850
    ro = ArcusRest(server.url, signer=sg.ArcusSigner(priv), address=ADDR, writes_allowed=False)
    with pytest.raises(LiveLockError):
        await ro.place_order(1, f, "c")
    await w.close()
    await ro.close()


# ------------------------------------------------------------------------------------------------ WebSockets
async def test_reconnecting_ws_replays_subscriptions(server: FakeServer) -> None:
    got: list[dict[str, Any]] = []
    server.ws_script = [{"type": "connected"}, {"type": "x", "v": 1}]
    ws = ReconnectingWS("t", server.url.replace("http", "ws") + "/ws", lambda m, r: got.append(m),
                        ping_payload={"type": "ping"}, ping_every_s=0.05, client_msgs_per_min=100)
    await ws.subscribe("a", {"type": "subscribe", "channel": "a"})
    ws.start()
    assert await ws.wait_connected(5)
    await asyncio.sleep(0.3)
    assert any(m.get("channel") == "a" for m in server.ws_msgs)
    assert any(m.get("type") == "ping" for m in server.ws_msgs)
    assert {"type": "x", "v": 1} in got
    await ws.subscribe("b", {"type": "subscribe", "channel": "b"})
    await ws.resubscribe("b", {"type": "unsubscribe", "channel": "b"})
    await asyncio.sleep(0.1)
    assert ws.resubscribes == 1 and ws.stale_keys(0) == []
    ws.touch("k", 0)
    assert ws.stale_keys(1) == ["k"]
    await ws.stop()


async def test_arcus_ws_dispatches_live_frames() -> None:
    frames = json.loads((FIX / "arcus_ws_frames.json").read_text())
    aw = ArcusWS("wss://unused")
    seen: dict[str, int] = {}

    def count(name: str):  # type: ignore[no-untyped-def]
        def f(*a: Any) -> None:
            seen[name] = seen.get(name, 0) + 1
        return f

    for ev in ("book", "bbo", "trades", "oracle", "predicted_funding", "markets"):
        aw.on(ev, count(ev))
    for fr in frames:
        await aw._on_message(fr["raw"], fr["recv_ts_us"])
    assert seen["book"] > 100 and seen["bbo"] > 10 and seen["oracle"] > 10 and seen["markets"] >= 1
    assert seen["predicted_funding"] >= 1
    assert aw.books["BTC-USD"].book.best_bid() is not None
    fut = asyncio.get_running_loop().create_future()
    aw._pending[7] = fut
    await aw._on_message({"method": "placeOrder", "id": 7, "status": 202, "result": {"orderId": "x"}}, 0)
    assert fut.result()["result"]["orderId"] == "x"
    await aw._on_message({"type": "error", "message": "bad"}, 0)


# ------------------------------------------------------------------------------------------------ adapters
class FakeArcusRest:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.pool_remaining: dict[str, int] = {}
        from bot.venues.http import HttpClient

        self.http = HttpClient("arcus", "http://x")

    async def place_order(self, ai: int, f: Any, cid: Any) -> dict[str, Any]:
        self.calls.append(("place", cid))
        return {"orderId": f"o-{cid}", "status": "ACK", "clientId": cid}

    async def batch_place(self, ai: int, orders: Any) -> dict[str, Any]:
        self.calls.append(("batch", len(orders)))
        return {"responses": [{"orderId": f"o-{c}", "status": "ACK", "clientId": c} for _, c in orders]}

    async def modify_order(self, ai: int, f: Any, **kw: Any) -> dict[str, Any]:
        self.calls.append(("modify", kw))
        return {"orderId": kw.get("order_id"), "status": "ACK"}

    async def cancel_order(self, ai: int, mid: int, **kw: Any) -> dict[str, Any]:
        self.calls.append(("cancel", kw))
        return {}

    async def batch_cancel(self, ai: int, c: Any) -> dict[str, Any]:
        self.calls.append(("batch_cancel", len(c)))
        return {}

    async def cancel_all(self, ai: int, mid: Any = None) -> dict[str, Any]:
        self.calls.append(("cancel_all", mid))
        return {}

    async def schedule_cancel(self, ai: int, t: Any) -> dict[str, Any]:
        self.calls.append(("dms", t))
        return {}

    async def set_leverage(self, *a: Any) -> dict[str, Any]:
        self.calls.append(("lev", a))
        return {}

    async def positions(self, a: str, i: int) -> dict[str, Any]:
        return {"positions": {"1": {"marketId": 1, "size": "0.0002", "side": "SHORT", "averageEntryPrice": "86000",
                                    "markPx": "86100", "unrealizedPnl": "-0.02", "marginMode": "CROSS"}}}

    async def open_orders(self, a: str, i: int) -> list[dict[str, Any]]:
        return [{"orderId": "o1", "marketId": 1, "side": "BUY", "status": "OPEN", "price": "86000",
                 "originalSize": "0.0002", "remainingSize": "0.0001", "updatedAt": 5, "clientId": "c1",
                 "timeInForce": "ALO"}]

    async def account(self, a: str, i: int) -> dict[str, Any]:
        return {"equity": "35.5", "freeCollateral": "30", "netDeposits": "35"}

    async def rate_limit(self, a: str, i: int) -> dict[str, Any]:
        return {"order": {"used": 100, "cap": 20000, "nextAvailableMs": 0}, "cancel": {"used": 0, "cap": 40000}}

    async def close(self) -> None:
        return None


async def test_arcus_adapter_paths() -> None:
    rest = FakeArcusRest()
    ad = ArcusAdapter(rest, None, address=ADDR, account_index=1, markets=MK[Venue.ARCUS])  # type: ignore[arg-type]
    await ad.connect()
    reqs = [OrderRequest(Venue.ARCUS, "BTC", Side.BUY, D("86000.0"), D("0.00012"), TIF.POST_ONLY, client_id=f"c{i}")
            for i in range(3)]
    st = await ad.place(reqs[:1])
    assert st[0].venue_order_id == "o-c0" and rest.calls[-1][0] == "place"
    st = await ad.place(reqs[1:])
    assert rest.calls[-1] == ("batch", 2) and len(st) == 2
    await ad.modify("c0", D("85990.0"), D("0.00012"))
    assert rest.calls[-1][1]["order_id"] == "o-c0" and rest.calls[-1][1]["client_id"] is None   # by orderId only
    ad._live["c1"].order_id = None
    n = len(rest.calls)
    with pytest.raises(VenueError, match="order id not known"):
        await ad.modify("c1", D("85990.0"), D("0.00012"))       # never by clientId alone (refused live 2026-09-25)
    assert len(rest.calls) == n
    await ad.cancel(["c1"])
    await ad.cancel(["c0", "c2"])
    assert rest.calls[-1] == ("batch_cancel", 2)
    await ad.cancel_all("BTC")
    await ad.arm_dead_mans_switch(123)
    await ad.set_leverage("BTC", 3)
    pos = await ad.positions()
    assert pos[0].size == D("-0.0002")
    oo = await ad.open_orders()
    assert oo[0].status is OrderStatus.PARTIALLY_FILLED and oo[0].tif is TIF.POST_ONLY
    assert (await ad.balances())["equity"] == D("35.5")
    b = ad.budget()
    assert b.order_remaining == 19900 and b.order_cap == 20000
    assert "rest_error_rate" in ad.health()
    await ad._on_orders({"orders": [{"orderId": "o-c0", "clientId": "c0", "status": "FILLED", "marketId": 1,
                                     "side": "BUY", "originalSize": "0.00012", "remainingSize": "0"}]}, 1, {})
    u = await ad._orders_q.get()
    assert u.status is OrderStatus.FILLED and "c0" not in ad.live_orders()
    await ad._on_fills([{"tradeId": "t1", "orderId": "o-c0", "marketId": 1, "side": "BUY", "size": "0.00012",
                         "price": "86000", "fee": "0", "role": "MAKER", "createdAt": 1, "clientId": "c0"}], 1,
                       {"type": "channel_data"})
    f = await ad._fills_q.get()
    assert f.is_maker and f.trade_id == "t1"
    with pytest.raises(ValueError):
        await ad.place([OrderRequest(Venue.ARCUS, "BTC", Side.BUY, D("1"), D("1"), TIF.POST_ONLY)])
    await ad.close()

