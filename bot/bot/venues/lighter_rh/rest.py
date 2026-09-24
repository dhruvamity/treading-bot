"""Lighter RH REST client (docs: rate-limits, reference/*).

Standard accounts: 60 requests per rolling minute; for heavy endpoints the premium-weight rule gives a lower
cap (24,000 / weight when that is below 60, e.g. trades/recentTrades 600 -> 40/min, changeAccountTier -> 8/min).
sendTx/sendTxBatch: 60/min for standard accounts. Whether that shares the REST budget is an open question
(A8.2), so by default ONE window covers both (conservative); flip `sendtx_shares_rest_budget` once measured.
Exceeding returns 429/405 with a 60 s firewall cooldown, so we back off hard.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import orjson

from bot.common.errors import LiveLockError, RateLimited
from bot.common.logging import Log
from bot.common.ratelimit import RollingWindow
from bot.venues.http import HttpClient
from bot.venues.lighter_rh.models import raise_for_error

log = Log("lighter.rest")

PREMIUM_BUDGET = 24_000
WEIGHTS = {"/api/v1/sendTx": 6, "/api/v1/sendTxBatch": 6, "/api/v1/nextNonce": 6, "/api/v1/publicPools": 50,
           "/api/v1/txFromL1TxHash": 50, "/api/v1/accountInactiveOrders": 100, "/api/v1/deposit/latest": 100,
           "/api/v1/apikeys": 150, "/api/v1/transferFeeInfo": 500, "/api/v1/trades": 600,
           "/api/v1/recentTrades": 600, "/api/v1/changeAccountTier": 3000}
DEFAULT_WEIGHT = 300


def per_endpoint_cap(path: str, standard_limit: int = 60) -> int:
    w = WEIGHTS.get(path, DEFAULT_WEIGHT)
    return min(standard_limit, PREMIUM_BUDGET // w)


class LighterRest:
    def __init__(self, base_url: str, *, rest_per_min: int = 60, sendtx_per_min: int = 60,
                 sendtx_shares_rest_budget: bool = True, writes_allowed: bool = False,
                 headroom: float = 0.8) -> None:
        self.http = HttpClient("lighter_rh", base_url)
        self._rest = RollingWindow(max(1, int(rest_per_min * headroom)))
        self._send = self._rest if sendtx_shares_rest_budget else RollingWindow(max(1, int(sendtx_per_min * headroom)))
        self._per_ep: dict[str, RollingWindow] = {}
        self._writes_allowed = writes_allowed
        self.limit_events = 0
        self.last_volume_quota: int | None = None

    async def close(self) -> None:
        await self.http.close()

    def budget_remaining(self) -> int:
        return self._rest.remaining()

    def sendtx_remaining(self) -> int:
        return self._send.remaining()

    async def _acquire(self, path: str) -> None:
        cap = per_endpoint_cap(path)
        if cap < 60:
            w = self._per_ep.setdefault(path, RollingWindow(cap))
            await w.take(1)
        win = self._send if path in ("/api/v1/sendTx", "/api/v1/sendTxBatch") else self._rest
        await win.take(1)

    async def _call(self, method: str, path: str, *, params: dict[str, Any] | None = None,
                    form: dict[str, Any] | None = None, auth: str | None = None) -> Any:
        await self._acquire(path)
        headers = {"Authorization": auth, "PreferAuthServer": "true"} if auth else None
        status, body, _ = await self.http.request(method, path, params=params, form=form, headers=headers)
        if status in (429, 405):
            self.limit_events += 1
            log.warning("rate_limited", venue="lighter_rh", reason=f"HTTP {status}", data={"path": path})
            # Firewall cooldown is 60 s static: freeze both windows.
            for _ in range(self._rest.limit):
                self._rest.try_take(1)
        raise_for_error(status, body)
        return body

    # ---------------------------------------------------------------- public
    async def status(self) -> Any:
        return await self._call("GET", "/")

    async def order_books(self) -> list[dict[str, Any]]:
        return list((await self._call("GET", "/api/v1/orderBooks"))["order_books"])

    async def order_book_details(self, market_id: int | None = None) -> list[dict[str, Any]]:
        body = await self._call("GET", "/api/v1/orderBookDetails",
                                params={"market_id": market_id} if market_id is not None else None)
        return list(body.get("order_book_details") or [])

    async def fundings(self, market_id: int, *, start_s: int, end_s: int, count_back: int = 750,
                       resolution: str = "1h") -> list[dict[str, Any]]:
        body = await self._call("GET", "/api/v1/fundings", params={
            "market_id": market_id, "resolution": resolution, "start_timestamp": start_s,
            "end_timestamp": end_s, "count_back": count_back})
        return list(body.get("fundings") or [])

    async def candles(self, market_id: int, resolution: str, *, start_s: int, end_s: int,
                      count_back: int = 500) -> list[dict[str, Any]]:
        body = await self._call("GET", "/api/v1/candles", params={
            "market_id": market_id, "resolution": resolution, "start_timestamp": start_s,
            "end_timestamp": end_s, "count_back": count_back})
        return list(body.get("c") or [])

    async def recent_trades(self, market_id: int, limit: int = 100) -> list[dict[str, Any]]:
        body = await self._call("GET", "/api/v1/recentTrades", params={"market_id": market_id, "limit": limit})
        return list(body.get("trades") or [])

    async def trades(self, *, market_id: int, limit: int = 100, cursor: str | None = None,
                     sort_by: str = "timestamp", account_index: int | None = None,
                     auth: str | None = None) -> dict[str, Any]:
        return dict(await self._call("GET", "/api/v1/trades", params={
            "market_id": market_id, "limit": limit, "cursor": cursor, "sort_by": sort_by,
            "account_index": account_index}, auth=auth))

    async def system_config(self) -> dict[str, Any]:
        return dict(await self._call("GET", "/api/v1/systemConfig"))

    async def accounts_by_l1(self, l1_address: str) -> dict[str, Any]:
        return dict(await self._call("GET", "/api/v1/accountsByL1Address", params={"l1_address": l1_address}))

    async def account(self, account_index: int) -> dict[str, Any]:
        body = await self._call("GET", "/api/v1/account", params={"by": "index", "value": account_index})
        accts = body.get("accounts") or []
        return dict(accts[0]) if accts else {}

    async def api_keys(self, account_index: int, api_key_index: int = 255) -> list[dict[str, Any]]:
        body = await self._call("GET", "/api/v1/apikeys", params={"account_index": account_index,
                                                                  "api_key_index": api_key_index})
        return list(body.get("api_keys") or [])

    async def next_nonce(self, account_index: int, api_key_index: int) -> int:
        body = await self._call("GET", "/api/v1/nextNonce", params={"account_index": account_index,
                                                                    "api_key_index": api_key_index})
        return int(body["nonce"])

    # ---------------------------------------------------------------- auth reads
    async def active_orders(self, account_index: int, auth: str, market_id: int | None = None) -> list[dict[str, Any]]:
        body = await self._call("GET", "/api/v1/accountActiveOrders",
                                params={"account_index": account_index, "market_id": market_id}, auth=auth)
        return list(body.get("orders") or [])

    async def account_limits(self, account_index: int, auth: str) -> dict[str, Any]:
        return dict(await self._call("GET", "/api/v1/accountLimits", params={"account_index": account_index},
                                     auth=auth))

    async def position_funding(self, account_index: int, auth: str, *, limit: int = 100,
                               start_s: int | None = None) -> list[dict[str, Any]]:
        body = await self._call("GET", "/api/v1/positionFunding", params={
            "account_index": account_index, "limit": limit, "start_timestamp": start_s}, auth=auth)
        return list(body.get("position_fundings") or [])

    # ---------------------------------------------------------------- writes
    async def send_tx(self, tx_type: int, tx_info: str, *, price_protection: bool = True) -> dict[str, Any]:
        if not self._writes_allowed:
            raise LiveLockError("Lighter writes are disabled for this client (paper mode or triple lock closed)")
        body = dict(await self._call("POST", "/api/v1/sendTx", form={
            "tx_type": tx_type, "tx_info": tx_info, "price_protection": "true" if price_protection else "false"}))
        if body.get("volume_quota_remaining") is not None:
            self.last_volume_quota = int(body["volume_quota_remaining"])
        return body

    async def send_tx_batch(self, tx_types: Sequence[int], tx_infos: Sequence[str]) -> dict[str, Any]:
        if not self._writes_allowed:
            raise LiveLockError("Lighter writes are disabled for this client (paper mode or triple lock closed)")
        if not 0 < len(tx_types) == len(tx_infos) <= 50:
            raise ValueError("sendTxBatch takes 1-50 transactions with matching types/infos")
        return dict(await self._call("POST", "/api/v1/sendTxBatch", form={
            "tx_types": orjson.dumps(list(tx_types)).decode(), "tx_infos": orjson.dumps(list(tx_infos)).decode()}))


__all__ = ["LighterRest", "RateLimited", "per_endpoint_cap"]
