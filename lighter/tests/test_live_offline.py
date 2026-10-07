"""The live exchange's account-stream parsing and signing, with frames shaped like Lighter's (docs/websocket), and
no network: nothing here sends a request."""

import time

import pytest

from lighter_bot.trade.exchange import Change
from lighter_bot.trade.live import LiveExchange
from lighter_bot.trade.strategy import BUY, SELL, Quote
from lighter_bot.venue import consts as C
from lighter_bot.venue import signer
from lighter_bot.venue.market import Market
from lighter_bot.venue.nonce import ClientIds, Nonces

M = Market(market_id=1, symbol="BTC", price_decimals=1, size_decimals=5, min_base=0.0002, min_quote=10.0,
           imf_min=200, imf_default=5000, mmf=120)
ACCT = 4242


class NoFeed:
    def bbo(self):
        return 84000.0, 84001.0, 1.0, 1.0


@pytest.fixture
def ex(cfg, tmp_path):
    from dataclasses import replace
    priv, _ = signer.generate_key()
    c = replace(cfg, creds=replace(cfg.creds, private_key=priv, api_key_index=5))
    e = LiveExchange(M, NoFeed(), c, ACCT, ClientIds(tmp_path / "ids"), Nonces(tmp_path / "n"))
    e.started_ms = 0
    return e


def test_changes_are_signed_into_one_batch(ex):
    txs, new, _ = ex._sign_changes([Change("new", Quote(BUY, 84000.04, 0.012345, "b")),
                                    Change("modify", Quote(SELL, 84010.0, 0.01, "a"), cid=99),
                                    Change("cancel", cid=77)])
    assert [t.tx_type for t in txs] == [C.TX_CREATE_ORDER, C.TX_MODIFY_ORDER, C.TX_CANCEL_ORDER]
    f = [signer.tx_fields(t) for t in txs]
    assert f[0]["Price"] == 840000 and f[0]["BaseAmount"] == 1234 and f[0]["TimeInForce"] == C.TIF_POST_ONLY
    assert f[1]["Index"] == 99 and f[1]["Price"] == 840100
    assert f[2]["Index"] == 77
    assert f[0]["Nonce"] < f[1]["Nonce"] < f[2]["Nonce"]            # a batch needs rising nonces
    assert new[0].px == pytest.approx(84000.0) and new[0].state == "sent"


def test_order_updates(ex):
    _, new, _ = ex._sign_changes([Change("new", Quote(BUY, 84000.0, 0.01, "b"))])
    o = new[0]
    ex.orders[o.cid] = o
    ex.on_msg({"channel": "account_orders:1", "type": "update/account_orders", "orders": {"1": [
        {"client_order_index": o.cid, "order_index": 2**48 + 5, "price": "84000.0", "remaining_base_amount": "0.01000",
         "status": "open", "is_ask": False}]}})
    assert ex.orders[o.cid].state == "open" and ex.orders[o.cid].oid == 2**48 + 5
    ex.on_msg({"channel": "account_orders:1", "type": "update/account_orders", "orders": {"1": [
        {"client_order_index": o.cid, "status": "canceled-post-only", "price": "84000.0"}]}})
    assert ex.orders[o.cid].state == "done" and ex.rejects
    # an order the bot did not place becomes one to cancel
    ex.on_msg({"channel": "account_orders:1", "type": "update/account_orders", "orders": {"1": [
        {"client_order_index": 0, "order_index": 2**48 + 9, "price": "83000.0", "remaining_base_amount": "0.5",
         "status": "open", "is_ask": False}]}})
    assert any(x.tag == "foreign" for x in ex.live_orders())


def test_positions_fills_and_equity(ex):
    fills = []
    ex.fill_cbs.append(fills.append)
    now_ms = int(time.time() * 1000)
    trade = {"trade_id": 1, "price": "84000.0", "size": "0.01", "is_maker_ask": False, "bid_account_id": ACCT,
             "ask_account_id": 9, "bid_client_id": 0, "timestamp": now_ms}
    ex.on_msg({"channel": "account_all:4242", "type": "subscribed/account_all",
               "positions": {"1": {"position": "0.01", "sign": 1, "avg_entry_price": "84000.0"}},
               "trades": {"1": [trade]}})
    assert ex.acct.pos == pytest.approx(0.01) and ex.acct.entry == 84000.0
    assert not fills                                   # the snapshot's trades are history
    ex.on_msg({"channel": "account_all:4242", "type": "update/account_all",
               "positions": {"1": {"position": "0.02", "sign": -1, "avg_entry_price": "84100.0"}},
               "trades": {"1": [{**trade, "trade_id": 2, "is_maker_ask": True, "bid_account_id": 9,
                                 "ask_account_id": ACCT, "maker_fee": 0}]}})
    assert ex.acct.pos == pytest.approx(-0.02)
    assert len(fills) == 1 and fills[0].side == SELL and fills[0].maker and fills[0].fee == 0
    ex.on_msg({"channel": "user_stats:4242", "type": "update/user_stats",
               "stats": {"portfolio_value": "101.5", "available_balance": "60", "total_stats": {}}})
    assert ex.acct.equity == pytest.approx(101.5)
