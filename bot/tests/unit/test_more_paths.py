"""Strategy / Autopilot / paper venue / state / engine / recorder paths not covered elsewhere (offline)."""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal as D
from pathlib import Path
from typing import Any

import pytest

from bot.autopilot.allocator import Candidate, allocate
from bot.autopilot.features import FeatureEngine, Features, MarkoutTracker
from bot.autopilot.params import choose_params, fills_cap_from_budget
from bot.autopilot.regime import choose_mode, eligibility
from bot.common.config import AutopilotCfg, DNSession, load_session
from bot.core.book import L2Book
from bot.core.liveparams import LiveParams
from bot.core.marketdata import MarketDataHub, MarketView
from bot.core.state import StateStore
from bot.research.sim.engine import SimConfig, Simulator
from bot.research.sim.events import merge
from bot.research.sim.synthetic import book_events, sine_path
from bot.strategies import make_strategy
from bot.strategies.base import StrategyContext
from bot.strategies.dn_carry import CarrySignal, DNCarry, ev_bps, forecast_spread
from bot.strategies.mid import MidStrategy
from bot.strategies.points_overlay import PointsOverlay
from bot.venues.base import (
    TIF,
    OrderRequest,
    OrderState,
    OrderStatus,
    Position,
    PublicTrade,
    Side,
    Venue,
)
from bot.venues.paper.adapter import LatencyModel, PaperVenue
from tests.helpers import fixture_markets, mm_session

MK = fixture_markets()
A, L = MK[Venue.ARCUS]["BTC"], MK[Venue.LIGHTER_RH]["BTC"]
S = 1_790_000_000_000_000
ROOT = Path(__file__).parents[2]


def view(venue: Venue, bid: str, ask: str, size: str = "5") -> MarketView:
    v = MarketView(venue, "BTC")
    v.book.load([(D(bid), D(size))], [(D(ask), D(size))], 1)
    v.book_ts_us = S
    return v


# ------------------------------------------------------------------------------------------------ autopilot
def feats(**kw: Any) -> Features:
    f = Features(ts_us=S, sigma_1h=0.004, spread_bps=1.0, spread_median_bps=1.0, depth_q_usd=10_000,
                 depth_5q_usd=50_000, n_bars=120)
    for k, v in kw.items():
        setattr(f, k, v)
    return f


def test_choose_mode_branches() -> None:
    cfg = AutopilotCfg()
    v = Venue.ARCUS
    assert choose_mode(feats(), cfg, venue=v, other_depth_usd=None, safety_paused=False, band_zone=False,
                       event_window=True).mode == "pause"
    assert choose_mode(feats(markout_60s_bps=-5.0), cfg, venue=v, other_depth_usd=None, safety_paused=False,
                       band_zone=False, event_window=False).hard_stop
    assert choose_mode(feats(depth_5q_usd=500), cfg, venue=v, other_depth_usd=1e6, safety_paused=False,
                       band_zone=False, event_window=False).mode == "blend"
    assert choose_mode(feats(er=0.7), cfg, venue=v, other_depth_usd=None, safety_paused=False, band_zone=False,
                       event_window=False).mode == "rgrid"
    assert choose_mode(feats(er=0.7, sigma_1h=0.05), cfg, venue=v, other_depth_usd=None, safety_paused=False,
                       band_zone=False, event_window=False).mode == "pause"
    assert choose_mode(feats(er=0.1, oer=3.0, variance_ratio=0.8), cfg, venue=v, other_depth_usd=None,
                       safety_paused=False, band_zone=False, event_window=False).mode == "mid"
    assert choose_mode(feats(er=0.1, oer=3.0, variance_ratio=1.2), cfg, venue=v, other_depth_usd=None,
                       safety_paused=False, band_zone=False, event_window=False).mode == "grid"
    assert choose_mode(feats(er=0.4, oer=0.5), cfg, venue=v, other_depth_usd=None, safety_paused=False,
                       band_zone=False, event_window=False).mode == "signal"
    e = eligibility(feats(depth_q_usd=5), venue=v, online=False, region_ok=False, min_notional_usd=100, capital_usd=35,
                    q_usd=6, oi_headroom_ok=False, event_window=True, feed_lag_s=3, error_rate=0.1)
    assert not e.ok and len(e.reasons) == 8
    assert eligibility(feats(), venue=v, online=True, region_ok=True, min_notional_usd=5, capital_usd=35, q_usd=6,
                       oi_headroom_ok=True, event_window=False, feed_lag_s=0.1, error_rate=0.0).ok


