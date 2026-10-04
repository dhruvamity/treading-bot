"""Determinism, fill-model sanity, liquidation and strategy behaviour: the live strategies and engine run end to end
through the test simulator (tests/sim) on synthetic markets."""

from __future__ import annotations

from decimal import Decimal as D

from bot.core.book import L2Book
from bot.strategies import quoting as qt
from bot.venues.base import Side, Venue
from bot.venues.paper.fills import FillMode, QueueFillModel, SimOrder
from tests.helpers import fixture_markets, mm_session
from tests.sim.engine import SimConfig, Simulator
from tests.sim.events import merge
from tests.sim.margin import check_liquidation
from tests.sim.synthetic import book_events, sine_path, trend_path

MK = fixture_markets()
A = MK[Venue.ARCUS]["BTC"]
S = 1_790_000_000_000_000


# ------------------------------------------------------------------------------------------------ D4
def book(bid: str, ask: str, size: str = "1") -> L2Book:
    b = L2Book()
    b.load([(D(bid), D(size))], [(D(ask), D(size))], 1)
    return b


def test_back_of_queue_never_fills_without_trades_at_price() -> None:
    m = QueueFillModel(mode=FillMode.PESSIMISTIC, step=D("0.001"))
    b = book("100", "101", "5")
    o = SimOrder("a", Side.BUY, D("100"), D("1"))
    m.submit(o)
    assert m.arrive(o, b, 0) and o.queue_ahead == D("5")
    assert m.on_trade(D("101"), D("10"), Side.BUY, 1) == []  # trades elsewhere
    assert m.on_trade(D("100"), D("4"), Side.SELL, 2) == []  # still 1 ahead
    f = m.on_trade(D("100"), D("1.5"), Side.SELL, 3)
    assert len(f) == 1 and f[0].size == D("0.5")


def test_trade_through_fills_fully_and_post_only_rejects() -> None:
    m = QueueFillModel(step=D("0.001"))
    b = book("100", "101", "5")
    o = SimOrder("a", Side.BUY, D("100"), D("1"))
    m.submit(o)
    m.arrive(o, b, 0)
    f = m.on_trade(D("99.5"), D("0.01"), Side.SELL, 1)
    assert f and f[0].size == D("1") and f[0].trade_through
    x = SimOrder("x", Side.BUY, D("101"), D("1"))
    m.submit(x)
    assert not m.arrive(x, b, 0) and m.post_only_rejects == 1


def test_cancel_ahead_pessimistic_vs_optimistic() -> None:
    for mode, expect in ((FillMode.PESSIMISTIC, D("2.5")), (FillMode.OPTIMISTIC, D("1"))):
        m = QueueFillModel(mode=mode, step=D("0.001"))
        b = book("100", "101", "5")
        b.listener = m.on_level_change
        o = SimOrder("a", Side.BUY, D("100"), D("1"))
        m.submit(o)
        m.arrive(o, b, 0)  # 5 ahead of us
        b.set_level(True, D("100"), D("8"))  # 3 join behind us
        b.set_level(True, D("100"), D("4"))  # 4 cancelled, no trades
        # optimistic (FIFO): all 4 were ahead -> 1 left; pessimistic (pro-rata): 5 - 4 x 5/8 = 2.5
        assert o.queue_ahead == expect


def test_stale_quote_filled_before_cancel_takes_effect() -> None:
    m = QueueFillModel(step=D("0.001"))
    b = book("100", "101", "0")
    o = SimOrder("a", Side.BUY, D("100"), D("1"))
    m.submit(o)
    m.arrive(o, b, 0)
    m.request_cancel("a", effective_us=300_000)
    assert m.on_trade(D("99"), D("1"), Side.SELL, 100_000)  # before the cancel lands
    o2 = SimOrder("b", Side.BUY, D("100"), D("1"))
    m.submit(o2)
    m.arrive(o2, b, 0)
    m.request_cancel("b", effective_us=300_000)
    assert m.expire_cancels(300_000)
    assert m.on_trade(D("99"), D("1"), Side.SELL, 400_000) == []


# ------------------------------------------------------------------------------------------------ D6
def test_liquidation_prices() -> None:
    a = check_liquidation(Venue.ARCUS, D("1"), {"BTC": D("0.001")}, {"BTC": D("86000")}, {"BTC": A})
    assert a and a[0].kind == "arcus_full" and a[0].close_size == D("-0.001")
    assert check_liquidation(Venue.ARCUS, D("50"), {"BTC": D("0.001")}, {"BTC": D("86000")}, {"BTC": A}) == []


# ------------------------------------------------------------------------------------------------ simulator
def evs(path, seconds: int, seed: int = 3):  # type: ignore[no-untyped-def]
    return list(merge([book_events(Venue.ARCUS, "BTC", path, start_us=S, seconds=seconds, tick=A.tick_size,
                                   step=A.step_size, trades_per_s=2.0, trade_size=D("0.01"), seed=seed)]))


def run(sessions, events, **cfg):  # type: ignore[no-untyped-def]
    return Simulator(sessions, MK, SimConfig(**cfg)).run_sync(events)


MID = {"mode": "mid", "spacing_bps": 1, "levels_per_side": 1}


