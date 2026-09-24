"""Arcus wallet (EIP-712) signatures. OWNER-MACHINE ONLY: these need the wallet private key (A2.7).

- CreateApiKey (docs: onboarding/create-api-key). Domain {"name": "Arcus API Key", "version": "1", chainId},
  NO verifyingContract. Non-zero accountIndex must sign the accountIndex-bearing type; with a nonce, the
  combined type binds both.
- Transfer between subaccounts of the same wallet (docs: exchange/submit-internal-transfer). Domain
  {"name": "Arcus Transfer", "version": "1", chainId, verifyingContract = BridgeVault}. Amount in quote
  quantums (1e9 = $1).
"""

from __future__ import annotations

from typing import Any

from eth_account import Account
from eth_account.messages import encode_typed_data

CHAIN_IDS = {"mainnet": 4663, "testnet": 46630, "staging": 421614}
# BridgeVault per environment (docs: exchange/submit-withdrawal, read 2026-09-23).
BRIDGE_VAULT = {
    "mainnet": "0x14b107cf534239c59571b066cb6497a321da897c",
    "testnet": "0x9a6d3499149fea853efe775a3577701539054eaf",
    "staging": "0xfcb43af23e80dbbe7d951af49aa4eefb4eff8c2c",
}
QUOTE_QUANTUMS_PER_USD = 10**9


def create_api_key_typed_data(
    *, env: str, api_wallet_name: str, public_key_hex: str, valid_until_ms: int, account_index: int,
    nonce: str | None,
) -> dict[str, Any]:
    fields: list[dict[str, str]] = [
        {"name": "apiWalletName", "type": "string"},
        {"name": "apiWalletPublicKey", "type": "string"},
        {"name": "validUntil", "type": "uint256"},
    ]
    message: dict[str, Any] = {
        "apiWalletName": api_wallet_name,
        "apiWalletPublicKey": public_key_hex.removeprefix("0x").lower(),
        "validUntil": valid_until_ms,
    }
    if nonce is not None:
        fields.append({"name": "nonce", "type": "string"})
        message["nonce"] = nonce
    # Always bind accountIndex (required for non-zero, accepted for 0).
    fields.append({"name": "accountIndex", "type": "uint8"})
    message["accountIndex"] = account_index
    return {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
            ],
            "CreateApiKey": fields,
        },
        "primaryType": "CreateApiKey",
        "domain": {"name": "Arcus API Key", "version": "1", "chainId": CHAIN_IDS[env]},
        "message": message,
    }


def transfer_typed_data(*, env: str, address: str, from_index: int, to_index: int, amount_quantums: int,
                        nonce: str) -> dict[str, Any]:
    return {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "Transfer": [
                {"name": "ethereumAddress", "type": "address"},
                {"name": "fromAccountIndex", "type": "uint8"},
                {"name": "toAccountIndex", "type": "uint8"},
                {"name": "amount", "type": "uint256"},
                {"name": "nonce", "type": "string"},
            ],
        },
        "primaryType": "Transfer",
        "domain": {"name": "Arcus Transfer", "version": "1", "chainId": CHAIN_IDS[env],
                   "verifyingContract": BRIDGE_VAULT[env]},
        "message": {"ethereumAddress": address, "fromAccountIndex": from_index, "toAccountIndex": to_index,
                    "amount": amount_quantums, "nonce": nonce},
    }


def sign_typed(wallet_private_key: str, typed: dict[str, Any]) -> dict[str, str]:
    """Returns {r, s, v} hex strings as the Arcus endpoints expect. v is 27/28."""
    msg = encode_typed_data(full_message=typed)
    sig = Account.sign_message(msg, private_key=wallet_private_key)
    v = sig.v if sig.v >= 27 else sig.v + 27
    return {"r": hex(sig.r), "s": hex(sig.s), "v": hex(v)}


def recover_typed(typed: dict[str, Any], signature: dict[str, str]) -> str:
    msg = encode_typed_data(full_message=typed)
    return str(Account.recover_message(msg, vrs=(int(signature["v"], 16), int(signature["r"], 16),
                                                 int(signature["s"], 16))))
