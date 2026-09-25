"""Strategy / paper venue / state / market-data paths not covered elsewhere (offline)."""

from __future__ import annotations

import json
from decimal import Decimal as D
from pathlib import Path
from typing import Any

import pytest

from bot.common.config import load_session
from bot.common.errors import ConfigError
from bot.core.book import L2Book
from bot.core.liveparams import LiveParams
from bot.core.marketdata import MarketDataHub, MarketView
from bot.core.state import StateStore
from bot.strategies import make_strategy
from bot.strategies.base import StrategyContext
from bot.strategies.mid import MidStrategy
from bot.venues.base import (
    TIF,
    OrderRequest,
    OrderState,
    OrderStatus,
    Position,
    Side,
    Venue,
)
from bot.venues.paper.adapter import LatencyModel, PaperVenue
from tests.helpers import fixture_markets, mm_session

MK = fixture_markets()
A = MK[Venue.ARCUS]["BTC"]
S = 1_790_000_000_000_000
ROOT = Path(__file__).parents[2]


def view(venue: Venue, bid: str, ask: str, size: str = "5") -> MarketView:
    v = MarketView(venue, "BTC")
    v.book.load([(D(bid), D(size))], [(D(ask), D(size))], 1)
    v.book_ts_us = S
    return v


# ------------------------------------------------------------------------------------------------ strategies
def test_mid_quotes_and_blocks() -> None:
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
    from bot.common.indicators import ema, rsi

    assert rsi([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15], 14) == 100.0 and rsi([1.0] * 5, 14) is None
    assert ema([1.0, 1.0, 1.0], 3) == 1.0 and ema([1.0], 3) is None
    # auto grid spacing: k x sigma_1h / sqrt(F), floored at 2 maker fees + 1 bp and 2 ticks, capped at the max
    assert qt.vol_spacing(0.01, 25, k_delta=1, delta_min=0.0005, delta_max=0.01, maker_fee=0, tick_frac=1e-6) == 0.002
    assert qt.vol_spacing(0.01, 25, k_delta=1, delta_min=0.0005, delta_max=0.001, maker_fee=0, tick_frac=1e-6) == 0.001
    assert qt.vol_spacing(0.0, 25, k_delta=1, delta_min=0.0005, delta_max=0.01, maker_fee=0.001, tick_frac=1e-6) == 0.01


def test_make_strategy_from_session_files(tmp_path: Path) -> None:
    files = [f for f in sorted((ROOT / "config" / "sessions").glob("*.yaml")) if f.name != "pilot.yaml"]
    assert files
    for f in files:
        s = make_strategy(load_session(f))
        assert hasattr(s, "on_tick")
    dn = tmp_path / "dn.yaml"   # a two-venue session file from before 2026-09-25 is refused with a clear message
    dn.write_text("session_id: dn_spy\nstrategy: dn_carry\nmarket: SPY\n")
    with pytest.raises(ConfigError, match="no longer supported"):
        load_session(dn)


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


async def test_liveparams_refresh_and_change_log(tmp_path: Path) -> None:
    fa = _FakeArcus()
    lp = LiveParams(arcus=fa, out_dir=tmp_path)  # type: ignore[arg-type]
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
    assert hub.get(Venue.ARCUS, "ETH") is None