def test_params_and_allocator() -> None:
    cfg = AutopilotCfg()
    ps = choose_params(feats(markout_60s_bps=-2.0), cfg, venue=Venue.ARCUS, capital_usd=35, leverage=5,
                       inventory_cap_usd=30, venue_min_usd=8.6, price=86000, maker_fee=0.0, tick_frac=1.2e-6,
                       off_hours=False, fills_cap=60)
    assert 5 <= ps.delta_bps <= 100 and ps.levels >= 1 and ps.size_usd >= 10.3 and ps.kappa == 2.0
    off = choose_params(feats(), cfg, venue=Venue.LIGHTER_RH, capital_usd=35, leverage=3, inventory_cap_usd=30,
                        venue_min_usd=17.2, price=86000, maker_fee=0.0, tick_frac=1.2e-6, off_hours=True, fills_cap=10)
    assert off.levels <= 5 and off.delta_bps >= 10
    assert fills_cap_from_budget(Venue.LIGHTER_RH, arcus_pool_remaining=None, hours_left=8) == 540
    assert fills_cap_from_budget(Venue.ARCUS, arcus_pool_remaining=8000, hours_left=8) == 500
    cands = [Candidate("arcus", "BTC", "grid", 0.002), Candidate("arcus", "ETH", "grid", 0.003),
             Candidate("lighter_rh", "SPY", "dn", 0.001, kind="dn"), Candidate("x", "Y", "dn", 0.0009, kind="dn"),
             Candidate("arcus", "SOL", "mid", -0.01)]
    out = allocate(cands)
    assert [(c.market, c.kind) for c in out] == [("ETH", "mm"), ("SPY", "dn")]


def test_feature_engine_and_markouts() -> None:
    v = view(Venue.ARCUS, "86000.0", "86000.2")
    o = view(Venue.LIGHTER_RH, "85990.0", "86010.0")
    for i in range(200):
        px = D(str(86000 + (i % 7)))
        v.on_trade(PublicTrade(Venue.ARCUS, "BTC", S + i * 60_000_000, px, D("0.01"), Side.BUY, str(i),
                               taker_address="0xa" if i % 3 else "0xb"))
    fe = FeatureEngine()
    for k in range(40):
        f = fe.compute(v, S + 200 * 60_000_000, other=o)
        del k
    assert f.n_bars > 100 and f.taker_hhi is not None and f.basis_z is not None
    mt = MarkoutTracker()
    mt.add_fill(S, 1, 100.0)
    mt.update(S + 31_000_000, 100.01)
    assert mt.mean(30) == pytest.approx(1.0) and mt.mean(300) is None


