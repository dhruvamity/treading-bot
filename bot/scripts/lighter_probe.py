#!/usr/bin/env python3
"""Acceptance test B3 [HUMAN GATE]: ONE post-only limit order of the market minimum on Lighter RH, placed >= 5% away
from mid (cannot fill), observed on account_all_orders, then cancelled. Records send->visible and cancel->gone timings
(first samples for the maker-latency question A8.3) to data/latency_probe/. Use --testnet to rehearse on testnet.

    python scripts/lighter_probe.py --market BTC [--testnet]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.common.secrets import SecretStore, load_dotenv
from bot.core.creds import resolve_lighter
from bot.core.liveparams import LiveParams
from bot.venues.base import Venue
from bot.venues.lighter_rh import signer as ls
from bot.venues.lighter_rh.auth import AuthTokenManager
from bot.venues.lighter_rh.models import price_to_int, size_to_int
from bot.venues.lighter_rh.nonce import NonceManager
from bot.venues.lighter_rh.rest import LighterRest
from bot.venues.lighter_rh.ws import LighterWS


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="BTC")
    ap.add_argument("--testnet", action="store_true")
    a = ap.parse_args()
    env = "testnet" if a.testnet else "mainnet"
    url = "https://api.rh-testnet.lighter.xyz" if a.testnet else "https://api.rh.lighter.xyz"
    wsurl = "wss://api.rh-testnet.lighter.xyz/stream" if a.testnet else "wss://api.rh.lighter.xyz/stream"
    load_dotenv(".env")
    lc = await resolve_lighter(LighterRest(url), SecretStore(), a.testnet)
    acct, kidx = lc.account_index, lc.api_key_index
    signer = ls.LighterSigner(url=url, chain_id=466324 if not a.testnet else 300, account_index=acct,
                              api_key_index=kidx, private_key_hex=lc.private_key)
    err = signer.check_client()
    if err:
        sys.exit(f"API key check failed: {err}")
    rest = LighterRest(url, writes_allowed=True)
    lp = LiveParams(lighter=rest)
    await lp.refresh()
    m = lp.markets[Venue.LIGHTER_RH][a.market]
    auth = AuthTokenManager(signer)
    ws = LighterWS(wsurl, readonly=False)
    seen: dict[str, float] = {}
    coi = int(time.time()) % 2**40

    def on_orders(frame: dict, recv: int) -> None:  # type: ignore[type-arg]
        for rows in (frame.get("orders") or {}).values():
            for o in rows:
                if int(o.get("client_order_index") or 0) == coi:
                    seen.setdefault(str(o.get("status")), time.monotonic())

    ws.on("account_all_orders", on_orders)
    await ws.subscribe_market(m.venue_market_id, a.market, trades=False, stats=False, ticker=False)
    await ws.subscribe_account(acct, auth.token(), ("account_all_orders",))
    ws.start()
    await ws.ws.wait_connected()
    await asyncio.sleep(3)
    mid = ws.books[m.venue_market_id].book.mid()
    if mid is None:
        sys.exit("no book")
    price = m.round_price(mid * Decimal("0.95"), is_bid=True)
    min_base = max(m.min_size, (m.min_notional / price))
    size = (min_base / m.step_size).to_integral_value(rounding="ROUND_CEILING") * m.step_size
    print(f"{env}: POST-ONLY BUY {size} {a.market} @ {price} (notional ${size * price:.2f}, {(1 - price / mid):.1%} below mid "
          f"{mid}); cannot fill; will cancel immediately after it is visible.")
    if input("Type CONFIRM to send this one order: ").strip() != "CONFIRM":
        sys.exit("aborted")
    nonces = NonceManager(Path("state") / f"lighter_nonce_{acct}_{kidx}")
    tx = signer.create_order(market_index=m.venue_market_id, client_order_index=coi, base_amount=size_to_int(size, m),
                             price=price_to_int(price, m), is_ask=False, order_type=ls.ORDER_TYPE_LIMIT,
                             time_in_force=ls.TIF_POST_ONLY, reduce_only=False,
                             order_expiry=ls.DEFAULT_28_DAY_ORDER_EXPIRY, nonce=nonces.next())
    t_send = time.monotonic()
    print("sendTx:", await rest.send_tx(tx.tx_type, tx.tx_info))
    for _ in range(100):
        if "open" in seen:
            break
        await asyncio.sleep(0.05)
    t_visible = seen.get("open")
    ctx = signer.cancel_order(market_index=m.venue_market_id, order_index=coi, nonce=nonces.next())
    t_cancel = time.monotonic()
    print("cancel sendTx:", await rest.send_tx(ctx.tx_type, ctx.tx_info))
    for _ in range(100):
        if any(k.startswith("canceled") for k in seen):
            break
        await asyncio.sleep(0.05)
    t_gone = next((v for k, v in seen.items() if k.startswith("canceled")), None)
    rec = {"ts": time.time(), "env": env, "market": a.market,
           "send_to_visible_ms": (t_visible - t_send) * 1000 if t_visible else None,
           "cancel_to_gone_ms": (t_gone - t_cancel) * 1000 if t_gone else None, "statuses": list(seen)}
    print(json.dumps(rec, indent=1))
    out = Path("data/latency_probe")
    out.mkdir(parents=True, exist_ok=True)
    with (out / "lighter_probe.jsonl").open("a") as fh:
        fh.write(json.dumps(rec) + "\n")
    await ws.stop()
    await rest.close()


if __name__ == "__main__":
    asyncio.run(main())
