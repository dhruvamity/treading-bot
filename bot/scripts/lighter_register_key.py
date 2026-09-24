#!/usr/bin/env python3
"""Register an API key on Lighter RH (change-pub-key, tx type 8). RUN ON THE OWNER'S MACHINE (A2.7).

Steps: find the account index for the wallet -> generate an API key with the official signer -> sign the change-pub-key
transaction with the NEW key (L2) and the WALLET (EIP-191 over the signer's message) -> [HUMAN GATE] confirm ->
sendTx -> wait until /api/v1/apikeys shows the key -> write the API private key into .env (0600).
Slots 0-3 and 157 are reserved by Lighter's own interfaces; the default slot here is 4.
Integrator approval (tx 45) is NOT needed: it only lets a partner charge fees and is rejected on standard accounts
(docs: partner-attribution). The account must hold collateral before a pubkey change (error 21126 otherwise).

    python scripts/lighter_register_key.py --env mainnet --l1 0xYourWallet
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eth_account import Account
from eth_account.messages import encode_defunct

from bot.common.secrets import upsert_dotenv
from bot.venues.lighter_rh.rest import LighterRest
from bot.venues.lighter_rh.signer import LighterSigner, generate_api_key

URLS = {"mainnet": "https://api.rh.lighter.xyz", "testnet": "https://api.rh-testnet.lighter.xyz"}
CHAIN = {"mainnet": 466324, "testnet": 300}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", choices=["testnet", "mainnet"], required=True)
    ap.add_argument("--l1", required=True, help="wallet address that owns the Lighter account")
    ap.add_argument("--api-key-index", type=int, default=4)
    ap.add_argument("--account-index", type=int, help="override (default: master account of the wallet)")
    a = ap.parse_args()
    if a.api_key_index in (0, 1, 2, 3, 157) or not 0 <= a.api_key_index <= 254:
        sys.exit("choose an API key index in 4..254 (0-3 and 157 are reserved)")
    rest = LighterRest(URLS[a.env], writes_allowed=True)
    try:
        accts = await rest.accounts_by_l1(a.l1)
        subs = accts.get("sub_accounts") or []
        if not subs and a.account_index is None:
            sys.exit(f"no Lighter account for {a.l1} on {a.env}: deposit USDG first")
        acct_index = a.account_index if a.account_index is not None else int(subs[0]["index"])
        print(f"Lighter account index {acct_index} (sub-accounts: {[s['index'] for s in subs]})")
        priv, pub = generate_api_key()
        signer = LighterSigner(url=URLS[a.env], chain_id=CHAIN[a.env], account_index=acct_index,
                               api_key_index=a.api_key_index, private_key_hex=priv)
        try:
            nonce = await rest.next_nonce(acct_index, a.api_key_index)
        except Exception:
            nonce = 0
        tx, msg = signer.change_pub_key_unsigned(new_pubkey_hex=pub, nonce=nonce)
        wallet_key = getpass.getpass("Wallet private key (hidden, used once, not stored): ").strip()
        wallet = Account.from_key(wallet_key)
        if wallet.address.lower() != a.l1.lower():
            sys.exit("wallet key does not match --l1")
        l1sig = Account.sign_message(encode_defunct(text=msg), private_key=wallet_key).signature.to_0x_hex()
        del wallet_key
        info = json.loads(tx.tx_info)
        info["L1Sig"] = l1sig
        print(f"About to register API key slot {a.api_key_index} on account {acct_index} ({a.env}); public key {pub[:18]}…")
        if input("Type CONFIRM to send the change-pub-key transaction: ").strip() != "CONFIRM":
            sys.exit("aborted")
        resp = await rest.send_tx(tx.tx_type, json.dumps(info, separators=(",", ":")))
        print("sendTx:", {k: resp.get(k) for k in ("code", "message", "tx_hash")})
        for _ in range(60):
            keys = await rest.api_keys(acct_index, a.api_key_index)
            if any((k.get("public_key") or "").lower().removeprefix("0x") == pub.lower().removeprefix("0x") for k in keys):
                break
            await asyncio.sleep(2)
        else:
            sys.exit("key not visible after 2 minutes; NOT storing it. Check /api/v1/apikeys.")
        sys_cfg = await rest.system_config()
        print("system config fee_collector_account_index:", sys_cfg.get("fee_collector_account_index"),
              "(no integrator approval is sent: not needed for our own trading)")
    finally:
        await rest.close()
    pre = "LIGHTER_TESTNET_" if a.env == "testnet" else "LIGHTER_"
    upsert_dotenv(f"{pre}ADDRESS", a.l1)
    path = upsert_dotenv(f"{pre}API_PRIVATE_KEY", priv)
    if a.api_key_index != 4:
        upsert_dotenv(f"{pre}API_KEY_INDEX", str(a.api_key_index))
    if a.account_index is not None:
        upsert_dotenv(f"{pre}ACCOUNT_INDEX", str(acct_index))
    print(f"wrote {pre}ADDRESS and {pre}API_PRIVATE_KEY to {path}. Treat it as sensitive: the Lighter signer can "
          "also sign secure withdrawals to your own wallet.")

if __name__ == "__main__":
    asyncio.run(main())
