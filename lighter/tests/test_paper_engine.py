"""The paper exchange's fills and the engine's order diff, without a network."""

import asyncio
import time

import pytest

from lighter_bot.config import Latency, Requests
from lighter_bot.trade.engine import Engine, RunSpec, RunState, send_control
from lighter_bot.trade.exchange import Change, Order, PaperExchange
from lighter_bot.trade.strategy import BUY, SELL, Quote
from lighter_bot.venue.book import Book
from lighter_bot.venue.market import Market
from lighter_bot.venue.nonce import ClientIds

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


def test_paper_orders_expire_as_live_ones_do(tmp_path):
    ex = paper(tmp_path)
    t0 = time.time()
    asyncio.run(ex.send([Change("new", Quote(BUY, 100.00, 1.0, "b"))], "quote"))
    ex.tick(t0 + 1)
    o = ex.live_orders()[0]
    assert o.state == "open" and o.expires == pytest.approx(t0 + 330, abs=2)
    ex.tick(t0 + 329)
    assert ex.live_orders()                                  # the engine replaces it long before this
    ex.tick(t0 + 332)
    assert not ex.live_orders() and o.why_done == "canceled-expired" and not ex.rejects


def test_engine_diff_keeps_modifies_and_cancels(tmp_path):
    ex = paper(tmp_path)
    run = RunState(RunSpec("X", "Mid 0", 10.0))
    eng = Engine(ex, run, tmp_path)
    live = [Order(1, BUY, 100.00, 1.0, "b", state="open"), Order(2, SELL, 100.05, 1.0, "a", state="open"),
            Order(3, BUY, 99.0, 1.0, "foreign", state="open"), Order(4, SELL, 100.5, 1.0, "x", state="sent")]
    ch = eng.diff([Quote(BUY, 100.00, 1.05, "b"), Quote(SELL, 100.02, 1.0, "a")], live)
    kinds = sorted((c.kind, c.cid) for c in ch)
    assert kinds == [("cancel", 3), ("modify", 2)]            # the bid is within tolerance; 4 is in flight


def test_engine_diff_replaces_a_live_order_close_to_its_expiry(tmp_path):
    eng = Engine(paper(tmp_path), RunState(RunSpec("X", "Mid 0", 10.0)), tmp_path)
    now = 1_000_000.0
    live = [Order(1, BUY, 100.00, 1.0, "b", state="open", expires=now + 119),      # Lighter drops it in under 2 minutes
            Order(2, SELL, 100.02, 1.0, "a", state="open", expires=now + 300),
            Order(3, BUY, 100.00, 1.0, "c", state="sent", expires=now + 5)]        # in flight: left alone
    ch = eng.diff([Quote(BUY, 100.00, 1.0, "b"), Quote(SELL, 100.02, 1.0, "a"), Quote(BUY, 100.00, 1.0, "c")], live, now)
    assert [(c.kind, c.cid) for c in ch] == [("cancel", 1), ("new", 0)] and ch[1].quote.tag == "b"
    assert eng.diff([Quote(SELL, 100.02, 1.0, "a")], live[1:2], now + 181)[0].kind == "cancel"   # 119 s left by then


def test_the_engine_keeps_the_stop_on_the_venue_at_twice_its_own_position_stop(tmp_path):
    ex = paper(tmp_path)
    eng = Engine(ex, RunState(RunSpec("X", "Mid 0", 10.0, capital=100.0)), tmp_path)
    asked: list[tuple[float, float]] = []

    async def protect(now: float, loss_usd: float) -> None:
        asked.append((now, loss_usd))

    ex.protect = protect                                     # the live exchange has one; paper has none
    now = time.time()
    asyncio.run(eng.step(now))
    assert asked == [(now, pytest.approx(2 * eng.sizes.pos_stop_usd))] and eng.sizes.pos_stop_usd > 0


def test_a_run_with_a_time_limit_keeps_its_position_unless_told_to_end_flat(tmp_path):
    def held(sub: str) -> tuple[PaperExchange, Engine]:
        ex = paper(tmp_path / sub)
        eng = Engine(ex, RunState(RunSpec("X", "Mid 0", 10.0, capital=100.0)), tmp_path / sub)
        eng.period = 0.02
        ex._book_fill(BUY, 100.01, 0.5)                       # the bot is long when its time is up
        ex.cash -= 0.5 * 100.01
        return ex, eng

    ex, eng = held("a")
    asyncio.run(eng.run_loop(0.1))
    assert ex.acct.pos == 0.5 and not eng.stop_after_close and not [o for o in ex.live_orders() if o.state == "open"]
    ex, eng = held("b")
    t0 = time.time()

    async def go() -> None:
        task = asyncio.create_task(eng.run_loop(0.1, True))
        while not task.done() and time.time() - t0 < 5:
            await asyncio.sleep(0.05)
            if eng.stop_after_close:
                want = [o for o in ex.live_orders() if o.tag == "exit" and o.state == "open"]
                if want and ex.acct.pos:                      # the maker exit at the touch: let it fill
                    ex.cash += ex.acct.pos * want[0].px
                    ex._book_fill(SELL, want[0].px, ex.acct.pos)
                    want[0].state = "done"
        await task

    asyncio.run(go())
    assert eng.stop_after_close and ex.acct.pos == 0 and time.time() - t0 < 5      # closed first, then stopped


def test_a_position_too_small_for_a_resting_order_is_closed_with_a_taker_order(tmp_path):
    """The first live run (2026-10-10) ended with $9 of SPY: under Lighter's $10 minimum. The maker exit was refused
    40 times in 20 s before the taker order closed it."""
    ex = paper(tmp_path)
    eng = Engine(ex, RunState(RunSpec("X", "Mid 0", 10.0, capital=100.0)), tmp_path)
    now = time.time()
    asyncio.run(eng.step(now))
    ex.orders.clear()
    ex._book_fill(BUY, 100.01, 0.05)                         # $5: under the market's $10 minimum
    ex.cash -= 0.05 * 100.01
    eng.ask_close("closing: asked")
    asyncio.run(eng.step(now + 1))
    assert [q for _, q in ex.pending_takers] == [-0.05] and not [o for o in ex.live_orders() if o.tag == "exit"]
    ex.pending_takers.clear()
    ex._book_fill(BUY, 100.01, 0.45)                         # $50 now: a maker order at the touch, as before
    ex.cash -= 0.45 * 100.01
    asyncio.run(eng.step(now + 5))
    assert [(o.tag, o.qty, o.reduce_only) for o in ex.live_orders()] == [("exit", 0.5, True)] and not ex.pending_takers


def test_the_engine_holds_its_order_changes_after_a_refused_request(tmp_path):
    ex = paper(tmp_path)
    eng = Engine(ex, RunState(RunSpec("X", "Mid 0", 10.0, capital=100.0)), tmp_path)
    now = time.time()
    ex.hold_until = now + 2.0                                # what the live exchange sets when Lighter refuses a request
    asyncio.run(eng.step(now))
    assert not ex.live_orders()
    asyncio.run(eng.step(now + 2.5))
    assert {o.side for o in ex.live_orders()} == {BUY, SELL}


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
