"""One Telegram bot for the whole trading bot: Arcus as always, Lighter as /l_<command>, the funding arbitrage as
/arb_<command> (arcus/telegram/others.py). Nothing here reaches Telegram, a venue or a real process."""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from arcus.telegram import others
from tests.unit.test_telegram import OWNER, STRANGER, _app, _bot, msg, press


class FakePanel:
    """Stands for lighter's Panel: records what the one bot hands it."""

    def __init__(self) -> None:
        self.texts: list[tuple[int, int, str, bool]] = []
        self.buttons: list[tuple[int, int, str, bool]] = []
        self.code_chats: set[int] = set()

    def waits_for_code(self, chat: int) -> bool:
        return chat in self.code_chats

    async def text(self, chat: int, user: int, text: str, *, read_only: bool = False) -> None:
        self.texts.append((chat, user, text, read_only))

    async def button(self, chat: int, msg_id: int, data: str, *, read_only: bool = False) -> str:
        self.buttons.append((chat, msg_id, data, read_only))
        return "noted"


def test_which_part_a_command_belongs_to() -> None:
    p = others.part_of
    assert p("l") == ("l", "") and p("lighter") == ("l", "") and p("l_status") == ("l", "status")
    assert p("lighter_run") == ("l", "run") and p("arb") == ("arb", "") and p("arb_hold") == ("arb", "hold")
    for arcus in ("status", "logs", "run", "closeall", "balance", "auto", "pilotclose"):     # never taken for a part
        assert p(arcus) is None, arcus


async def test_lighter_commands_and_buttons_go_to_the_lighter_panel_and_nothing_else_does(tmp_path: Path) -> None:
    bot, api, _ = _bot(tmp_path, _app(tmp_path))
    panel = FakePanel()
    bot.lighter.panel = panel
    await bot.handle(msg("/l_status"))
    await bot.handle(msg("/l run SPY smart +1 sl=10"))
    await bot.handle(msg("/lighter"))
    await bot.handle(msg("/L_CLOSEALL"))
    assert [t[2] for t in panel.texts] == ["/status", "/run SPY smart +1 sl=10", "/menu", "/closeall"]
    assert all(t[:2] == (OWNER, OWNER) and t[3] is False for t in panel.texts)

    answered: list[tuple[str, str]] = []

    async def answer(callback_id: str, text: str = "") -> None:
        answered.append((callback_id, text))
    api.answer = answer                                                     # type: ignore[method-assign]
    await bot.handle(press("l f:lev:max"))
    assert panel.buttons == [(OWNER, 7, "f:lev:max", False)] and answered == [("cb", "noted")]   # its note on the button

    await bot.handle(msg("/l_closeall", chat=STRANGER, user=STRANGER))      # a stranger reaches no part of the bot
    await bot.handle(press("l ok:abc", chat=STRANGER, user=STRANGER))
    assert len(panel.texts) == 4 and len(panel.buttons) == 1

    await bot.handle(msg("/ping"))                                          # an Arcus command stays an Arcus command
    assert "PONG" in api.sent[-1][1] and len(panel.texts) == 4


async def test_a_typed_code_reaches_lighter_only_when_lighter_waits_for_one(tmp_path: Path) -> None:
    bot, api, _ = _bot(tmp_path, _app(tmp_path))
    panel = FakePanel()
    bot.lighter.panel = panel
    await bot.handle(msg("123456"))
    assert "UNKNOWN CODE" in api.sent[-1][1] and not panel.texts
    panel.code_chats.add(OWNER)                                             # a Lighter LIVE start asked for its code
    await bot.handle(msg("123456"))
    assert panel.texts == [(OWNER, OWNER, "123456", False)]


async def test_a_read_only_bot_tells_the_parts_so(tmp_path: Path) -> None:
    bot, api, _ = _bot(tmp_path, _app(tmp_path), read_only=True)
    panel = FakePanel()
    bot.lighter.panel = panel
    panel.code_chats.add(OWNER)
    await bot.handle(msg("/l_closeall"))
    await bot.handle(press("l ok:abc"))
    await bot.handle(msg("123456"))                                         # no LIVE start is confirmed read-only
    assert panel.texts == [(OWNER, OWNER, "/closeall", True)] and panel.buttons[0][3] is True
    await bot.handle(msg("/arb_hold 72"))
    assert "READ-ONLY" in api.sent[-1][1]
    await bot.handle(msg("/arb_start live"))
    assert "READ-ONLY" in api.sent[-1][1] and not bot.pending


