"""Market parsing from live fixtures, symbol mapping, LiveParams change detection (A5), triple lock (B4),
Lighter signer offline, nonces, key expiry (B5)."""

from __future__ import annotations

import json
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path

import pytest

from bot.common.errors import LiveLockError
from bot.core.keys import check_keys, key_status
from bot.core.livelock import RunMode, lock_state, mainnet_writes_allowed, resolve_mode
from bot.core.liveparams import diff_markets
from bot.venues.arcus.models import base_fee_tier, fee_tier_for_volume
from bot.venues.arcus.models import parse_market as parse_arcus
from bot.venues.arcus.rest import ArcusRest
from bot.venues.base import Venue
from bot.venues.lighter_rh.models import parse_market as parse_lighter
from bot.venues.lighter_rh.models import price_to_int, size_to_int
from bot.venues.lighter_rh.nonce import NonceManager
from bot.venues.lighter_rh.rest import LighterRest, per_endpoint_cap
from bot.venues.symbols import SymbolMap

FIX = Path(__file__).parents[1] / "fixtures" / "live"


def arcus_markets() -> dict:
    fees = json.loads((FIX / "arcus_feetiers.json").read_text())
    mk, tk = base_fee_tier(fees)
    return {m.base: m for m in (parse_arcus(x, maker_fee=mk, taker_fee=tk)
                                for x in json.loads((FIX / "arcus_markets.json").read_text())["markets"])}


def lighter_markets() -> dict:
    obs = json.loads((FIX / "lighter_orderbooks.json").read_text())["order_books"]
    det = {d["market_id"]: d for d in json.loads((FIX / "lighter_obd_btc.json").read_text())["order_book_details"]}
    return {m.base: m for m in (parse_lighter(o, det.get(o["market_id"])) for o in obs if o["market_type"] == "perp")}


def test_arcus_market_parse() -> None:
    m = arcus_markets()
    btc, spy = m["BTC"], m["SPY"]
    assert btc.venue_market_id == 1 and btc.tick_size == D("0.1") and btc.min_notional == D("5")
    assert btc.maker_fee == 0 and btc.taker_fee == D("0.000225")
    assert btc.tick_at(D("600000")) == D("0.2")
    assert spy.rth == (14400, 72000, "America/New_York") and spy.oi_cap_usd == D("2000000")
    assert spy.offhours_imf == D("0.03")


def test_fee_tiers() -> None:
    fees = json.loads((FIX / "arcus_feetiers.json").read_text())
    assert fee_tier_for_volume(fees, D(0))[0] == "Base"
    name, maker, taker = fee_tier_for_volume(fees, D(1_000_000_000))
    assert name == "VIP" and maker == D("-0.00002") and taker == D("0.0001")


def test_lighter_market_parse_and_encoding() -> None:
    m = lighter_markets()
    btc = m["BTC"]
    assert btc.venue_market_id == 1 and btc.tick_size == D("0.1") and btc.step_size == D("0.00001")
    assert btc.imf == D("0.02") and btc.mmf == D("0.012") and btc.close_out_mf == D("0.008")
    assert btc.min_notional == D("10") and btc.liquidation_fee == D("0.01")
    assert price_to_int(D("86522.7"), btc) == 865227
    assert size_to_int(D("0.00020"), btc) == 20
    with pytest.raises(ValueError):
        price_to_int(D("86522.75"), btc)


def test_symbol_map_and_ratio_pairs() -> None:
    sm = SymbolMap.build(list(arcus_markets().values()) + list(lighter_markets().values()))
    both = sm.both()
    for b in ("BTC", "ETH", "SPY", "QQQ", "NVDA", "TSLA"):
        assert b in both
    assert ("GLD", "XAU") in sm.ratio_pairs_present()
    assert sm.get("btc", Venue.ARCUS).venue_symbol == "BTC-USD"


