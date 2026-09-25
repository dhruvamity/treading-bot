"""Scout backtester: fill rules, the $100 account's stops, and parity with the live strategy and engine."""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Callable
from decimal import Decimal as D
from pathlib import Path

import numpy as np
import pytest

from bot.core.book import L2Book
from bot.core.marketdata import MarketView
from bot.scout.sim import BUY, SELL, Book, Config, MarketInfo, MidPolicy, Risk, Sim, Window
from bot.scout.tape import US_DAY, DayTape, TapeStore, day_start_us
from bot.strategies import make_strategy
from bot.strategies.base import StrategyContext
from bot.venues.base import Side, Venue
from tests.helpers import fixture_markets, mm_session
from tests.sim.engine import SimConfig, Simulator
from tests.sim.events import merge
from tests.sim.synthetic import book_events, trend_path

S = 1_000_000
T0 = day_start_us("2026-09-20")
MI = MarketInfo(tick=0.01, step=0.0001, min_notional=5.0)


def tape(path: Callable[[int], float], seconds: int, *, spread_bps: float = 4.0, every_s: int = 5,
         sweep_bps: float = 6.0, size: float = 1.0, start: int = T0,
         side: Callable[[int], bool] | None = None) -> DayTape:
    """A BBO row per second around path(s), and every `every_s` a taker order that prints at the touch and then
    `sweep_bps` through it (one sequenceNumber). side(s) -> True for a taker buy (default: alternate)."""
    ts, bid, ask = [], [], []
    tts, px, sz, buy, seq = [], [], [], [], []
    k = 0
    for s in range(seconds):
        m = path(s)
        h = m * spread_bps / 2e4
        t = start + s * S
        ts.append(t)
        bid.append(round(m - h, 2))
        ask.append(round(m + h, 2))
        if s % every_s == 0 and s > 0:
            k += 1
            b = side(s) if side else (k % 2 == 0)
            touch = ask[-1] if b else bid[-1]
            deep = touch * (1 + (sweep_bps if b else -sweep_bps) / 1e4)
            for j, p in enumerate((touch, round(deep, 2))):
                tts.append(t + 500_000 + j)
                px.append(p)
                sz.append(size / 2)
                buy.append(b)
                seq.append(k)
    n = len(ts)
    return DayTape("TEST-USD", "2026-09-20",
                   {"ts": np.array(ts, np.int64), "bid": np.array(bid), "ask": np.array(ask),
                    "bid_sz": np.full(n, 1.0), "ask_sz": np.full(n, 1.0)},
                   {"ts": np.array(tts, np.int64), "px": np.array(px), "sz": np.array(sz),
                    "buy": np.array(buy, bool), "seq": np.array(seq, np.int64), "tid": np.arange(len(tts))})


def run(t: DayTape, cfg: Config, risk: Risk | None = None, start: int = T0, end: int | None = None):  # type: ignore[no-untyped-def]
    end = end or int(t.bbo["ts"][-1]) + S
    return Sim(cfg, risk or Risk(), MI).run(Window(t, start, end, warmup_s=0))


FLAT = Config("deep 3bp", "mid", spacing_bps=3, safety=False)


def test_ranging_market_fills_both_sides_and_earns_the_spread() -> None:
    r = run(tape(lambda s: 100 + 0.02 * math.sin(s / 60), 4 * 3600), FLAT)
    assert r.maker_fills > 100 and r.taker_fills == 0
    assert 5 <= r.maker_usd / r.maker_fills <= 50   # $25 clips, skewed up to 2x by inventory
    assert r.pnl > 0 and r.pos_stops == 0 and r.day_stops == 0


def test_a_trade_at_our_price_does_not_fill_us() -> None:
    # takers print only at the touch (no sweep); quotes join the touch -> we never learn our queue place: no fills
    t = tape(lambda s: 100.0, 3600, spread_bps=6, sweep_bps=0)
    r = run(t, Config("touch", "mid", style="normal", spacing_bps=0.5, safety=False))
    assert r.maker_fills == 0


def test_one_taker_order_fills_us_for_at_most_its_own_size() -> None:
    t = tape(lambda s: 100.0, 600, size=0.1)   # each taker order is 0.1 units (~$10), our clip is $25
    r = run(t, FLAT)
    assert r.maker_fills > 0
    assert r.maker_usd / r.maker_fills <= 10.0 + 1e-6


def test_position_stop_exits_maker_then_taker_then_cools_down() -> None:
    # falling price, only taker sells: we keep buying, the position stop fires, the maker exit can never fill
    t = tape(lambda s: 100 * (1 - 0.00002 * s), 3 * 3600, side=lambda s: False)
    r = run(t, FLAT, Risk(pos_stop_usd=0.25, daily_stop_usd=50, kill_usd=50))
    assert r.pos_stops >= 2                     # stopped, cooled down, quoted again, stopped again
    assert r.taker_fills >= r.pos_stops         # every stop ended with a taker exit
    assert r.taker_usd > 0 and r.fees > 0


