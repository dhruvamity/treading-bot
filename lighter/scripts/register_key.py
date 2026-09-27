"""Register a new API key with your Lighter (Robinhood Chain) account. Run it yourself, on your own machine:

    .venv/bin/python scripts/register_key.py [--slot 4] [--testnet]

It asks for your wallet's private key (typed, not echoed, used once to sign the key change and never stored), makes a
new API key pair, registers its public key in the slot (4-254; 0-3 and 157 belong to the Lighter apps), checks Lighter
holds it, and prints the lines to put in lighter/.env. It uses Lighter's official SDK for this one wallet-signed step.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot", type=int, default=4)
    ap.add_argument("--testnet", action="store_true")
    a = ap.parse_args()
    if a.slot in (0, 1, 2, 3, 157) or not 2 <= a.slot <= 254:
        raise SystemExit("choose a slot from 4 to 254 (not 157)")
    import eth_account
    import lighter
    url = "https://api.rh-testnet.lighter.xyz" if a.testnet else "https://api.rh.lighter.xyz"
    chain = 300 if a.testnet else 466324
    eth_key = getpass.getpass("Wallet private key (not shown, not stored): ").strip()
    addr = eth_account.Account.from_key(eth_key).address
    client = lighter.ApiClient(configuration=lighter.Configuration(host=url))
    try:
        subs = (await lighter.AccountApi(client).accounts_by_l1_address(l1_address=addr)).sub_accounts
    except lighter.ApiException as e:
        raise SystemExit(f"no Lighter account for {addr}: deposit on Lighter first ({e})") from None
    account = min(int(s.index) for s in subs)
    priv, pub, err = lighter.create_api_key()
    if err:
        raise SystemExit(err)
    signer = lighter.SignerClient(url=url, account_index=account, api_private_keys={a.slot: priv}, chain_id=chain)
    print(f"Registering a new key in slot {a.slot} of account {account} ({addr[:8]}…)")
    _, err = await signer.change_api_key(eth_private_key=eth_key, new_pubkey=pub, api_key_index=a.slot)
    if err:
        raise SystemExit(f"Lighter refused: {err}")
    await asyncio.sleep(10)
    err = signer.check_client()
    await signer.close()
    await client.close()
    if err:
        raise SystemExit(f"registered, but Lighter does not show it yet: {err}. Try `lbot doctor` in a minute.")
    print("\nDone. Put these in lighter/.env (chmod 600):\n")
    print(f"LIGHTER_ADDRESS={addr}")
    print(f"LIGHTER_API_PRIVATE_KEY={priv}")
    print(f"LIGHTER_API_KEY_INDEX={a.slot}")
    if a.testnet:
        print("LIGHTER_ENV=testnet")


if __name__ == "__main__":
    asyncio.run(main())
