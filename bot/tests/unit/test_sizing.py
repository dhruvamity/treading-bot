"""Capital-based sizing: the same setup on any account size, stops as % of the capital, the venue-minimum floor and
the liquidity ceiling, and the live engine following the account's equity (bot/common/sizing.py)."""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal as D
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import yaml

from bot.common import sizing
from bot.common.config import AppConfig, SizingDefaults, load_session
from bot.common.sizing import Pct, bucket, min_capital, sizes, target_capital
from bot.core.livelock import RunMode
from bot.scout.capital import choose
from bot.scout.pilot import Pilot, session_for
from bot.scout.scan import BY_NAME, MENU, MarketInfo, Scanner, _score, liquidity, risk_key, taker_orders
from bot.scout.sim import Risk
from bot.telegram.control import Control
from bot.venues.base import Venue
from tests.helpers import fixture_markets
from tests.sim.engine import Simulator
from tests.unit.test_go_live import ADDR, K0, Env, FakeArcus, doctor, levels

MK = fixture_markets()


# ------------------------------------------------------------------------------------------------ the formulas
def test_bucket_rounds_down_to_the_series() -> None:
    assert bucket(100) == 100 and bucket(99.99) == 90 and bucket(3.5) == 3.2 and bucket(1234) == 1100
    assert bucket(0) == 0 and bucket(-5) == 0 and bucket(1_000_000) == 1_000_000 and bucket(0.95) == 0.9
    for x in (0.37, 7.7, 55, 101, 999, 12_345, 7_654_321):
        b = bucket(x)
        assert b <= x and b >= x / 1.15   # never above the equity, at most ~13% below it


def test_sizes_are_a_fixed_share_of_capital() -> None:
    s = sizes(100, 20, 16.67)
    assert (s.order, s.cap, s.pos_stop, s.daily_stop, s.kill) == pytest.approx((800, 1600, 1, 2, 10))
    assert s.cap_off == pytest.approx(100 * 16.67 / 1.25)
    for k in (0.1, 10, 1000):   # the same setup scales linearly with the capital
        t = sizes(100 * k, 20, 16.67)
        assert (t.order, t.cap, t.cap_off, t.pos_stop, t.daily_stop, t.kill) == pytest.approx(
            (s.order * k, s.cap * k, s.cap_off * k, s.pos_stop * k, s.daily_stop * k, s.kill * k))
    custom = sizes(200, 10, pct=Pct(position_stop=0.5, daily_stop=1.5, kill=5))
    assert (custom.pos_stop, custom.daily_stop, custom.kill) == pytest.approx((1, 3, 10))


def test_the_liquidity_ceiling_caps_orders_and_the_capital_the_stops_use() -> None:
    s = sizes(100_000, 20, order_max=10_000)
    assert s.order == pytest.approx(10_000) and s.cap == pytest.approx(20_000)
    assert s.capital == pytest.approx(1_250)          # the stops are taken on what is used, not on $100k
    assert (s.pos_stop, s.daily_stop, s.kill) == pytest.approx((12.5, 25, 125))
    assert sizes(100, 20, order_max=10_000).capital == 100   # not binding: everything as usual


def test_minimum_capital_keeps_the_smallest_order_above_the_venue_minimum() -> None:
    assert min_capital(5, 16.67) == pytest.approx(1.2 * 5 * 2.5 / 16.67)   # QQQ at max: under $1
    assert min_capital(5, 2) == pytest.approx(7.5)
    assert min_capital(18.04, 6.67) == pytest.approx(1.2 * 18.04 * 2.5 / 6.67)   # SNDK: ~$8
    lo = min_capital(5, 6.67)
    assert sizes(lo, 10, 6.67).cap_off / 2 == pytest.approx(1.2 * 5)       # the off-hours order is exactly 1.2x min


def test_target_capital_follows_equity_within_what_a_backtest_covered() -> None:
    assert target_capital(1_000) == 1_000
    assert target_capital(1_000, covered=100) == 125              # at most 1.25x the backtested capital
    assert target_capital(1_000, frac=0.5) == 500
    assert target_capital(1_000, max_capital=300) == 280          # capped, then bucketed down
    assert target_capital(97.3) == 90