def test_liveparams_detects_change() -> None:
    old = arcus_markets()
    new = dict(old)
    new["BTC"] = replace(old["BTC"], tick_size=D("0.5"), min_notional=D("10"))
    del new["ETH"]
    ch = diff_markets(old, new, 1)
    fields = {(c.market, c.field) for c in ch}
    assert ("BTC", "tick_size") in fields and ("BTC", "min_notional") in fields and ("ETH", "__delisted__") in fields
    assert diff_markets(old, old, 1) == []


def test_live_lock() -> None:
    """Live needs --live AND an approval: the typed LIVE prompt, or live_enabled in every session (unattended)."""
    for cli in (False, True):
        for sess in (False, True):
            for typed in (False, True):
                lk = lock_state(cli_live=cli, session_live_enabled=sess, typed_confirmation=typed)
                assert lk.open == (cli and (sess or typed))
                if not lk.open:
                    with pytest.raises(LiveLockError):
                        resolve_mode(RunMode.LIVE, lk)
                    assert not mainnet_writes_allowed(RunMode.LIVE, lk)
                assert not mainnet_writes_allowed(RunMode.PAPER, lk)
                assert not mainnet_writes_allowed(RunMode.TESTNET, lk)
    assert mainnet_writes_allowed(RunMode.LIVE, lock_state(cli_live=True, session_live_enabled=False, typed_confirmation=True))
    assert mainnet_writes_allowed(RunMode.LIVE, lock_state(cli_live=True, session_live_enabled=True))
    with pytest.raises(LiveLockError, match="--live"):
        resolve_mode(RunMode.LIVE, lock_state(cli_live=False, session_live_enabled=True, typed_confirmation=True))


async def test_rest_clients_refuse_writes_without_lock() -> None:
    from bot.venues.arcus.signing import ArcusSigner, OrderFields

    priv, _ = ArcusSigner.generate()
    rest = ArcusRest("https://api.arcus.xyz", signer=ArcusSigner(priv), address="0x" + "1" * 40,
                     writes_allowed=False, is_mainnet=True)
    f = OrderFields(1, "BUY", D("100.0"), D("0.001"), "ALO", 1, False, D("0.1"), D("0.00000001"))
    for coro in (rest.place_order(1, f, "alx1"), rest.cancel_all(1), rest.schedule_cancel(1, 1),
                 rest.cancel_order(1, 1, client_id="x"), rest.set_leverage(1, 1, 2)):
        with pytest.raises(LiveLockError):
            await coro
    await rest.close()
    lr = LighterRest("https://api.rh.lighter.xyz", writes_allowed=False)
    with pytest.raises(LiveLockError):
        await lr.send_tx(14, "{}")
    with pytest.raises(LiveLockError):
        await lr.send_tx_batch([14], ["{}"])
    await lr.close()


def test_lighter_endpoint_caps() -> None:
    assert per_endpoint_cap("/api/v1/trades") == 40
    assert per_endpoint_cap("/api/v1/changeAccountTier") == 8
    assert per_endpoint_cap("/api/v1/orderBooks") == 60


def test_nonce_manager_monotonic_and_persistent(tmp_path) -> None:
    p = tmp_path / "nonce"
    n = NonceManager(p)
    a = n.next(now_ms=1000)
    b = n.next(now_ms=1000)
    c = n.next(now_ms=900)
    assert a == 1000 and b == 1001 and c == 1002
    assert NonceManager(p).next(now_ms=500) == 1003  # restart never reuses
    assert len(set(n.reserve(10, now_ms=0))) == 10


