"""The command layout: each bot starts and stops only itself, `tbot` starts the whole machine, and Arcus's Telegram
commands can be spelled like the other two (/arcus_status)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from arcus import cli, ops
from arcus.common.config import AppConfig
from arcus.telegram import others


class Calls:
    def __init__(self, mp: pytest.MonkeyPatch) -> None:
        self.started: list[str] = []
        self.stopped: list[str] = []
        self.others: list[tuple[Any, ...]] = []
        mp.setattr(ops, "start", lambda app, name, role="all": (self.started.append(name) or True, f"{name}: started"))
        mp.setattr(ops, "stop", lambda app, name: (self.stopped.append(name) or True, f"{name}: stopped"))
        mp.setattr(ops, "lighter_up", lambda role="all": self.started.append("lighter-scout") or "lighter scout: started")
        mp.setattr(ops, "others_down", lambda everything, **kw: self.others.append((everything, kw)) or ["x"])
        mp.setattr(ops, "daytrade_up", lambda role="all": self.started.append(f"daytrade-{role}") or "daytrade: started")
        mp.setattr(ops, "daytrade_down", lambda: self.stopped.append("daytrade") or "daytrade: stopped")


ENV = {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "1"}


def test_the_arcus_part_starts_only_arcus(monkeypatch: pytest.MonkeyPatch) -> None:
    c = Calls(monkeypatch)
    ops.up_parts(AppConfig(), ENV, True, ("arcus",))
    assert c.started == ["scout", "guardian"]            # its scout, and the guardian while a live run exists


def test_the_lighter_part_and_the_telegram_part_start_only_themselves(monkeypatch: pytest.MonkeyPatch) -> None:
    c = Calls(monkeypatch)
    ops.up_parts(AppConfig(), ENV, False, ("lighter",))
    assert c.started == ["lighter-scout"]
    c.started.clear()
    ops.up_parts(AppConfig(), ENV, False, ("telegram",))
    assert c.started == ["telegram"]


def test_all_parts_start_everything_and_a_recorder_never_starts_telegram(monkeypatch: pytest.MonkeyPatch) -> None:
    c = Calls(monkeypatch)
    lines = ops.up_parts(AppConfig(), ENV, False, ops.PARTS)
    assert c.started == ["scout", "lighter-scout", "telegram", "daytrade-all"]
    c.started.clear()
    lines = ops.up_parts(AppConfig(), {**ENV, "BOT_ROLE": "recorder"}, False, ops.PARTS)
    assert c.started == ["scout", "lighter-scout", "daytrade-recorder"] and any("telegram: not started" in x for x in lines)
    c.started.clear()
    ops.up_parts(AppConfig(), {**ENV, "BOT_ROLE": "trader"}, False, ops.PARTS)
    assert c.started == ["scout", "lighter-scout", "telegram", "daytrade-trader"]


def test_down_stops_only_the_parts_named_and_the_arbitrage_only_with_all_three(monkeypatch: pytest.MonkeyPatch) -> None:
    c = Calls(monkeypatch)
    ops.down_parts(AppConfig(), ("arcus",), everything=True, live_running=True)
    assert c.stopped == ["scout", "guardian"] and c.others == []        # --all stops the Arcus runs elsewhere
    c.stopped.clear()
    ops.down_parts(AppConfig(), ("lighter",), everything=True, live_running=False)
    assert c.stopped == [] and c.others == [(True, {"lighter": True, "arbitrage": False})]
    c.others.clear()
    ops.down_parts(AppConfig(), ops.PARTS, everything=True, live_running=False)
    assert c.stopped == ["telegram", "scout", "guardian", "daytrade"]
    assert c.others == [(True, {"lighter": True, "arbitrage": True})]
    c.stopped.clear()
    c.others.clear()
    ops.down_parts(AppConfig(), ("arcus", "lighter", "telegram"), everything=True, live_running=False)
    assert c.stopped == ["telegram", "scout", "guardian"] and c.others[0][1]["arbitrage"] is True   # as before daytrade


def test_a_live_run_keeps_its_guardian_on_a_plain_down(monkeypatch: pytest.MonkeyPatch) -> None:
    c = Calls(monkeypatch)
    ops.down_parts(AppConfig(), ("arcus",), everything=False, live_running=True)
    assert c.stopped == ["scout"]


# ------------------------------------------------------------------------------------------------ daytrade
@pytest.fixture
def dt_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """daytrade's state folder in a temporary place, for its process helpers and its status summary."""
    pytest.importorskip("daytrade")
    from daytrade import bot as dbot
    from daytrade import ops as dops

    state = tmp_path / "daytrade-state"
    state.mkdir()
    monkeypatch.setattr(dops, "STATE", state)
    summary = dbot.summary
    monkeypatch.setattr(dbot, "summary", lambda mode="paper", state_dir=state: summary(mode, state_dir))
    return state


