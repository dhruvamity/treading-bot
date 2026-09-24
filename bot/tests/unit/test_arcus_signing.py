"""Arcus Scheme 1 / Scheme 2 golden and property tests (B1).

Goldens are the exact byte layout of the docs' guides (rest-trading, websocket-trading), which build the payload
by string formatting; our builder must produce identical bytes for identical inputs.
"""

from __future__ import annotations

import json
from decimal import Decimal as D

import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519
from hypothesis import given
from hypothesis import strategies as st

from bot.venues.arcus import signing as sg
from bot.venues.arcus.eip712 import (
    create_api_key_typed_data,
    recover_typed,
    sign_typed,
    transfer_typed_data,
)

ADDR = "0xAbCdEf0123456789aBcDeF0123456789abCDef01"
TICK, STEP = D("0.1"), D("0.00000001")


def _fields(**kw: object) -> sg.OrderFields:
    base = dict(market_id=1, side="BUY", price=D("82270.2"), size=D("0.001"), tif="GTT",
                good_til_us=1_793_000_000_000_000, reduce_only=False, tick_size=TICK, step_size=STEP)
    base.update(kw)
    return sg.OrderFields(**base)  # type: ignore[arg-type]


def test_place_payload_matches_docs_layout() -> None:
    ts, ai = 1_790_000_000_123_456_789, 0
    f = _fields()
    guide = (f'{{"ad":"{ADDR.lower()}","ai":{ai},"ct":{ts},"g":{f.good_til_us * 1000},'
             f'"m":1,"op":1,"p":{822702},"q":{100000},'
             f'"r":0,"s":0,"t":0,"v":1}}')
    assert sg.place_payload(ADDR, ai, ts, f).decode() == guide


def test_place_payload_with_client_id_and_alo() -> None:
    ts = 1_790_000_000_000_000_001
    f = _fields(side="SELL", tif="ALO", reduce_only=True)
    out = sg.place_payload(ADDR, 1, ts, f, "alg1a2B").decode()
    assert out == (f'{{"ad":"{ADDR.lower()}","ai":1,"c":"alg1a2B","ct":{ts},"g":{f.good_til_us * 1000},'
                   f'"m":1,"op":1,"p":822702,"q":100000,"r":1,"s":1,"t":3,"v":1}}')


def test_cancel_payload_by_order_id_and_client_id() -> None:
    ts = 1_790_000_000_000_000_000
    by_id = sg.cancel_payload(ADDR, 2, ts, 23, order_id="338a02ee433a4b66").decode()
    assert by_id == f'{{"ad":"{ADDR.lower()}","ai":2,"ct":{ts},"id":"338a02ee433a4b66","m":23,"op":2,"v":1}}'
    by_c = sg.cancel_payload(ADDR, 2, ts, 23, client_id="alx1").decode()
    assert by_c == f'{{"ad":"{ADDR.lower()}","ai":2,"c":"alx1","ct":{ts},"m":23,"op":2,"v":1}}'
    with pytest.raises(ValueError):
        sg.cancel_payload(ADDR, 2, ts, 23, order_id="a", client_id="b")


def test_modify_payload_identity_rules() -> None:
    ts = 1_790_000_000_000_000_000
    f = _fields()
    newest = json.loads(sg.modify_payload(ADDR, 0, ts, f, order_id="abc", client_id="cid1"))
    assert "id" in newest and "c" not in newest and newest["op"] == 3
    older = json.loads(sg.modify_payload(ADDR, 0, ts, f, order_id="abc", client_id="cid1",
                                         echo_client_id_with_order_id=True))
    assert older["id"] == "abc" and older["c"] == "cid1"
    by_c = json.loads(sg.modify_payload(ADDR, 0, ts, f, client_id="cid1"))
    assert "id" not in by_c and by_c["c"] == "cid1"


def test_inexact_price_rejected() -> None:
    with pytest.raises(ValueError):
        sg.place_payload(ADDR, 0, 1, _fields(price=D("82270.25")))


def test_band_tick_never_changes_divisor() -> None:
    # A price in a coarser band (tick 0.2 above 500k) still signs with the TOP-LEVEL tick 0.1.
    out = json.loads(sg.place_payload(ADDR, 0, 1, _fields(price=D("600000.2"))))
    assert out["p"] == 6000002


def test_scheme2_message() -> None:
    m = sg.scheme2_message(1_790_000_000_000_000_000, "cancelAllOrders", {"address": ADDR, "accountIndex": 1})
    assert m == (b"1790000000000000000cancelAllOrders" + json.dumps(
        {"accountIndex": 1, "address": ADDR}, separators=(",", ":")).encode())


def test_signature_verifies() -> None:
    priv, pub = sg.ArcusSigner.generate()
    s = sg.ArcusSigner(priv)
    assert s.api_key == pub and len(pub) == 64
    msg = sg.place_payload(ADDR, 0, 1, _fields())
    sig = bytes.fromhex(s.sign(msg))
    assert len(sig) == 64
    ed25519.Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub)).verify(sig, msg)
    assert "api_key" in repr(s) and priv not in repr(s)


@given(st.integers(0, 9), st.integers(1, 10**6), st.integers(1, 10**9),
       st.sampled_from(["BUY", "SELL"]), st.sampled_from(["GTT", "FOK", "IOC", "ALO"]), st.booleans(),
       st.one_of(st.none(), st.from_regex(r"[A-Za-z0-9_-]{1,36}", fullmatch=True)))
def test_canonical_payload_properties(ai: int, p: int, q: int, side: str, tif: str, ro: bool, cid: str | None) -> None:
    f = _fields(price=TICK * p, size=STEP * q, side=side, tif=tif, reduce_only=ro)
    raw = sg.place_payload(ADDR, ai, 1_790_000_000_000_000_000, f, cid)
    s = raw.decode()
    assert " " not in s and "\n" not in s
    obj = json.loads(s)
    assert list(obj) == sorted(obj)
    assert obj["ad"] == ADDR.lower()
    assert ("c" in obj) == bool(cid)
    if cid:
        assert obj["c"] == cid  # verbatim, case preserved
    assert obj["p"] == p and obj["q"] == q and obj["r"] == int(ro)
    assert obj["s"] == sg.SIDE_CODE[side] and obj["t"] == sg.TIF_CODE[tif]


def test_eip712_create_api_key_and_transfer_roundtrip() -> None:
    from eth_account import Account

    acct = Account.create()
    td = create_api_key_typed_data(env="testnet", api_wallet_name="bot-sub1", public_key_hex="aa" * 32,
                                   valid_until_ms=1_800_000_000_000, account_index=1, nonce="1790000000000000000")
    assert td["domain"] == {"name": "Arcus API Key", "version": "1", "chainId": 46630}
    assert "verifyingContract" not in td["domain"]
    assert [f["name"] for f in td["types"]["CreateApiKey"]] == [
        "apiWalletName", "apiWalletPublicKey", "validUntil", "nonce", "accountIndex"]
    sig = sign_typed(acct.key.hex(), td)
    assert int(sig["v"], 16) in (27, 28)
    assert recover_typed(td, sig).lower() == acct.address.lower()
    tt = transfer_typed_data(env="mainnet", address=acct.address, from_index=0, to_index=1,
                             amount_quantums=25 * 10**9, nonce="1")
    assert tt["domain"]["chainId"] == 4663 and tt["domain"]["name"] == "Arcus Transfer"
    assert recover_typed(tt, sign_typed(acct.key.hex(), tt)).lower() == acct.address.lower()