def test_lighter_signer_offline() -> None:
    lib = pytest.importorskip("lighter")
    from bot.venues.lighter_rh import signer as ls

    priv, _pub = ls.generate_api_key()
    from bot.common.errors import AuthError

    with pytest.raises(AuthError):
        ls.LighterSigner(url="https://api.rh-testnet.lighter.xyz", chain_id=300, account_index=1, api_key_index=3,
                         private_key_hex=priv)  # reserved slot
    s = ls.LighterSigner(url="https://api.rh-testnet.lighter.xyz", chain_id=300, account_index=1, api_key_index=4,
                         private_key_hex=priv)
    tx = s.create_order(market_index=1, client_order_index=7, base_amount=20, price=800000, is_ask=False,
                        order_type=ls.ORDER_TYPE_LIMIT, time_in_force=ls.TIF_POST_ONLY, reduce_only=False,
                        order_expiry=ls.DEFAULT_28_DAY_ORDER_EXPIRY, nonce=1_790_000_000_000)
    info = tx.info()
    assert tx.tx_type == 14 and info["TimeInForce"] == 2 and info["Nonce"] == 1_790_000_000_000
    assert info["L2TxAttributes"]["4"] == 1  # SkipNonce on
    dms = s.cancel_all(time_in_force=ls.CANCEL_ALL_SCHEDULED, time_ms=1_790_000_060_000, nonce=1_790_000_000_001)
    assert dms.tx_type == 16 and dms.info()["TimeInForce"] == 1
    assert ls.leverage_to_imf_fraction(3) == 3334
    del lib


def test_key_expiry_monitor() -> None:
    now = 1_790_000_000_000
    assert key_status("k", now + 100 * 3600 * 1000, now).level == "ok"
    assert key_status("k", now + 71 * 3600 * 1000, now).level == "warn"
    assert key_status("k", now - 1, now).level == "expired"
    assert [k.level for k in check_keys({"a": now + 1, "b": now + 10**12}, now)] == ["warn", "ok"]


def test_arcus_modify_cancel_replace_keeps_order_live(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Arcus cancel-replace modify: CANCELED(cancelReason=MODIFY_CANCELED) then PLACED under the same orderId.
    The first leg must not be terminal, or the bot forgets a resting order and places a duplicate."""
    import asyncio

    from bot.core.state import StateStore
    from bot.venues.arcus.adapter import ArcusAdapter
    from bot.venues.arcus.models import parse_order
    from bot.venues.base import TIF, OrderRequest, OrderStatus, Side
    from tests.helpers import fixture_markets

    mk = fixture_markets()[Venue.ARCUS]
    by_id = {m.venue_market_id: m.base for m in mk.values()}
    base = {"orderId": "9", "clientId": "algab1", "marketId": mk["BTC"].venue_market_id, "side": "BUY",
            "price": "86000", "originalSize": "0.0002", "remainingSize": "0.0002"}
    assert parse_order({**base, "status": "CANCELED", "cancelReason": "MODIFY_CANCELED"}, by_id).status is OrderStatus.PENDING_NEW
    assert parse_order({**base, "status": "CANCELED"}, by_id).status is OrderStatus.CANCELED
    assert parse_order({**base, "status": "CANCEL_ACKNOWLEDGED"}, by_id).status is OrderStatus.OPEN
    assert parse_order({**base, "status": "EXPIRED"}, by_id).status is OrderStatus.EXPIRED
    assert parse_order({**base, "status": "SOMETHING_NEW"}, by_id).status is OrderStatus.PENDING_NEW

    ad = ArcusAdapter(None, None, address="0x" + "ab" * 20, account_index=0, markets=mk)  # type: ignore[arg-type]
    st = StateStore(tmp_path / "s.sqlite")
    req = OrderRequest(Venue.ARCUS, "BTC", Side.BUY, D("86000"), D("0.0002"), TIF.POST_ONLY, client_id="algab1")
    st.on_intent(req, "sess")
    ad._live["algab1"] = __import__("bot.venues.arcus.adapter", fromlist=["_Live"])._Live(req, mk["BTC"], "9", 0)

    async def feed() -> list[OrderStatus]:
        out = []
        for upd in ({**base, "status": "OPEN"}, {**base, "status": "CANCELED", "cancelReason": "MODIFY_CANCELED"},
                    {**base, "status": "PLACED", "price": "86001"}):
            await ad._on_orders([upd], 1, {"type": "update"})
            s_ = await ad._orders_q.get()
            st.on_update(s_)
            out.append(st.orders["algab1"].status)
        return out

    assert asyncio.run(feed()) == [OrderStatus.OPEN, OrderStatus.PENDING_NEW, OrderStatus.OPEN]
    assert "algab1" in ad.live_orders()
    st.close()
