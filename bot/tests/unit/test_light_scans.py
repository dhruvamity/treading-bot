"""Light scans next to a trading bot (the last 24 h re-run only for settings that pass on their full days, low CPU
priority, one worker while a bot runs, the capital held until it moves 25%), the balance history, and the settings
the owner changes from Telegram."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from bot.common import settings
from bot.common.config import SizingDefaults, load_session
from bot.core.balances import BalanceLog, pnl
from bot.scout.capital import STATE, forget, settle
from bot.scout.scan import Pct, long_reasons, passes_long, risk_key
from bot.scout.service import SCAN_NOW, scan_workers, take_trigger
from bot.scout.sim import Risk
from tests.unit.test_sizing import _engine, _pilot_session
from tests.unit.test_telegram import OWNER, _app, _bot, _running_paper, msg, press

ROOT = Path(__file__).parents[2]
DAY = {"config": "deep 3bp", "pnl": 0.5, "maker_fills": 50, "maker_usd": 10_000, "day_stops": 0, "killed": False,
       "taker_usd": 0, "actions": 100}


# ------------------------------------------------------------------------------------------------ the shortlist
def test_only_settings_that_pass_on_their_full_days_are_rechecked() -> None:
    r = Risk.for_capital(100, 10).__dict__
    days = ("2026-09-20", "2026-09-21", "2026-09-22")
    good = {"risk": r, "days": {d: [DAY] for d in days}}
    bad = {"risk": r, "days": {d: [DAY | {"pnl": -3.0}] for d in days}}
    assert passes_long(good, "deep 3bp", Pct()) and not passes_long(bad, "deep 3bp", Pct())
    assert not passes_long({"risk": r, "days": {}}, "deep 3bp", Pct())                    # no full day yet
    assert not passes_long({**good, "skip": "needs $8"}, "deep 3bp", Pct())               # capital too small
    assert long_reasons([DAY], 100, Pct()) == ["1 full day of data (needs 3)"]
    assert long_reasons([DAY | {"maker_fills": 2}] * 3, 100, Pct()) == ["too few fills (2.0/day)"]


def test_scan_workers_step_aside_for_a_running_bot() -> None:
    assert scan_workers(8, bot_running=True) == max(1, min(8, (os.cpu_count() or 2) - 2))   # the bot keeps 2 cores
    assert scan_workers(3, bot_running=False) == 3
    assert scan_workers("auto", bot_running=False) == max(1, (os.cpu_count() or 2) - 1)


def test_scan_workers_run_at_the_lowest_cpu_priority() -> None:
    out = subprocess.run([sys.executable, "-c", "import os; from bot.scout.scan import lower_priority; "
                          "lower_priority(); print(os.nice(0))"], capture_output=True, text=True, cwd=ROOT,
                         timeout=60)
    assert out.stdout.strip() == "19", out.stderr


def test_cache_keys_treat_100_and_100_point_0_alike() -> None:
    a = Risk.for_capital(100, 10).__dict__
    assert risk_key({**a, "capital_usd": 100}) == risk_key({**a, "capital_usd": 100.0})


# ------------------------------------------------------------------------------------------------ capital
def test_the_scan_capital_moves_only_on_a_25_percent_change_once_a_day(tmp_path: Path) -> None:
    acct = "account equity $1,000.00"
    assert settle(tmp_path, 1000, acct, today="2026-09-25") == (1000, acct, True)       # first scan
    assert settle(tmp_path, 1180, acct, today="2026-09-26")[:2] == (1000, acct)         # +18%: held
    assert settle(tmp_path, 1400, acct, today="2026-09-26")[2] is True                  # +40% on a new day
    assert settle(tmp_path, 2000, acct, today="2026-09-26")[0] == 1400                  # again the same day: held
    assert settle(tmp_path, 1100, "paper capital: no funded account", today="2026-09-26")[2] is True   # kind
    assert settle(tmp_path, 250, "fixed", today="2026-09-26")[:2] == (250, "fixed")    # a fixed amount: at once
    assert settle(tmp_path, 300, "fixed", today="2026-09-26")[0] == 300
    forget(tmp_path)
    assert not (tmp_path / STATE).exists()



def test_scan_now_trigger_fires_once(tmp_path: Path) -> None:
    (tmp_path / SCAN_NOW).touch()
    assert take_trigger(tmp_path / SCAN_NOW) and not take_trigger(tmp_path / SCAN_NOW)


# ------------------------------------------------------------------------------------------------ balances
def test_balance_history_keeps_deposits_apart_from_trading(tmp_path: Path) -> None:
    log = BalanceLog(tmp_path / "balances.jsonl")
    now = time.time()
    log.record(source="scout", equity=100, net_deposits=100, ts=now - 8 * 86400)
    log.record(source="scout", equity=104, net_deposits=100, ts=now - 2 * 86400)
    log.record(source="scout", equity=205, net_deposits=200, ts=now - 3600)    # deposited $100 more, earned $1
    assert log.record(source="bot", equity=205.5, net_deposits=200, ts=now, min_interval_s=300)
    assert not log.record(source="bot", equity=205.6, net_deposits=200, ts=now + 60, min_interval_s=300)
    s = log.summary(now=now + 1)
    assert s["latest"]["equity"] == 205.5 and pnl(s["latest"]) == pytest.approx(5.5)
    assert s["7d"]["equity_change"] == pytest.approx(101.5) and s["7d"]["pnl_change"] == pytest.approx(1.5)
    assert s["7d"]["deposits_change"] == pytest.approx(100)
    assert log.latest() == s["latest"] and len(log.rows()) == 4


# ------------------------------------------------------------------------------------------------ settings
def test_settings_are_checked_and_layered_over_the_config(tmp_path: Path) -> None:
    base = SizingDefaults()
    assert settings.parse("trade_share", "50%") == 50 and settings.parse("capital", "Auto") == "auto"
    assert settings.parse("max_capital", "none") is None and settings.parse("capital", "$1,500") == 1500
    assert settings.parse("scan_workers", "auto") == "auto" and settings.parse("scan_every", "45") == 45
    for name, bad in (("trade_share", "0"), ("kill", "90"), ("scan_every", "5"), ("nope", "1"), ("capital", "x")):
        with pytest.raises(ValueError):
            settings.parse(name, bad)
    settings.save(tmp_path, "trade_share", 50, base)
    with pytest.raises(ValueError, match="not saved"):
        settings.save(tmp_path, "position_stop", 5, base)          # above the 2% daily stop
    z = settings.effective_sizing(base, settings.load(tmp_path))
    assert z.capital_frac == 0.5 and z.position_stop_pct == 1.0
    assert settings.reset(tmp_path, "trade_share") == {}


def test_the_live_bot_applies_the_owners_settings_at_its_next_resize(tmp_path: Path) -> None:
    now = 1_790_000_000_000_000
    eng, s = _engine(tmp_path, 1_000)
    eng.state.kv_set("sizing_ok", "1000.00:BTC")
    state = tmp_path / "state"
    settings.save(state, "trade_share", 50, SizingDefaults())
    eng.settings_dir = state
    eng.resize(now)
    assert eng.size_capital == 500 and s.order_size_usd == pytest.approx(4_000)
    settings.save(state, "capital", 250.0, SizingDefaults())       # a fixed amount: at most that
    eng.resize(now + 86_400_000_000)
    assert eng.size_capital == 125   # 250 x 50%
    settings.save(state, "kill", 20.0, SizingDefaults())
    eng.resize(now + 2 * 86_400_000_000)
    assert s.kill_usd == pytest.approx(25) and s.sizing is not None and s.sizing.kill_pct == 10   # session untouched


# ------------------------------------------------------------------------------------------------ Telegram
async def test_telegram_changes_a_setting_after_a_confirm(tmp_path: Path) -> None:
    app = _app(tmp_path)
    bot, api, _ = _bot(tmp_path, app)
    (tmp_path / "state" / STATE).write_text(json.dumps({"usd": 100, "kind": "account"}))
    await bot.handle(msg("/set trade_share 50"))
    assert "Set <b>trade_share</b> to <b>50%</b>" in api.sent[-1][1]
    assert settings.load(tmp_path / "state") == {}                            # nothing until confirmed
    await bot.handle(press(f"ok {next(iter(bot.pending))}"))
    assert settings.load(tmp_path / "state") == {"trade_share": 50}
    assert not (tmp_path / "state" / STATE).exists() and (tmp_path / "state" / SCAN_NOW).exists()   # rescan now
    await bot.handle(msg("/set position_stop 5"))                             # above the daily stop
    assert "⚠️" in api.sent[-1][1] and not bot.pending
    await bot.handle(msg("/settings"))
    assert "trade_share</b> 50% ✏️" in api.sent[-1][1]
    await bot.handle(msg("/set trade_share default"))
    await bot.handle(press(f"ok {next(iter(bot.pending))}"))
    assert settings.load(tmp_path / "state") == {}


async def test_telegram_balance_reads_logs_and_shows_the_history(tmp_path: Path, monkeypatch: Any) -> None:
    import bot.scout.capital as cap

    app = _app(tmp_path)
    (tmp_path / "config" / "venues").mkdir(parents=True)
    shutil.copy(ROOT / "config" / "venues" / "arcus.yaml", tmp_path / "config" / "venues" / "arcus.yaml")
    bot, api, _ = _bot(tmp_path, app)
    BalanceLog(tmp_path / "state" / "balances.jsonl").record(source="scout", equity=100, net_deposits=100,
                                                            ts=time.time() - 2 * 86400)

    async def snap(url: str, idx: int = 0) -> dict[str, float]:
        return {"equity": 112.5, "free": 90.0, "net_deposits": 110.0}

    monkeypatch.setattr(cap, "account_snapshot", snap)
    await bot.handle(msg("/balance"))
    text = api.sent[-1][1]
    assert "Equity <b>$112.50</b>" in text and "trading PnL <b>+$2.50</b>" in text
    assert "7 d +$12.50 (trading +$2.50)" in text
    assert len(BalanceLog(tmp_path / "state" / "balances.jsonl").rows()) == 2   # this reading was logged


async def test_telegram_scan_now_touches_the_trigger(tmp_path: Path) -> None:
    app = _app(tmp_path)
    _running_paper(tmp_path, app)
    bot, api, _ = _bot(tmp_path, app)
    await bot.handle(msg("/scannow"))
    assert (tmp_path / "state" / SCAN_NOW).exists() and "Scan started" in api.sent[-1][1]
    assert api.sent[-1][0] == OWNER


def test_pilot_sessions_still_load(tmp_path: Path) -> None:
    assert load_session(_pilot_session(tmp_path)).sizing is not None


# ------------------------------------------------------------------------------------------------ bot up / down
def test_up_and_down_start_and_stop_background_services(tmp_path: Path, monkeypatch: Any) -> None:
    from bot import ops
    from bot.common.config import AppConfig

    app = AppConfig(state_dir=str(tmp_path / "state"), logs_dir=str(tmp_path / "logs"))
    monkeypatch.setattr(ops, "bot_bin", lambda: sys.executable)
    monkeypatch.setattr(ops, "BY_NAME", {"scout": ops.Service("scout", ("-c", "import time; time.sleep(60)"), "x", 10)})
    ok, text = ops.start(app, "scout")
    assert ok and ops.pid_of(app, "scout") and "started" in text
    assert ops.start(app, "scout")[0] is False                                  # already running: not twice
    ok, text = ops.stop(app, "scout")
    assert ok and ops.pid_of(app, "scout") is None and not ops.pid_path(app, "scout").exists()
    assert ops.stop(app, "scout") == (False, "scout: not running")


def test_up_starts_only_what_this_machine_needs() -> None:
    from bot import ops
    from bot.common.config import AppConfig

    app = AppConfig()
    assert set(ops.wanted(app, {}, live_running=False)) == {"scout"}
    tg = {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "1"}
    assert set(ops.wanted(app, tg, live_running=True)) == {"scout", "telegram", "guardian"}
    assert "guardian" in ops.skipped(tg, live_running=False)   # with no live bot it would fire at once


def test_status_screen_renders_on_an_empty_machine(tmp_path: Path) -> None:
    from bot import ops
    from bot.common.config import AppConfig

    app = AppConfig(state_dir=str(tmp_path / "state"), logs_dir=str(tmp_path / "logs"))
    text = ops.dashboard(app, {}, tmp_path)
    for part in ("SERVICES", "TRADING BOT", "deployed: nothing", "SCOUT", "no scan yet", "BALANCE"):
        assert part in text


def test_a_scan_stops_within_seconds_when_asked() -> None:
    import threading
    from concurrent.futures import ProcessPoolExecutor

    from bot.scout.scan import ScanStopped, _gather, _halt

    stop = threading.Event()
    ex = ProcessPoolExecutor(max_workers=2)
    threading.Timer(0.5, stop.set).start()
    t0 = time.time()
    with pytest.raises(ScanStopped):
        for _ in _gather(ex, [30, 30, 30, 30], stop, fn=time.sleep):   # four 30-second jobs
            pass
    _halt(ex)
    assert time.time() - t0 < 5
