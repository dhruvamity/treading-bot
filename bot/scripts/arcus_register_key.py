#!/usr/bin/env python3
"""Register (or rotate) an Arcus API key for one subaccount. RUN ON THE OWNER'S MACHINE, never the server (A2.7).

- Generates a fresh Ed25519 key pair locally.
- Asks for the WALLET private key with a hidden prompt; it is used once to sign the EIP-712 CreateApiKey message and
  is never written anywhere.
- POSTs /v1/createApiKey (accountIndex-bearing type + replay nonce), waits until /v1/apiKeys lists the key,
  then writes ONLY the address and the Ed25519 API key into .env (0600). The subaccount and expiry are not stored:
  the bot reads them from GET /v1/apiKeys.
- Rotation: re-register with the same --name (Arcus upserts by apiWalletName, which revokes the old key).

    python scripts/arcus_register_key.py --env mainnet --account 0 --days 90
    python scripts/arcus_register_key.py --env testnet --account 0 --days 30
Alternative: generate the key in the Arcus web app (API Keys page) and paste it into .env as ARCUS_API_PRIVATE_KEY.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eth_account import Account

from bot.common.secrets import load_dotenv, upsert_dotenv
from bot.venues.arcus.eip712 import create_api_key_typed_data, sign_typed
from bot.venues.arcus.rest import ArcusRest
from bot.venues.arcus.signing import ArcusSigner

URLS = {"mainnet": "https://api.arcus.xyz", "testnet": "https://api.testnet.arcus.xyz"}


async def main() -> None:
    load_dotenv(".env")
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", choices=["testnet", "mainnet"], required=True)
    ap.add_argument("--account", type=int, required=True, help="subaccount 0-9 (1 = market making, 2 = DN leg)")
    ap.add_argument("--name", help="apiWalletName (default bot-sub<N>); same name = rotation")
    ap.add_argument("--days", type=int, default=90, help="validity, 1-180 days")
    ap.add_argument("--dry-run", action="store_true", help="print the typed data, send nothing")
    a = ap.parse_args()
    if not 0 <= a.account <= 9 or not 1 <= a.days <= 180:
        sys.exit("account must be 0-9 and days 1-180")
    name = a.name or f"bot-sub{a.account}"
    priv, pub = ArcusSigner.generate()
    valid_until = int(time.time() * 1000) + a.days * 86_400_000
    nonce = str(time.time_ns())
    wallet_key = getpass.getpass("Wallet private key (hidden, used once, not stored): ").strip()
    acct = Account.from_key(wallet_key)
    typed = create_api_key_typed_data(env=a.env, api_wallet_name=name, public_key_hex=pub, valid_until_ms=valid_until,
                                      account_index=a.account, nonce=nonce)
    sig = sign_typed(wallet_key, typed)
    del wallet_key
    body = {"address": acct.address, "publicKey": pub, "apiWalletName": name, "validUntil": valid_until,
            "accountIndex": a.account, "nonce": nonce, "signature": sig}
    print(f"wallet {acct.address}  env {a.env}  subaccount {a.account}  name {name}")
    print(f"new API key (public) {pub}  valid until {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(valid_until / 1000))}")
    if a.dry_run:
        print({k: v for k, v in body.items() if k != "signature"})
        return
    rest = ArcusRest(URLS[a.env])
    try:
        resp = await rest.create_api_key(body)
        print("createApiKey:", {k: resp.get(k) for k in ("apiKey", "accountIndex", "validUntil")})
        for _ in range(60):
            keys = await rest.api_keys(acct.address, a.account)
            if any(k.get("apiKey") == pub for k in keys):
                break
            await asyncio.sleep(1)
        else:
            sys.exit("key not visible after 60 s; NOT storing it. Check /v1/apiKeys manually.")
    finally:
        await rest.close()
    pre = "ARCUS_TESTNET_" if a.env == "testnet" else "ARCUS_"
    upsert_dotenv(f"{pre}ADDRESS", acct.address)
    existing = os.environ.get(f"{pre}API_PRIVATE_KEY")
    var = f"{pre}API_PRIVATE_KEY" if not existing else f"{pre}API_PRIVATE_KEY_SUB{a.account}"
    path = upsert_dotenv(var, priv)
    print(f"wrote {pre}ADDRESS and {var} to {path} (subaccount {a.account}, valid {a.days} days). "
          "Check with: bot keys")

if __name__ == "__main__":
    asyncio.run(main())
