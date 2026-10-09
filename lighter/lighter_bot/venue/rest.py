"""Lighter REST client with the standard account's request budget.

A standard account may make 60 requests a minute in all (IP and L1 address), and transactions (sendTx, sendTxBatch)
count against the same 60. The Budget keeps a rolling one-minute window and splits it three ways:
- quotes: requotes, at most `quotes_per_min`; a requote that does not fit is skipped (the order stays as it is);
- reserve: exits, the dead man's switch, cancels; they may use anything left;
- reads: account and market reads; they never use the last `reserve_per_min`.
A 429 (or a "too many requests" code) pauses everything for the cooldown Lighter asks (60 s for its firewall).
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from typing import Any

import aiohttp

from lighter_bot.config import Requests
from lighter_bot.venue import consts as C
from lighter_bot.venue.signer import SignedTx

QUOTE, RESERVE, READ = "quote", "reserve", "read"


class ApiError(RuntimeError):
    def __init__(self, status: int, code: int | None, message: str) -> None:
        super().__init__(f"HTTP {status} code {code}: {message}")
        self.status = status
        self.code = code
        self.message = message


class Budget:
    def __init__(self, cfg: Requests, clock: Any = time.monotonic) -> None:
        self.cfg = cfg
        self.clock = clock
        self.sent: deque[tuple[float, str]] = deque()
        self.blocked_until = 0.0
        self.quote_cap = cfg.quotes_per_min      # lowered 20% at each rate-limit answer (never under 20)

    def _trim(self, now: float) -> None:
        while self.sent and self.sent[0][0] <= now - 60.0:
            self.sent.popleft()

    def used(self, kind: str | None = None) -> int:
        self._trim(self.clock())
        return sum(1 for _, k in self.sent if kind is None or k == kind)

    def allows(self, kind: str) -> bool:
        now = self.clock()
        if now < self.blocked_until:
            return False
        self._trim(now)
        total = len(self.sent)
        if total >= self.cfg.per_min:
            return False
        if kind == QUOTE:
            return (sum(1 for _, k in self.sent if k == QUOTE) < self.quote_cap
                    and total < self.cfg.per_min - self.cfg.reserve_per_min // 2)
        if kind == READ:
            return total < self.cfg.per_min - self.cfg.reserve_per_min
        return True

    def take(self, kind: str) -> bool:
        if not self.allows(kind):
            return False
        self.sent.append((self.clock(), kind))
        return True

    def wait_s(self, kind: str) -> float:
        """Seconds until `kind` fits (0 if it fits now)."""
        now = self.clock()
        if now < self.blocked_until:
            return self.blocked_until - now
        if self.allows(kind):
            return 0.0
        self._trim(now)
        return max(0.05, self.sent[0][0] + 60.0 - now) if self.sent else 0.05

    def block(self, seconds: float) -> None:
        self.blocked_until = max(self.blocked_until, self.clock() + seconds)

    def rate_limited(self) -> None:
        """Lighter said too many: pause everything for its firewall's 60 s and requote less from now on."""
        self.block(60.0)
        self.quote_cap = max(20, int(self.quote_cap * 0.8))