async def test_a_part_that_is_not_installed_says_so_and_the_rest_works(tmp_path: Path) -> None:
    bot, api, _ = _bot(tmp_path, _app(tmp_path))
    bot.lighter.why = "ModuleNotFoundError: No module named 'lighter_bot'"
    bot.lighter.load = lambda: None                                         # type: ignore[method-assign]
    bot.arb.why = "ModuleNotFoundError: No module named 'arbitrage'"
    bot.arb.load = lambda: None                                             # type: ignore[method-assign]
    await bot.handle(msg("/l_status"))
    assert "LIGHTER IS NOT AVAILABLE" in api.sent[-1][1] and "make install" in api.sent[-1][1]
    await bot.handle(press("l status"))
    assert "LIGHTER IS NOT AVAILABLE" in api.sent[-1][1]
    await bot.handle(msg("/arb_status"))
    assert "FUNDING ARB IS NOT AVAILABLE" in api.sent[-1][1]
    await bot.handle(msg("/ping"))
    assert "PONG" in api.sent[-1][1]


async def test_the_menu_and_the_help_name_all_three_parts(tmp_path: Path) -> None:
    from arcus.telegram.views import COMMANDS, HELP, menu_keyboard

    data = [d for row in menu_keyboard() for _, d in row]
    assert "l_menu" in data and "arb_menu" in data
    assert "/l_status" in HELP and "/arb_status" in HELP
    assert {"l", "arb"} <= {c for c, _ in COMMANDS}
    assert all(c.islower() and len(c) <= 32 and len(d) <= 256 for c, d in COMMANDS)     # Telegram's own limits


# ------------------------------------------------------------------------------------------------ the real Lighter panel
async def test_the_real_lighter_panel_inside_the_one_bot(tmp_path: Path) -> None:
    pytest.importorskip("lighter_bot")
    from lighter_bot import config as lconfig
    from lighter_bot.telegram.embed import Panel, PanelApi

    root = tmp_path / "lighter"
    (root / "config").mkdir(parents=True)
    shutil.copy(Path(lconfig.ROOT) / "config" / "app.yaml", root / "config" / "app.yaml")
    cfg = lconfig.load(root, env={})
    cfg.ensure_dirs()
    wire: list[tuple[str, dict[str, Any]]] = []

    class Wire(PanelApi):
        def __init__(self) -> None:
            pass

        async def call(self, method: str, **params: Any) -> Any:
            wire.append((method, params))
            return {"message_id": 9}

    bot, api, _ = _bot(tmp_path, _app(tmp_path))
    bot.lighter.panel = Panel(cfg, "token", OWNER, api=Wire())
    await bot.handle(msg("/l"))
    method, sent = wire[-1]
    assert method == "sendMessage" and sent["chat_id"] == OWNER and sent["text"].startswith("<b>LIGHTER</b> · ")
    assert "/l_closeall" in sent["text"] and "/l_run" in sent["text"]
    buttons = [b["callback_data"] for row in sent["reply_markup"]["inline_keyboard"] for b in row]
    assert "l status" in buttons and all(b.startswith("l ") for b in buttons)
    await bot.handle(press("l status"))                                     # the button comes back to Lighter
    assert wire[-1][1]["text"].startswith("<b>LIGHTER</b> · ") and api.sent == []       # and Arcus sent nothing
    await bot.handle(msg("/l_closeall"))                                    # a control: Lighter asks first
    assert "Confirm: close" in wire[-1][1]["text"]


# ------------------------------------------------------------------------------------------------ the funding arbitrage
@pytest.fixture
def arb_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The arbitrage's files in a temporary folder, and a recorder instead of its process starter."""
    pytest.importorskip("arbitrage")
    from arbitrage import cli, config, ops

    home = tmp_path / "arb"
    (home / "state").mkdir(parents=True)
    for mod in (cli, config, ops):
        monkeypatch.setattr(mod, "ROOT", home)
        monkeypatch.setattr(mod, "STATE", home / "state")
    monkeypatch.delenv("ARB_LIVE", raising=False)
    monkeypatch.setattr(others.Arb, "live_allowed", staticmethod(lambda: False))
    return home


