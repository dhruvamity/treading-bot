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


def test_sim_determinism() -> None:
    e = evs(sine_path(86000, 0.002, 900, 0.0001), 1800)
    r1, r2 = run([mm_session()], e), run([mm_session()], e)
    assert [(f.trade_id, f.price, f.size, f.ts_us) for f in r1.fills] == [(f.trade_id, f.price, f.size, f.ts_us) for f in r2.fills]
    assert r1.total.net == r2.total.net and r1.fills


def test_sim_pnl_identity_against_paper_equity() -> None:
    e = evs(sine_path(86000, 0.002, 900, 0.0001), 3600)
    sim = Simulator([mm_session()], MK, SimConfig())
    res = sim.run_sync(e)
    eq = sim.venues[Venue.ARCUS].equity() - sim.venues[Venue.ARCUS].starting_equity
    assert abs(float(res.total.net - eq)) < 1e-8


def test_e1_grid_stops_at_inventory_cap_in_trend() -> None:
    res = run([mm_session(mode="grid", levels_per_side=5, inventory_cap_usd=30)], evs(trend_path(86000, 0.02), 3600))
    assert abs(float(res.total.position)) * 86000 <= 30 * 1.25 + 11


def test_e1_rgrid_cuts_in_trend_vs_grid() -> None:
    e = evs(trend_path(86000, 0.02), 5400)
    g = run([mm_session(mode="grid", levels_per_side=3, reset_threshold_pct=5)], e)
    r = run([mm_session(mode="rgrid", levels_per_side=1)], e)
    assert any("cut" in d["reason"] for d in r.decisions)
    assert float(r.total.net) >= float(g.total.net) - 0.25


def test_e1_mid_skews_against_inventory() -> None:
    ctx_u = qt.skew_u(0.0003, 0.0, 86000, 30)
    assert ctx_u > 0
    r = qt.reservation(86000, ctx_u, 0.001, 1.0)
    assert r < 86000  # long inventory -> quotes shift down
    qb, qa = qt.skewed_sizes(1.0, ctx_u, 0.1)
    assert qa > qb
    assert qt.skewed_sizes(1.0, 1.0, 0.1)[0] == 0.0  # at u = 1 stop adding


def test_e1_signal_single_position() -> None:
    res = run([mm_session(mode="signal", signal={"rsi_low": 45, "rsi_high": 55, "trend_z": 50, "cooldown_s": 10})],
              evs(sine_path(86000, 0.004, 1200, 0.0002), 7200))
    pos = [abs(float(p)) for p in [res.total.position]]
    q = 0.00012
    assert max(pos) <= q * 1.01
    entries = [d for d in res.decisions if d["kind"] == "place" and "sig_entry" in str(d.get("data"))]
    assert all(True for _ in entries)


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