def test_sim_determinism() -> None:
    e = evs(sine_path(86000, 0.002, 900, 0.0001), 1800)
    r1, r2 = run([mm_session(**MID)], e), run([mm_session(**MID)], e)
    assert [(f.trade_id, f.price, f.size, f.ts_us) for f in r1.fills] == [(f.trade_id, f.price, f.size, f.ts_us) for f in r2.fills]
    assert r1.total.net == r2.total.net and r1.fills


def test_sim_pnl_identity_against_paper_equity() -> None:
    e = evs(sine_path(86000, 0.002, 900, 0.0001), 3600)
    sim = Simulator([mm_session(**MID)], MK, SimConfig())
    res = sim.run_sync(e)
    eq = sim.venues[Venue.ARCUS].equity() - sim.venues[Venue.ARCUS].starting_equity
    assert abs(float(res.total.net - eq)) < 1e-8


def test_e1_grid_stops_at_inventory_cap_in_trend() -> None:
    res = run([mm_session(mode="grid", levels_per_side=5, inventory_cap_usd=30)], evs(trend_path(86000, 0.02), 3600))
    assert abs(float(res.total.position)) * 86000 <= 30 * 1.25 + 11


def test_retired_modes_are_refused_and_old_names_map() -> None:
    """2026-09-26: two modes, as on Tread.fi. RGrid and the RSI signal lost on every market and were removed; a
    session from before names the Grid `anchor` and the biases long_skew / short_skew."""
    import pytest

    for mode in ("rgrid", "signal"):
        with pytest.raises(ValueError, match="retired"):
            mm_session(mode=mode)
    s = mm_session(mode="anchor", bias="long_skew", recentre_after_s=120, signal={"rsi_len": 14})
    assert (s.mode, s.bias, s.bias_frac) == ("grid", "long", 0.5)


def test_e1_mid_skews_against_inventory() -> None:
    ctx_u = qt.skew_u(0.0003, 0.0, 86000, 30)
    assert ctx_u > 0
    r = qt.reservation(86000, ctx_u, 0.001, 1.0)
    assert r < 86000  # long inventory -> quotes shift down
    qb, qa = qt.skewed_sizes(1.0, ctx_u, 0.1)
    assert qa > qb
    assert qt.skewed_sizes(1.0, 1.0, 0.1)[0] == 0.0  # at u = 1 stop adding


def test_e1_bias_holds_part_of_the_cap() -> None:
    """Over a choppy half hour a Long bias holds about half the $30 cap long on average (while still trading both
    sides), Short the mirror image, Neutral about flat."""
    e = evs(sine_path(86000, 0.002, 900, 0.0001), 1800)
    kw = {**MID, "inventory_cap_usd": 30, "order_size_usd": 10, "bias_frac": 0.5}

    def mean_position_usd(bias: str) -> float:
        fills = run([mm_session(**kw, bias=bias)], e).fills
        pos, path = 0.0, []
        for f in fills:
            pos += float(f.size) * (1 if f.side is Side.BUY else -1)
            path.append(pos * 86000)
        assert {f.side for f in fills} == {Side.BUY, Side.SELL}
        return sum(path) / len(path)

    assert 10 < mean_position_usd("long") < 20 and -20 < mean_position_usd("short") < -10
    assert abs(mean_position_usd("neutral")) < 3


def test_f3_no_look_ahead() -> None:
    """F3: shift every event after T by +1 h; every decision and fill at or before T must be unchanged."""
    import dataclasses

    base = evs(sine_path(86000, 0.003, 600, 0.0001), 3600)
    cut = S + 2400 * 1_000_000
    shifted = [e if e.ts_us <= cut else dataclasses.replace(e, ts_us=e.ts_us + 3_600_000_000) for e in base]
    for sess in (mm_session(mode="grid", levels_per_side=3), mm_session(mode="mid", spacing_bps="auto",
                                                                        levels_per_side="auto", order_size_usd="auto")):
        r1, r2 = run([sess], base), run([sess], shifted)

        def upto(r):  # type: ignore[no-untyped-def]
            return ([(d["ts"], d["kind"], d["reason"]) for d in r.decisions if d["ts"] <= cut],
                    [(f.ts_us, f.side, f.price, f.size) for f in r.fills if f.ts_us <= cut])

        d1, f1 = upto(r1)
        d2, f2 = upto(r2)
        assert d1 and d1 == d2, sess.mode
        assert f1 == f2, sess.mode


def test_every_setup_mode_starts_and_quotes_on_the_live_engine() -> None:
    """Smart had a strategy and a backtest but no client-id code: every live Smart start died building the engine
    with "unknown strategy code for 'smart'" (5 starts, 27 Sep - 2 Oct 2026), and the tests never built one."""
    from bot.common.ids import ClientIdFactory
    from bot.strategies import setup

    e = evs(sine_path(86000, 0.002, 900, 0.0001), 600)
    for mode in setup.MODES:
        assert ClientIdFactory(mode, 1).arcus().startswith("al")
        res = run([mm_session(mode=mode, spacing_bps=1, levels_per_side=1)], e)
        assert res.fills, mode
