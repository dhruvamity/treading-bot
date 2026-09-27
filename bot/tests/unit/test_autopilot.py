"""The autopilot and what it stands on: sessions, regime, playbook, earnings calendar, /auto (2026-09-27)."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import math
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from bot.core import earnings
from bot.core.calendar import TradingCalendar
from bot.scout import autopilot as ap
from bot.scout import playbook as pbk
from bot.scout import regime as rg
from bot.scout import sessions
from bot.scout.tape import DayTape

UTC = dt.UTC


def at(s: str) -> float:
    return dt.datetime.fromisoformat(s).replace(tzinfo=UTC).timestamp()


# ------------------------------------------------------------------------------------------------ sessions
@pytest.mark.parametrize("when,want", [
    ("2026-09-28 00:30", "asia"), ("2026-09-28 06:59", "asia"), ("2026-09-28 07:00", "london"),   # BST: London 08:00
    ("2026-09-28 13:29", "london"), ("2026-09-28 13:30", "us_open"), ("2026-09-28 16:00", "us_pm"),
    ("2026-09-28 20:00", "late"), ("2026-09-28 23:59", "late"), ("2026-09-29 00:00", "asia"),
    ("2026-10-02 20:59", "late"), ("2026-10-02 21:00", "weekend"), ("2026-10-04 21:59", "weekend"),
    ("2026-10-04 22:00", "late"),                                              # US futures reopen: the week starts
    ("2026-12-01 07:30", "asia"), ("2026-12-01 08:00", "london"),             # winter: London 08:00 = 08:00 UTC
    ("2026-12-01 14:00", "london"), ("2026-12-01 14:30", "us_open"),          # New York 09:30 EST
])
def test_sessions_follow_each_citys_clock(when: str, want: str) -> None:
    assert sessions.session_of(at(when)) == want


def test_session_spans_cover_a_day_in_order() -> None:
    sp = sessions.spans(at("2026-09-28 00:00"), 24)
    assert [s for _, _, s in sp] == ["asia", "london", "us_open", "us_pm", "late"]
    assert sp[0][0] == at("2026-09-28 00:00") and sp[-1][1] == at("2026-09-29 00:00")
    assert sessions.ist(at("2026-09-28 13:30")) == "Mon 19:00"


# ------------------------------------------------------------------------------------------------ regime
def test_regime_buckets_and_shock() -> None:
    assert [rg.bucket(x) for x in (0.5, 1.0, 1.5, 3.0, math.nan)] == ["calm", "normal", "busy", "wild", "normal"]
    rng = np.random.default_rng(1)
    calm = 100 * np.exp(np.cumsum(rng.normal(0, 1e-4, 91)))            # ~1 bp a minute
    r = rg.of("BTC-USD", calm, 0, usual_rv=16.0)                        # usual: ~2 bp a minute
    assert r.bucket == "calm" and not r.shock
    jumpy = calm.copy()
    jumpy[-3:] *= 1.004                                                   # a 40 bp minute in the last 15
    r = rg.of("BTC-USD", jumpy, 0, usual_rv=16.0)
    assert r.shock and r.jump_bps > 35 and "shock" in r.text


def test_minute_mids_use_the_book_then_trades() -> None:
    m = 60_000_000
    bbo = {"ts": np.array([10, m + 10], np.int64), "bid": np.array([99.0, 100.0]), "ask": np.array([101.0, 102.0])}
    trades = {"ts": np.array([2 * m + 5], np.int64), "px": np.array([50.0])}
    mids = rg.minute_mids(bbo, trades, 0, 3 * m)
    assert mids.tolist() == [100.0, 101.0, 101.0]                       # the book wins where it has a price
    mids = rg.minute_mids({"ts": np.zeros(0, np.int64), "bid": np.zeros(0), "ask": np.zeros(0)}, trades, 0, 3 * m)
    assert np.isnan(mids[0]) and mids[-1] == 50.0


# ------------------------------------------------------------------------------------------------ playbook
def test_synthetic_book_is_one_tick_around_the_last_print() -> None:
    tr = {"ts": np.array([5, 5, 9], np.int64), "px": np.array([100.0, 100.1, 99.9]), "sz": np.ones(3),
          "buy": np.array([True, True, False]), "seq": np.arange(3), "tid": np.arange(3)}
    t = pbk.synth_book(DayTape("BTC-USD", "d", {}, tr), 0.1, 2.0)
    assert t.bbo["ts"].tolist() == [6, 10]                               # after the last print of each moment
    assert t.bbo["bid"].tolist() == pytest.approx([100.0, 99.9]) and t.bbo["ask"].tolist() == pytest.approx([100.1, 100.0])


def _rows(cost_by_session: dict[str, float], vol: float = 10_000.0, days: int = 1,
          order: tuple[str, ...] = ("asia",) * 7 + ("london",) * 6 + ("us_open",) * 3 + ("us_pm",) * 4 + ("late",) * 4
          ) -> list[list[Any]]:
    """Hourly rows [h, setup, vol, pnl, session, regime, ratio] with a set cost per session."""
    out = []
    for _ in range(days):
        for h, sess in enumerate(order):
            c = cost_by_session.get(sess, 2.0)
            out.append([h, "Mid 0", vol, -vol * c / 1e4, sess, "normal", 1.0])
    return out


def test_estimates_shrink_thin_cells_toward_their_parents() -> None:
    cells = pbk.aggregate([*_rows({"asia": 1.0}, days=5), [0, "Mid 0", 10_000.0, -5.0, "asia", "wild", 3.0]])["Mid 0"]
    v, c, n = pbk.estimate(cells, "asia", "wild")         # one wild hour at 5 bp against 35 asia hours at 1 bp
    assert n == 1 and 1.0 < c < 2.0 and v == pytest.approx(10_000.0)
    v, c, n = pbk.estimate(cells, "asia", "calm")         # no calm hours: the session's own estimate
    assert n == 36 and 1.0 < c < 1.4                     # Asia's 1.1 bp, pulled a little toward all hours' 2 bp


def test_the_tuner_picks_the_ceiling_that_buys_the_most() -> None:
    # London (hours 0-11 here) trades as much as Asia (12-23) but costs 2.5x more
    rows = _rows({"asia": 1.0, "london": 2.5}, order=("london",) * 12 + ("asia",) * 12)
    pb = {"markets": {"BTC-USD": {"setups": pbk.aggregate(rows * 10)}}}
    by_day = {"BTC-USD": {f"2026-09-{d:02d}": rows for d in range(1, 11)}}
    hours = pbk.hours_of(pb, by_day, sorted(by_day["BTC-USD"]))
    small = {c: pbk.replay(hours, c, 3.0) for c in (1.2, 3.0)}
    assert small[1.2][0] > 2 * small[3.0][0]               # $3 a day: waiting for Asia buys 2.5x the volume
    big = {c: pbk.replay(hours, c, 100.0) for c in (1.2, 3.0)}
    assert big[3.0][0] > big[1.2][0]                       # $100 a day: Asia alone cannot spend it
    tuned = pbk.tune(pb, by_day)
    assert tuned["3"] < tuned["100"] and pbk.ceiling_for({"ceilings": tuned}, 4.0) == tuned["3"]


def test_week_plan_names_the_fastest_setup_within_the_ceiling() -> None:
    rows = _rows({"asia": 1.0})
    pb = {"markets": {"BTC-USD": {"setups": pbk.aggregate(rows)}}}
    plan = pbk.week_plan(pb, 1.5, at("2026-09-28 00:00"), 24)
    picks = {p["session"]: p["pick"] for p in plan}
    assert picks["asia"]["setup"] == "Mid 0" and picks["london"] is None
    lines = ap.plan_lines(pb, 1.5, at("2026-09-28 00:00"))
    assert lines[0].startswith("Mon 05:30 IST Asia: BTC Mid 0") and "wait" in lines[1]


# ------------------------------------------------------------------------------------------------ rules
def test_the_pot_refills_daily_and_carries_up_to_a_week() -> None:
    s = ap.Settings(on=True, budget_day=5.0)
    st: dict[str, Any] = {"pot": 1.0, "refilled": "2026-09-27", "days": {"2026-09-27": {"volume": 1e4, "pnl": -4.0,
                                                                                      "runs": 2}}}
    assert ap.refill(st, s, at("2026-09-27 23:00")) is None                # same day: nothing
    summary = ap.refill(st, s, at("2026-09-28 00:01"))
    assert st["pot"] == 6.0 and summary and summary["day"] == "2026-09-27" and summary["pnl"] == -4.0
    st["refilled"] = "2026-09-10"
    ap.refill(st, s, at("2026-09-28 00:01"))
    assert st["pot"] == ap.POT_DAYS * 5.0                                  # never more than a week of budget


def test_events_block_every_market_and_earnings_only_the_stock(tmp_path: Path) -> None:
    (tmp_path / "events.csv").write_text("ts_et,kind,source,provisional\n2026-10-14 08:30,cpi,x,false\n")
    auto = tmp_path / "auto"
    auto.mkdir()
    (auto / "earnings.csv").write_text("symbol,date,session,source\nNVDA,2026-11-18,amc,nasdaq\n")
    cal = TradingCalendar.load(tmp_path, auto=auto)
    cpi = at("2026-10-14 12:30")                                            # 08:30 New York
    assert ap.event_block(cal, "BTC-USD", int((cpi - 3600) * 1e6)) is None   # 60 min before: fine
    assert ap.event_block(cal, "BTC-USD", int((cpi - 46 * 60) * 1e6)) is None
    assert "CPI" in (ap.event_block(cal, "BTC-USD", int((cpi - 40 * 60) * 1e6)) or "")   # 40 min before: flat
    assert "CPI" in (ap.event_block(cal, "SPY-USD", int((cpi + 20 * 60) * 1e6)) or "")
    nv = at("2026-11-18 21:00")                                              # 16:00 New York
    assert "NVDA earnings" in (ap.event_block(cal, "NVDA-USD", int((nv + 20 * 3600) * 1e6)) or "")
    assert ap.event_block(cal, "BTC-USD", int(nv * 1e6)) is None


def _opt(m: str, setup: str, vol: float, cost: float) -> dict[str, Any]:
    return {"market": m, "setup": setup, "vol_h": vol, "cost_bp": cost, "hours": 20}


def test_decide_starts_keeps_switches_and_stops() -> None:
    btc, spy = _opt("BTC-USD", "Mid 0", 150_000, 1.2), _opt("SPY-USD", "Mid +3", 12_000, 0.7)
    assert ap.choose([btc, spy, _opt("QQQ-USD", "Mid 0", 1_000, 0.1)], 1.5) == btc
    assert ap.choose([btc, spy], 1.0) == spy
    now = 10_000.0
    d = ap.decide(running=None, now=now, pick=btc, current=None, blocked_running=None, pot_left=5, max_cost_bp=1.5)
    assert d.action == "start" and d.target == btc
    assert ap.decide(running=None, now=now, pick=btc, current=None, blocked_running=None, pot_left=5,
                     max_cost_bp=1.5, last_stop=now - 60).action == "idle"               # resting after a stop
    assert ap.decide(running=None, now=now, pick=btc, current=None, blocked_running=None, pot_left=0.2,
                     max_cost_bp=1.5).reason.startswith("budget used")
    run = {"market": "SPY-USD", "setup": "Mid +3", "since": now - 60}
    assert ap.decide(running=run, now=now, pick=btc, current=spy, blocked_running=None, pot_left=5,
                     max_cost_bp=1.5).action == "keep"                                  # held under 20 min
    run["since"] = now - 3600
    d = ap.decide(running=run, now=now, pick=btc, current=spy, blocked_running=None, pot_left=5, max_cost_bp=1.5)
    assert d.action == "switch" and d.target == btc                                     # 12x the volume
    d = ap.decide(running=run, now=now, pick=None, current=_opt("SPY-USD", "Mid +3", 12_000, 1.9),
                  blocked_running=None, pot_left=5, max_cost_bp=1.5)
    assert d.action == "stop" and "1.90 bp" in d.reason
    assert ap.decide(running=run, now=now, pick=None, current=_opt("SPY-USD", "Mid +3", 12_000, 1.6),
                     blocked_running=None, pot_left=5, max_cost_bp=1.5).action == "keep"   # within the hysteresis
    d = ap.decide(running=run, now=now, pick=btc, current=spy, blocked_running="CPI Wed 12:30 UTC", pot_left=5,
                  max_cost_bp=1.5)
    assert d.action == "stop" and "CPI" in d.reason


def test_configure_checks_and_the_owner_takes_over(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="budget"):
        ap.configure(tmp_path, budget_day=0.1)
    with pytest.raises(ValueError, match="ceiling"):
        ap.configure(tmp_path, max_cost_bp=9)
    s = ap.configure(tmp_path, on=True, budget_day=4.0)
    st = ap.load(tmp_path)
    assert s.on and st["pot"] == 0.0 and st["refilled"] is None         # the first tick adds the day's money
    assert ap.turn_off(tmp_path, "your /stop (owner)") and not ap.is_on(tmp_path)
    assert ap.load(tmp_path)["off_why"] == "your /stop (owner)" and not ap.turn_off(tmp_path, "again")


# ------------------------------------------------------------------------------------------------ the loop
class _Ctl:
    def __init__(self, tmp: Path) -> None:
        self.app = SimpleNamespace(state_dir="state")
        self.kv: dict[str, str] = {}
        self.up = False

    def alive(self, mode: str) -> bool:
        return self.up

    def _kv_get(self, mode: str, k: str) -> str | None:
        return self.kv.get(k)

    def view(self, mode: str) -> Any:
        return SimpleNamespace(snapshot={"risk": {"all_stopped": "this run lost $2.00, its limit is $2.00"}})


class _Pilot:
    def __init__(self, root: Path) -> None:
        self.root, self.control = root, _Ctl(root)
        self.events: list[tuple[str, str]] = []
        self.deployed: list[dict[str, Any]] = []
        self.closed = 0
        self._active: dict[str, Any] | None = None

    def event(self, kind: str, text: str, **k: Any) -> dict[str, Any]:
        self.events.append((kind, text))
        return {"kind": kind, "text": text}

    def latest_scan(self) -> dict[str, Any]:
        return {}

    def find(self, market: str, setup: str, lev: str) -> dict[str, Any]:
        return {"market": market, "setting": setup, "leverage": 40.0}

    def active(self) -> dict[str, Any] | None:
        return self._active

    async def deploy(self, c: dict[str, Any], *, live: bool, by: str) -> str:
        self.deployed.append({**c, "live": live, "by": by})
        self._active = {"run_id": f"r{len(self.deployed)}", "by": by}
        self.control.up = True
        return "ok"

    async def close(self, *, by: str) -> str:
        self.closed += 1
        self.control.up = False
        self._active = None
        return "closed"


def _loop(tmp_path: Path, monkeypatch: Any, clock: list[float], regime: str = "calm",
          events_csv: str = "") -> tuple[ap.Autopilot, _Pilot]:
    (tmp_path / "state").mkdir(exist_ok=True)
    pilot = _Pilot(tmp_path)
    pb = {"markets": {"BTC-USD": {"leverage": 40, "setups": pbk.aggregate(
        [[h, "Mid 0", 100_000.0, -100_000.0 * c / 1e4, s, r, 1.0] for h in range(24)
         for s in sessions.SESSIONS for r, c in (("calm", 1.0), ("wild", 3.0))] * 3)}}}
    monkeypatch.setattr(pbk, "load", lambda root: pb)
    reg = {"r": regime}

    def fake_now(store: Any, market: str, now_us: int, u: Any, recent: Any = None) -> rg.Regime:
        ratio = 0.5 if reg["r"] == "calm" else 3.0
        return rg.Regime(market, ratio, reg["r"], 10.0, 20.0, 0.0, 1.0, False, 5.0)
    monkeypatch.setattr(rg, "now", fake_now)
    monkeypatch.setattr(rg, "usual", lambda store, m, now_us: rg.Usual({}, "d"))
    cal = TradingCalendar()
    if events_csv:
        (tmp_path / "cal").mkdir()
        (tmp_path / "cal" / "events.csv").write_text("ts_et,kind,source,provisional\n" + events_csv)
        cal = TradingCalendar.load(tmp_path / "cal")
    a = ap.Autopilot(tmp_path, pilot, calendar=cal, clock=lambda: clock[0])
    a.regime = reg  # type: ignore[attr-defined]
    return a, pilot


async def test_the_loop_starts_when_calm_books_the_run_and_stops_when_wild(tmp_path: Path, monkeypatch: Any) -> None:
    clock = [at("2026-10-03 10:00")]                                       # a Saturday
    a, pilot = _loop(tmp_path, monkeypatch, clock)
    state = tmp_path / "state"
    assert await a.tick() is None                                          # off: nothing
    ap.configure(state, on=True, budget_day=5.0)
    d = await a.tick()
    assert d and d.action == "start" and pilot.deployed[-1]["market"] == "BTC-USD"
    assert pilot.deployed[-1]["max_loss_usd"] == 5.0 and pilot.deployed[-1]["live"] is False   # run stop = the pot
    assert "AUTOPILOT STARTED · PAPER" in pilot.events[-1][1] and "Weekend · calm" in pilot.events[-1][1]
    clock[0] += 60
    pilot.control.kv.update({"run_pnl:r1": "-1.50", "run_vol:r1": "20000"})
    d = await a.tick()
    assert d and d.action == "keep" and ap.load(state)["run"]["pnl"] == -1.5
    a.regime["r"] = "wild"  # type: ignore[attr-defined]
    clock[0] += 60
    d = await a.tick()
    assert d and d.action == "stop" and pilot.closed == 1
    st = ap.load(state)
    assert st["run"] is None and st["pot"] == pytest.approx(3.5)            # $5 - $1.50
    assert st["days"]["2026-10-03"] == {"volume": 20000.0, "pnl": -1.5, "runs": 1}
    assert "AUTOPILOT STOPPED" in pilot.events[-1][1] and "Mid 0 now 2.9" in pilot.events[-1][1]   # the wild cell
    a.regime["r"] = "calm"  # type: ignore[attr-defined]
    clock[0] += 60
    d = await a.tick()
    assert d and d.action == "idle" and "resting" in d.reason               # 10 minutes after a stop


async def test_the_loop_books_a_run_that_ended_on_its_stop(tmp_path: Path, monkeypatch: Any) -> None:
    clock = [at("2026-10-03 10:00")]
    a, pilot = _loop(tmp_path, monkeypatch, clock)
    ap.configure(tmp_path / "state", on=True, budget_day=2.0)
    await a.tick()
    pilot.control.kv.update({"run_pnl:r1": "-2.00", "run_vol:r1": "30000"})
    pilot.control.up = False                                                # the engine stopped it at its limit
    clock[0] += ap.START_GRACE_S + 1
    d = await a.tick()
    st = ap.load(tmp_path / "state")
    assert st["pot"] == 0.0 and d and d.reason.startswith("budget used")
    assert any("RUN ENDED" in t and "this run lost $2.00" in t for _k, t in pilot.events)
    clock[0] = at("2026-10-04 00:00") + 5                                   # the next day's money
    await a.tick()
    assert ap.load(tmp_path / "state")["pot"] == 2.0 and len(pilot.deployed) == 2
    assert any("AUTOPILOT · 2026-10-03" in t for _k, t in pilot.events)


async def test_the_loop_goes_flat_before_cpi(tmp_path: Path, monkeypatch: Any) -> None:
    clock = [at("2026-10-14 11:30")]                                        # 60 min before CPI (12:30 UTC)
    a, pilot = _loop(tmp_path, monkeypatch, clock, events_csv="2026-10-14 08:30,cpi,x,false\n")
    ap.configure(tmp_path / "state", on=True)
    assert (await a.tick()).action == "start"  # type: ignore[union-attr]
    clock[0] = at("2026-10-14 11:50")
    d = await a.tick()
    assert d and d.action == "stop" and "CPI" in d.reason and pilot.closed == 1
    clock[0] = at("2026-10-14 13:10")                                       # after the window: back to work
    assert (await a.tick()).action == "start"  # type: ignore[union-attr]


async def test_a_run_the_owner_starts_elsewhere_turns_it_off(tmp_path: Path, monkeypatch: Any) -> None:
    clock = [at("2026-10-03 10:00")]
    a, pilot = _loop(tmp_path, monkeypatch, clock)
    ap.configure(tmp_path / "state", on=True)
    pilot._active = {"market": "SPY-USD", "by": "cli", "mode": "paper", "since": 4e9}   # `bot pilot approve`
    pilot.control.up = True
    assert await a.tick() is None
    assert not ap.is_on(tmp_path / "state") and "You started SPY-USD yourself" in pilot.events[-1][1]
    assert not pilot.deployed and pilot.closed == 0                     # the owner's run is left alone


async def test_live_needs_the_switch_and_a_passing_doctor(tmp_path: Path, monkeypatch: Any) -> None:
    clock = [at("2026-10-03 10:00")]
    a, pilot = _loop(tmp_path, monkeypatch, clock)
    ap.configure(tmp_path / "state", on=True, mode="live")
    monkeypatch.delenv("BOT_PILOT_LIVE", raising=False)
    await a.tick()
    assert not ap.is_on(tmp_path / "state") and "LIVE is off" in pilot.events[-1][1] and not pilot.deployed
    monkeypatch.setenv("BOT_PILOT_LIVE", "1")
    ap.configure(tmp_path / "state", on=True, mode="live")

    async def doctor(c: dict[str, Any]) -> tuple[bool, str]:
        return False, "FAIL margin: free margin too low"
    a._doctor = doctor  # type: ignore[method-assign]
    await a.tick()
    assert not pilot.deployed and pilot.events[-1][0] == "auto_alert" and "free margin" in pilot.events[-1][1]


# ------------------------------------------------------------------------------------------------ earnings
def test_earnings_rows_merge_and_calendar_hours(tmp_path: Path) -> None:
    payload = {"data": {"rows": [{"symbol": "NVDA", "time": "time-after-hours"}, {"symbol": "IBM", "time": "x"},
                                 {"symbol": "AMD", "time": "time-pre-market"}, {"symbol": "TSLA",
                                                                                "time": "time-not-supplied"}]}}
    rows = earnings.rows_of(payload, "2026-10-21", {"NVDA", "AMD", "TSLA"})
    assert [(r["symbol"], r["session"]) for r in rows] == [("NVDA", "amc"), ("AMD", "bmo"), ("TSLA", "")]
    old = [{"symbol": "NVDA", "date": "2026-08-26", "session": "", "source": "x"},
           {"symbol": "NVDA", "date": "2026-10-20", "session": "", "source": "x"},     # moved: replaced
           {"symbol": "MU", "date": "2026-01-01", "session": "", "source": "x"}]       # too old: dropped
    out = earnings.merge(old, rows, dt.date(2026, 10, 1))
    assert [(r["symbol"], r["date"]) for r in out] == [("NVDA", "2026-08-26"), ("AMD", "2026-10-21"),
                                                       ("NVDA", "2026-10-21"), ("TSLA", "2026-10-21")]
    p = earnings.auto_path(tmp_path)
    earnings.write(p, out)
    cal = TradingCalendar.for_app(tmp_path, root=tmp_path / "none")
    ev = {e.symbol: dt.datetime.fromtimestamp(e.ts_us / 1e6, UTC).strftime("%H:%M") for e in cal.events
          if e.ts_us > at("2026-10-01 00:00") * 1e6}
    assert ev == {"AMD": "13:30", "NVDA": "20:00", "TSLA": "20:00"}          # bmo 09:30, amc/unknown 16:00 New York
    p.write_text("symbol,date,session,source\n")
    cal.reload()
    assert not [e for e in cal.events if e.kind == "earnings"]


async def test_earnings_refresh_keeps_the_old_file_when_nasdaq_is_down(tmp_path: Path, monkeypatch: Any) -> None:
    mj = tmp_path / "markets.json"
    mj.write_text(json.dumps({"markets": [{"marketDisplayName": "NVDA-USD", "category": "EQUITIES",
                                           "status": "ONLINE"}, {"marketDisplayName": "BTC-USD",
                                                                 "category": "CRYPTO", "status": "ONLINE"}]}))
    assert earnings.stock_symbols(mj) == {"NVDA"}

    class Resp:
        def __init__(self, body: Any) -> None:
            self.body = body

        async def __aenter__(self) -> Resp:
            return self

        async def __aexit__(self, *a: Any) -> None:
            return None

        def raise_for_status(self) -> None:
            if self.body is None:
                import aiohttp
                raise aiohttp.ClientError("down")

        async def json(self, content_type: Any = None) -> Any:
            return self.body

    class Sess:
        def __init__(self, body: Any) -> None:
            self.body, self.urls = body, []

        def get(self, url: str) -> Resp:
            self.urls.append(url)
            return Resp(self.body)

    earnings.write(earnings.auto_path(tmp_path), [{"symbol": "NVDA", "date": "2026-10-20", "session": "",
                                                   "source": "x"}])
    monkeypatch.setattr(earnings, "PAUSE_S", 0.0)
    res = await earnings.refresh(tmp_path, mj, today=dt.date(2026, 10, 12), session=Sess(None))  # type: ignore[arg-type]
    assert not res["ok"] and earnings.read(earnings.auto_path(tmp_path))[0]["date"] == "2026-10-20"
    body = {"data": {"rows": [{"symbol": "NVDA", "time": "time-after-hours"}]}}
    s = Sess(body)
    res = await earnings.refresh(tmp_path, mj, today=dt.date(2026, 10, 12), session=s)  # type: ignore[arg-type]
    assert res["ok"] and len(s.urls) == 15                                   # weekdays of the next 21 days
    assert {r["date"] for r in earnings.read(earnings.auto_path(tmp_path))} >= {"2026-10-12", "2026-10-30"}


# ------------------------------------------------------------------------------------------------ recorder
def test_the_recorder_keeps_recent_minutes_in_memory(tmp_path: Path) -> None:
    from bot.scout.record import ScoutRecorder

    r = ScoutRecorder(tmp_path, rest_url="http://x", ws_url="ws://x")
    r.display["BTC"] = "BTC-USD"
    r.bbo["BTC-USD"] = __import__("bot.scout.tape", fromlist=["BboBuffer"]).BboBuffer()
    m = 60_000_000
    for ts, bid in ((5 * m + 1, 100.0), (5 * m + 30_000_000, 101.0), (6 * m + 2, 102.0)):
        r._on_bbo("BTC", {"timestamp": ts, "bestBid": {"price": bid}, "bestAsk": {"price": bid + 2}}, 0)
    assert r.recent("BTC-USD") == [(6 * m, 102.0), (7 * m, 103.0)]           # the last mid of each minute


# ------------------------------------------------------------------------------------------------ Telegram and pilot
async def test_telegram_auto_on_off_and_takeover(tmp_path: Path, monkeypatch: Any) -> None:
    from tests.unit.test_telegram import _buttons, _cand, _with_pilot, msg, press

    bot, api, pilot, _calls = _with_pilot(tmp_path, [_cand("BTC-USD", "Mid 0", lev=40)])
    await bot.handle(msg("/auto"))
    assert "🤖 <b>AUTOPILOT</b>" in api.sent[-1][1] and "OFF · PAPER" in api.sent[-1][1]
    assert "No playbook yet" in api.sent[-1][1] and "auto on live" in _buttons(api)
    await bot.handle(msg("/auto on paper budget=3"))
    assert "Budget $3.00/day" not in api.texts() and "budget $3.00/day" in api.sent[-1][1]
    await bot.handle(press(f"ok {next(iter(bot.pending))}"))
    st = ap.load(tmp_path / "state")
    assert st["settings"]["on"] and st["settings"]["budget_day"] == 3.0 and st["settings"]["mode"] == "paper"
    assert "AUTOPILOT ON" in api.sent[-1][1] and "auto off" in _buttons(api)
    await bot.handle(msg("/auto cost 1.7"))
    assert ap.settings_of(ap.load(tmp_path / "state")).max_cost_bp == 1.7 and "1.70 bp" in api.sent[-1][1]
    await bot.handle(msg("/auto budget 0.1"))
    assert "NOT SAVED" in api.sent[-1][1]
    monkeypatch.delenv("BOT_PILOT_LIVE", raising=False)
    await bot.handle(msg("/auto on live"))
    assert "LIVE IS OFF" in api.sent[-1][1]
    ctl = pilot.control                                                   # the owner's /stop takes over
    monkeypatch.setattr(ctl, "is_running", lambda mode: True)
    monkeypatch.setattr(ctl, "request_stop", lambda mode, by: None)
    monkeypatch.setattr(bot, "_ensure_stopped", lambda ctx, mode: asyncio.sleep(0))
    await bot.handle(msg("/stop paper"))
    await bot.handle(press(f"ok {next(iter(bot.pending))}"))
    assert not ap.is_on(tmp_path / "state") and "You took over with /stop" in api.texts()


def test_the_pilot_leaves_autopilot_runs_alone(tmp_path: Path) -> None:
    from bot.scout.pilot import Pilot
    from tests.unit.test_telegram import _app, _bot, _cand, _scan

    app = _app(tmp_path)
    _b, _api, ctl = _bot(tmp_path, app)
    pilot = Pilot(tmp_path, ctl)
    ap.configure(tmp_path / "state", on=True)
    assert pilot.review(_scan([_cand("BTC-USD", "Mid 0")])) == []           # no offer while the autopilot runs
    assert pilot.state()["last_review"]["profile"] == "auto"


async def test_the_scout_service_runs_the_autopilot_earnings_and_playbook(tmp_path: Path, monkeypatch: Any) -> None:
    """One pass of `bot scout run` with the network and the backtests stubbed: the autopilot's loop starts and stops
    with the service, and the daily earnings fetch and playbook build run."""
    import os
    import signal

    from bot.scout import service as svc
    from bot.scout.pilot import Pilot
    from tests.unit.test_telegram import _app, _bot

    app = _app(tmp_path)
    _b, _api, ctl = _bot(tmp_path, app)
    pilot = Pilot(tmp_path, ctl)
    calls: list[str] = []

    def fake_scan(root: Path, **kw: Any) -> dict[str, Any]:
        calls.append("scan")
        return {"ts_us": 0, "took_s": 0.1, "day_jobs": 0, "top": [], "all": []}

    async def fake_refresh(state_dir: Any, markets_json: Any, **kw: Any) -> dict[str, Any]:
        calls.append("earnings")
        return {"ok": True}

    def fake_build(self: Any, now_us: Any = None, **kw: Any) -> dict[str, Any]:
        calls.append("playbook")
        return {"markets": {}, "built_days": 0, "left_days": 0}

    async def no_snapshot(*a: Any, **k: Any) -> None:
        return None

    ticks: list[float] = []
    real_tick = ap.Autopilot.tick

    async def tick(self: Any) -> Any:
        ticks.append(1.0)
        return await real_tick(self)

    monkeypatch.setattr(svc, "FIRST_SCAN_S", 0.0)
    monkeypatch.setattr(svc, "scan", fake_scan)
    monkeypatch.setattr(svc, "save_scan", lambda root, res: calls.append("saved"))
    monkeypatch.setattr(svc, "account_snapshot", no_snapshot)
    monkeypatch.setattr(svc.earnings, "refresh", fake_refresh)
    monkeypatch.setattr(svc.pbk.Playbook, "build", fake_build)
    monkeypatch.setattr(ap.Autopilot, "tick", tick)
    real_review = pilot.review

    def review(res: dict[str, Any]) -> Any:
        out = real_review(res)
        asyncio.get_running_loop().call_later(0.2, os.kill, os.getpid(), signal.SIGTERM)   # `bot down`
        return out
    pilot.review = review  # type: ignore[method-assign]
    await asyncio.wait_for(svc.run_service(tmp_path, pilot, rest_url="http://x", ws_url="ws://x", record=False,
                                           capital=100.0), 20)
    assert calls[:4] == ["earnings", "scan", "saved", "playbook"] and ticks
