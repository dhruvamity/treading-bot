"""Leverage: sizes from each market's maximum, the RWA off-hours margin, liquidation rules, and the live wiring."""

from __future__ import annotations

import datetime as dt
import math
from decimal import Decimal as D
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from bot.common import settings
from bot.common.config import MMSession, SessionWindowCfg
from bot.core.book import ArcusBookSync, L2Book, SyncResult
from bot.core.marketdata import MarketView
from bot.scout.pilot import session_for
from bot.scout.record import ScoutRecorder
from bot.scout.scan import BY_NAME, Scanner, leverages, max_leverage, session_mask
from bot.scout.sim import MarketInfo, Risk, Sim, Window
from bot.scout.tape import DEPTH_N, BboBuffer, DepthBuffer, TapeStore, TradeBuffer
from bot.strategies import make_strategy
from bot.strategies.base import StrategyContext
from bot.venues.base import Venue
from tests.helpers import fixture_markets, mm_session
from tests.unit.test_scout import FLAT, MI, T0, S, run, tape

ET = ZoneInfo("America/New_York")
RTH = {"startSecondsOfDay": 14400, "endSecondsOfDay": 72000, "timezone": "America/New_York", "isOvernight": False}


def _us(y: int, mo: int, d: int, h: int, mi: int = 0) -> int:
    return int(dt.datetime(y, mo, d, h, mi, tzinfo=ET).timestamp()) * 1_000_000


def _meta(name: str, imf: str, off: str | None = None) -> dict[str, object]:
    return {"marketDisplayName": name, "initialMarginFraction": imf, "offHoursInitialMarginFraction": off or imf}


def test_sizes_follow_leverage() -> None:
    r = Risk.at_leverage(25, 16.67)
    assert r.leverage == 25 and r.cap_usd == 100 * 25 / 1.25 and r.order_usd == r.cap_usd / 2
    assert r.cap_off_usd == 100 * 16.67 / 1.25
    assert 1.25 * r.cap_usd == 100 * 25            # the risk engine's hard cap lands exactly on the venue maximum
    assert (r.daily_stop_usd, r.pos_stop_usd, r.kill_usd) == (2.0, 1.0, 10.0)   # the dollar stops do not scale
    assert math.isclose(r.off_scale(), 16.67 / 25)


def test_max_leverage_and_the_ladder() -> None:
    assert max_leverage(_meta("SPY-USD", "0.02", "0.03")) == (50.0, 33.33)
    assert max_leverage(_meta("BTC-USD", "0.025")) == (40.0, 40.0)                # the Arcus maximum, uncapped
    assert [x for x, _ in leverages(_meta("BTC-USD", "0.025"))] == [40.0, 20.0, 10.0, 5.0, 2.0]
    assert max_leverage(_meta("ETH-USD", "0.04")) == (25.0, 25.0)
    assert [x for x, _ in leverages(_meta("GLD-USD", "0.04", "0.06"))] == [25.0, 20.0, 10.0, 5.0, 2.0]
    assert [x for x, _ in leverages(_meta("HOOD-USD", "0.1", "0.15"))] == [10.0, 5.0, 2.0]
    assert leverages(_meta("HOOD-USD", "0.1", "0.15"))[0] == (10.0, 6.67)


