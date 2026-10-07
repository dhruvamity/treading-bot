"""The autopilot's decisions with a hand-made scan; no process is started (the pilot's start is replaced)."""

import asyncio
import time

import pytest

from lighter_bot.scout import autopilot, pilot


def scan_with(rows, table=None):
    return {"t": time.time(), "capital": 100, "lists": {"most": rows, "cheapest": [], "max": rows},
            "table": table if table is not None else rows}


def row(market="SPY", setup="Smart +0.5", vol=3e6, cost=0.01, why=()):
    return {"market": market, "setup": setup, "leverage": 50, "capital": 100, "volume_d": vol, "cost_1k": cost,
            "why": list(why)}


@pytest.fixture
def started(monkeypatch):
    calls = []
    monkeypatch.setattr(pilot, "start", lambda cfg, spec, **kw: calls.append((spec, kw)) or 123)
    return calls


def test_off_does_nothing(cfg, started):
    asyncio.run(autopilot.tick(cfg, scan_with([row()])))
    assert not started


def test_starts_the_best_within_the_ceiling_with_the_pot_as_run_stop(cfg, started):
    autopilot.turn_on(cfg, "paper", budget=5)
    asyncio.run(autopilot.tick(cfg, scan_with([row(cost=0.5), row("ETH", "Mid +1", 1e6, 0.02)])))
    spec, _ = started[0]
    assert (spec.market, spec.setup, spec.source, spec.sl) == ("ETH", "Mid +1", "auto", 5.0)


def test_waits_for_a_release(cfg, started):
    autopilot.turn_on(cfg, "paper", budget=5)
    ev = cfg.root / "config" / "calendars"
    ev.mkdir(parents=True, exist_ok=True)
    import datetime as dt
    from zoneinfo import ZoneInfo
    ny = dt.datetime.now(ZoneInfo("America/New_York")) + dt.timedelta(minutes=20)
    (ev / "events.csv").write_text(f"ts_et,kind,source,provisional\n{ny:%Y-%m-%d %H:%M},cpi,test,false\n")
    a = asyncio.run(autopilot.tick(cfg, scan_with([row()])))
    assert not started and "CPI" in a.last


def test_pot_refills_and_caps(cfg):
    a = autopilot.turn_on(cfg, "paper", budget=5)
    a.pot, a.day = 34.0, "2000-01-01"
    a.save(cfg)
    a = asyncio.run(autopilot.tick(cfg, None))
    assert a.pot == 35.0                                   # 7 days of budget at most


def test_earnings_blackout(cfg, started):
    autopilot.turn_on(cfg, "paper", budget=5)
    p = cfg.state_dir / "calendars"
    p.mkdir(parents=True, exist_ok=True)
    (p / "earnings.csv").write_text(f"symbol,t,day,session\nNVDA,{time.time() + 3600},x,y\n")
    asyncio.run(autopilot.tick(cfg, scan_with([row("NVDA", "Smart +1")])))
    assert not started
