import time

import pytest

from lbot.venue import consts as C
from lbot.venue.book import Book
from lbot.venue.market import Market, parse_markets
from lbot.venue.nonce import ClientIds, Nonces
from lbot.venue.rest import QUOTE, READ, RESERVE, Budget

BTC = {"symbol": "BTC", "market_id": 1, "market_type": "perp", "status": "active", "taker_fee": "0.0000",
       "maker_fee": "0.0000", "min_base_amount": "0.00020", "min_quote_amount": "10.000000",
       "supported_size_decimals": 5, "supported_price_decimals": 1, "default_initial_margin_fraction": 5000,
       "min_initial_margin_fraction": 200, "maintenance_margin_fraction": 120, "last_trade_price": 84000.0,
       "daily_quote_token_volume": 4e7, "market_config": {"force_reduce_only": False, "hidden": False}}


def test_market_scaling_and_leverage():
    m = parse_markets({"order_book_details": [BTC]})["BTC"]
    assert m.tick == pytest.approx(0.1) and m.step == pytest.approx(1e-5)
    assert m.max_leverage == 50
    assert m.price_int(84000.05, side_buy=True) == 840000
    assert m.price_int(84000.05, side_buy=False) == 840001
    assert m.price_int(84000.1 + 1e-9, side_buy=False) == 840001        # float noise stays on the tick
    assert m.size_int(0.012345678) == 1234
    assert m.leverage_fraction(20) == 500 and m.leverage_fraction(100) == 200
    assert m.min_order_usd(84000) == pytest.approx(max(10, 0.0002 * 84000))
    assert Market.from_dict(m.as_dict()) == m


def test_book_nonce_continuity():
    b = Book(1)
    b.snapshot({"bids": [{"price": "100", "size": "1"}], "asks": [{"price": "101", "size": "2"}], "nonce": 10})
    assert b.update({"bids": [{"price": "100.5", "size": "3"}], "asks": [], "nonce": 12, "begin_nonce": 10}) == "ok"
    assert b.best_bid() == (100.5, 3.0)
    assert b.update({"bids": [], "asks": [], "nonce": 11, "begin_nonce": 9}) == "stale"
    assert b.update({"bids": [{"price": "100.5", "size": "0"}], "asks": [], "nonce": 20, "begin_nonce": 15}) == "gap"
    assert not b.synced


async def test_a_book_gap_resubscribes_once_until_the_snapshot():
    """Every update after a gap reads as a gap until the new snapshot (Lighter sends one every 50 ms): one re-subscribe,
    not one per update; a snapshot that never comes is asked for again after RESYNC_RETRY_S."""
    import asyncio

    from lbot.trade import feed as feed_mod
    m = Market(market_id=1, symbol="BTC", price_decimals=1, size_decimals=5, min_base=0.0002, min_quote=10.0,
               imf_min=200, imf_default=5000, mmf=120)
    f = feed_mod.MarketFeed("wss://offline.invalid/stream", m)
    sent: list[str] = []

    async def resubscribe(channel: str) -> None:
        sent.append(channel)
    f.ws.resubscribe = resubscribe
    book = lambda n, begin, sub=False: {"channel": "order_book:1", "type": "subscribed/order_book" if sub else  # noqa: E731
                                        "update/order_book", "order_book": {"bids": [{"price": "100", "size": "1"}],
                                                                            "asks": [{"price": "101", "size": "1"}],
                                                                            "nonce": n, "begin_nonce": begin}}
    f.on_msg(book(10, 0, sub=True))
    f.on_msg(book(12, 11))                        # the gap
    for n in range(13, 20):                       # updates in flight before the new snapshot
        f.on_msg(book(n, n - 1))
    await asyncio.sleep(0)
    assert sent == ["order_book/1"]
    assert not f.book.synced
    f.resync_at -= feed_mod.RESYNC_RETRY_S + 1    # no snapshot came: ask again
    f.on_msg(book(21, 20))
    await asyncio.sleep(0)
    assert len(sent) == 2
    f.on_msg(book(30, 0, sub=True))               # the snapshot: in sync, and the next gap is a new one
    assert f.book.synced and f.resync_at == 0.0
    f.on_msg(book(33, 31))
    await asyncio.sleep(0)
    assert len(sent) == 3


def test_nonces_and_client_ids_rise_and_survive_a_restart(tmp_path):
    p = tmp_path / "n.txt"
    n = Nonces(p)
    a = n.take(3, now_ms=1_000)
    assert a == [1000, 1001, 1002]
    assert Nonces(p).take(1, now_ms=500) == [1003]          # the clock went back: still rising
    ids = ClientIds(tmp_path / "c.txt")
    x = ids.take(2, now_ms=1_790_000_000_000)
    assert x[1] == x[0] + 1 and x[1] < C.MAX_CLIENT_ORDER_INDEX


def test_budget_keeps_room_for_exits():
    t = [0.0]
    from lbot.config import Requests
    b = Budget(Requests(per_min=10, quotes_per_min=6, reserve_per_min=4), clock=lambda: t[0])
    assert sum(b.take(QUOTE) for _ in range(10)) == 6
    assert b.take(READ) is False                  # reads never use the reserve
    assert b.take(RESERVE) and b.take(RESERVE)
    t[0] = 61.0
    assert b.take(QUOTE)


def test_signer_signs_offline():
    from lbot.venue import signer
    priv, _ = signer.generate_key()
    s = signer.Signer("https://example.invalid", priv, 466324, 4, 7)
    tx = s.create_order(market=1, client_index=123, size=100, price=840000, is_ask=True, order_type=C.ORDER_LIMIT,
                        tif=C.TIF_POST_ONLY, reduce_only=False, expiry=-1, nonce=int(time.time() * 1000))
    f = signer.tx_fields(tx)
    assert tx.tx_type == C.TX_CREATE_ORDER
    assert (f["AccountIndex"], f["ApiKeyIndex"], f["ClientOrderIndex"], f["IsAsk"], f["TimeInForce"]) == (7, 4, 123, 1, 2)
    assert f["L2TxAttributes"] == {"4": 1}                      # SkipNonce on
    with pytest.raises(signer.SignerError):
        signer.Signer("https://example.invalid", priv, 466324, 2, 7)     # an app's slot