def test_the_owner_caps_btc_and_eth_leverage_from_telegram(tmp_path: Path) -> None:
    """/set crypto_lev: BTC and ETH at most that leverage in the scan; max (the default) = what Arcus allows."""
    assert settings.parse("crypto_lev", "40x") == 40 and settings.parse("crypto_lev", "MAX") == "max"
    with pytest.raises(ValueError):
        settings.parse("crypto_lev", "60")
    assert settings.lev_caps({}) == {} and settings.lev_caps({"crypto_lev": "max"}) == {}
    caps = settings.lev_caps({"crypto_lev": 20})
    assert caps == {"BTC-USD": 20.0, "ETH-USD": 20.0}
    assert max_leverage(_meta("BTC-USD", "0.025"), caps) == (20.0, 20.0)
    assert max_leverage(_meta("SPY-USD", "0.02", "0.03"), caps) == (50.0, 33.33)   # every other market: Arcus's
    assert settings.show("crypto_lev", "max") == "Arcus max" and settings.show("crypto_lev", 20.0) == "20x"
    btc = {**_meta("BTC-USD", "0.025"), "markPrice": "86000", "minOrderNotional": "5", "minOrderSize": "0.0001"}
    mi = MarketInfo(0.1, 0.0001, 5.0, 0.0001)
    assert [r["leverage"] for r in Scanner(tmp_path, lev_caps=caps, ladder=True).risks_for(btc, mi)] == [20, 10, 5, 2]
    assert [r["leverage"] for r in Scanner(tmp_path, ladder=True).risks_for(btc, mi)] == [40, 20, 10, 5, 2]
    assert [r["leverage"] for r in Scanner(tmp_path).risks_for(btc, mi)] == [40]     # default: the maximum only


def test_session_mask_follows_the_underlying_hours() -> None:
    f = session_mask(RTH, ["2026-11-26"])
    t = np.array([_us(2026, 9, 21, 3, 59), _us(2026, 9, 21, 4, 0), _us(2026, 9, 21, 19, 59),
                  _us(2026, 9, 21, 20, 0), _us(2026, 9, 26, 12), _us(2026, 11, 26, 12), _us(2026, 11, 27, 12)])
    assert f(t).tolist() == [False, True, True, False, False, False, True]   # Mon pre-4am, in, in, closed, Sat, holiday
    assert session_mask(None, []) is None                                      # crypto: always open


def test_off_hours_shrinks_orders_and_the_cap() -> None:
    t = tape(lambda s: 100.0, 2 * 3600, side=lambda s: False, size=50)   # takers only sell: we keep buying
    risk = Risk.at_leverage(10, 5, pos_stop_usd=1e9, daily_stop_usd=1e9, kill_usd=1e9)   # $800 cap; $400 off-hours
    on = Sim(FLAT, risk, MI).run(Window(t, T0, T0 + 2 * 3600 * S, warmup_s=0))
    off = Sim(FLAT, risk, MI).run(Window(t, T0, T0 + 2 * 3600 * S, warmup_s=0, rth=lambda x: np.zeros(len(x), bool)))
    assert on.max_pos_usd > risk.cap_off_usd * 1.01           # in session the position grows past the off-hours cap
    assert off.max_pos_usd <= 1.2 * risk.cap_off_usd + 1      # off-hours it stays within it


def test_liquidation_distance_cuts_half_with_a_taker_order() -> None:
    # a steady fall with only taker sells: we keep buying up to a large leveraged position; with a high MMF the
    # distance to liquidation drops under 4 sigma and the risk rule sells half at market
    t = tape(lambda s: 100 * (1 - 0.00001 * s), 3 * 3600, side=lambda s: False, size=50)
    mi = MarketInfo(tick=0.01, step=0.0001, min_notional=5.0, mmf=0.03)
    r = Sim(FLAT, Risk.at_leverage(25, pos_stop_usd=1e9, daily_stop_usd=1e9, kill_usd=1e9), mi).run(
        Window(t, T0, T0 + 3 * 3600 * S, warmup_s=0))
    assert r.liq_reduces >= 1 and r.taker_fills >= 1


def test_liquidation_counts_as_a_kill() -> None:
    t = tape(lambda s: 100 * (1 - 0.00005 * s), 2 * 3600, side=lambda s: False, size=50)
    mi = MarketInfo(tick=0.01, step=0.0001, min_notional=5.0, mmf=0.5)   # absurd MMF: liquidated on the first fill
    r = Sim(FLAT, Risk.at_leverage(10, pos_stop_usd=1e9, daily_stop_usd=1e9, kill_usd=1e9), mi).run(
        Window(t, T0, T0 + 2 * 3600 * S, warmup_s=0))
    assert r.liquidated and r.killed and r.end_pos_usd == 0


