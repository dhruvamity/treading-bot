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
    assert c.started == ["scout", "lighter-scout", "telegram"]
    c.started.clear()
    lines = ops.up_parts(AppConfig(), {**ENV, "BOT_ROLE": "recorder"}, False, ops.PARTS)
    assert c.started == ["scout", "lighter-scout"] and any("telegram: not started" in x for x in lines)


def test_down_stops_only_the_parts_named_and_the_arbitrage_only_with_all_three(monkeypatch: pytest.MonkeyPatch) -> None:
    c = Calls(monkeypatch)
    ops.down_parts(AppConfig(), ("arcus",), everything=True, live_running=True)
    assert c.stopped == ["scout", "guardian"] and c.others == []        # --all stops the Arcus runs elsewhere
    c.stopped.clear()
    ops.down_parts(AppConfig(), ("lighter",), everything=True, live_running=False)
    assert c.stopped == [] and c.others == [(True, {"lighter": True, "arbitrage": False})]
    c.others.clear()
    ops.down_parts(AppConfig(), ops.PARTS, everything=True, live_running=False)
    assert c.stopped == ["telegram", "scout", "guardian"] and c.others == [(True, {"lighter": True, "arbitrage": True})]


def test_a_live_run_keeps_its_guardian_on_a_plain_down(monkeypatch: pytest.MonkeyPatch) -> None:
    c = Calls(monkeypatch)
    ops.down_parts(AppConfig(), ("arcus",), everything=False, live_running=True)
    assert c.stopped == ["scout"]


def test_part_names_and_the_scouts_shortcut() -> None:
    assert cli._parts([]) == ops.PARTS
    assert cli._parts(["scouts"]) == ("arcus", "lighter") and cli._parts(["both"]) == ("arcus", "lighter")
    assert cli._parts(["telegram", "arcus", "telegram"]) == ("telegram", "arcus")
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
    assert got == [("up", ("lighter", "telegram")), ("up", ("arcus", "lighter")), ("down", ("lighter",), False),
                   ("down", ops.PARTS, True)]


def test_arcus_can_be_spelled_like_the_other_two_in_telegram() -> None:
    f = others.arcus_spelling
    assert f("arcus_status", []) == ("status", []) and f("a_run", ["SPY"]) == ("run", ["SPY"])
    assert f("arcus", ["closeall", "taker"]) == ("closeall", ["taker"]) and f("a", []) == ("menu", [])
    assert f("status", []) == ("status", []) and f("account", []) == ("account", [])       # not a prefix
    assert f("auto", ["on"]) == ("auto", ["on"]) and f("l_status", []) == ("l_status", [])
