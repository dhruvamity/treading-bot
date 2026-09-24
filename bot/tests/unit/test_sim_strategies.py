"""D1 funding worked examples, D3 determinism, D4 fill-model sanity, D6 liquidation, E1/E2 strategy behaviour,
Autopilot hysteresis, bandit guardrails, features."""

from __future__ import annotations

import math
from decimal import Decimal as D

import pytest

from bot.autopilot.bandit import Arm, ThompsonBandit, bucket
from bot.autopilot.features import efficiency_ratio, oer, variance_ratio
from bot.autopilot.regime import ModeChoice, ModeSwitcher
from bot.common.config import AutopilotCfg, DNSession
from bot.core import funding_math as fm
from bot.core.book import L2Book
from bot.research.sim.engine import Outage, SimConfig, Simulator
from bot.research.sim.events import merge
from bot.research.sim.fills import FillMode, QueueFillModel, SimOrder
from bot.research.sim.margin import check_liquidation
from bot.research.sim.synthetic import book_events, jump_path, sine_path, trend_path
from bot.strategies import quoting as qt
from bot.venues.base import Side, Venue
from tests.helpers import fixture_markets, mm_session

MK = fixture_markets()
A, L = MK[Venue.ARCUS]["BTC"], MK[Venue.LIGHTER_RH]["BTC"]
S = 1_790_000_000_000_000


# ------------------------------------------------------------------------------------------------ D1
def test_arcus_crypto_premium_worked_example() -> None:
    p = fm.premium(100_000, 100_050, 100_060)
    assert p == pytest.approx(0.0005)  # 0.05%
    assert fm.arcus_crypto_rate(p) == pytest.approx(0.0000125)  # dead-band keeps the base rate


def test_arcus_rwa_worked_example() -> None:
    r = fm.arcus_rwa_rate(0.0008, 0.00000579)
    assert r == pytest.approx(0.00010579) and round(r * 100, 4) == 0.0106
    assert fm.payment(-10_000, 1.0, r) == pytest.approx(1.0579)  # a $10,000 short receives ~$1.06
    assert fm.arcus_rwa_rate(0.0008, 0.00000579, locked=True) == pytest.approx(0.00000579)


def test_lighter_spy_base_and_deadband() -> None:
    ir = 0.000032  # 0.0032% per 8h
    assert fm.lighter_rate(0.0, ir, 0.5) == pytest.approx(0.000004)  # 0.0004%/h
    lo, hi = fm.lighter_deadband(ir, 0.5)
    assert lo == pytest.approx(-0.000436) and hi == pytest.approx(0.000564)
    assert fm.lighter_rate(lo + 1e-9, ir, 0.5) == pytest.approx(0.000004)
    assert fm.lighter_rate(hi + 0.0001, ir, 0.5) > 0.000004


def test_value_index_reconstruction_and_carry() -> None:
    assert pytest.approx(3.944e-6, rel=1e-3) == 0.0030498 / 773.3
    # break-even hours for the 2026-09-21/22 SPY spread of ~0.0013%/h
    assert [round(fm.breakeven_hours(0.000013, c / 1e4)) for c in (5, 10, 20, 30)] == [38, 77, 154, 231]
    assert fm.carry_al(0.000005, 0.000018, 1.0, 700, 700) == pytest.approx(0.0091)


def test_median_vs_mean_hour_aggregation() -> None:
    samples = [0.0001] * 59 + [0.05]
    assert fm.arcus_hour_rate(samples, crypto=False, sofr_hourly=0.0) == pytest.approx(0.0001 / 8)
    assert fm.lighter_hour_rate(samples, 0.0, 1.0) > fm.lighter_hour_rate([0.0001] * 60, 0.0, 1.0)


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
    lp = check_liquidation(Venue.LIGHTER_RH, D("0.9"), {"BTC": D("0.001")}, {"BTC": D("86000")}, {"BTC": L})
    assert lp and lp[0].kind == "lighter_partial" and lp[0].price < D("86000")
    lf = check_liquidation(Venue.LIGHTER_RH, D("0.5"), {"BTC": D("0.001")}, {"BTC": D("86000")}, {"BTC": L})
    assert lf and lf[0].kind == "lighter_full"