# ------------------------------------------------------------------------------------------------ the scout
def _meta(market: str, price: float, imf: float, off_imf: float, min_size: float) -> dict[str, Any]:
    return {"marketDisplayName": market, "markPrice": str(price), "initialMarginFraction": str(imf),
            "offHoursInitialMarginFraction": str(off_imf), "minOrderNotional": "5", "minOrderSize": str(min_size)}


def test_scanner_sizes_at_the_capital_and_skips_leverages_it_cannot_fund(tmp_path: Path) -> None:
    sndk = _meta("SNDK-USD", 1803.89, 0.1, 0.15, 0.01)          # minimum order $18.04
    mi = MarketInfo(0.01, 0.01, 5.0, 0.01)
    rs = Scanner(tmp_path, capital=10).risks_for(sndk, mi)
    assert [r["leverage"] for r in rs] == [10, 5, 2]
    assert all(r["capital_usd"] == 10 and r["pos_stop_usd"] == pytest.approx(0.1) for r in rs)
    need = {r["leverage"]: r["min_capital_usd"] for r in rs}
    assert need[10] == pytest.approx(8.12, abs=0.01) and need[2] == pytest.approx(27.06, abs=0.01)
    ok = [r["leverage"] for r in rs if r["used_usd"] >= r["min_capital_usd"]]
    assert ok == [10]                                            # $10 funds SNDK only at its maximum
    big = Scanner(tmp_path, capital=100_000).risks_for(sndk, mi, order_max=3_000)
    assert {r["order_usd"] for r in big} == {3_000}              # every rung stops at the ceiling
    assert [round(r["used_usd"]) for r in big] == [750, 1500, 3750]


def test_cache_keys_ignore_the_price_dependent_minimum_and_a_non_binding_ceiling() -> None:
    a = Risk.for_capital(100, 20, 16.67, min_capital=0.9, order_max=10_000)
    b = Risk.for_capital(100, 20, 16.67, min_capital=1.1, order_max=20_000)
    assert risk_key(a.__dict__) == risk_key(b.__dict__)          # neither changes the backtest
    c = Risk.for_capital(100_000, 20, 16.67, order_max=10_000)
    d = Risk.for_capital(100_000, 20, 16.67, order_max=20_000)
    assert risk_key(c.__dict__) != risk_key(d.__dict__)          # a binding ceiling does


def test_go_thresholds_are_percent_of_the_capital_used() -> None:
    day = {"config": "deep 3bp", "pnl": -2.0, "maker_fills": 50, "maker_usd": 10_000, "day_stops": 0,
           "killed": False, "taker_usd": 0, "actions": 100}
    rec = {**day, "hours": 24, "tail_pnl": 0.0, "pnl": 0.0}
    for cap, go in ((100, False), (1_000, True)):                # -$2/day: -2% of $100, -0.2% of $1,000
        r = Risk.for_capital(cap, 10).__dict__
        c = next(x for x in _score("QQQ-USD", {"days": {"2026-09-20": [day]}, "recent": [rec]}, {"age_s": 1},
                                   r, True, Pct()) if x.setting == "deep 3bp")
        assert ("loses" not in "; ".join(c.reasons)) is go
    skip = {"days": {}, "recent": [], "skip": "needs $27.06 of capital at 2x (Arcus minimum order)"}
    c = _score("SNDK-USD", skip, {"age_s": 1}, Risk.for_capital(10, 2).__dict__, False, Pct())[0]
    assert c.too_small and not c.go and c.reasons[0].startswith("needs $27.06")


def test_liquidity_groups_prints_into_taker_orders() -> None:
    tr = {"ts": np.arange(6), "px": np.full(6, 100.0), "sz": np.array([1, 2, 3, 1, 1, 50.0]),
          "seq": np.array([7, 7, 7, 0, 0, 9])}
    assert sorted(taker_orders(tr)) == [100, 100, 600, 5000]     # seq 7 is one order; unnumbered prints count alone
    liq = liquidity(tr)
    assert liq["takers"] == 4 and liq["volume"] == 5800 and 600 < liq["p99"] <= 5000