def test_daily_stop_blocks_new_orders_until_the_next_utc_day() -> None:
    start = T0 + US_DAY - 3 * 3600 * S          # 21:00 UTC; the day ends 3 h later
    fall = tape(lambda s: 100 * (1 - 0.0001 * min(s, 3600)), 6 * 3600, start=start, side=lambda s: False)
    r = run(fall, FLAT, Risk(pos_stop_usd=50, daily_stop_usd=0.5, kill_usd=50), start=start)
    assert r.day_stops == 1
    # the same run cut at midnight: every fill after the stop and before 00:00 UTC would show up here
    first_day = Sim(FLAT, Risk(pos_stop_usd=50, daily_stop_usd=0.5, kill_usd=50), MI).run(
        Window(fall, start, T0 + US_DAY, warmup_s=0))
    assert first_day.day_stops == 1
    until_stop = Sim(FLAT, Risk(pos_stop_usd=50, daily_stop_usd=0.5, kill_usd=50), MI).run(
        Window(fall, start, r.first_day_stop_us + S, warmup_s=0))
    assert first_day.maker_fills == until_stop.maker_fills   # nothing filled between the stop and 00:00 UTC
    assert r.maker_fills > first_day.maker_fills             # after midnight it quoted and traded again by itself


def test_kill_flattens_and_stops_for_good() -> None:
    t = tape(lambda s: 100 * (1 - 0.00005 * s), 4 * 3600, side=lambda s: False)
    r = run(t, FLAT, Risk(pos_stop_usd=50, daily_stop_usd=50, kill_usd=0.5))
    assert r.killed and r.taker_fills >= 1 and r.end_pos_usd == 0
    assert r.min_equity_delta > -3                # the loss stayed near the kill level


def test_store_roundtrip_dedupes_trades(tmp_path: Path) -> None:
    t = tape(lambda s: 100.0, 120)
    st = TapeStore(tmp_path)
    st.write_part("TEST-USD", "bbo", "a", t.bbo)
    st.write_part("TEST-USD", "trades", "a", t.trades)
    st.write_part("TEST-USD", "trades", "b", t.trades)   # same trades again from another source
    back = st.load_day("TEST-USD", "2026-09-20")
    assert back.n_trades == t.n_trades and back.n_bbo == t.n_bbo


# ------------------------------------------------------------------------------------------------ parity
@pytest.mark.parametrize("inv", [D(0), D("0.05"), D("-0.2")])
def test_mid_policy_quotes_what_the_live_strategy_quotes(inv: D) -> None:
    m = fixture_markets()[Venue.ARCUS]["AMD"]
    sess = mm_session(market="AMD", mode="mid", execution_style="passive", passive_k_sigma=0.0, spacing_bps=3,
                      levels_per_side=1, skew_kappa=0.0, order_size_usd=25, inventory_cap_usd=50)
    view = MarketView(Venue.ARCUS, "AMD")
    view.book = L2Book()
    view.book.load([(D("620.00"), D("1"))], [(D("620.40"), D("1"))], 1)
    ctx = StrategyContext(now_us=T0, venue=Venue.ARCUS, market=m, view=view, params=sess, inventory=inv)
    out = make_strategy(sess).on_tick(ctx)
    live = sorted((o.side, o.price_ticks, o.size_quantums) for o in out.desired[(Venue.ARCUS, "AMD")])
    mi = MarketInfo(float(m.tick_size), float(m.step_size), float(m.min_notional), float(m.min_size))
    pol = MidPolicy(Config("x", "mid", spacing_bps=3), Risk(order_usd=25, cap_usd=50), mi)
    q, _ = pol.quotes(Book(T0, 620.0, 620.4, 620.2, float(inv), None, 0.0, 0.0, deque()))
    ours = sorted((Side.BUY if s == BUY else Side.SELL, round(p / mi.tick), round(qq / mi.step)) for s, p, qq, _t in q)
    assert live == ours
    assert {s for s, *_ in q} <= {BUY, SELL}


def test_live_engine_position_stop_closes_with_a_taker_order() -> None:
    mk = fixture_markets()
    a = mk[Venue.ARCUS]["BTC"]
    ev = list(merge([book_events(Venue.ARCUS, "BTC", trend_path(86000, -0.01), start_us=1_790_000_000_000_000,
                                 seconds=3600, tick=a.tick_size, step=a.step_size, half_spread_ticks=5,
                                 trades_per_s=2.0, trade_size=D("0.01"), seed=3)]))
    sess = mm_session(mode="mid", execution_style="aggressive", spacing_bps=2, levels_per_side=1, order_size_usd=25,
                      inventory_cap_usd=50, capital_usd=100, pos_stop_usd=0.25, exit_taker_after_s=20,
                      cooldown_s=60, daily_stop_usd=50, kill_usd=50)
    res = Simulator([sess], mk, SimConfig()).run_sync(ev)
    reasons = [str(d.get("reason")) for d in res.decisions]
    assert any(r.startswith("open position") and "stop $0.25" in r for r in reasons)
    assert any("maker exit timed out" in r for r in reasons)
    assert any(f.tag == "exit_ioc" for f in res.fills)   # the unfilled maker exit was replaced by a taker order