class Rest:
    def __init__(self, base_url: str, budget: Budget | None = None, *, timeout_s: float = 10.0) -> None:
        self.base = base_url.rstrip("/")
        self.budget = budget or Budget(Requests())
        self.timeout = aiohttp.ClientTimeout(total=timeout_s)
        self._session: aiohttp.ClientSession | None = None
        self.auth: str | None = None          # a token from the signer, for the auth-gated reads

    async def session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self.timeout)
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()

    async def _slot(self, kind: str, wait: bool) -> bool:
        while True:
            if self.budget.take(kind):
                return True
            if not wait:
                return False
            await asyncio.sleep(min(5.0, self.budget.wait_s(kind)))

    def _check(self, status: int, body: Any) -> Any:
        code = body.get("code") if isinstance(body, dict) else None
        if status == 429 or status == 405 or code == C.ERR_TOO_MANY_REQUESTS:
            self.budget.rate_limited()
            raise ApiError(status, code, "rate limited: pausing requests for 60 s")
        if status >= 400 or (isinstance(code, int) and code not in (0, 200)):
            msg = body.get("message", "") if isinstance(body, dict) else str(body)[:200]
            raise ApiError(status, code, msg)
        return body

    async def get(self, path: str, params: dict[str, Any] | None = None, *, auth: bool = False,
                  kind: str = READ, wait: bool = True) -> Any:
        if not await self._slot(kind, wait):
            raise ApiError(0, None, "request budget used up for this minute")
        headers = {"Authorization": self.auth} if auth and self.auth else {}
        s = await self.session()
        async with s.get(f"{self.base}{path}", params={k: v for k, v in (params or {}).items() if v is not None},
                         headers=headers) as r:
            text = await r.text()
            try:
                body = json.loads(text) if text else {}
            except ValueError:
                body = {"message": text[:200]}
            return self._check(r.status, body)

    async def post(self, path: str, form: dict[str, Any], *, kind: str, wait: bool, auth: bool = False) -> Any:
        if not await self._slot(kind, wait):
            return None
        headers = {"Authorization": self.auth} if auth and self.auth else {}
        s = await self.session()
        async with s.post(f"{self.base}{path}", data={k: str(v) for k, v in form.items()}, headers=headers) as r:
            text = await r.text()
            try:
                body = json.loads(text) if text else {}
            except ValueError:
                body = {"message": text[:200]}
            return self._check(r.status, body)

    # ---------------------------------------------------------------- transactions
    async def send(self, txs: list[SignedTx], *, kind: str = RESERVE, wait: bool = True) -> Any:
        """One request: sendTx for one transaction, sendTxBatch for several (at most 50). Returns None when `kind`
        has no room this minute and `wait` is False (nothing was sent)."""
        if not txs:
            return None
        if len(txs) > C.MAX_BATCH:
            raise ValueError(f"at most {C.MAX_BATCH} transactions in one batch")
        if len(txs) == 1:
            return await self.post("/api/v1/sendTx", {"tx_type": txs[0].tx_type, "tx_info": txs[0].tx_info},
                                   kind=kind, wait=wait)
        return await self.post("/api/v1/sendTxBatch", {"tx_types": json.dumps([t.tx_type for t in txs]),
                                                       "tx_infos": json.dumps([t.tx_info for t in txs])},
                               kind=kind, wait=wait)

    # ---------------------------------------------------------------- reads
    async def markets(self) -> Any:
        return await self.get("/api/v1/orderBookDetails", {"filter": "perp"})

    async def status(self) -> Any:
        return await self.get("/")

    async def accounts_by_l1(self, l1: str) -> Any:
        return await self.get("/api/v1/accountsByL1Address", {"l1_address": l1})

    async def account(self, index: int) -> Any:
        return await self.get("/api/v1/account", {"by": "index", "value": str(index)})

    async def tx(self, tx_hash: str) -> Any:
        return await self.get("/api/v1/tx", {"by": "hash", "value": tx_hash})

    async def active_orders(self, account: int, market: int | None = None) -> Any:
        return await self.get("/api/v1/accountActiveOrders", {"account_index": account, "market_id": market},
                              auth=True)

    async def api_keys(self, account: int, key_index: int = 255) -> Any:
        return await self.get("/api/v1/apikeys", {"account_index": account, "api_key_index": key_index})

    async def account_limits(self, account: int) -> Any:
        return await self.get("/api/v1/accountLimits", {"account_index": account}, auth=True)

    async def live_points(self, account: int) -> Any:
        return await self.get("/api/v1/livePoints/total", {"account_index": account}, auth=True)

    async def pnl(self, account: int, start_s: int, end_s: int, resolution: str = "1d", count: int = 30) -> Any:
        return await self.get("/api/v1/pnl", {"by": "index", "value": str(account), "resolution": resolution,
                                              "start_timestamp": start_s, "end_timestamp": end_s,
                                              "count_back": count, "ignore_transfers": "false"}, auth=True)

    async def candles(self, market: int, resolution: str, start_s: int, end_s: int, count: int = 500) -> Any:
        return await self.get("/api/v1/candles", {"market_id": market, "resolution": resolution,
                                                  "start_timestamp": start_s, "end_timestamp": end_s,
                                                  "count_back": count})
