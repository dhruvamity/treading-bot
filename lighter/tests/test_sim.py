"""The backtest's rules on a tape built by hand."""

import numpy as np
import pytest

from lighter_bot.scout.sim import MarketRules, Sim, SimCfg, Window
from lighter_bot.scout.tape import DayTape, Tape, empty
from lighter_bot.trade.sizing import Stops, sizes

US = 1_000_000
T0 = 20_000 * 86_400 * US          # a UTC midnight
MR = MarketRules(tick=0.01, step=0.01, min_base=0.01, min_quote=10.0, mmf=0.012)


def tape(bbo_rows, trade_rows, depth_rows=()):
    t = DayTape("X", "day")
    b = np.array(bbo_rows, dtype=float) if bbo_rows else np.zeros((0, 5))
    t.bbo = {"ts": b[:, 0].astype(np.int64), "bid": b[:, 1], "ask": b[:, 2], "bid_sz": b[:, 3], "ask_sz": b[:, 4]}
    tr = empty("trades")
    if trade_rows:
        a = np.array(trade_rows, dtype=float)
        tr = {"ts": a[:, 0].astype(np.int64), "px": a[:, 1], "sz": a[:, 2], "buy": a[:, 3].astype(bool),
              "grp": a[:, 4].astype(np.int64), "taker": np.zeros(len(a), np.int64), "maker": np.zeros(len(a), np.int64),
              "liq": np.zeros(len(a), bool), "tid": np.arange(len(a), dtype=np.int64)}
    t.trades = tr
    return t


def flat_book(seconds=600, bid=100.0, ask=100.02, sz=5.0):
    return [(T0 + s * US, bid, ask, sz, sz) for s in range(seconds)]


def run(t, setup="mid 0", cfg=None, capital=100.0, lev=10.0):
    from lighter_bot.trade.strategy import parse
    s = parse(setup)
    w = Window(t, T0, T0 + 600 * US, cfg or SimCfg(warmup_s=0))
    return Sim(s.params(), sizes(capital, lev, Stops(50, 90, 99)), MR, cfg or SimCfg(warmup_s=0), s.name).run(w)


def test_an_order_better_than_the_book_is_filled_by_the_next_taker():
    # Mid 0 on a 2-tick book: the ask improves to 100.01 (flat, the bid steps back to 100.00); a taker buys at the
    # old best ask at t=10 s: it would have met our ask first
    trades = [(T0 + 10 * US, 100.02, 3.0, 1, 1)]
    r = run(tape(flat_book(), trades), "mid 0")
    assert r.maker_fills == 1
    assert r.maker_usd == pytest.approx(3.0 * 100.01, rel=1e-6)


def test_nothing_fills_before_the_speed_bump_lets_the_order_in():
    # first decision at 0 s, the order lands 0.28 s later; a taker at 0.1 s cannot reach it
    trades = [(T0 + 100_000, 100.02, 3.0, 1, 1)]
    r = run(tape(flat_book(), trades), "mid 0")
    assert r.maker_fills == 0


def test_joining_the_best_waits_behind_the_queue_shown():
    # Touch 0 joins the best bid with 5 ahead: a 3-lot print at our price does not reach us, a 4-lot next one does
    trades = [(T0 + 10 * US, 100.00, 3.0, 0, 1), (T0 + 11 * US, 100.00, 4.0, 0, 2)]
    r = run(tape(flat_book(), trades), "touch 0")
    assert r.maker_fills == 1
    assert r.maker_usd == pytest.approx(2.0 * 100.00, rel=1e-6)


def test_through_only_model_ignores_prints_at_our_price():
    trades = [(T0 + 10 * US, 100.00, 30.0, 0, 1)]
    r = run(tape(flat_book(), trades), "touch 0", SimCfg(warmup_s=0, queue=False))
    assert r.maker_fills == 0


def test_the_request_budget_skips_requotes():
    # the mid moves a tick every second: Mid 0 wants to requote every second, the budget allows 3 a minute
    rows = [(T0 + s * US, 100.0 + 0.01 * s, 100.02 + 0.01 * s, 5, 5) for s in range(120)]
    r = run(tape(rows, []), "mid 0", SimCfg(warmup_s=0, quotes_per_min=3))
    assert r.skipped > 50 and r.requests <= 3 * 3


def test_a_resting_order_is_replaced_before_its_expiry_and_goes_to_the_back_of_the_queue():
    # the live bot's orders expire 5.5 minutes after they are placed and are replaced 2 minutes before that
    quiet = run(tape(flat_book(), []), "touch 0")
    assert quiet.requests == 3                                    # placed, and replaced at 210 s and 420 s
    assert run(tape(flat_book(), []), "touch 0", SimCfg(warmup_s=0, renew_s=0)).requests == 1
    # 5 ahead of us; 3 trade at our price at 100 s; 4 more at 300 s. An order that had rested all along is reached
    # (2 left ahead); the replaced one is behind 5 again and is not
    trades = [(T0 + 100 * US, 100.00, 3.0, 0, 1), (T0 + 300 * US, 100.00, 4.0, 0, 2)]
    assert run(tape(flat_book(), trades), "touch 0", SimCfg(warmup_s=0, renew_s=0)).maker_fills == 1
    assert run(tape(flat_book(), trades), "touch 0").maker_fills == 0


def test_a_stopped_position_under_the_minimum_order_is_closed_with_a_taker_order():
    # Touch 0 joins the bid with 5 ahead; a print of 5.05 at our price leaves us 0.05 ($5, under the $10 minimum).
    # The bid then falls 3 cents: past a position stop of a tenth of a cent. No maker order can rest for $5.
    from lighter_bot.trade.strategy import parse
    rows = [(T0 + s * US, 100.0 if s < 20 else 99.97, 100.02 if s < 20 else 99.99, 5.0, 5.0) for s in range(120)]
    t = tape(rows, [(T0 + 10 * US, 100.00, 5.05, 0, 1)])
    s = parse("touch 0")
    cfg = SimCfg(warmup_s=0)
    r = Sim(s.params(), sizes(100.0, 10.0, Stops(0.001, 90, 99)), MR, cfg, s.name).run(Window(t, T0, T0 + 120 * US, cfg))
    assert r.pos_stops == 1 and r.taker_fills == 1 and r.taker_usd == pytest.approx(0.05 * 99.97, rel=1e-3)
    assert r.end_pos_usd == 0


def test_tape_round_trip_and_dedupe(tmp_path):
    tp = Tape(tmp_path)
    cols = {"ts": np.array([1, 2], np.int64), "bid": np.array([1.0, 1.0]), "ask": np.array([2.0, 2.0]),
            "bid_sz": np.array([1.0, 1.0]), "ask_sz": np.array([1.0, 1.0])}
    tp.write("X", "2026-01-01", "bbo", cols, part="a")
    tp.write("X", "2026-01-01", "bbo", cols, part="b")         # the same rows from a second writer
    got = tp.load("X", "2026-01-01", "bbo")
    assert list(got["ts"]) == [1, 2]
    assert tp.days("X") == ["2026-01-01"] and tp.markets() == ["X"]