def test_default_risk_is_unchanged_without_leverage() -> None:
    r = run(tape(lambda s: 100 + 0.02 * math.sin(s / 60), 3600), FLAT)
    assert Risk().off_scale() == 1.0 and r.maker_fills > 0 and not r.liquidated


def test_pilot_session_carries_leverage_and_sizes() -> None:
    risk = Risk.at_leverage(50, 33.33)
    s = session_for("SPY-USD", BY_NAME["deep 3bp"], risk, live=False)
    sess = MMSession.model_validate(s)
    assert sess.leverage_max == 50 and sess.inventory_cap_usd == 4000 and sess.order_size_usd == 2000
    assert sess.inventory_cap_off_usd is not None and math.isclose(sess.inventory_cap_off_usd, 4000 * 33.33 / 50,
                                                                   rel_tol=1e-3)
    assert math.isclose(sess.off_hours.size_mult, 33.33 / 50, rel_tol=1e-3)
    assert (sess.daily_stop_usd, sess.pos_stop_usd, sess.kill_usd) == (2.0, 1.0, 10.0)


def test_pilot_sessions_match_the_backtest_rules() -> None:
    s = MMSession.model_validate(session_for("NVDA-USD", BY_NAME["anchor 3bp"], Risk(), live=False))
    assert (s.mode, s.spacing_bps, s.reset_threshold_pct, s.session.skip_et) == ("anchor", 3, 0.1, [])
    assert s.safety_pause.move_sigma_1s >= 1e9 and s.safety_pause.spread_x_median >= 1e9   # anchor: no pause
    assert make_strategy(s).name == "anchor"
    s = MMSession.model_validate(session_for("QQQ-USD", BY_NAME["touch 1bp, skip US session"], Risk(), live=False))
    assert s.session.skip_et == ["09:00-16:30"] and (s.mode, s.execution_style) == ("mid", "normal")
    assert s.safety_pause.move_sigma_1s == 6 and s.safety_pause.spread_x_median == 3   # the backtest's pause rules
    for cfg in BY_NAME.values():   # the backtest has no thin-depth rule, so no pilot session has one either
        assert MMSession.model_validate(session_for("QQQ-USD", cfg, Risk(), live=False)).safety_pause.depth_frac_min == 0


@pytest.mark.parametrize("bad", ["9-16", "16:30-09:00", "09:00-09:00", "25:00-26:00"])
def test_skip_windows_must_be_same_day_new_york_times(bad: str) -> None:
    with pytest.raises(ValueError):
        SessionWindowCfg(skip_et=[bad])


def test_live_strategy_uses_the_off_hours_cap() -> None:
    m = fixture_markets()[Venue.ARCUS]["AMD"]
    sess = mm_session(market="AMD", mode="mid", execution_style="passive", passive_k_sigma=0.0, spacing_bps=3,
                      levels_per_side=1, skew_kappa=0.0, order_size_usd=400, inventory_cap_usd=800,
                      inventory_cap_off_usd=400, off_hours={"spacing_mult": 1, "size_mult": 0.5, "allow_mid": True})
    view = MarketView(Venue.ARCUS, "AMD")
    view.book = L2Book()
    view.book.load([(D("620.00"), D("10"))], [(D("620.40"), D("10"))], 1)

    def sides(off: bool) -> set[str]:
        inv = D("0.6")   # ~$372 long: under the $800 cap, near the $400 off-hours cap
        ctx = StrategyContext(now_us=T0, venue=Venue.ARCUS, market=m, view=view, params=sess, inventory=inv,
                              off_hours=off)
        out = make_strategy(sess).on_tick(ctx)
        return {o.side.value for o in out.desired[(Venue.ARCUS, "AMD")]}
    assert sides(False) == {"buy", "sell"}
    assert sides(True) == {"sell"}               # off-hours the next buy would pass 1.2x the $400 cap