def test_daytrade_starts_on_a_trader_and_never_on_a_recorder_or_a_scout(dt_state: Path,
                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    from daytrade import ops as dops

    calls: list[str] = []
    monkeypatch.setattr(dops, "start", lambda state=None: calls.append("start") or (True, "started (pid 4242), log x"))
    trader = {"BOT_ROLE": "trader"}
    assert ops.up_parts(AppConfig(), trader, False, ("daytrade",)) == [
        "daytrade paper bot: started (pid 4242), log x  [paper only: it sends no orders]"]
    for r in ("recorder", "scout"):
        assert ops.up_parts(AppConfig(), {"BOT_ROLE": r}, False, ("daytrade",)) == [
            f"daytrade: not started (this machine is a {r}: the trader runs it)"]
    assert calls == ["start"]

    monkeypatch.setattr(dops, "stop", lambda state=None: calls.append("stop") or (True, "stopped"))
    assert ops.down_parts(AppConfig(), ("daytrade",), everything=False, live_running=False) == [
        "daytrade paper bot: stopped"]
    assert calls == ["start", "stop"]


def test_daytrade_never_starts_a_process_under_tests(dt_state: Path) -> None:
    line = ops.daytrade_up("trader")                     # the real starter: DAYTRADE_NO_SPAWN (conftest) refuses it
    assert line.startswith("daytrade: not started (RuntimeError: refusing") and not (dt_state / "paper.pid").exists()
    assert ops.daytrade_down() == "daytrade paper bot: not running"


def test_daytrade_that_cannot_be_imported_breaks_no_other_part(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "daytrade", None)                      # `import daytrade` fails
    assert ops.daytrade_up("trader").startswith("daytrade: not started (ModuleNotFoundError")
    assert ops.daytrade_down().startswith("daytrade: ModuleNotFoundError")
    assert ops.daytrade_status("trader")[2].startswith("  not available (ModuleNotFoundError")


def test_tbot_status_shows_the_daytrade_summary_on_a_trader(dt_state: Path, tmp_path: Path) -> None:
    import json

    assert ops.daytrade_status("trader") == ["", "DAYTRADE (paper only: it sends no orders)",
                                             "  PAPER: never started", "  start it: tbot up daytrade"]
    (dt_state / "paper_status.json").write_text(json.dumps({"utc": "2026-10-15 14:00:05", "slots": {
        "spy-orb15-tf5@lighter": {"state": "flat, 1 trade(s) today", "last_bar_utc": "13:59"}}}))
    (dt_state / "paper_trades.jsonl").write_text(json.dumps({"slot": "spy-orb15-tf5@lighter", "r": -1.05}) + "\n")
    lines = ops.daytrade_status("trader")
    assert "  PAPER NOT running · last look 2026-10-15 14:00:05 UTC" in lines
    assert any("spy-orb15-tf5@lighter: flat, 1 trade(s) today" in x for x in lines)
    assert any("1 trades, 0 won, -1.05R in all" in x for x in lines)
    assert ops.daytrade_status("recorder")[2:] == ["  not here: it runs on the trader machine"]

    app = AppConfig(state_dir=str(tmp_path / "state"), logs_dir=str(tmp_path / "logs"))
    screen = ops.dashboard(app, {"BOT_ROLE": "trader"}, tmp_path)          # `tbot status` on a trader
    assert "THIS MACHINE: trader" in screen and "DAYTRADE (paper only" in screen and "1 trades, 0 won" in screen


def test_part_names_and_the_scouts_shortcut() -> None:
    assert cli._parts([]) == ops.PARTS
    assert cli._parts(["scouts"]) == ("arcus", "lighter") and cli._parts(["both"]) == ("arcus", "lighter")
    assert cli._parts(["telegram", "arcus", "telegram"]) == ("telegram", "arcus")
    assert cli._parts(["daytrade"]) == ("daytrade",) and "daytrade" in cli._parts([])
    with pytest.raises(SystemExit):
        cli._parts(["arcus-only"])


def test_tbot_hands_the_shared_commands_to_the_arcus_ones(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []
    monkeypatch.setattr(cli, "main", lambda argv=None: seen.append(list(argv or [])))
    for argv in (["telegram", "--read-only"], ["export", "--full"], ["import"], ["sync", "status"], ["recommend", "SPY"]):
        cli.main_tbot(argv)
    assert seen == [["telegram", "--read-only"], ["export", "--full"], ["import"], ["sync", "status"], ["recommend", "SPY"]]


def test_tbot_up_and_scout_reach_the_right_parts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    got: list[Any] = []
    monkeypatch.setattr(cli, "_up", lambda parts, show=True: got.append(("up", parts)))
    monkeypatch.setattr(cli, "_down", lambda parts, everything, label: got.append(("down", parts, everything)))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BOT_HOME", str(tmp_path))
    cli.main_tbot(["up", "lighter", "telegram"])
    cli.main_tbot(["scout"])
    cli.main_tbot(["scout", "lighter", "--stop"])
    cli.main_tbot(["down", "--all"])
    cli.main_tbot(["up", "daytrade"])
    cli.main_tbot(["down", "daytrade"])
    assert got == [("up", ("lighter", "telegram")), ("up", ("arcus", "lighter")), ("down", ("lighter",), False),
                   ("down", ops.PARTS, True), ("up", ("daytrade",)), ("down", ("daytrade",), False)]


def test_tbot_role_writes_the_role_whatever_the_env_file_looks_like(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                                                                    capsys: pytest.CaptureFixture[str]) -> None:
    from arcus.common import role

    env = tmp_path / ".env"
    env.write_text("ARCUS_ADDRESS=0xabc\n# BOT_ROLE=all is the default\n")     # an .env from before the roles: no line
    role.write("trader", env)
    assert env.read_text() == "ARCUS_ADDRESS=0xabc\n# BOT_ROLE=all is the default\nBOT_ROLE=trader\n"
    assert env.stat().st_mode & 0o777 == 0o600
    env.write_text("BOT_ROLE=\nX=1\nBOT_ROLE=scout\n")                         # the template's empty line, and a second
    role.write("recorder", env)
    assert env.read_text() == "BOT_ROLE=recorder\nX=1\n"
    with pytest.raises(ValueError):
        role.write("tradr", env)
    # what `tbot up` says first: the role, and on a small machine that was never told its role, a warning
    assert role.note("trader", 842.0) == [role.note("trader")[0]] and "this machine: trader" in role.note("trader")[0]
    small = role.note("all", 842.0)
    assert len(small) == 2 and "842 MB" in small[1] and "tbot role trader" in small[1]
    assert len(role.note("all", 16000.0)) == 1 and len(role.note("all", None)) == 1

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BOT_HOME", str(tmp_path))
    monkeypatch.setenv("BOT_ROLE", "all")                   # so that the test's end puts back whatever was there,
    monkeypatch.delenv("BOT_ROLE")                          # whatever the command leaves in the environment
    monkeypatch.setenv("X", "0")
    env.write_text("X=1\n")
    cli.main_tbot(["role", "trader"])
    assert env.read_text() == "X=1\nBOT_ROLE=trader\n"
    said = capsys.readouterr().out
    assert "BOT_ROLE=trader is now in .env" in said and "this machine: trader" in said and "tbot down" in said
    monkeypatch.delenv("BOT_ROLE")
    env.write_text("BOT_ROLE=recorder\nBOT_ROLE=\nBOT_ROLE=trader\n")    # written twice (an `echo >>`): the last counts,
    cli.main_tbot(["role"])                                 # as Lighter and the arbitrage read the same file
    assert "this machine: trader (Telegram and trading)" in capsys.readouterr().out


def test_arcus_can_be_spelled_like_the_other_two_in_telegram() -> None:
    f = others.arcus_spelling
    assert f("arcus_status", []) == ("status", []) and f("a_run", ["SPY"]) == ("run", ["SPY"])
    assert f("arcus", ["closeall", "taker"]) == ("closeall", ["taker"]) and f("a", []) == ("menu", [])
    assert f("status", []) == ("status", []) and f("account", []) == ("account", [])       # not a prefix
    assert f("auto", ["on"]) == ("auto", ["on"]) and f("l_status", []) == ("l_status", [])
