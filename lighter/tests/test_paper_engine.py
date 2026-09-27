"""The paper exchange's fills and the engine's order diff, without a network."""

import asyncio
import time

import pytest

from lbot.config import Latency, Requests
from lbot.trade.engine import Engine, RunSpec, RunState, send_control
from lbot.trade.exchange import Change, Order, PaperExchange
from lbot.trade.strategy import BUY, SELL, Quote
from lbot.venue.book import Book
from lbot.venue.market import Market
from lbot.venue.nonce import ClientIds

M = Market(market_id=1, symbol="X", price_decimals=2, size_decimals=2, min_base=0.01, min_quote=10.0, imf_min=200,
           imf_default=5000, mmf=120)


class FakeFeed:
    def __init__(self):
        self.book = Book(1)
        self.book.snapshot({"bids": [{"price": "100.00", "size": "5"}], "asks": [{"price": "100.02", "size": "5"}],
                            "nonce": 1})
        self.trade_listeners = []

    def bbo(self):
        b, a = self.book.best_bid(), self.book.best_ask()
        return b[0], a[0], b[1], a[1]

    def fresh(self, max_age_s=10.0):
        return True

    async def start(self):
        return None

    async def stop(self):
        return None


def paper(tmp_path):
    return PaperExchange(M, FakeFeed(), Requests(), Latency(maker_ms=0, network_ms=0, taker_ms=0),
                         ClientIds(tmp_path / "ids"), 100.0)


def trade(px, sz, taker_buy, h="h1"):
    return {"price": str(px), "size": str(sz), "is_maker_ask": taker_buy, "tx_hash": h, "usd_amount": str(px * sz)}


def test_paper_fills_through_and_behind_the_queue(tmp_path):
    ex = paper(tmp_path)
    fills = []
    ex.fill_cbs.append(fills.append)
    asyncio.run(ex.send([Change("new", Quote(BUY, 100.01, 2.0, "b")), Change("new", Quote(SELL, 100.02, 2.0, "a"))],
                        "quote"))
    ex.tick(time.time() + 1)
    ex.on_trades([trade(100.00, 1.5, False, "s1")])          # a taker sells at 100.00: through our 100.01 bid
    assert fills and fills[0].side == BUY and fills[0].qty == pytest.approx(1.5)
    ex.on_trades([trade(100.02, 4.0, True, "b1")])           # a taker buys 4 at our ask: 5 were ahead
    assert len(fills) == 1
    ex.on_trades([trade(100.02, 3.0, True, "b2")])           # 1 ahead left: we get 2
    assert fills[-1].side == SELL and fills[-1].qty == pytest.approx(2.0)
    assert ex.acct.pos == pytest.approx(-0.5)


def test_paper_rejects_a_crossing_post_only(tmp_path):
    ex = paper(tmp_path)
    asyncio.run(ex.send([Change("new", Quote(BUY, 100.02, 1.0, "b"))], "quote"))
    ex.tick(time.time() + 1)
    assert not ex.live_orders() and ex.rejects


def test_engine_diff_keeps_modifies_and_cancels(tmp_path):
    ex = paper(tmp_path)
    run = RunState(RunSpec("X", "Mid 0", 10.0))
    eng = Engine(ex, run, tmp_path)
    live = [Order(1, BUY, 100.00, 1.0, "b", state="open"), Order(2, SELL, 100.05, 1.0, "a", state="open"),
            Order(3, BUY, 99.0, 1.0, "foreign", state="open"), Order(4, SELL, 100.5, 1.0, "x", state="sent")]
    ch = eng.diff([Quote(BUY, 100.00, 1.05, "b"), Quote(SELL, 100.02, 1.0, "a")], live)
    kinds = sorted((c.kind, c.cid) for c in ch)
    assert kinds == [("cancel", 3), ("modify", 2)]            # the bid is within tolerance; 4 is in flight


def test_engine_quotes_and_obeys_controls(tmp_path):
    ex = paper(tmp_path)
    run = RunState(RunSpec("X", "Mid 0", 10.0, capital=100.0))
    eng = Engine(ex, run, tmp_path)
    asyncio.run(eng.step(time.time()))
    assert {o.side for o in ex.live_orders()} == {BUY, SELL}
    send_control(tmp_path, "paper", "pause")
    asyncio.run(eng.step(time.time() + 1))
    assert eng.paused and "paused" in eng.why
    send_control(tmp_path, "paper", "stop")
    asyncio.run(eng.step(time.time() + 2))
    assert eng._stop.is_set()


def test_paper_queue_is_found_despite_float_noise(tmp_path):
    ex = paper(tmp_path)
    fills = []
    ex.fill_cbs.append(fills.append)
    asyncio.run(ex.send([Change("new", Quote(BUY, 100.00000000001, 2.0, "b"))], "quote"))   # float noise
    ex.tick(time.time() + 1)
    ex.on_trades([trade(100.00, 4.0, False, "s1")])          # 5 shown ahead at 100.00: nothing reaches us
    assert not fills


def test_a_restart_keeps_the_stops_state(tmp_path):
    ex = paper(tmp_path)
    run = RunState(RunSpec("X", "Mid 0", 10.0, capital=100.0), guard={"day": 1, "day_eq": None, "peak": None,
                                                                         "state": "killed"})
    eng = Engine(ex, run, tmp_path)
    asyncio.run(eng.step(time.time()))
    assert eng.guard.state == "killed" and not ex.live_orders()          # a kill waits for your resume
