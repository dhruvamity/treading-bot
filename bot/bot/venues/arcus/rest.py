"""Arcus REST client (docs: api-reference/public/*, api-reference/exchange/*, api-reference/rate-limits).

Client-side IP-weight accounting keeps us under the 1,500/min bucket; the recorder uses a bucket capped at 50%.
Order writes cost 0 IP weight and draw on per-subaccount pools (tracked from `rateLimit.remaining`).
All query timestamps are µs; signing timestamps are ns.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from decimal import Decimal
from typing import Any, Literal

from bot.common.errors import LiveLockError, RateLimited
from bot.common.logging import Log
from bot.common.ratelimit import TokenBucket
from bot.venues.arcus import signing as sg
from bot.venues.arcus.models import raise_for_error
from bot.venues.http import HttpClient

log = Log("arcus.rest")

# Base weights (docs: rate-limits "Weight tiers").
WEIGHTS: dict[str, int] = {
    "/": 1, "/v1/time": 1, "/v1/compliance": 1, "/health": 0,
    "/v1/bbo": 2, "/v1/mids": 2, "/v1/account": 2, "/v1/positions": 2, "/v1/order": 2, "/v1/feetiers": 2,
    "/v1/leverages": 2, "/v1/accountStats": 2, "/v1/rateLimit": 2,
    "/v1/prices": 20, "/v1/markets": 20, "/v1/trade": 20, "/v1/trades": 20, "/v1/candles": 20,
    "/v1/portfolio": 20, "/v1/openOrders": 20, "/v1/orders": 20, "/v1/fills": 20, "/v1/funding": 20,
    "/v1/fundingRates": 20, "/v1/accountTransferUpdates": 20, "/v1/apiKeys": 20, "/v1/createApiKey": 20,
    "/v1/revokeApiKey": 20,
    "/v1/setLeverage": 125, "/v1/withdraw": 125, "/v1/transfer": 125,
}
# Post-flight per-item divisor for list endpoints.
LIST_DIVISOR: dict[str, int] = {"/v1/candles": 60, "/v1/openOrders": 50, "/v1/trades": 20, "/v1/fills": 20,
                                "/v1/orders": 20, "/v1/funding": 20, "/v1/fundingRates": 20}
LIST_KEYS = {"/v1/candles": "candles", "/v1/openOrders": "orders", "/v1/trades": "trades", "/v1/fills": "fills",
             "/v1/orders": "orders", "/v1/funding": "fundingPayments", "/v1/fundingRates": "fundingRates"}


def _weight_key(path: str) -> str:
    if path.startswith("/v1/l2OrderBook"):
        return "/v1/l2OrderBook"
    if path.startswith("/v1/bbo/"):
        return "/v1/bbo"
    return path


class ArcusRest:
    def __init__(
        self,
        base_url: str,
        *,
        ip_bucket: TokenBucket | None = None,
        signer: sg.ArcusSigner | None = None,
        address: str | None = None,
        writes_allowed: bool = False,
        is_mainnet: bool = False,
    ) -> None:
        self.http = HttpClient("arcus", base_url)
        self.ip = ip_bucket or TokenBucket(1500, 25)
        self.signer = signer
        self.address = address
        self._writes_allowed = writes_allowed
        self.is_mainnet = is_mainnet
        self.pool_remaining: dict[str, int] = {}

    async def close(self) -> None:
        await self.http.close()

    # ---------------------------------------------------------------- transport
    async def _get(self, path: str, params: dict[str, Any] | None = None, *, n_levels: int = 0) -> Any:
        key = _weight_key(path)
        w = 2 + n_levels // 20 if key == "/v1/l2OrderBook" else WEIGHTS.get(key, 20)
        if w:
            await self.ip.acquire(w)
        status, body, headers = await self.http.request("GET", path, params=params)
        if status == 429:
            ra = float(headers.get("Retry-After", "1"))
            self.ip.drain(ra)
        raise_for_error(status, body)
        div = LIST_DIVISOR.get(key)
        if div and isinstance(body, dict):
            items = body.get(LIST_KEYS[key]) or []
            self.ip.charge(len(items) // div)
        return body

    def _require_write(self) -> sg.ArcusSigner:
        if not self._writes_allowed:
            raise LiveLockError("Arcus writes are disabled for this client (paper mode or triple lock closed)")
        if self.signer is None or not self.address:
            raise LiveLockError("Arcus writes need an API key signer and master address")
        return self.signer

    async def _post_signed(self, path: str, body: dict[str, Any], ts_ns: int, signature: str,
                           client_id: str | None = None) -> Any:
        signer = self._require_write()
        w = WEIGHTS.get(path, 0)
        if w:
            await self.ip.acquire(w)
        status, resp, headers = await self.http.request(
            "POST", path, params={"address": self.address}, json_body=body,
            headers=sg.auth_headers(signer, ts_ns, signature))
        if status == 429 and isinstance(resp, dict) and resp.get("reason") in (None, "ip"):
            self.ip.drain(float(headers.get("Retry-After", "1")))
        raise_for_error(status, resp, client_id=client_id)
        rl = resp.get("rateLimit") if isinstance(resp, dict) else None
        if isinstance(rl, dict) and "pool" in rl:
            self.pool_remaining[rl["pool"]] = int(rl["remaining"])
        return resp

    # ---------------------------------------------------------------- public reads
    async def root(self) -> Any:
        return await self._get("/")

    async def compliance(self) -> Any:
        return await self._get("/v1/compliance")

    async def server_time(self) -> dict[str, Any]:
        return dict(await self._get("/v1/time"))

    async def leverages(self, address: str, account_index: int, market: str | None = None) -> list[dict[str, Any]]:
        body = await self._get("/v1/leverages", {"address": address, "accountIndex": account_index, "market": market})
        return list(body.get("leverages") or [])

    async def markets(self) -> list[dict[str, Any]]:
        return list((await self._get("/v1/markets"))["markets"])

    async def feetiers(self) -> dict[str, Any]:
        return dict(await self._get("/v1/feetiers"))

    async def l2_orderbook(self, market: str, n_levels: int = 100) -> dict[str, Any]:
        return dict(await self._get(f"/v1/l2OrderBook/{market}", {"nLevels": n_levels}, n_levels=n_levels))

    async def bbo(self, market: str) -> dict[str, Any]:
        return dict(await self._get(f"/v1/bbo/{market}"))

    async def prices(self, market: str | None = None) -> Any:
        return await self._get("/v1/prices", {"market": market})

    async def funding_rates(self, market: str, *, from_us: int | None = None, to_us: int | None = None,
                            limit: int = 1000) -> list[dict[str, Any]]:
        body = await self._get("/v1/fundingRates", {"market": market, "from": from_us, "to": to_us, "limit": limit})
        return list(body.get("fundingRates") or [])

    async def trades(self, market: str, *, from_us: int | None = None, to_us: int | None = None,
                     limit: int = 1000) -> list[dict[str, Any]]:
        body = await self._get("/v1/trades", {"market": market, "from": from_us, "to": to_us, "limit": limit})
        return list(body.get("trades") or [])

    async def candles(self, market: str, timeframe: str, *, to_us: int, from_us: int | None = None,
                      countback: int | None = None) -> list[dict[str, Any]]:
        body = await self._get("/v1/candles", {"market": market, "timeframe": timeframe, "to": to_us,
                                              "from": from_us, "countback": countback})
        return list(body.get("candles") or [])

    # ---------------------------------------------------------------- account reads (public by address)
    async def account(self, address: str, account_index: int) -> dict[str, Any]:
        return dict(await self._get("/v1/account", {"address": address, "accountIndex": account_index}))

    async def positions(self, address: str, account_index: int) -> dict[str, Any]:
        return dict(await self._get("/v1/positions", {"address": address, "accountIndex": account_index}))

    async def open_orders(self, address: str, account_index: int, market: str | None = None,
                          limit: int = 1000) -> list[dict[str, Any]]:
        body = await self._get("/v1/openOrders", {"address": address, "accountIndex": account_index,
                                                  "market": market, "limit": limit})
        return list(body.get("orders") or [])

    async def fills(self, address: str, account_index: int, *, from_us: int | None = None, limit: int = 1000,
                    market: str | None = None) -> list[dict[str, Any]]:
        body = await self._get("/v1/fills", {"address": address, "accountIndex": account_index, "from": from_us,
                                             "limit": limit, "market": market})
        return list(body.get("fills") or [])

    async def funding_payments(self, address: str, account_index: int, *, from_us: int | None = None,
                               limit: int = 1000) -> list[dict[str, Any]]:
        body = await self._get("/v1/funding", {"address": address, "accountIndex": account_index,
                                               "from": from_us, "limit": limit})
        return list(body.get("fundingPayments") or [])

    async def rate_limit(self, address: str, account_index: int) -> dict[str, Any]:
        body = dict(await self._get("/v1/rateLimit", {"address": address, "accountIndex": account_index}))
        if int(body.get("accountIndex", account_index)) != account_index:
            raise ValueError(f"rateLimit served accountIndex {body.get('accountIndex')} != {account_index}")
        return body

    async def api_keys(self, address: str, account_index: int | None = None) -> list[dict[str, Any]]:
        body = await self._get("/v1/apiKeys", {"address": address, "accountIndex": account_index})
        return list(body.get("apiKeys") or [])

    # ---------------------------------------------------------------- signed writes
    def _order_body(self, account_index: int, f: sg.OrderFields, ts_ns: int, client_id: str | None,
                    order_type: Literal["LIMIT", "MARKET"] = "LIMIT") -> dict[str, Any]:
        assert self.address
        b: dict[str, Any] = {
            "address": self.address, "accountIndex": account_index, "marketId": f.market_id,
            "orderSide": f.side, "orderType": order_type, "quantity": _s(f.size), "price": _s(f.price),
            "timeInForce": f.tif, "goodTilTime": str(f.good_til_us), "timestamp": ts_ns,
        }
        if f.reduce_only:
            b["reduceOnly"] = True
        if client_id:
            b["clientId"] = client_id
        return b

    async def place_order(self, account_index: int, f: sg.OrderFields, client_id: str | None = None,
                          order_type: Literal["LIMIT", "MARKET"] = "LIMIT") -> dict[str, Any]:
        signer = self._require_write()
        assert self.address
        ts = time.time_ns()
        payload = sg.place_payload(self.address, account_index, ts, f, client_id)
        body = self._order_body(account_index, f, ts, client_id, order_type)
        return dict(await self._post_signed("/v1/placeOrder", body, ts, signer.sign(payload), client_id))

    async def batch_place(self, account_index: int, orders: Sequence[tuple[sg.OrderFields, str | None]]) -> dict[str, Any]:
        signer = self._require_write()
        assert self.address
        if not 0 < len(orders) <= 39:
            raise ValueError("batchPlaceOrders is kept at <= 39 orders (0 IP weight)")
        ts = time.time_ns()
        elems = []
        for f, cid in orders:
            b = self._order_body(account_index, f, ts, cid)
            b["signature"] = signer.sign(sg.place_payload(self.address, account_index, ts, f, cid))
            elems.append(b)
        return dict(await self._post_signed("/v1/batchPlaceOrders", {"orders": elems}, ts, elems[0]["signature"]))

    async def modify_order(self, account_index: int, f: sg.OrderFields, *, order_id: str | None,
                           client_id: str | None, echo_client_id: bool = False) -> dict[str, Any]:
        signer = self._require_write()
        assert self.address
        ts = time.time_ns()
        payload = sg.modify_payload(self.address, account_index, ts, f, order_id=order_id, client_id=client_id,
                                    echo_client_id_with_order_id=echo_client_id)
        body: dict[str, Any] = {
            "address": self.address, "accountIndex": account_index, "marketId": f.market_id,
            "goodTilTime": str(f.good_til_us), "quantity": _s(f.size), "price": _s(f.price), "side": f.side,
            "timeInForce": f.tif, "reduceOnly": f.reduce_only, "clientTime": str(ts),
        }
        if order_id:
            body["orderId"] = order_id
        else:
            body["clientId"] = client_id
        return dict(await self._post_signed("/v1/modifyOrder", body, ts, signer.sign(payload), client_id))

    async def cancel_order(self, account_index: int, market_id: int, *, order_id: str | None = None,
                           client_id: str | None = None) -> dict[str, Any]:
        signer = self._require_write()
        assert self.address
        ts = time.time_ns()
        payload = sg.cancel_payload(self.address, account_index, ts, market_id, order_id=order_id,
                                    client_id=client_id)
        body: dict[str, Any] = {"address": self.address, "accountIndex": account_index, "marketId": market_id,
                                "timestamp": ts}
        if order_id:
            body.update(kind="orderId", orderId=order_id)
        else:
            body.update(kind="clientId", clientId=client_id)
        return dict(await self._post_signed("/v1/cancelOrder", body, ts, signer.sign(payload), client_id))

    async def batch_cancel(self, account_index: int, cancels: Sequence[tuple[int, str | None, str | None]]) -> dict[str, Any]:
        """cancels: (market_id, order_id, client_id), <= 100."""
        signer = self._require_write()
        assert self.address
        if not 0 < len(cancels) <= 100:
            raise ValueError("batchCancelOrders takes 1-100 cancels")
        ts = time.time_ns()
        elems = []
        for mid, oid, cid in cancels:
            b: dict[str, Any] = {"address": self.address, "accountIndex": account_index, "marketId": mid,
                                 "timestamp": ts}
            if oid:
                b.update(kind="orderId", orderId=oid)
            else:
                b.update(kind="clientId", clientId=cid)
            b["signature"] = signer.sign(sg.cancel_payload(self.address, account_index, ts, mid, order_id=oid,
                                                           client_id=None if oid else cid))
            elems.append(b)
        return dict(await self._post_signed("/v1/batchCancelOrders", {"cancels": elems}, ts, elems[0]["signature"]))

    async def _scheme2(self, action: str, body: dict[str, Any]) -> dict[str, Any]:
        signer = self._require_write()
        ts = time.time_ns()
        sig = signer.sign(sg.scheme2_message(ts, action, body))
        return dict(await self._post_signed(f"/v1/{action}", body, ts, sig))

    async def cancel_all(self, account_index: int, market_id: int | None = None) -> dict[str, Any]:
        assert self.address
        body: dict[str, Any] = {"address": self.address, "accountIndex": account_index}
        if market_id is not None:
            body["marketId"] = market_id
        return await self._scheme2("cancelAllOrders", body)

    async def schedule_cancel(self, account_index: int, deadline_us: int | None) -> dict[str, Any]:
        """Dead man's switch: arm/refresh with an absolute µs deadline 5 s-5 min ahead; None disarms."""
        assert self.address
        body: dict[str, Any] = {"address": self.address, "accountIndex": account_index}
        if deadline_us is not None:
            body["time"] = deadline_us
        return await self._scheme2("scheduleCancel", body)

    async def set_leverage(self, account_index: int, market_id: int, leverage: int,
                           isolated: bool | None = None) -> dict[str, Any]:
        assert self.address
        body: dict[str, Any] = {"address": self.address, "accountIndex": account_index, "marketId": market_id,
                                "leverage": leverage}
        if isolated is not None:
            body["isolated"] = isolated
        return await self._scheme2("setLeverage", body)

    # ---------------------------------------------------------------- wallet-signed (owner machine)
    async def create_api_key(self, body: dict[str, Any]) -> dict[str, Any]:
        await self.ip.acquire(20)
        status, resp, _ = await self.http.request("POST", "/v1/createApiKey", json_body=body)
        raise_for_error(status, resp)
        return dict(resp)

    async def transfer(self, body: dict[str, Any]) -> dict[str, Any]:
        await self.ip.acquire(125)
        status, resp, _ = await self.http.request("POST", "/v1/transfer", json_body=body)
        raise_for_error(status, resp)
        return dict(resp)


def _s(d: Decimal) -> str:
    """Decimal -> plain string without exponent (e.g. 0.00010000 -> '0.0001')."""
    s = format(d.normalize(), "f")
    return s if s not in ("-0", "") else "0"


__all__ = ["ArcusRest", "RateLimited"]