def test_scout_capital_comes_from_the_account_or_the_paper_amount() -> None:
    z = SizingDefaults()
    assert choose("auto", 1_234.5, z)[0] == 1_100 and "account equity" in choose("auto", 1_234.5, z)[1]
    assert choose("auto", None, z) == (100, "paper capital: no funded account $100.00")
    assert choose("250", 9_999, z)[0] == 250
    half = SizingDefaults(capital_frac=0.5, max_capital_usd=400)
    assert choose(None, 1_000, half)[0] == 400                   # 1000 x 0.5 = 500, capped at 400


# ------------------------------------------------------------------------------------------------ pilot + engine
def _pilot_session(tmp_path: Path, capital: float = 100, lev: float = 20, off: float = 16.67,
                   order_max: float | None = None) -> Path:
    r = Risk.for_capital(capital, lev, off, order_max=order_max, min_capital=0.9)
    p = tmp_path / "pilot.yaml"
    p.write_text(yaml.safe_dump(session_for("BTC-USD", BY_NAME["deep 3bp, skew"], r, live=False)))
    return p


def test_the_session_file_reproduces_the_backtest_sizes_exactly(tmp_path: Path) -> None:
    for cap, om in ((100, None), (37, None), (100_000, 5_000)):
        r = Risk.for_capital(cap, 20, 16.67, order_max=om, min_capital=0.9)
        s = load_session(_pilot_session(tmp_path, cap, order_max=om))
        before = (s.order_size_usd, s.inventory_cap_usd, s.inventory_cap_off_usd, s.pos_stop_usd, s.daily_stop_usd,
                  s.kill_usd, s.capital_usd)
        assert s.sizing is not None and s.sizing.backtest_capital_usd == cap
        sizing.apply(s, cap)   # what the engine does on day one when the equity is the backtested capital
        after = (s.order_size_usd, s.inventory_cap_usd, s.inventory_cap_off_usd, s.pos_stop_usd, s.daily_stop_usd,
                 s.kill_usd, s.capital_usd)
        assert after == pytest.approx(before, rel=1e-6)
        assert s.order_size_usd == pytest.approx(round(r.order_usd, 2))


def _engine(tmp_path: Path, equity: float, **kw: Any) -> Any:
    sess = load_session(_pilot_session(tmp_path, **kw))
    sim = Simulator([sess], MK)
    eng = sim.engines[0]
    eng.set_account(Venue.ARCUS, D(str(equity)), D(str(equity)))
    return eng, sess


def test_the_engine_follows_equity_up_to_what_the_scout_covered(tmp_path: Path) -> None:
    now = 1_790_000_000_000_000
    eng, s = _engine(tmp_path, 1_000)
    eng.resize(now)
    assert eng.size_capital == D("125") and s.order_size_usd == pytest.approx(1_000)   # 1.25x the $100 backtest
    assert s.pos_stop_usd == pytest.approx(1.25) and s.kill_usd == pytest.approx(12.5)
    assert eng.risk.market_limits[(Venue.ARCUS, "BTC")].position_cap_usd == D(str(s.inventory_cap_usd * 1.25))
    eng.state.kv_set("sizing_ok", "1000.00:ETH")                 # another market's check does not count
    eng.resize(now + 86_400_000_000)
    assert s.order_size_usd == pytest.approx(1_000)
    eng.state.kv_set("sizing_ok", "1000.00:BTC")                 # the scout found it GO at $1,000
    eng.resize(now + 86_400_000_000 + 3_600_000_000)
    assert s.order_size_usd == pytest.approx(1_000)              # same UTC day: sizes stay put
    eng.resize(now + 2 * 86_400_000_000)                         # 00:00 UTC: re-read
    assert eng.size_capital == D("1000") and s.order_size_usd == pytest.approx(8_000)
    assert (s.pos_stop_usd, s.daily_stop_usd, s.kill_usd) == pytest.approx((10, 20, 100))


