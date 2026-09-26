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
from bot.scout.scan import Pct, Risk, Scanner, _score
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


def test_every_market_needs_three_full_days() -> None:
    # server, 2026-09-26: CRCL and HOOD (1 full day) ranked beside 5-day setups; MU showed "worst day +$1.08, 1 days"
    day = {"config": "deep 3bp", "pnl": 0.5, "maker_fills": 50, "maker_usd": 10_000, "day_stops": 0, "killed": False,
           "taker_usd": 0, "actions": 100}
    rec = {**day, "hours": 24, "tail_pnl": 0.0}
    r = Risk.for_capital(100, 10).__dict__

    def go(n_days: int) -> list[str]:
        bt = {"days": {f"2026-09-2{i}": [day] for i in range(n_days)}, "recent": [rec]}
        return next(c for c in _score("MU-USD", bt, {"age_s": 1}, r, True, Pct()) if c.setting == "deep 3bp").reasons

    assert go(1) == ["1 full day of data (needs 3)"]
    assert go(2) == ["2 full days of data (needs 3)"]
    assert go(3) == []


def test_a_setting_added_to_the_menu_fills_into_cached_days(tmp_path: Path, monkeypatch: Any) -> None:
    """A new menu entry is backtested on the cached days for itself only: no SIM_VERSION bump, no full recompute."""
    import concurrent.futures as cf

    from bot.scout import scan as sc

    ran: list[dict[str, list[str]] | None] = []

    def fake_window(args: tuple[Any, ...]) -> dict[str, list[dict[str, Any]]]:
        only = args[10] if len(args) > 10 else None
        ran.append(only)
        return {sc.risk_key(r): [{"config": n, "pnl": 0.0} for n in (only or {}).get(sc.risk_key(r), args[5])]
                for r in args[6]}

    class Inline:   # the scan's process pool, run in this process so the fake window is used
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        def submit(self, fn: Any, job: Any) -> cf.Future[Any]:
            f: cf.Future[Any] = cf.Future()
            f.set_result(fn(job))
            return f

        def shutdown(self, *a: Any, **k: Any) -> None:
            pass

    monkeypatch.setattr(sc, "_run_window", fake_window)
    monkeypatch.setattr(sc, "ProcessPoolExecutor", Inline)
    s = Scanner(tmp_path, capital=100, workers=1)
    r = Risk.for_capital(100, 10).__dict__ | {"min_capital_usd": 0.0}
    rk = sc.risk_key(r)
    cp = s.cache_path("QQQ-USD", "2026-09-22", rk)
    cp.parent.mkdir(parents=True)
    cp.write_text(json.dumps([{"config": n, "pnl": 1.0} for n in sc.BY_NAME if n != "touch 0bp"]))   # older menu
    monkeypatch.setattr(s, "full_days", lambda m, now: ["2026-09-22"])
    monkeypatch.setattr(s, "order_max", lambda m, days: None)
    monkeypatch.setattr(s, "risks_for", lambda meta, mi, om: [r])
    monkeypatch.setattr(s, "shortlist", False)   # re-run every last 24 h: the fake rows carry no stats
    out = s.backtest(["QQQ-USD"], day_start_us("2026-09-23"), {"QQQ-USD": sc.MarketInfo(0.01, 0.001)},
                     {"QQQ-USD": {}})
    assert ran[0] == {rk: ["touch 0bp"]}                        # only the missing setting was backtested
    day = out["QQQ-USD"][rk]["days"]["2026-09-22"]
    assert sorted(x["config"] for x in day) == sorted(sc.BY_NAME)
    assert json.loads(cp.read_text()) == day                     # and the cache now holds it too


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


def test_a_scan_stops_at_its_time_budget_and_the_next_scan_carries_on(tmp_path: Path, monkeypatch: Any) -> None:
    """2026-09-26: a scan at a new capital ran for hours. Full days now run busiest market first, most recent day
    first, for at most the budget; the rest waits for the next scan (finished days are cached), and a market with no
    finished day yet is `pending`: not ranked, rather than ranked on nothing."""
    import concurrent.futures as cf
    import time as _t

    from bot.scout import scan as sc

    ran: list[tuple[str, int]] = []

    def fake_window(args: tuple[Any, ...]) -> dict[str, list[dict[str, Any]]]:
        ran.append((args[1], args[2]))
        _t.sleep(0.2)
        return {sc.risk_key(r): [{"config": n, "pnl": 0.0} for n in args[5]] for r in args[6]}

    monkeypatch.setattr(sc, "_run_window", fake_window)
    monkeypatch.setattr(sc, "ProcessPoolExecutor",
                        lambda max_workers, initializer: cf.ThreadPoolExecutor(1))   # in-process, one at a time
    r = Risk.for_capital(100, 10).__dict__ | {"min_capital_usd": 0.0}
    mk = ["NVDA-USD", "QQQ-USD", "BTC-USD"]
    flow = {"BTC-USD": 9e7, "QQQ-USD": 1e6, "NVDA-USD": 3e5}

    def scanner(budget_s: float) -> Scanner:
        s = Scanner(tmp_path, capital=100, workers=1, budget_s=budget_s, shortlist=False)
        monkeypatch.setattr(s, "full_days", lambda m, now: ["2026-09-21", "2026-09-22"])
        monkeypatch.setattr(s, "order_max", lambda m, days: None)
        monkeypatch.setattr(s, "risks_for", lambda meta, mi, om: [r])
        monkeypatch.setattr(s, "flow", lambda m, days: flow[m])
        s.backtest(mk, day_start_us("2026-09-23"), {m: sc.MarketInfo(0.01, 0.001) for m in mk}, {m: {} for m in mk})
        return s

    first = scanner(0.05)
    assert ran[0] == ("BTC-USD", day_start_us("2026-09-22"))       # the busiest market, its latest day, first
    assert first.day_jobs >= 1 and first.left_jobs >= 1 and first.day_jobs + first.left_jobs == 6
    assert "BTC-USD" not in first.pending and "NVDA-USD" in first.pending
    assert first.cache_path("BTC-USD", "2026-09-22", sc.risk_key(r)).exists()
    second = scanner(0)                                             # no limit: only what was left, from the cache on
    assert second.day_jobs == first.left_jobs and not second.pending
