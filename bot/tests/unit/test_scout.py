"""Scout backtester: fill rules, the $100 account's stops, and parity with the live strategy and engine."""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Callable
from decimal import Decimal as D
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from bot.core.book import L2Book
from bot.core.marketdata import MarketView
from bot.scout.sim import BUY, SELL, AnchorPolicy, Book, Config, MarketInfo, MidPolicy, Risk, Sim, Window
from bot.scout.tape import US_DAY, DayTape, TapeStore, day_start_us
from bot.strategies import make_strategy
from bot.strategies.base import StrategyContext
from bot.venues.base import Fill, Side, Venue
from tests.helpers import fixture_markets, mm_session
from tests.sim.engine import SimConfig, Simulator
from tests.sim.events import merge
from tests.sim.synthetic import book_events, sine_path, trend_path

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


@pytest.mark.parametrize(("bid", "ask"), [("620.00", "620.40"), ("100.10", "100.30"), ("642.97", "643.05")])
def test_touch_quotes_sit_where_the_backtest_puts_them(bid: str, ask: str) -> None:
    # "touch 1bp" on a spread wider than 2 bp quotes AT the touch: float noise (620.2 + 0.2 = 620.4000000000001)
    # once rounded the live ask a tick behind it, where the backtest never assumed it was
    m = fixture_markets()[Venue.ARCUS]["AMD"]
    sess = mm_session(market="AMD", mode="mid", execution_style="normal", spacing_bps=1, levels_per_side=1,
                      skew_kappa=0.0, order_size_usd=25, inventory_cap_usd=50)
    view = MarketView(Venue.ARCUS, "AMD")
    view.book = L2Book()
    view.book.load([(D(bid), D("1"))], [(D(ask), D("1"))], 1)
    ctx = StrategyContext(now_us=T0, venue=Venue.ARCUS, market=m, view=view, params=sess)
    live = sorted((o.side, o.price_ticks) for o in make_strategy(sess).on_tick(ctx).desired[(Venue.ARCUS, "AMD")])
    mi = MarketInfo(float(m.tick_size), float(m.step_size), float(m.min_notional), float(m.min_size))
    b, a = float(bid), float(ask)
    q, _ = MidPolicy(Config("touch 1bp", "mid", style="normal", spacing_bps=1), Risk(order_usd=25, cap_usd=50),
                     mi).quotes(Book(T0, b, a, (b + a) / 2, 0.0, None, 0.0, 0.0, deque()))
    assert live == sorted((Side.BUY if sd == BUY else Side.SELL, round(p / mi.tick)) for sd, p, _q, _t in q)


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


def test_live_engine_runs_the_anchor_grid() -> None:
    mk = fixture_markets()
    a = mk[Venue.ARCUS]["BTC"]

    def run_on(path: Any) -> Any:
        ev = list(merge([book_events(Venue.ARCUS, "BTC", path, start_us=1_790_000_000_000_000, seconds=1800,
                                     tick=a.tick_size, step=a.step_size, half_spread_ticks=5, trades_per_s=2.0,
                                     trade_size=D("0.01"), seed=5)]))
        sess = mm_session(mode="anchor", spacing_bps=3, reset_threshold_pct=0.1, levels_per_side=1, order_size_usd=25,
                          inventory_cap_usd=50, capital_usd=100, daily_stop_usd=50, pos_stop_usd=50, kill_usd=50,
                          safety_pause={"move_sigma_1s": 1e9, "spread_x_median": 1e9, "depth_frac_min": 0.0})
        return Simulator([sess], mk, SimConfig()).run_sync(ev)

    # 4 bp of noise a second: prices jump through quotes 3 bp away, as sweeps do on a real book
    chop = run_on(sine_path(86000, 0.0003, 120, 0.0004))
    grid = [f for f in chop.fills if f.tag in ("b0", "a0")]
    assert len(grid) > 100 and {f.side for f in grid} == {Side.BUY, Side.SELL}
    trend = run_on(trend_path(86000, -0.01, 0.0004))     # -1%/h: it fills long, stalls, then soft-resets
    assert any("anchor soft reset" in str(d.get("reason")) for d in trend.decisions)


