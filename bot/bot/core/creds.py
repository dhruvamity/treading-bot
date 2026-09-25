"""Credentials: the few values the owner puts in `.env`, plus everything the venues can tell us about them.

Arcus
    ARCUS_ADDRESS            the wallet that owns the API key(s)
    ARCUS_API_PRIVATE_KEY    one API key; add more (one per extra subaccount) as ARCUS_API_PRIVATE_KEY_<anything>
  Each key is bound to exactly ONE subaccount by the venue. Which one, its status and its expiry come from
  GET /v1/apiKeys, so nothing about them is configured by hand (and nothing can be configured wrong).
Testnet runs read the same names with the ARCUS_TESTNET_ prefix.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from bot.common.errors import SecretsError
from bot.common.secrets import SecretStore

_HEX64 = re.compile(r"[0-9a-f]{64}")
_ADDR = re.compile(r"0x[0-9a-fA-F]{40}")


def arcus_prefix(testnet: bool) -> str:
    return "ARCUS_TESTNET_" if testnet else "ARCUS_"


def normalize_address(value: str | None, var: str) -> str:
    v = (value or "").strip()
    if not _ADDR.fullmatch(v):
        raise SecretsError(f"{var} must be a 0x-prefixed 40-hex wallet address (got {'nothing' if not v else 'a malformed value'})")
    return v


def normalize_ed25519_key(value: str | None, var: str) -> str:
    v = (value or "").strip().lower().removeprefix("0x")
    if not _HEX64.fullmatch(v):
        raise SecretsError(f"{var} must be the 64-hex Arcus API private key (got {'nothing' if not v else f'{len(v)} chars'})")
    return v


def ed25519_public_hex(private_hex: str) -> str:
    k = ed25519.Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_hex))
    return k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()


# ------------------------------------------------------------------------------------------------ Arcus
@dataclass(frozen=True, slots=True, repr=False)
class ArcusKey:
    var: str                    # the .env name it came from (never the value)
    private_key: str = ""
    public_key: str = ""
    account_index: int | None = None
    name: str | None = None
    status: str | None = None
    valid_until_ms: int | None = None

    def __repr__(self) -> str:  # never print the private key
        return (f"ArcusKey(var={self.var}, public_key={self.public_key[:8]}…, account_index={self.account_index}, "
                f"name={self.name}, status={self.status}, valid_until_ms={self.valid_until_ms})")

    @property
    def active(self) -> bool:
        return self.account_index is not None and (self.status or "").upper() == "ACTIVE"

    def hours_left(self, now_ms: int | None = None) -> float | None:
        if self.valid_until_ms is None:
            return None
        return (self.valid_until_ms - (now_ms if now_ms is not None else int(time.time() * 1000))) / 3_600_000


def arcus_address(secrets: SecretStore, testnet: bool = False) -> str:
    var = f"{arcus_prefix(testnet)}ADDRESS"
    return normalize_address(secrets.get(var), var)


def arcus_private_keys(secrets: SecretStore, testnet: bool = False) -> dict[str, str]:
    """{env name: private key hex} for ARCUS_API_PRIVATE_KEY and any ARCUS_API_PRIVATE_KEY_<suffix>."""
    base = f"{arcus_prefix(testnet)}API_PRIVATE_KEY"
    names = [n for n in secrets.names_with_prefix(base) if n == base or n.startswith(base + "_")]
    if not names:
        raise SecretsError(f"{base} is not set: put your Arcus API private key in .env")
    return {n: normalize_ed25519_key(secrets.get(n), n) for n in names}


async def discover_arcus_keys(rest: Any, address: str, privs: dict[str, str]) -> list[ArcusKey]:
    """Match each local key to the venue's key list (public by address). A key the venue does not list comes back
    with account_index None: it was never registered, was revoked, or belongs to a different address."""
    listed = await rest.api_keys(address)
    by_pub: dict[str, dict[str, Any]] = {}
    for k in listed:
        pub = str(k.get("apiKey") or k.get("publicKey") or "").lower().removeprefix("0x")
        if pub:
            by_pub[pub] = k
    out = []
    for var, priv in sorted(privs.items()):
        pub = ed25519_public_hex(priv)
        row = by_pub.get(pub)
        vu = row.get("validUntil") if row else None
        out.append(ArcusKey(var=var, private_key=priv, public_key=pub,
                            account_index=int(row["accountIndex"]) if row and row.get("accountIndex") is not None else None,
                            name=row.get("apiWalletName") if row else None,
                            status=str(row.get("status")) if row else None,
                            valid_until_ms=int(vu) if vu is not None and str(vu).isdigit() else None))
    return out


def key_for_account(keys: list[ArcusKey], account_index: int) -> ArcusKey:
    for k in keys:
        if k.active and k.account_index == account_index:
            return k
    have = ", ".join(f"{k.var} -> {'subaccount ' + str(k.account_index) if k.account_index is not None else 'NOT registered'}"
                     f"{'' if k.active or k.account_index is None else f' ({k.status})'}" for k in keys) or "none"
    bound = sorted({k.account_index for k in keys if k.active and k.account_index is not None})
    hint = (f"set `account_index: {bound[0]}` in the session file" if bound else
            "register the key for this address (Arcus web app > API Keys) and check ARCUS_ADDRESS")
    raise SecretsError(f"no active Arcus API key for subaccount {account_index} (keys: {have}); {hint}")


__all__ = ["ArcusKey", "arcus_address", "arcus_prefix", "arcus_private_keys", "discover_arcus_keys",
           "ed25519_public_hex", "key_for_account", "normalize_address", "normalize_ed25519_key"]
