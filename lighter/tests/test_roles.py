"""Two machines (BOT_ROLE): what the Lighter scout does in each role, a trader's follow review, and the guards."""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from lighter_bot.config import load
from lighter_bot.scout import pilot, service


def _role(root, role: str):
    c = load(root, env={"BOT_ROLE": role})
    c.ensure_dirs()
    return c


def test_the_role_is_read_once_for_the_three_bots_and_a_typing_mistake_is_refused(root):
    assert load(root, env={}).role == "all" and load(root, env={"BOT_ROLE": " Trader "}).role == "trader"
    with pytest.raises(ValueError, match="must be one of all, trader, recorder, scout"):
        load(root, env={"BOT_ROLE": "recoder"})
    jobs = {r: (c.records, c.ranks, c.trades) for r in ("all", "scout", "recorder", "trader")
            for c in [load(root, env={"BOT_ROLE": r})]}
    assert jobs == {"all": (True, True, True), "scout": (True, True, False), "recorder": (True, False, False),
                    "trader": (False, False, True)}
    assert load(root, env={}).no_trading() == "" and "recorder" in _role(root, "recorder").no_trading()


def test_a_machine_that_does_not_trade_starts_no_run(root):
    from lighter_bot.trade.engine import RunSpec

    for r in ("recorder", "scout"):
        with pytest.raises(ValueError, match=f"this machine is a {r}"):
            pilot.start(_role(root, r), RunSpec(market="SPY", setup="Smart +1", leverage=50.0, mode="paper"))
    assert not (root / "state" / "pilot_events.jsonl").exists()


def _scan(cfg, t: float) -> None:
    (cfg.data_dir / "scout").mkdir(parents=True, exist_ok=True)
    (cfg.data_dir / "scout" / "latest.json").write_text(json.dumps({"t": t, "lists": {"most": []}, "table": []}))


def test_a_trader_reviews_each_new_scan_once_and_never_an_old_one_at_start(root, monkeypatch):
    cfg = _role(root, "trader")
    seen: list[float] = []
    monkeypatch.setattr(pilot, "review", lambda c, scan: seen.append(scan["t"]))
    monkeypatch.setattr(pilot, "offer", lambda c, scan: None)
    now = time.time()
    memory: dict = {}
    assert service.follow_scan(cfg, memory, now) is False                 # nothing has arrived yet
    _scan(cfg, now - 5 * 3600)                                            # there when the service starts: hours old
    assert service.follow_scan(cfg, memory, now) is False and seen == []
    _scan(cfg, now + 60)                                                  # a new one arrives
    assert service.follow_scan(cfg, memory, now + 120) is True
    assert service.follow_scan(cfg, memory, now + 240) is False and seen == [now + 60]


def test_a_scan_on_a_machine_that_does_not_trade_reviews_no_run(root, monkeypatch):
    cfg = _role(root, "scout")
    called: list[str] = []

    class FakeScanner:
        def __init__(self, c):
            pass

        def scan(self, capital, **kw):
            return {"t": 1.0, "lists": {}, "table": []}

    monkeypatch.setattr(service, "Scanner", FakeScanner)
    monkeypatch.setattr(pilot, "review", lambda c, s: called.append("review"))
    monkeypatch.setattr(pilot, "offer", lambda c, s: called.append("offer"))
    service.scan_once(cfg, 100.0, supervise=False)
    assert called == []
    service.scan_once(cfg, 100.0)
    assert called == ["review", "offer"]


def test_a_trader_follows_without_recording_or_scanning(root, monkeypatch):
    cfg = _role(root, "trader")
    did: list[str] = []

    def no_scan(*a, **k):
        raise AssertionError("a machine that does not rank must never scan")

    async def markets(c):
        did.append("markets")
        return {}

    async def tick(c, scan, now=None):
        did.append(f"autopilot scan={'yes' if scan else 'no'}")

    async def earnings(c, syms):
        return 0

    monkeypatch.setattr(service, "scan_once", no_scan)
    monkeypatch.setattr("lighter_bot.trade.runner.fetch_markets", markets)
    monkeypatch.setattr(service.autopilot, "tick", tick)
    monkeypatch.setattr(service.calendar, "fetch_earnings", earnings)
    _scan(cfg, time.time() - 10 * 3600)                                   # lists that stopped arriving
    (cfg.state_dir / "scan_now").write_text("1")
    asyncio.run(service.run(cfg, record=False, rank=False, supervise=True, seconds=0.5))
    assert did[:2] == ["markets", "autopilot scan=no"]                    # old lists are not lists to start from
    assert not (cfg.state_dir / "scan_now").exists()                      # asked for a scan: nothing scans here

    did.clear()
    asyncio.run(service.run(cfg, record=False, rank=False, supervise=False, seconds=0.3))
    assert did == []                                                      # no jobs: no autopilot, no market list