def test_live_engine_quotes_nothing_in_a_skip_window() -> None:
    mk = fixture_markets()
    a = mk[Venue.ARCUS]["BTC"]
    start = 1_790_000_000_000_000   # Monday 2026-09-21 10:13 New York time
    ev = list(merge([book_events(Venue.ARCUS, "BTC", trend_path(86000, 0.0), start_us=start, seconds=900,
                                 tick=a.tick_size, step=a.step_size, half_spread_ticks=5, trades_per_s=2.0,
                                 trade_size=D("0.01"), seed=4)]))

    def run_with(skip: list[str]) -> Any:
        sess = mm_session(mode="mid", execution_style="aggressive", spacing_bps=2, levels_per_side=1,
                          order_size_usd=25, inventory_cap_usd=50, capital_usd=100, session={"skip_et": skip})
        return Simulator([sess], mk, SimConfig()).run_sync(ev)

    assert run_with([]).fills
    res = run_with(["09:00-16:30"])
    assert not res.fills
    assert any("skip window 09:00-16:30" in str(d.get("reason")) for d in res.decisions)
    assert run_with(["16:30-17:00"]).fills


# ------------------------------------------------------------------------------------------------ anchor, skip windows
ANCHOR = Config("anchor 3bp", "anchor", spacing_bps=3, reset_pct=0.1, safety=False)


def at(mid: float, pos: float, spread_bps: float = 1.0) -> Book:
    h = mid * spread_bps / 2e4
    return Book(T0, mid - h, mid + h, mid, pos, None, 0.0, 0.0, deque())


def prices(q: list[tuple[int, float, float, str]]) -> set[tuple[int, float, str]]:
    return {(s, round(p, 2), tag) for s, p, _q, tag in q}


def test_anchor_quotes_around_the_last_fill_and_soft_resets() -> None:
    pol = AnchorPolicy(ANCHOR, Risk(order_usd=25, cap_usd=50), MI)
    q, _ = pol.quotes(at(100.0, 0.0))
    assert prices(q) == {(BUY, 99.97, "b0"), (SELL, 100.03, "a0")}   # flat: mid -/+ 3 bp
    pol.on_fill(BUY, 99.97, 0.25, "b0", T0, 0.25)
    q, _ = pol.quotes(at(99.95, 0.25))
    # the sell stays 3 bp above the buy (not 3 bp above the lower mid); the next buy is 3 bp under the buy
    assert prices(q) == {(BUY, 99.94, "b0"), (SELL, 100.0, "a0")}
    q, _ = pol.quotes(at(99.80, 0.25))       # 0.17% under the last fill: stop adding, close at the touch
    assert [(s, tag) for s, _p, _q, tag in q] == [(SELL, "exit")] and q[0][2] == 0.25
    q, _ = pol.quotes(at(99.95, 0.25))       # still closing, even though the price came back inside the reset
    assert [tag for *_x, tag in q] == ["exit"]
    pol.on_fill(SELL, 99.95, 0.25, "exit", T0, 0.0)
    q, _ = pol.quotes(at(99.95, 0.0))        # flat: starts over around the mid
    assert prices(q) == {(BUY, 99.92, "b0"), (SELL, 99.98, "a0")}