# ------------------------------------------------------------------------------------------------ strategies
def test_mid_quotes_and_venue_blocks() -> None:
    s = MidStrategy(mm_session(mode="mid", spacing_bps=2, levels_per_side=2, execution_style="normal", offset_bps=-1))
    ctx = StrategyContext(now_us=S, venue=Venue.ARCUS, market=A, view=view(Venue.ARCUS, "86000.0", "86000.2"), params=s.p)
    out = s.on_tick(ctx)
    orders = out.desired[(Venue.ARCUS, "BTC")]
    assert len(orders) == 4 and {o.side for o in orders} == {Side.BUY, Side.SELL}
    for o in orders:
        assert (o.side is Side.BUY and o.price_ticks < 860002) or (o.side is Side.SELL and o.price_ticks > 860000)
    for style in ("aggressive", "passive"):
        s2 = MidStrategy(mm_session(mode="mid", execution_style=style))
        assert s2.on_tick(StrategyContext(now_us=S, venue=Venue.ARCUS, market=A, view=view(Venue.ARCUS, "86000.0",
                                                                                           "86000.5"), params=s2.p)).desired
    lctx = StrategyContext(now_us=S, venue=Venue.LIGHTER_RH, market=L, view=view(Venue.LIGHTER_RH, "86000.0", "86000.2"),
                           params=s.p)
    assert "blocked on Lighter" in s.on_tick(lctx).reason
    ctx.off_hours = True
    assert "off-hours" in s.on_tick(ctx).reason
    ctx.off_hours = False
    ctx.event_window = True
    ctx.inventory = D("0.0002")
    out = s.on_tick(ctx)
    ex = out.desired[(Venue.ARCUS, "BTC")]
    assert len(ex) == 1 and ex[0].reduce_only and ex[0].side is Side.SELL


def test_bias_path_and_participation() -> None:
    from bot.strategies import quoting as qt

    assert qt.bias_target_usd("long_skew", 0.5, 20) == 20
    assert qt.bias_target_usd("short_skew", 0.25, 20) == -10
    assert qt.bias_target_usd("long_skew", 1.0, 20) == 0
    assert qt.participation_mult(30, 100, 25) == 1.5 and qt.participation_mult(10, 100, 25) == 1.0
    assert qt.rsi([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15], 14) == 100.0
    assert qt.dgrid_delta(0.01, 25, k_delta=1, delta_min=0.0005, delta_max=0.01, maker_fee=0, tick_frac=1e-6,
                          venue=Venue.LIGHTER_RH, sigma_1s=0.001) >= 3 * 0.001 * 0.3 ** 0.5


def test_dn_carry_ev_and_state_machine() -> None:
    assert forecast_spread(0.0001, 10, model="persistence", mean=0.0, halflife_h=5) == 0.0001
    assert forecast_spread(0.0001, 10, model="ar1", mean=0.0, halflife_h=5) < 0.0001
    sig = CarrySignal(r_a=0.00005, r_l=0.00001, basis_exec_long_a=0.0002, basis_exec_short_a=-0.0002, basis_mean=0.0,
                      basis_sigma=0.0005, basis_halflife_h=24, spread_halflife_h=math_inf(), spread_mean=0.0)
    long_a = ev_bps(sig, 1, 24, model="persistence", cost_entry_bps=2, cost_exit_bps=2, risk_lambda=0.5)
    short_a = ev_bps(sig, -1, 24, model="persistence", cost_entry_bps=2, cost_exit_bps=2, risk_lambda=0.5)
    assert short_a > long_a  # Arcus pays more -> be short Arcus / long Lighter
    sess = DNSession.model_validate(dict(session_id="d", strategy="dn_carry", market="BTC", entry_ev_bps=-100,
                                         legs={"arcus": {"account_index": 2, "role": "maker"},
                                               "lighter_rh": {"role": "hedge"}}))
    s = DNCarry(sess)
    av, lv = view(Venue.ARCUS, "86000.0", "86000.2"), view(Venue.LIGHTER_RH, "86000.0", "86002.0")
    av.predicted_funding_h, lv.predicted_funding_h = 0.00005, 0.00001
    ctx = StrategyContext(now_us=S, venue=Venue.ARCUS, market=A, view=av, params=sess, other_market=L, other_view=lv)
    out = s.on_tick(ctx)
    assert s.phase == "entering" and s.direction == -1 and "ENTER" in out.reason
    assert out.desired[(Venue.ARCUS, "BTC")][0].side is Side.SELL
    ctx.inventory = D("-0.0003")
    out = s.on_tick(ctx)
    assert out.hedge_intents and out.hedge_intents[0].side is Side.BUY and out.hedge_intents[0].venue is Venue.LIGHTER_RH
    ctx.other_inventory = D("0.0003")
    ctx.extra["margin_x_mm"] = 1.5
    s.on_tick(ctx)
    ctx.extra["avoid_window"] = True
    s.on_tick(ctx)
    assert s.phase == "exiting"
    out = s.on_stop(ctx)
    assert all(o.reduce_only for o in out.desired[(Venue.ARCUS, "BTC")])
    p = PointsOverlay(sess)
    ctx2 = StrategyContext(now_us=S, venue=Venue.ARCUS, market=A, view=av, params=sess, other_market=L, other_view=lv)
    p.on_tick(ctx2)
    ctx2.now_us += 3_600_000_000
    ctx2.inventory = D("-0.0003")
    out = p.on_tick(ctx2)
    assert p.min_hold_h == 12 and out.metrics["oi_hours_usd"] > 0 and p.entries == 1


