"""Read-only access to the two venues: public endpoints, no key, no order.

Arcus (docs: api-reference/public): /v1/markets carries each market's last funding rate, its estimate of the next one
and when it is due; /v1/fundingRates the hourly history; /v1/candles hourly oracle prices; /v1/account an account's
equity by address.
Lighter on Robinhood Chain (docs: reference/funding-rates, fundings, orderbookdetails, account-1): /api/v1/funding-rates
gives its current rate as an 8-HOUR figure (divide by 8: funding is paid hourly); /api/v1/fundings the hourly history,
`rate` in percent with a `direction` (long = longs pay).

Requests are paced: Arcus allows 1,500 weight a minute per IP (a list call costs 20 and more), Lighter 60 requests a
minute, and the market-making bots share both limits.
"""

from __future__ import annotations

import asyncio
import math
import time
from typing import Any

import aiohttp

from arb.rank import Leg

ARCUS = "https://api.arcus.xyz"
LIGHTER = "https://api.rh.lighter.xyz"
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


class Http:
    def __init__(self, gap_s: float) -> None:
        self.gap_s = gap_s
        self._last = 0.0
        self._lock = asyncio.Lock()
        self._session: aiohttp.ClientSession | None = None
        self.calls = 0

    async def get(self, url: str, params: dict[str, Any] | None = None) -> Any:
        async with self._lock:
            wait = self._last + self.gap_s - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()
        if self._session is None:
            self._session = aiohttp.ClientSession(headers=HEADERS, timeout=aiohttp.ClientTimeout(total=30))
        self.calls += 1
        params = {k: str(v) for k, v in (params or {}).items() if v is not None}
        for attempt in range(4):
            async with self._session.get(url, params=params) as r:
                if r.status == 200:
                    return await r.json(content_type=None)
                if r.status not in (429, 502, 503, 504) or attempt == 3:
                    raise RuntimeError(f"{url}: HTTP {r.status} {(await r.text())[:200]}")
            await asyncio.sleep(3 * (attempt + 1))
        return None

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None


# ------------------------------------------------------------------------------------------------ parsing (pure)
def arcus_leg(m: dict[str, Any]) -> Leg | None:
    if m.get("type") != "PERPETUAL" or not str(m.get("marketDisplayName", "")).endswith("-USD"):
        return None
    mark = float(m.get("markPrice") or m.get("oraclePrice") or 0)
    imf = float(m.get("initialMarginFraction") or 0)
    rate = float(m.get("fundingRate") or 0)
    nxt = m.get("nextFundingRate")
    return Leg(venue="arcus", symbol=str(m["baseAsset"]), rate_h=rate,
               next_rate_h=float(nxt) if nxt is not None else rate,
               next_at=int(m.get("nextFundingAt") or 0), mark=mark, imf=imf,
               imf_off=float(m.get("offHoursInitialMarginFraction") or imf),
               mmf=float(m.get("maintenanceMarginFraction") or 0),
               min_notional=float(m.get("minOrderNotional") or 0), min_size=float(m.get("minOrderSize") or 0),
               step=float(m.get("stepSize") or 0), tick=float(m.get("tickSize") or 0),
               volume_24h=float(m.get("volume24hNotional") or 0), oi_usd=float(m.get("openInterest") or 0) * mark,
               online=m.get("status") == "ONLINE", off_hours=bool(m.get("isOutsideRth")))