@pytest.mark.parametrize("inv", [D(0), D("0.05"), D("-0.05")])
def test_anchor_policy_quotes_what_the_live_strategy_quotes(inv: D) -> None:
    m = fixture_markets()[Venue.ARCUS]["AMD"]
    sess = mm_session(market="AMD", mode="anchor", spacing_bps=3, reset_threshold_pct=0.1, levels_per_side=1,
                      order_size_usd=25, inventory_cap_usd=50, skew_kappa=0.0)
    view = MarketView(Venue.ARCUS, "AMD")
    view.book = L2Book()
    view.book.load([(D("620.00"), D("1"))], [(D("620.40"), D("1"))], 1)
    live = make_strategy(sess)
    mi = MarketInfo(float(m.tick_size), float(m.step_size), float(m.min_notional), float(m.min_size))
    pol = AnchorPolicy(Config("x", "anchor", spacing_bps=3, reset_pct=0.1), Risk(order_usd=25, cap_usd=50), mi)
    ctx = StrategyContext(now_us=T0, venue=Venue.ARCUS, market=m, view=view, params=sess, inventory=inv)
    if inv:
        side = Side.BUY if inv > 0 else Side.SELL
        live.on_fill(ctx, Fill(Venue.ARCUS, "AMD", "c1", side, D("620.10"), abs(inv), D(0), True, False, T0, "t1",
                               tag="b0" if inv > 0 else "a0"))
        pol.on_fill(BUY if inv > 0 else SELL, 620.10, float(abs(inv)), "b0", T0, float(inv))
    out = live.on_tick(ctx)
    got = sorted((o.side, o.price_ticks, o.size_quantums) for o in out.desired[(Venue.ARCUS, "AMD")])
    q, _ = pol.quotes(Book(T0, 620.0, 620.4, 620.2, float(inv), None, 0.0, 0.0, deque()))
    ours = sorted((Side.BUY if s == BUY else Side.SELL, round(p / mi.tick), round(qq / mi.step)) for s, p, qq, _t in q)
    assert got == ours and len(got) == 2
    if inv > 0:   # holding: the sell sits 3 bp above the fill, not around the 620.20 mid
        assert max(p for s, p, _ in got if s is Side.SELL) == 62029


def test_skip_window_is_the_new_york_session_on_trading_days_only() -> None:
    def utc(day: str, hh: int, mm: int = 0, ss: int = 0) -> int:
        return day_start_us(day) + ((hh * 60 + mm) * 60 + ss) * S

    start, end = day_start_us("2026-09-19"), day_start_us("2026-09-22")          # Saturday to Monday, EDT
    w = Window(tape(lambda s: 100.0, 60, start=start), start, end, warmup_s=0, holidays=["2026-11-26"])
    skip = w.skip(("09:00-16:30",))

    def on(ts: int) -> bool:
        return skip[(ts - w.w0) // S]

    assert not on(utc("2026-09-19", 14)) and not on(utc("2026-09-20", 14))        # the weekend trades
    assert on(utc("2026-09-21", 13)) and on(utc("2026-09-21", 20, 29, 59))         # Monday 09:00-16:30 EDT
    assert not on(utc("2026-09-21", 12, 59, 59)) and not on(utc("2026-09-21", 20, 30))
    start, end = day_start_us("2026-11-26"), day_start_us("2026-11-28")          # Thanksgiving, then an early close
    w = Window(tape(lambda s: 100.0, 60, start=start), start, end, warmup_s=0, holidays=["2026-11-26"])
    skip = w.skip(("09:00-16:30",))
    assert not on(utc("2026-11-26", 15))                                          # a full holiday trades
    assert on(utc("2026-11-27", 14)) and not on(utc("2026-11-27", 13, 59))        # EST: 09:00 ET = 14:00 UTC


def test_a_skip_window_stops_new_quotes() -> None:
    start = day_start_us("2026-09-21") + 13 * 3600 * S                           # Monday 09:00 EDT
    t = tape(lambda s: 100 + 0.02 * math.sin(s / 60), 2 * 3600, start=start)
    assert run(t, FLAT, start=start).maker_fills > 0
    assert run(t, Config("x", "mid", spacing_bps=3, safety=False, skip_et=("09:00-16:30",)), start=start).maker_fills == 0
    assert run(t, Config("x", "mid", spacing_bps=3, safety=False, skip_et=("16:30-17:00",)), start=start).maker_fills > 0
