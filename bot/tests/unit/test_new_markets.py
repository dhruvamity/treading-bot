"""New Arcus listings: pre-listed OFFLINE markets, fields not set yet, a partial first day, listing-week flow, and a
market that goes offline under a running bot."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import time
from decimal import Decimal as D
from pathlib import Path
from typing import Any

import numpy as np

from bot.common.config import AppConfig
from bot.core.calendar import TradingCalendar
from bot.core.livelock import RunMode
from bot.core.liveparams import LiveParams
from bot.core.marketdata import MarketView
from bot.core.risk import RiskAction, RiskEngine
from bot.scout import record
from bot.scout.pilot import Pilot
from bot.scout.scan import Pct, Risk, Scanner, _score, listed_days_ago
from bot.scout.tape import US_DAY, TapeStore, day_start_us
from bot.telegram.control import Control
from bot.venues.arcus.models import parse_market
from bot.venues.base import Venue
from tests.helpers import fixture_markets, mm_session
from tests.unit.test_go_live import ADDR, K0, Env, FakeArcus, doctor, levels

MK = fixture_markets()
FIX = Path(__file__).parents[1] / "fixtures" / "live"
S = 1_000_000


def _raw(name: str = "BTC-USD") -> dict[str, Any]:
    ms = json.loads((FIX / "arcus_markets.json").read_text())["markets"]
    return dict(next(m for m in ms if m["marketDisplayName"] == name))


# ------------------------------------------------------------------------------------------------ parsing
def test_a_listing_without_a_session_timezone_still_parses() -> None:
    m = _raw() | {"regularTradingHours": {"startSecondsOfDay": 14400, "endSecondsOfDay": 72000},
                  "addedTimestamp": 1790283864}
    mk = parse_market(m, maker_fee=D(0), taker_fee=D("0.000225"))
    assert mk.rth == (14400, 72000, "America/New_York") and mk.extra["addedTimestamp"] == 1790283864


class _Rest:
    def __init__(self, markets: list[dict[str, Any]]) -> None:
        self._m = markets

    async def feetiers(self) -> dict[str, Any]:
        return {"tiers": [{"level": 0, "maker_fee_ppm": 0, "taker_fee_ppm": 225}]}

    async def markets(self) -> list[dict[str, Any]]:
        return self._m


def test_one_malformed_listing_does_not_stop_the_others_from_loading() -> None:
    broken = _raw() | {"marketDisplayName": "NEWCO-USD", "marketId": 999, "maintenanceMarginFraction": None}
    lp = LiveParams(arcus=_Rest([_raw(), broken]))  # type: ignore[arg-type]
    asyncio.run(lp.refresh())
    assert set(lp.markets[Venue.ARCUS]) == {"BTC"}


# ------------------------------------------------------------------------------------------------ live risk gate
def test_quoting_stops_while_the_market_is_not_online() -> None:
    risk = RiskEngine()
    m = MK[Venue.ARCUS]["BTC"]
    view = MarketView(Venue.ARCUS, "BTC")
    view.status = "OFFLINE"
    d = risk.band_or_oi(view, m)
    assert d is not None and d.action is RiskAction.STOP_MARKET_QUOTING and "OFFLINE" in d.reason
    assert not risk.quoting_allowed(Venue.ARCUS, "BTC", 0)[0]
    view.status = "ONLINE"
    assert risk.band_or_oi(view, m) is None and risk.quoting_allowed(Venue.ARCUS, "BTC", 0)[0]
    view.status = None     # before the markets channel speaks, the hourly REST status counts
    assert risk.band_or_oi(view, dataclasses.replace(m, status="OFFLINE")) is not None


# ------------------------------------------------------------------------------------------------ doctor
def _markets_with(name: str, **kw: Any) -> dict[Venue, dict[str, Any]]:
    mk = {v: dict(per) for v, per in MK.items()}
    mk[Venue.ARCUS][name] = dataclasses.replace(MK[Venue.ARCUS][name], **kw)
    return mk


def test_doctor_blocks_live_on_a_market_that_is_not_trading(tmp_path: Path, monkeypatch: Any) -> None:
    import tests.unit.test_go_live as g

    env = Env(ARCUS_ADDRESS=ADDR, ARCUS_API_PRIVATE_KEY=K0)
    s = mm_session(account_index=0)
    monkeypatch.setattr(g, "MK", _markets_with("BTC", status="OFFLINE"))
    assert "FAIL" in levels(doctor([s], FakeArcus(), env, tmp=tmp_path), "market")
    assert "FAIL" not in levels(doctor([s], FakeArcus(), env, mode=RunMode.PAPER, tmp=tmp_path), "market")


def test_doctor_flags_a_new_listing_and_a_stock_without_an_earnings_date(tmp_path: Path, monkeypatch: Any) -> None:
    import tests.unit.test_go_live as g

    env = Env(ARCUS_ADDRESS=ADDR, ARCUS_API_PRIVATE_KEY=K0)
    btc = MK[Venue.ARCUS]["BTC"]
    monkeypatch.setattr(g, "MK", _markets_with("BTC", category="EQUITIES", oi_cap_usd=D(100_000),
                                               extra={**btc.extra, "addedTimestamp": time.time() - 3 * 86400}))
    rep = doctor([mm_session(account_index=0)], FakeArcus(), env, tmp=tmp_path)
    msgs = " | ".join(c.message for c in rep.checks)
    assert "listed 3 days ago, open-interest cap $100,000" in msgs
    assert "no BTC earnings date" in msgs and "WARN" in levels(rep, "calendar")


# ------------------------------------------------------------------------------------------------ recorder
class _WS:
    made: list[_WS] = []

    def __init__(self, url: str, n_levels: int = 1) -> None:
        self.subs: list[str] = []
        _WS.made.append(self)

    def on(self, event: str, fn: Any) -> None:
        pass

    def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def subscribe_market(self, d: str, **kw: Any) -> None:
        self.subs.append(d)


def test_new_listings_join_without_resubscribing_the_others(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(record, "ArcusWS", _WS)
    _WS.made = []
    rec = record.ScoutRecorder(tmp_path, rest_url="http://x", ws_url="ws://x")
    first = [f"M{i:02d}-USD" for i in range(50)]
    assert asyncio.run(rec.subscribe(first)) == first
    assert [len(w.subs) for w in _WS.made] == [45, 5]             # 45 markets (90 subscriptions) per connection
    assert asyncio.run(rec.subscribe([*first, "KBONK-USD"])) == ["KBONK-USD"]
    assert [len(w.subs) for w in _WS.made] == [45, 6] and _WS.made[1].subs[-1] == "KBONK-USD"


def test_a_failed_market_list_read_is_retried_not_fatal(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(record, "ArcusWS", _WS)
    rec = record.ScoutRecorder(tmp_path, rest_url="http://x", ws_url="ws://x")

    async def boom() -> list[str]:
        raise OSError("network down")

    rec.refresh_markets = boom  # type: ignore[method-assign]
    assert asyncio.run(rec._update_markets()) is False


# ------------------------------------------------------------------------------------------------ scout
def _bbo(t0: int, t1: int) -> dict[str, np.ndarray]:
    ts = np.arange(t0, t1, 60 * S, dtype=np.int64)
    n = len(ts)
    return {"ts": ts, "bid": np.full(n, 99.0), "ask": np.full(n, 101.0), "bid_sz": np.ones(n), "ask_sz": np.ones(n)}


def test_a_markets_partial_first_day_is_not_a_full_day(tmp_path: Path) -> None:
    store = TapeStore(tmp_path / "tape")
    d0 = day_start_us("2026-09-20")
    for k in range(3):   # the recorder (BTC) up all three days
        store.write_part("BTC-USD", "bbo", "t", _bbo(d0 + k * US_DAY, d0 + (k + 1) * US_DAY))
    store.write_part("KBONK-USD", "bbo", "t", _bbo(d0 + 15 * 3600 * S, d0 + 3 * US_DAY))   # listed 15:00 UTC
    store.write_part("OLD-USD", "bbo", "t", _bbo(d0 + 600 * S, d0 + 3 * US_DAY))            # recorded from 00:10
    sc = Scanner(tmp_path)
    now = d0 + 3 * US_DAY + 3600 * S
    assert sc.full_days("KBONK-USD", now) == ["2026-09-21", "2026-09-22"]
    assert sc.full_days("OLD-USD", now) == ["2026-09-20", "2026-09-21", "2026-09-22"]


def test_a_new_listing_needs_three_full_days() -> None:
    day = {"config": "deep 3bp", "pnl": 0.5, "maker_fills": 50, "maker_usd": 10_000, "day_stops": 0, "killed": False,
           "taker_usd": 0, "actions": 100}
    rec = {**day, "hours": 24, "tail_pnl": 0.0}
    r = Risk.for_capital(100, 10).__dict__

    def go(n_days: int, listed: float | None) -> list[str]:
        bt = {"days": {f"2026-09-2{i}": [day] for i in range(n_days)}, "recent": [rec]}
        return next(c for c in _score("KBONK-USD", bt, {"age_s": 1}, r, True, Pct(), listed_days=listed)
                    if c.setting == "deep 3bp").reasons

    assert "new market (trading 2 days): 2 of 3 full days" in go(2, 2.0)
    assert go(3, 4.0) == [] and go(1, 400.0) == [] and go(1, None) == []
    now = int(time.time() * 1e6)
    assert round(listed_days_ago({"addedTimestamp": now / 1e6 - 5 * 86400}, now) or 0) == 5
    assert listed_days_ago({}, now) is None
    d = day_start_us("2026-09-22")        # pre-listed in May, first seen by the recorder (up since 09-19) on 09-22
    may = {"addedTimestamp": 1778786860}
    up = day_start_us("2026-09-19") + 12 * 3600 * S
    assert round(listed_days_ago(may, d + 2 * US_DAY, d, up) or 0) == 2
    assert (listed_days_ago(may, d, up + 300 * S, up) or 0) > 100   # picked up with the rest at start: not new


def test_imported_history_does_not_make_other_markets_look_new(tmp_path: Path) -> None:
    # 2026-09-26 on the server: the arcus-mm import holds BTC books from 09-19, the scout's recorder started on 09-23,
    # and every market outside the import (MSFT, listed for months) was flagged "new market (trading 3 days)"
    st = TapeStore(tmp_path)

    def bbo(market: str, part: str, t0: int) -> None:
        ts = np.arange(t0, t0 + 3600 * S, 60 * S, dtype=np.int64)
        st.write_part(market, "bbo", part, {"ts": ts, "bid": np.full(len(ts), 99.0), "ask": np.full(len(ts), 101.0),
                                            "bid_sz": np.ones(len(ts)), "ask_sz": np.ones(len(ts))})

    start = day_start_us("2026-09-23") + (20 * 3600 + 36 * 60) * S          # the recorder's first row
    bbo("BTC-USD", "arcusmm-raw-2026-09-19", day_start_us("2026-09-19"))     # imported history
    bbo("BTC-USD", "rec203603-497276", start)
    bbo("MSFT-USD", "rec203603-497276", start + 90 * S)
    bbo("KBONK-USD", "rec073000-497310", day_start_us("2026-09-25") + 7 * 3600 * S)   # turned ONLINE later
    bbo("OLD-USD", "arcusmm-raw-2026-09-19", day_start_us("2026-09-19"))     # imported only: no recorder rows
    assert st.first_recorded_us("BTC-USD") == start and st.first_recorded_us("OLD-USD") is None
    now = day_start_us("2026-09-26")
    listed_long_ago = {"addedTimestamp": 1778786860}
    msft, kbonk = (listed_days_ago(listed_long_ago, now, st.first_recorded_us(m), st.first_recorded_us("BTC-USD"))
                   for m in ("MSFT-USD", "KBONK-USD"))
    assert (msft or 0) > 100
    assert kbonk is not None and 0.5 < kbonk < 1


def test_the_pilot_pauses_a_deployment_whose_market_goes_offline(tmp_path: Path) -> None:
    app = AppConfig(state_dir=str(tmp_path / "state"))
    ctl = Control(app, root=tmp_path)
    pilot = Pilot(tmp_path, ctl)
    pilot.save({"active": {"market": "GME-USD", "config": "deep 3bp @ 10x", "mode": "paper"}})
    ctl.is_running = lambda mode: True  # type: ignore[method-assign]
    ev = pilot.review({"top": [], "all": [], "offline": ["GME-USD"]})
    assert ev[0]["kind"] == "paused" and "offline" in ev[0]["text"]
    assert "GME" in json.loads(ctl._kv_get("paper", "paused") or "{}")
    assert TradingCalendar().next_event(0, {"earnings"}, "GME") is None


def test_the_scan_skips_a_malformed_listing_instead_of_failing(tmp_path: Path) -> None:
    from bot.scout.scan import load_markets

    ok = {"marketDisplayName": "QQQ-USD", "status": "ONLINE", "tickSize": "0.01", "stepSize": "0.001",
          "minOrderNotional": "5", "minOrderSize": "0.001", "maintenanceMarginFraction": "0.026"}
    bad = {"marketDisplayName": "NEWCO-USD", "status": "ONLINE", "tickSize": None, "stepSize": "0.1"}
    pre = {"marketDisplayName": "F-USD", "status": "OFFLINE", "tickSize": "0.01", "stepSize": "0.0000001"}
    p = tmp_path / "markets.json"
    p.write_text(json.dumps({"markets": [ok, bad, pre]}))
    assert list(load_markets(p)) == ["QQQ-USD"]
