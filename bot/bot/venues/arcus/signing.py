"""Arcus request signing (docs: api-reference/authentication, guides/rest-trading).

Scheme 1 (placeOrder op 1, cancelOrder op 2, modifyOrder op 3, untriggered TPSL op 4): the signed bytes ARE a
compact, key-sorted JSON object built from engine integers. No prefix. `ct` equals the X-Timestamp (ns).
  - `ad` lowercase master address (the only case-folded field)
  - `c` clientId, omitted entirely when empty, signed verbatim
  - `p` = price / TOP-LEVEL tickSize (exact), `q` = size / stepSize (exact)
  - `g` = goodTilTime_us * 1000 (ns)
Batches: Scheme 1 per element, all sharing one X-Timestamp as `ct`; REST still needs an X-Signature header
(any element's signature).

Scheme 2 (cancelAllOrders, setLeverage, scheduleCancel, WS authenticate):
  signature = ed25519(timestamp + action + canonical_json(body)), no delimiters, sorted keys, no whitespace.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

import orjson
from cryptography.hazmat.primitives.asymmetric import ed25519

from bot.common.decimal import to_units_exact
from bot.common.ids import validate_arcus_client_id

OP_PLACE, OP_CANCEL, OP_MODIFY, OP_TPSL = 1, 2, 3, 4
TIF_CODE = {"GTT": 0, "FOK": 1, "IOC": 2, "ALO": 3}
SIDE_CODE = {"BUY": 0, "SELL": 1}
PAYLOAD_VERSION = 1


def canonical_json(obj: Any) -> bytes:
    """Sorted keys, no whitespace. orjson's OPT_SORT_KEYS output is compact by default."""
    return orjson.dumps(obj, option=orjson.OPT_SORT_KEYS)


class ArcusSigner:
    """Holds the Ed25519 API key. The private key never leaves this object and is never logged."""

    def __init__(self, private_key_hex: str) -> None:
        raw = bytes.fromhex(private_key_hex.removeprefix("0x"))
        if len(raw) == 64:  # some tools export seed||pub
            raw = raw[:32]
        if len(raw) != 32:
            raise ValueError("Arcus API private key must be 32 bytes (64 hex chars)")
        self._sk = ed25519.Ed25519PrivateKey.from_private_bytes(raw)
        self.api_key = self._sk.public_key().public_bytes_raw().hex()

    @staticmethod
    def generate() -> tuple[str, str]:
        """(private_hex, public_hex). Used by owner-machine key registration."""
        sk = ed25519.Ed25519PrivateKey.generate()
        return sk.private_bytes_raw().hex(), sk.public_key().public_bytes_raw().hex()

    def sign(self, message: bytes) -> str:
        return self._sk.sign(message).hex()

    def __repr__(self) -> str:  # never expose key material
        return f"ArcusSigner(api_key={self.api_key[:8]}…)"


@dataclass(frozen=True, slots=True)
class OrderFields:
    """Engine-side order fields shared by place/modify payloads."""

    market_id: int
    side: Literal["BUY", "SELL"]
    price: Decimal
    size: Decimal
    tif: Literal["GTT", "FOK", "IOC", "ALO"]
    good_til_us: int
    reduce_only: bool
    tick_size: Decimal  # TOP-LEVEL tickSize: tiers never change the signed divisor
    step_size: Decimal


def _base(address: str, account_index: int, ct_ns: int, client_id: str | None) -> dict[str, Any]:
    d: dict[str, Any] = {"ad": address.lower(), "ai": account_index, "ct": ct_ns, "v": PAYLOAD_VERSION}
    if client_id:
        d["c"] = validate_arcus_client_id(client_id)
    return d


def _order_part(f: OrderFields) -> dict[str, Any]:
    return {
        "g": f.good_til_us * 1000,
        "m": f.market_id,
        "p": to_units_exact(f.price, f.tick_size),
        "q": to_units_exact(f.size, f.step_size),
        "r": 1 if f.reduce_only else 0,
        "s": SIDE_CODE[f.side],
        "t": TIF_CODE[f.tif],
    }


def place_payload(address: str, account_index: int, ct_ns: int, f: OrderFields, client_id: str | None = None,
                  *, tpsl: bool = False) -> bytes:
    d = _base(address, account_index, ct_ns, client_id)
    d.update(_order_part(f))
    d["op"] = OP_TPSL if tpsl else OP_PLACE
    return canonical_json(d)


def cancel_payload(address: str, account_index: int, ct_ns: int, market_id: int, *,
                   order_id: str | None = None, client_id: str | None = None) -> bytes:
    if bool(order_id) == bool(client_id):
        raise ValueError("cancel needs exactly one of order_id or client_id")
    d = _base(address, account_index, ct_ns, client_id)
    if order_id:
        d["id"] = order_id
    d["m"] = market_id
    d["op"] = OP_CANCEL
    return canonical_json(d)


def modify_payload(address: str, account_index: int, ct_ns: int, f: OrderFields, *,
                   order_id: str | None = None, client_id: str | None = None,
                   echo_client_id_with_order_id: bool = False) -> bytes:
    """Modify identity: the modify-order page (newest) says exactly one of `id`/`c`; the authentication page
    says `id` always plus `c` echoed when the resting order had one. `echo_client_id_with_order_id` selects the
    older rule. [VERIFY] on testnet; default follows the newest page."""
    if not order_id and not client_id:
        raise ValueError("modify needs order_id or client_id")
    if order_id and client_id and not echo_client_id_with_order_id:
        client_id = None
    d = _base(address, account_index, ct_ns, client_id)
    if order_id:
        d["id"] = order_id
    d.update(_order_part(f))
    d["op"] = OP_MODIFY
    return canonical_json(d)


def scheme2_message(timestamp_ns: int, action: str, body: dict[str, Any]) -> bytes:
    return str(timestamp_ns).encode() + action.encode() + canonical_json(body)


def auth_headers(signer: ArcusSigner, ts_ns: int, signature: str) -> dict[str, str]:
    return {"X-API-Key": signer.api_key, "X-Timestamp": str(ts_ns), "X-Signature": signature}
