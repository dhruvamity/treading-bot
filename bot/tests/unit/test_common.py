from __future__ import annotations

from decimal import Decimal as D

import pytest
from hypothesis import given
from hypothesis import strategies as st

from bot.common.decimal import InexactConversion, round_price, tick_for_price, to_units_exact
from bot.common.ids import ARCUS_CLIENT_ID_RE, ClientIdFactory, b36
from bot.common.logging import REDACTED, redact, redact_str, register_secret
from bot.common.secrets import SecretStore, mask
from bot.common.time import dt_to_us, us_to_dt, utc_date_str

TIERS = [(D("0.1"), D("500000")), (D("0.2"), D("1000000")), (D("0.5"), D("2000000")), (D("5"), None)]


def test_to_units_exact() -> None:
    assert to_units_exact(D("86600.3"), D("0.1")) == 866003
    assert to_units_exact("0.001", "0.00000001") == 100000
    with pytest.raises(InexactConversion):
        to_units_exact(D("86600.35"), D("0.1"))


def test_tick_tiers_and_rounding() -> None:
    assert tick_for_price(D("499999"), TIERS, D("0.1")) == D("0.1")
    assert tick_for_price(D("500000"), TIERS, D("0.1")) == D("0.1")  # band edge belongs to the lower band
    assert tick_for_price(D("500000.1"), TIERS, D("0.1")) == D("0.2")
    assert round_price(D("100.07"), is_bid=True, default_tick=D("0.1")) == D("100.0")
    assert round_price(D("100.01"), is_bid=False, default_tick=D("0.1")) == D("100.1")
    # rounding an ask up across a band boundary re-rounds to the coarser tick
    assert round_price(D("500000.05"), is_bid=False, tiers=TIERS, default_tick=D("0.1")) == D("500000.2")


@given(st.decimals(min_value=D("0.01"), max_value=D("999999"), places=4))
def test_rounding_direction_property(p: D) -> None:
    b = round_price(p, is_bid=True, default_tick=D("0.01"))
    a = round_price(p, is_bid=False, default_tick=D("0.01"))
    assert b <= p <= a
    assert a - b <= D("0.01")
    assert to_units_exact(b, D("0.01")) >= 0 and to_units_exact(a, D("0.01")) >= 0


def test_time_units() -> None:
    t = 1_790_000_000_000_000
    assert dt_to_us(us_to_dt(t)) == t and utc_date_str(t) == "2026-09-21"
    with pytest.raises(ValueError):
        dt_to_us(us_to_dt(t).replace(tzinfo=None))


def test_client_ids() -> None:
    f = ClientIdFactory("grid", 123456)
    ids = {f.arcus() for _ in range(1000)}
    assert len(ids) == 1000
    assert all(ARCUS_CLIENT_ID_RE.match(i) and len(i) <= 36 for i in ids)
    assert b36(0) == "0" and b36(35) == "z" and b36(36) == "10"


def test_redaction() -> None:
    key = "ab" * 32
    assert redact_str(f"key={key}") == f"key={REDACTED}"
    assert redact({"private_key": "x" * 10, "nested": {"token": "abc123456"}, "ok": 1}) == {
        "private_key": REDACTED, "nested": {"token": REDACTED}, "ok": 1}
    register_secret("my-very-secret-value")
    assert "my-very-secret-value" not in redact_str("oops my-very-secret-value leaked")


def test_secret_store_roundtrip(tmp_path) -> None:
    p = tmp_path / "secrets.enc"
    s = SecretStore(p, password="correct horse battery staple")
    s.init()
    s.set("ARCUS_API_PRIVATE_KEY", "0x" + "11" * 40)
    raw = p.read_bytes()
    assert b"11111111" not in raw  # encrypted at rest
    assert oct(p.stat().st_mode & 0o777) == "0o600"
    s2 = SecretStore(p, password="correct horse battery staple")
    assert s2.get("ARCUS_API_PRIVATE_KEY") == "0x" + "11" * 40
    from bot.common.errors import SecretsError

    with pytest.raises(SecretsError):
        SecretStore(p, password="wrong").get("ARCUS_API_PRIVATE_KEY")
    assert mask("abcdef") == "set (6 chars)" and mask(None) == "∅"