def test_depth_sampling_writes_top_levels(tmp_path: Path) -> None:
    rec = ScoutRecorder(tmp_path, rest_url="http://x", ws_url="ws://x", depth=True)
    rec.display["SPY"] = "SPY-USD"
    rec.bbo["SPY-USD"], rec.trades["SPY-USD"], rec.depth_buf["SPY-USD"] = BboBuffer(), TradeBuffer(), DepthBuffer()
    sync = ArcusBookSync()
    sync.on_snapshot({"lastSequenceId": 1, "timestamp": T0,
                      "bids": [["700.00", "1"], ["699.99", "2"]], "asks": [["700.02", "3"]]})
    rec._on_book("SPY", sync, SyncResult.APPLIED, T0)
    rec.sample_depth(T0)
    rec.sample_depth(T0 + S)                      # unchanged book: no second row
    rec.flush()
    d = TapeStore(tmp_path / "tape").load_depth("SPY-USD", T0 - S, T0 + 2 * S)
    assert len(d["ts"]) == 1 and d["bp"].shape == (1, DEPTH_N)
    assert d["bp"][0][:2].tolist() == [700.0, 699.99] and d["bs"][0][:3].tolist() == [1.0, 2.0, 0.0]
    assert d["ap"][0][0] == 700.02 and d["as_"][0][0] == 3.0


def test_a_large_order_fills_only_what_the_taker_printed_beyond_it() -> None:
    # every taker order sells 50 units: half at the touch (2 bp from mid), half 6 bp through it. Our $2,000 bid rests
    # at 3 bp, between the two: the taker would have used the touch level first, so at most 25 units reach us.
    t = tape(lambda s: 100.0, 1200, side=lambda s: False, size=50, every_s=60)
    risk = Risk.at_leverage(50, pos_stop_usd=1e9, daily_stop_usd=1e9, kill_usd=1e9)
    r = Sim(FLAT, risk, MI).run(Window(t, T0, T0 + 1200 * S, warmup_s=0))
    assert r.maker_fills > 0
    assert r.maker_usd / r.maker_fills <= 25 * 100 * 1.001    # never the taker's whole 50 units


@pytest.mark.parametrize("inv", [D(0), D("0.5"), D("-0.9")])
def test_live_quotes_match_the_backtest_at_leverage_with_skew(inv: D) -> None:
    """QQQ-like: 10x sizes ($400 orders, $800 cap), skew on (kappa 1), 3 bp from mid."""
    from collections import deque

    from bot.scout.sim import Book, Config, MidPolicy
    m = fixture_markets()[Venue.ARCUS]["AMD"]
    risk = Risk.at_leverage(10)
    s = session_for("AMD-USD", BY_NAME["deep 3bp, skew"], risk, live=False)
    sess = MMSession.model_validate({**s, "account_index": 1})
    view = MarketView(Venue.ARCUS, "AMD")
    view.book = L2Book()
    view.book.load([(D("620.00"), D("50"))], [(D("620.40"), D("50"))], 1)
    ctx = StrategyContext(now_us=T0, venue=Venue.ARCUS, market=m, view=view, params=sess, inventory=inv)
    out = make_strategy(sess).on_tick(ctx)
    live = sorted((o.side.value, o.price_ticks, o.size_quantums) for o in out.desired[(Venue.ARCUS, "AMD")])
    mi = MarketInfo(float(m.tick_size), float(m.step_size), float(m.min_notional), float(m.min_size))
    pol = MidPolicy(Config("x", "mid", spacing_bps=3, kappa=1.0), risk, mi)
    q, _ = pol.quotes(Book(T0, 620.0, 620.4, 620.2, float(inv), None, 0.0, 0.0, deque()))
    ours = sorted(("buy" if sd == 1 else "sell", round(p / mi.tick), round(qq / mi.step)) for sd, p, qq, _t in q)
    assert live == ours and live


def test_arcus_wallet_address_is_read_as_arcus_address(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from bot.common.secrets import SecretStore
    monkeypatch.delenv("ARCUS_ADDRESS", raising=False)
    monkeypatch.setenv("ARCUS_WALLET_ADDRESS", "0x" + "ab" * 20)
    assert SecretStore(tmp_path / "none.enc", password="").get("ARCUS_ADDRESS") == "0x" + "ab" * 20