def test_the_engine_shrinks_with_losses_and_stops_quoting_under_the_minimum(tmp_path: Path) -> None:
    now = 1_790_000_000_000_000
    eng, s = _engine(tmp_path, 60)
    eng.resize(now)
    assert eng.size_capital == D("56") and s.order_size_usd == pytest.approx(448)
    eng.set_account(Venue.ARCUS, D("0.5"), D("0.5"))
    eng.resize(now + 86_400_000_000)
    assert "under the $0.90" in eng.too_small


def test_paper_starts_with_the_backtested_capital(tmp_path: Path) -> None:
    from bot.core.runner import BotRunner

    sess = load_session(_pilot_session(tmp_path, 250))
    r = BotRunner.__new__(BotRunner)
    r.sessions, r.app = [sess], AppConfig()
    assert r._paper_equity(Venue.ARCUS) == 250


def test_a_go_review_records_the_capital_it_covered(tmp_path: Path) -> None:
    app = AppConfig(state_dir=str(tmp_path / "state"))
    ctl = Control(app, root=tmp_path)
    (tmp_path / "state").mkdir()
    from bot.core.state import StateStore

    StateStore(str(tmp_path / app.state_db_for("paper"))).close()
    pilot = Pilot(tmp_path, ctl)
    cand = {"market": "QQQ-USD", "config": "deep 3bp @ 20x", "go": True, "reasons": [], "volume_day": 1,
            "capital_usd": 2_500.0}
    pilot.save({"active": {"market": "QQQ-USD", "config": "deep 3bp @ 20x", "mode": "paper"}})
    ctl.is_running = lambda mode: True  # type: ignore[method-assign]
    pilot.review({"top": [cand], "all": [cand]})
    assert ctl._kv_get("paper", "sizing_ok") == "2500.00:QQQ"
    ctl.clear_sizing_ok("paper")                                 # what every approval does before starting
    assert ctl._kv_get("paper", "sizing_ok") == ""


def test_doctor_checks_equity_against_the_setups_minimum(tmp_path: Path) -> None:
    env = Env(ARCUS_ADDRESS=ADDR, ARCUS_API_PRIVATE_KEY=K0)
    s = load_session(_pilot_session(tmp_path))
    s.account_index = 0
    rep = doctor([s], FakeArcus(), env, tmp=tmp_path)            # FakeArcus: $50 equity
    line = next(c.message for c in rep.checks if c.area == "sizing" and "follow the equity" in c.message)
    assert "$50.00 of $50" in line and "order $400.00" in line
    assert s.order_size_usd == 800                               # the doctor sized a copy, not the session
    assert s.sizing is not None
    s.sizing.min_capital_usd = 75
    rep = doctor([s], FakeArcus(), env, tmp=tmp_path)
    assert "FAIL" in levels(rep, "arcus funds")
    assert "FAIL" not in levels(doctor([s], FakeArcus(), env, mode=RunMode.PAPER, tmp=tmp_path), "arcus funds")


def test_every_menu_setting_writes_a_valid_following_session(tmp_path: Path) -> None:
    for cfg in MENU:
        s = session_for("QQQ-USD", cfg, Risk.for_capital(40, 25, 16.67, min_capital=0.9), live=False)
        p = tmp_path / "x.yaml"
        p.write_text(yaml.safe_dump(s))
        sess = load_session(p)
        assert sess.sizing is not None and sess.sizing.follow_equity and sess.capital_usd == 40
        assert json.loads(json.dumps(s))   # plain data only


def test_the_live_scan_output_carries_the_capital() -> None:
    from bot.scout.scan import capital_line

    line = capital_line({"capital": {"usd": 250, "source": "account equity $262.10", "pct": Pct().__dict__}})
    assert "$250.00" in line and "position 1%" in line and "kill 10%" in line
    assert asyncio.iscoroutinefunction(__import__("bot.scout.capital", fromlist=["x"]).account_equity)