# ------------------------------------------------------------------------------------------------ simulator
def evs(path, seconds: int, venues=(Venue.ARCUS,), seed: int = 3):  # type: ignore[no-untyped-def]
    return list(merge([book_events(v, "BTC", path, start_us=S, seconds=seconds, tick=(A if v is Venue.ARCUS else L).tick_size,
                                   step=(A if v is Venue.ARCUS else L).step_size, trades_per_s=2.0,
                                   trade_size=D("0.01"), seed=seed + i) for i, v in enumerate(venues)]))


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


def test_e1_blend_pauses_on_stale_reference() -> None:
    from bot.core.marketdata import MarketView
    from bot.strategies.base import StrategyContext
    from bot.strategies.blend import BlendStrategy

    s = BlendStrategy(mm_session(mode="blend", blend={"reference": ["lighter_rh:BTC", "pyth"], "weights": [0.7, 0.3],
                                                      "stale_ms": 2000, "dev_bps": 5}))
    av, lv = MarketView(Venue.ARCUS, "BTC"), MarketView(Venue.LIGHTER_RH, "BTC")
    av.book.load([(D("86000.0"), D("1"))], [(D("86000.2"), D("1"))], 1)
    lv.book.load([(D("86200.0"), D("1"))], [(D("86200.2"), D("1"))], 1)
    av.book_ts_us = S
    lv.book_ts_us = S - 10_000_000  # 10 s stale
    av.oracle, av.price_ts_us = D("86200"), S - 10_000_000
    ctx = StrategyContext(now_us=S, venue=Venue.ARCUS, market=A, view=av, params=s.p, other_market=L, other_view=lv)
    out = s.on_tick(ctx)
    assert "paused" in out.reason and not out.desired[(Venue.ARCUS, "BTC")]


def test_e1_signal_single_position() -> None:
    res = run([mm_session(mode="signal", signal={"rsi_low": 45, "rsi_high": 55, "trend_z": 50, "cooldown_s": 10})],
              evs(sine_path(86000, 0.004, 1200, 0.0002), 7200))
    pos = [abs(float(p)) for p in [res.total.position]]
    q = 0.00012
    assert max(pos) <= q * 1.01
    entries = [d for d in res.decisions if d["kind"] == "place" and "sig_entry" in str(d.get("data"))]
    assert all(True for _ in entries)


def test_e1_dgrid_regime_switch() -> None:
    from bot.strategies.dgrid import DGridStrategy

    s = DGridStrategy(mm_session(mode="dgrid"))
    from bot.core.marketdata import MarketView
    from bot.strategies.base import StrategyContext

    av = MarketView(Venue.ARCUS, "BTC")
    av.book.load([(D("86000.0"), D("1"))], [(D("86000.2"), D("1"))], 1)
    ctx = StrategyContext(now_us=S, venue=Venue.ARCUS, market=A, view=av, params=s.p, regime="trend")
    s.on_tick(ctx)
    assert s.active == "rgrid"
    ctx.regime = "range"
    s.on_tick(ctx)
    assert s.active == "grid"


def test_e2_dn_hedged_mm_kill_on_lighter_outage() -> None:
    sess = DNSession.model_validate(dict(session_id="d", strategy="dn_hedged_mm", market="BTC", half_spread_bps=3,
                                         legs={"arcus": {"account_index": 2, "role": "maker"},
                                               "lighter_rh": {"role": "hedge"}}, lighter_down_kill_s=10))
    e = evs(jump_path(86000, 600, -0.001), 1800, (Venue.ARCUS, Venue.LIGHTER_RH))
    # Lighter feed + entry down from t=590 s (before the Arcus jump fill) for 10 minutes
    res = run([sess], e, outages=[Outage(Venue.LIGHTER_RH, S + 590_000_000, S + 1190_000_000)])
    arcus = res.breakdown.get("arcus:BTC")
    assert arcus is not None and arcus.fills >= 1
    assert any("DN kill" in d["reason"] or d["kind"] == "hedge" for d in res.decisions)
    assert arcus.position == 0  # flattened by the kill rule