def math_inf() -> float:
    import math

    return math.inf


def test_make_strategy_from_session_files() -> None:
    for f in sorted((ROOT / "config" / "sessions").glob("*.yaml")):
        s = make_strategy(load_session(f))
        assert hasattr(s, "on_tick")


def test_auto_strategy_runs_and_logs_modes() -> None:
    e = list(merge([book_events(v, "BTC", sine_path(86000, 0.002, 900, 0.0001), start_us=S, seconds=5400,
                                tick=(A if v is Venue.ARCUS else L).tick_size, step=(A if v is Venue.ARCUS else L).step_size,
                                trades_per_s=2.0, trade_size=D("0.01"), seed=5 + i)
                    for i, v in enumerate((Venue.ARCUS, Venue.LIGHTER_RH))]))
    sess = mm_session(mode="auto", spacing_bps="auto", levels_per_side="auto", order_size_usd="auto")
    r = Simulator([sess], MK, SimConfig()).run_sync(e)
    modes = [d for d in r.decisions if d["kind"] == "mode"]
    assert modes and r.mode_time_s and sum(r.mode_time_s.values()) > 5000


# ------------------------------------------------------------------------------------------------ paper venue
async def test_paper_venue_ioc_modify_funding_dms() -> None:
    now = {"t": S}
    book = L2Book()
    book.load([(D("86000.0"), D("1")), (D("85999.0"), D("1"))], [(D("86001.0"), D("0.0001")), (D("86002.0"), D("1"))], 1)
    pv = PaperVenue(Venue.ARCUS, {"BTC": A}, {"BTC": book}, now_us=lambda: now["t"], latency=LatencyModel.arcus())
    await pv.connect()
    r = OrderRequest(Venue.ARCUS, "BTC", Side.BUY, D("86002.0"), D("0.0002"), TIF.IOC, client_id="ioc1")
    await pv.place([r])
    now["t"] += 100_000
    pv.process(now["t"])
    upd, fills = pv.drain()
    assert len(fills) == 2 and fills[0].price == D("86001.0") and not fills[0].is_maker
    assert fills[0].fee == fills[0].price * fills[0].size * A.taker_fee
    assert upd[-1].status is OrderStatus.FILLED
    assert pv.acct.positions["BTC"] == D("0.0002")
    pay = pv.apply_funding("BTC", D("0.0000125"), D("86000"))
    assert pay < 0  # long pays positive funding
    mk = OrderRequest(Venue.ARCUS, "BTC", Side.SELL, D("86010.0"), D("0.0002"), TIF.POST_ONLY, client_id="m1")
    await pv.place([mk])
    now["t"] += 100_000
    pv.process(now["t"])
    st = await pv.modify("m1", D("86000.5"), D("0.0002"))
    assert st.status is OrderStatus.OPEN
    rej = await pv.modify("m1", D("85999.0"), D("0.0002"))
    assert rej.status is OrderStatus.REJECTED and rej.reject_reason == "POST_ONLY_WOULD_CROSS"
    await pv.place([OrderRequest(Venue.ARCUS, "BTC", Side.SELL, D("86050.0"), D("0.0002"), TIF.POST_ONLY, client_id="m2")])
    now["t"] += 100_000
    pv.process(now["t"])
    await pv.arm_dead_mans_switch(now["t"] + 1)
    now["t"] += 10
    pv.process(now["t"])
    upd, _ = pv.drain()
    assert any(u.reject_reason == "DEAD_MANS_SWITCH" for u in upd) and pv.dms_fires == 1
    ro = OrderRequest(Venue.ARCUS, "BTC", Side.BUY, D("86002.0"), D("0.0001"), TIF.IOC, reduce_only=True, client_id="ro")
    await pv.place([ro])
    now["t"] += 100_000
    pv.process(now["t"])
    upd, _ = pv.drain()
    assert upd[-1].reject_reason == "REDUCE_ONLY_WOULD_INCREASE"
    pos = await pv.positions()
    assert pos[0].size == D("0.0002") and (await pv.balances())["equity"] > 0
    assert await pv.open_orders() == [] and pv.budget().venue is Venue.ARCUS and pv.health()