def lighter_leg(d: dict[str, Any], rate_8h: float | None, now_s: float) -> Leg | None:
    if d.get("market_type", "perp") != "perp":
        return None
    mark = float(d.get("mark_price") or d.get("last_trade_price") or 0)
    rate = (rate_8h or 0.0) / 8
    imf = float(d.get("min_initial_margin_fraction") or 0) / 10_000
    return Leg(venue="lighter", symbol=str(d["symbol"]), rate_h=rate, next_rate_h=rate,
               next_at=(int(now_s) // 3600 + 1) * 3600, mark=mark, imf=imf, imf_off=imf,
               mmf=float(d.get("maintenance_margin_fraction") or 0) / 10_000,
               min_notional=float(d.get("min_quote_amount") or 0), min_size=float(d.get("min_base_amount") or 0),
               step=10 ** -int(d.get("size_decimals") or 0), tick=10 ** -int(d.get("price_decimals") or 0),
               volume_24h=float(d.get("daily_quote_token_volume") or 0),
               oi_usd=float(d.get("open_interest") or 0) * mark,
               online=d.get("status") == "active" and not (d.get("market_config") or {}).get("force_reduce_only"))


def lighter_history(rows: list[dict[str, Any]]) -> dict[int, float]:
    """{hour start (unix s): rate as a fraction per hour, + = longs pay}."""
    return {int(r["timestamp"]) // 3600 * 3600: float(r["rate"]) / 100 * (1 if r.get("direction") == "long" else -1)
            for r in rows}


def arcus_history(rows: list[dict[str, Any]]) -> dict[int, float]:
    return {int(r["time"]) // 1_000_000 // 3600 * 3600: float(r["fundingRate"]) for r in rows}


def sigma_day(closes: list[float]) -> float:
    """One day's typical move (a fraction) from hourly closes, oldest first. Hours with no change at all are left out:
    a stock's oracle stands still while its market is closed, and counting those would understate a trading day."""
    rets = [math.log(b / a) for a, b in zip(closes, closes[1:], strict=False) if a > 0 and b > 0 and a != b]
    if len(rets) < 12:
        return 0.0
    return math.sqrt(sum(r * r for r in rets) / len(rets) * 24)


# ------------------------------------------------------------------------------------------------ the two venues
class Arcus:
    def __init__(self) -> None:
        self.http = Http(gap_s=1.2)

    async def legs(self) -> dict[str, Leg]:
        body = await self.http.get(f"{ARCUS}/v1/markets")
        legs = (arcus_leg(m) for m in body.get("markets") or [])
        return {x.symbol: x for x in legs if x is not None}

    async def history(self, symbol: str, hours: int) -> dict[int, float]:
        now = int(time.time())
        body = await self.http.get(f"{ARCUS}/v1/fundingRates", {
            "market": f"{symbol}-USD", "limit": 1000, "from": (now - hours * 3600) * 1_000_000, "to": now * 1_000_000})
        return arcus_history(body.get("fundingRates") or [])

    async def closes_1h(self, symbol: str, hours: int = 336) -> list[float]:
        body = await self.http.get(f"{ARCUS}/v1/candles", {"market": f"{symbol}-USD", "timeframe": "1h",
                                                          "to": int(time.time() * 1e6), "countback": hours})
        rows = sorted(body.get("candles") or [], key=lambda c: int(c["openTime"]))
        return [float(c["close"]) for c in rows]

    async def top(self, symbol: str) -> tuple[float, float]:
        b = await self.http.get(f"{ARCUS}/v1/bbo/{symbol}-USD")
        return float(b["bestBid"]["price"]), float(b["bestAsk"]["price"])

    async def equity(self, address: str, account_index: int = 0) -> tuple[float, float]:
        """(equity, free collateral) in dollars; the account is public by address."""
        b = await self.http.get(f"{ARCUS}/v1/account", {"address": address, "accountIndex": account_index})
        return float(b.get("equity") or 0), float(b.get("freeCollateral") or 0)


class Lighter:
    def __init__(self) -> None:
        # a standard account's 60 requests a minute count per IP as well as per account: at one every 3 s a scan
        # never takes more than a third of what a Lighter bot on the same machine has
        self.http = Http(gap_s=3.0)
        self.ids: dict[str, int] = {}

    async def legs(self) -> dict[str, Leg]:
        details = (await self.http.get(f"{LIGHTER}/api/v1/orderBookDetails")).get("order_book_details") or []
        rates = {r["symbol"]: float(r["rate"]) for r in
                 (await self.http.get(f"{LIGHTER}/api/v1/funding-rates")).get("funding_rates") or []
                 if r.get("exchange") == "lighter"}
        now = time.time()
        out = {}
        for d in details:
            leg = lighter_leg(d, rates.get(d.get("symbol")), now)
            if leg is not None:
                out[leg.symbol] = leg
                self.ids[leg.symbol] = int(d["market_id"])
        return out

    async def history(self, symbol: str, hours: int) -> dict[int, float]:
        now = int(time.time())
        body = await self.http.get(f"{LIGHTER}/api/v1/fundings", {
            "market_id": self.ids[symbol], "resolution": "1h", "start_timestamp": now - hours * 3600,
            "end_timestamp": now, "count_back": min(hours + 2, 750)})
        return lighter_history(body.get("fundings") or [])

    async def top(self, symbol: str) -> tuple[float, float]:
        b = await self.http.get(f"{LIGHTER}/api/v1/orderBookOrders", {"market_id": self.ids[symbol], "limit": 1})
        return float(b["bids"][0]["price"]), float(b["asks"][0]["price"])

    async def equity(self, account_index: int) -> tuple[float, float]:
        b = await self.http.get(f"{LIGHTER}/api/v1/account", {"by": "index", "value": account_index})
        a = (b.get("accounts") or [{}])[0]
        return float(a.get("total_asset_value") or a.get("collateral") or 0), float(a.get("available_balance") or 0)
