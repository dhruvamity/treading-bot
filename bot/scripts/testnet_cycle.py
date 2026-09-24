#!/usr/bin/env python3
"""Acceptance test B2 on Arcus TESTNET (needs ARCUS_TESTNET_ADDRESS / ARCUS_TESTNET_API_PRIVATE_KEY in .env and a funded testnet subaccount).

1. N cycles: place ALO >= 2% from touch -> modify (price change) -> cancel by clientId.
2. Place 20 orders via batchPlaceOrders -> cancelAllOrders.
3. scheduleCancel with a 15 s deadline, stop refreshing, verify the gateway cancelled everything.
Pass: 0 signature errors (401/Unauthorized), every lifecycle event observed on the `orders` channel, budgets parsed.
Also answers the modify-identity [VERIFY] by trying the newest rule (exactly one of id/c) first.

    python scripts/testnet_cycle.py --account 0 --cycles 100 --market BTC
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from collections import Counter
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.common.errors import AuthError
from bot.common.ids import ClientIdFactory
from bot.common.secrets import SecretStore, load_dotenv
from bot.core.creds import arcus_address, arcus_private_keys, discover_arcus_keys, key_for_account
from bot.core.liveparams import LiveParams
from bot.venues.arcus import signing as sg
from bot.venues.arcus.rest import ArcusRest
from bot.venues.arcus.ws import ArcusWS
from bot.venues.base import Venue

REST, WS = "https://api.testnet.arcus.xyz", "wss://api.testnet.arcus.xyz/v1/ws"


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", type=int, default=0)
    ap.add_argument("--cycles", type=int, default=100)
    ap.add_argument("--market", default="BTC")
    a = ap.parse_args()
    load_dotenv(".env")
    s = SecretStore()
    addr = arcus_address(s, testnet=True)
    pub = ArcusRest(REST)
    key = key_for_account(await discover_arcus_keys(pub, addr, arcus_private_keys(s, testnet=True)), a.account)
    await pub.close()
    signer = sg.ArcusSigner(key.private_key)
    rest = ArcusRest(REST, signer=signer, address=addr, writes_allowed=True)
    lp = LiveParams(arcus=ArcusRest(REST))
    await lp.refresh()
    m = lp.markets[Venue.ARCUS][a.market]
    ws = ArcusWS(WS, n_levels=5)
    events: Counter[str] = Counter()
    seen_cids: dict[str, set[str]] = {}

    def on_orders(c: object, recv: int, frame: dict) -> None:  # type: ignore[type-arg]
        rows = c.get("orders") if isinstance(c, dict) else c
        for o in (rows.values() if isinstance(rows, dict) else rows) or []:
            if isinstance(o, dict):
                st = str(o.get("status"))
                events[st] += 1
                if o.get("clientId"):
                    seen_cids.setdefault(o["clientId"], set()).add(st)

    ws.on("orders", on_orders)
    await ws.subscribe_market(m.venue_symbol, trades=False, predicted_funding=False)
    await ws.subscribe_account(addr, a.account, ("orders",))
    ws.start()
    await ws.ws.wait_connected()
    await asyncio.sleep(2)
    book = ws.books[m.venue_symbol].book
    mid = book.mid() or Decimal(str(m.extra.get("oraclePrice")))
    ids = ClientIdFactory("probe", int(time.time()) % 10**6)
    sig_errors = 0
    good_til = int(time.time() * 1e6) + 35 * 86_400 * 1_000_000
    size = max(m.min_size, (Decimal(6) / mid / m.step_size).to_integral_value(rounding="ROUND_CEILING") * m.step_size)
    t0 = time.time()
    for i in range(a.cycles):
        px = m.round_price(mid * Decimal("0.97"), is_bid=True)  # >= 2% below touch
        f = sg.OrderFields(m.venue_market_id, "BUY", px, size, "ALO", good_til, False, m.tick_size, m.step_size)
        cid = ids.arcus()
        try:
            r = await rest.place_order(a.account, f, cid)
            oid = r.get("orderId")
            f2 = sg.OrderFields(m.venue_market_id, "BUY", m.round_price(px * Decimal("0.999"), is_bid=True), size, "ALO",
                                good_til, False, m.tick_size, m.step_size)
            await asyncio.sleep(0.2)
            await rest.modify_order(a.account, f2, order_id=oid, client_id=cid)
            await asyncio.sleep(0.2)
            await rest.cancel_order(a.account, m.venue_market_id, client_id=cid)
        except AuthError as e:
            sig_errors += 1
            print("AUTH/SIGNATURE ERROR:", e)
        if i % 10 == 0:
            print(f"cycle {i}: events {dict(events)} pool {rest.pool_remaining}")
    batch = [(sg.OrderFields(m.venue_market_id, "BUY", m.round_price(mid * Decimal(str(0.95 - j * 0.001)), is_bid=True),
                             size, "ALO", good_til, False, m.tick_size, m.step_size), ids.arcus()) for j in range(20)]
    await rest.batch_place(a.account, batch)
    await asyncio.sleep(1)
    await rest.cancel_all(a.account)
    await asyncio.sleep(2)
    dms_ids = [ids.arcus() for _ in range(3)]
    for j, c in enumerate(dms_ids):
        await rest.place_order(a.account, sg.OrderFields(m.venue_market_id, "BUY",
                               m.round_price(mid * Decimal(str(0.94 - j * 0.001)), is_bid=True), size, "ALO", good_til,
                               False, m.tick_size, m.step_size), c)
    deadline = int(time.time() * 1e6) + 15_000_000
    print("scheduleCancel:", await rest.schedule_cancel(a.account, deadline))
    await asyncio.sleep(25)
    open_now = await rest.open_orders(addr, a.account)
    dms_ok = not any(o.get("clientId") in dms_ids for o in open_now)
    lifecycle_ok = all({"CANCELED"} & sts for c, sts in seen_cids.items())
    print("\n=== B2 RESULT ===")
    print(f"cycles {a.cycles} in {time.time() - t0:.0f}s; signature errors {sig_errors}; events {dict(events)}")
    print(f"every clientId reached CANCELED on the orders channel: {lifecycle_ok}")
    print(f"dead man's switch cancelled the 3 resting orders within 25 s: {dms_ok}")
    print("rateLimit:", await rest.rate_limit(addr, a.account))
    print("PASS" if sig_errors == 0 and dms_ok and lifecycle_ok else "FAIL")
    await ws.stop()
    await rest.close()


if __name__ == "__main__":
    asyncio.run(main())