# ------------------------------------------------------------------------------------------------ state
def test_state_reconcile_and_persistence(tmp_path: Path) -> None:
    db = tmp_path / "s.sqlite"
    st = StateStore(db)
    r = OrderRequest(Venue.ARCUS, "BTC", Side.BUY, D("86000.0"), D("0.0002"), TIF.POST_ONLY, client_id="a1", tag="g-1")
    st.on_intent(r, "sess")
    st.on_update(OrderState("a1", "o1", OrderStatus.OPEN, D(0), None, None, S, Venue.ARCUS))
    r2 = OrderRequest(Venue.ARCUS, "BTC", Side.SELL, D("86100.0"), D("0.0002"), TIF.POST_ONLY, client_id="a2")
    st.on_intent(r2, "sess")
    st.on_update(OrderState("a2", "o2", OrderStatus.OPEN, D(0), None, None, S, Venue.ARCUS))
    st.set_position(Venue.ARCUS, "BTC", D("0.0001"), D("86000"))
    st2 = StateStore(db)  # replay from SQLite after a "crash"
    assert set(st2.orders) == {"a1", "a2"} and st2.position(Venue.ARCUS, "BTC") == D("0.0001")
    venue_open = [OrderState("a1", "o1", OrderStatus.OPEN, D(0), None, None, S, Venue.ARCUS),
                  OrderState("zz", "o9", OrderStatus.OPEN, D(0), None, None, S, Venue.ARCUS)]
    rep = st2.reconcile(Venue.ARCUS, venue_open, [Position(Venue.ARCUS, "BTC", D("0.0003"), D("86000"), D("86000"),
                                                            D(0), "cross", None)])
    assert [s.client_id for s in rep.unknown_live] == ["zz"] and rep.missing_local == ["a2"]
    assert rep.position_mismatch == [("BTC", D("0.0001"), D("0.0003"))] and not rep.clean
    assert st2.position(Venue.ARCUS, "BTC") == D("0.0003")
    st2.add_points("lighter_rh", "2026-38", 1234.5)
    assert st2.points() == [("lighter_rh", "2026-38", 1234.5)]
    st2.kv_set("k", "v")
    assert st2.kv_get("k") == "v"
    # terminal states never regress
    st2.on_update(OrderState("a1", "o1", OrderStatus.FILLED, D("0.0002"), D("86000"), None, S, Venue.ARCUS))
    st2.on_update(OrderState("a1", "o1", OrderStatus.OPEN, D(0), None, None, S, Venue.ARCUS))
    assert st2.orders["a1"].status is OrderStatus.FILLED
    assert st2.on_update(OrderState("", "o404", OrderStatus.OPEN, D(0), None, None, S, Venue.ARCUS)) is None
    st2.gc(keep_terminal=0)
    st.close()
    st2.close()


# ------------------------------------------------------------------------------------------------ liveparams
class _FakeArcus:
    def __init__(self) -> None:
        self.m = json.loads((ROOT / "tests" / "fixtures" / "live" / "arcus_markets.json").read_text())["markets"]
        self.f = json.loads((ROOT / "tests" / "fixtures" / "live" / "arcus_feetiers.json").read_text())

    async def feetiers(self) -> Any:
        return self.f

    async def markets(self) -> Any:
        return self.m