async def _settled(bot: Any) -> None:
    while bot._tasks:
        await asyncio.gather(*list(bot._tasks))


async def test_arb_reads_answer_at_once_and_a_paper_setting_needs_no_confirmation(tmp_path: Path, arb_home: Path) -> None:
    bot, api, _ = _bot(tmp_path, _app(tmp_path))
    await bot.handle(msg("/arb"))
    assert "FUNDING ARB" in api.sent[-1][1] and "/arb_hold 72" in api.sent[-1][1] and "Commands act on: PAPER" in api.sent[-1][1]
    await bot.handle(msg("/arb_status"))
    await _settled(bot)
    assert "FUNDING ARB · STATUS" in api.sent[-1][1] and "never run" in api.sent[-1][1]
    await bot.handle(msg("/arb_hold 72"))                                   # the holding time, from the phone
    await _settled(bot)
    assert "max_hold_h = 72" in api.sent[-1][1] and not bot.pending
    assert json.loads((arb_home / "settings.json").read_text())["max_hold_h"] == 72.0
    await bot.handle(msg("/arb settings"))                                  # "/arb settings" is "/arb_settings"
    await _settled(bot)
    assert "FUNDING ARB · SETTINGS" in api.sent[-1][1] and "max_hold_h" in api.sent[-1][1]
    await bot.handle(msg("/arb_backtest"))
    assert "UNKNOWN FUNDING ARB COMMAND" in api.sent[-1][1]


async def test_arb_changes_to_a_live_position_ask_first(tmp_path: Path, arb_home: Path) -> None:
    (arb_home / "state" / "position-live.json").write_text(json.dumps({"phase": "open"}))
    bot, api, _ = _bot(tmp_path, _app(tmp_path))
    await bot.handle(msg("/arb_close"))
    assert "CLOSE (LIVE)" in api.sent[-1][1] and len(bot.pending) == 1
    assert not (arb_home / "state" / "control-live.json").exists()          # nothing was asked of the bot yet
    pid = next(iter(bot.pending))
    await bot.handle(press(f"ok {pid}"))
    await _settled(bot)
    assert json.loads((arb_home / "state" / "control-live.json").read_text()) == {"close": True, "now": False}
    await bot.handle(msg("/arb_status paper"))                              # an explicit mode: the other bot
    await _settled(bot)
    assert "paper: never run" in api.sent[-1][1]


async def test_arb_start_and_stop(tmp_path: Path, arb_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from arbitrage import ops

    started: list[tuple[str, Any]] = []
    monkeypatch.setattr(ops, "start", lambda mode, money=None: (started.append((mode, money)), (True, f"{mode}: started"))[1])
    bot, api, _ = _bot(tmp_path, _app(tmp_path))
    await bot.handle(msg("/arb_start"))
    assert "PAPER NEEDS PRETEND MONEY" in api.sent[-1][1] and not bot.pending
    await bot.handle(msg("/arb_start 120 80"))
    assert "START PAPER" in api.sent[-1][1] and not started                 # a paper start asks with a button
    await bot.handle(press(f"ok {next(iter(bot.pending))}"))
    assert started == [("paper", {"arcus": 120.0, "lighter": 80.0})] and "PAPER STARTED" in api.texts()

    await bot.handle(msg("/arb_start live"))                                # the owner's switch comes first
    assert "LIVE IS OFF" in api.sent[-1][1] and len(started) == 1 and not bot.pending
    monkeypatch.setattr(others.Arb, "live_allowed", staticmethod(lambda: True))
    await bot.handle(msg("/arb_start live"))
    p = next(iter(bot.pending.values()))
    assert p.code and "START LIVE" in api.sent[-1][1]
    await bot.handle(press(f"ok {p.pid}"))                                  # a button does not start LIVE
    assert len(started) == 1
    await bot.handle(msg("000000" if p.code != "000000" else "111111"))     # nor does a wrong code
    assert len(started) == 1
    await bot.handle(msg(p.code))
    assert started[-1] == ("live", None) and "LIVE STARTED" in api.texts()

    await bot.handle(msg("/arb_stop"))
    assert "IS NOT RUNNING" in api.sent[-1][1]
