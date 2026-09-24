#!/usr/bin/env python3
"""Move USDG collateral between Arcus subaccounts of the same wallet (POST /v1/transfer, EIP-712 "Arcus Transfer").
OWNER'S MACHINE ONLY (needs the wallet key). Amounts in USD; sent as quote quantums (1e9 = $1).

    python scripts/arcus_transfer.py --env mainnet --from 0 --to 1 --usd 35
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
import time
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eth_account import Account

from bot.venues.arcus.eip712 import QUOTE_QUANTUMS_PER_USD, sign_typed, transfer_typed_data
from bot.venues.arcus.rest import ArcusRest

URLS = {"mainnet": "https://api.arcus.xyz", "testnet": "https://api.testnet.arcus.xyz"}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", choices=["testnet", "mainnet"], required=True)
    ap.add_argument("--from", dest="src", type=int, required=True)
    ap.add_argument("--to", dest="dst", type=int, required=True)
    ap.add_argument("--usd", type=Decimal, required=True)
    a = ap.parse_args()
    if a.src == a.dst:
        sys.exit("from and to must differ")
    amount = int(a.usd * QUOTE_QUANTUMS_PER_USD)
    key = getpass.getpass("Wallet private key (hidden, used once): ").strip()
    addr = Account.from_key(key).address
    nonce = str(time.time_ns())
    typed = transfer_typed_data(env=a.env, address=addr, from_index=a.src, to_index=a.dst, amount_quantums=amount,
                                nonce=nonce)
    sig = sign_typed(key, typed)
    del key
    print(f"transfer ${a.usd} from sub {a.src} to sub {a.dst} ({a.env}, {addr})")
    if input("Type CONFIRM: ").strip() != "CONFIRM":
        sys.exit("aborted")
    rest = ArcusRest(URLS[a.env])
    try:
        resp = await rest.transfer({"ethereumAddress": addr, "fromAccountIndex": a.src, "toAccountIndex": a.dst,
                                    "amount": str(amount), "nonce": nonce, "signature": sig})
        print(resp)
        print("Outcome arrives asynchronously: check GET /v1/accountTransferUpdates or `bot probe`.")
    finally:
        await rest.close()


if __name__ == "__main__":
    asyncio.run(main())