class _FakeLighter:
    async def order_books(self) -> Any:
        return json.loads((ROOT / "tests" / "fixtures" / "live" / "lighter_orderbooks.json").read_text())["order_books"]

    async def order_book_details(self) -> Any:
        return json.loads((ROOT / "tests" / "fixtures" / "live" / "lighter_obd_btc.json").read_text())["order_book_details"]


async def test_liveparams_refresh_and_change_log(tmp_path: Path) -> None:
    fa = _FakeArcus()
    lp = LiveParams(arcus=fa, lighter=_FakeLighter(), out_dir=tmp_path)  # type: ignore[arg-type]
    seen: list[Any] = []
    lp.listeners.append(seen.append)
    first = await lp.refresh()
    assert first and all(c.field == "__listed__" for c in first)
    fa.m[0] = dict(fa.m[0], tickSize="0.5")
    changes = await lp.refresh()
    assert [(c.market, c.field) for c in changes] == [(fa.m[0]["marketDisplayName"].split("-")[0], "tick_size")]
    assert (tmp_path / f"date={__import__('bot.common.time', fromlist=['utc_date_str']).utc_date_str(changes[0].ts_us)}"
            / "changes.jsonl").exists()
    assert len(seen) == 2 and lp.get(Venue.ARCUS, "BTC").venue_market_id == 1


# ------------------------------------------------------------------------------------------------ hub
def test_marketdata_hub_ticks() -> None:
    hub = MarketDataHub()
    v = hub.view(Venue.ARCUS, "btc")
    v.book.load([(D("100"), D("1"))], [(D("100.1"), D("1"))], 1)
    v.book_ts_us = S
    for i in range(3700):
        v.book.set_level(True, D("100"), D("1"))
        hub.tick_1s(S + i * 1_000_000)
    assert v.vol_1h.n >= 1 and v.sigma_1h() >= 0 and len(v.closed_bars()) > 50
    assert hub.stale_markets(S + 3700 * 1_000_000) == [(Venue.ARCUS, "BTC")]
    assert hub.get(Venue.LIGHTER_RH, "BTC") is None


# ------------------------------------------------------------------------------------------------ recorder handlers
def test_recorder_handlers_write_all_tables(tmp_path: Path) -> None:
    from bot.common.config import load_arcus_config, load_lighter_config
    from bot.research.loaders.read import read_table
    from bot.research.recorder.recorder import Recorder

    rec = Recorder(["BTC", "SPY"], load_arcus_config(ROOT / "config/venues/arcus.yaml"),
                   load_lighter_config(ROOT / "config/venues/lighter_rh.yaml"), data_dir=tmp_path)
    rec.arcus_display = {"BTC": "BTC-USD", "SPY": "SPY-USD"}
    rec.lighter_ids = {"BTC": 1, "SPY": 26}
    rec.lighter_ws.base_by_id.update({1: "BTC", 26: "SPY"})
    rec._wire_arcus()
    rec._wire_lighter()

    async def feed() -> None:
        for name, ws in (("arcus", rec.arcus_ws), ("lighter", rec.lighter_ws)):
            for fr in json.loads((ROOT / "tests" / "fixtures" / "live" / f"{name}_ws_frames.json").read_text()):
                await ws._on_message(fr["raw"], fr["recv_ts_us"])

    asyncio.run(feed())
    for disp, sync in rec.arcus_ws.books.items():
        rec._snapshot_row("arcus", disp.split("-")[0], sync.book, S)
    rec.writer.flush()
    for t in ("book_deltas", "bbo", "trades", "mark_oracle_index", "funding_pred", "market_attrs", "book_snapshots"):
        df = read_table(tmp_path, t)
        assert not df.is_empty(), t
        assert set(df["venue"].unique().to_list()) >= ({"arcus"} if t == "book_snapshots" else {"arcus", "lighter_rh"}) \
            or t in ("market_attrs", "funding_pred", "trades"), t