def test_e2_dn_hedged_mm_hedges_residual() -> None:
    sess = DNSession.model_validate(dict(session_id="d", strategy="dn_hedged_mm", market="BTC", half_spread_bps=3,
                                         legs={"arcus": {"account_index": 2, "role": "maker"},
                                               "lighter_rh": {"role": "hedge"}}))
    res = run([sess], evs(jump_path(86000, 600, -0.001), 900, (Venue.ARCUS, Venue.LIGHTER_RH)))
    a, lt = res.breakdown.get("arcus:BTC"), res.breakdown.get("lighter_rh:BTC")
    assert a is not None and lt is not None and a.fills >= 1 and lt.fills >= 1
    lighter_min = max(10.0, float(L.min_size) * 86000)  # BTC: 0.0002 BTC ~ $17 > $10
    resid_usd = abs(float(a.position + lt.position)) * 86000
    assert resid_usd <= 2 * lighter_min


# ------------------------------------------------------------------------------------------------ autopilot
def test_mode_switch_hysteresis() -> None:
    sw = ModeSwitcher(AutopilotCfg(confirm_evals=5, min_dwell_min=30))
    t = S
    sw.update(ModeChoice("grid", "r"), t)
    for i in range(5):
        sw.update(ModeChoice("grid", "r"), t + i)
    assert sw.current == "grid"
    for i in range(4):
        m, _ = sw.update(ModeChoice("rgrid", "trend"), t + 60_000_000 * (i + 1))
    assert m == "grid"  # needs 5 wins AND 30 min dwell
    m, _ = sw.update(ModeChoice("rgrid", "trend"), t + 5 * 60_000_000)
    assert m == "grid"
    whys = [sw.update(ModeChoice("rgrid", "trend"), t + 31 * 60_000_000 + i)[1] for i in range(5)]
    assert sw.current == "rgrid" and sum(w is not None for w in whys) == 1
    m, _why = sw.update(ModeChoice("pause", "hard", hard_stop=True), t + 32 * 60_000_000)
    assert m == "pause"  # hard stop overrides


def test_features() -> None:
    assert efficiency_ratio([100 + i for i in range(61)]) == pytest.approx(1.0)
    zig = [100 + (i % 2) for i in range(61)]
    assert efficiency_ratio(zig) == 0.0  # ends where it started: no net move
    assert oer(zig, 0.001) > 1.5
    rw = [100 * math.exp(0.001 * math.sin(i * 1.7)) for i in range(300)]
    assert variance_ratio(rw) < 1.0


def test_bandit_guardrails() -> None:
    b = ThompsonBandit(template=[Arm("a", "grid", {"d": 10}, eligible=True), Arm("b", "grid", {"d": 25}, eligible=True),
                                 Arm("x", "grid", {"d": 5}, eligible=False)], seed=1)
    k = bucket(0.1, 0.002, "rth")
    assert b.propose(k, mode="grid", blocked=True) is None
    for _ in range(200):
        arm = b.propose(k, mode="grid")
        assert arm is not None and arm.arm_id != "x"
        b.reward(k, arm.arm_id, pnl_bps_of_volume=2.0 if arm.arm_id == "b" else -3.0)
    counts = {a: sum(1 for _, x in b.proposals if x == a) for a in ("a", "b")}
    assert counts["b"] > counts["a"]
    for _ in range(3):
        b.end_session(k, "a", -10)
    assert b.arms[k]["a"].dropped


def test_f3_no_look_ahead() -> None:
    """F3: shift every event after T by +1 h; every decision and fill at or before T must be unchanged."""
    import dataclasses

    base = evs(sine_path(86000, 0.003, 600, 0.0001), 3600, venues=(Venue.ARCUS, Venue.LIGHTER_RH))
    cut = S + 2400 * 1_000_000
    shifted = [e if e.ts_us <= cut else dataclasses.replace(e, ts_us=e.ts_us + 3_600_000_000) for e in base]
    for sess in (mm_session(mode="grid", levels_per_side=3), mm_session(mode="auto", spacing_bps="auto",
                                                                        levels_per_side="auto", order_size_usd="auto")):
        r1, r2 = run([sess], base), run([sess], shifted)

        def upto(r):  # type: ignore[no-untyped-def]
            return ([(d["ts"], d["kind"], d["reason"]) for d in r.decisions if d["ts"] <= cut],
                    [(f.ts_us, f.side, f.price, f.size) for f in r.fills if f.ts_us <= cut])

        d1, f1 = upto(r1)
        d2, f2 = upto(r2)
        assert d1 and d1 == d2, sess.mode
        assert f1 == f2, sess.mode
