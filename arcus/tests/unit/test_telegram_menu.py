"""The Telegram "menu" layout (arcus/telegram/menu.py): off by default, Home + six buttons, the venue switch, one place
for destructive buttons, Back / Home on every screen, and buttons that all reach a real command. No network."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from arcus.common import settings
from arcus.telegram import menu, others, views
from tests.unit.test_telegram import OWNER, _app, _bot, _running_paper, msg, press

DESTRUCTIVE = {"stop", "cancelall", "closeall", "pauseneworders", "resumeaftersl"}


def datas(kb: Any) -> list[str]:
    return [d for row in kb for _label, d in row]


async def _menu_on(tmp_path: Path) -> tuple[Any, Any]:
    app = _app(tmp_path)
    _running_paper(tmp_path, app)
    bot, api, _ = _bot(tmp_path, app)

    async def fake_home(chat: int) -> tuple[str, Any]:
        v = bot._venue(chat)
        return f"HOME {v}", menu.home_keyboard(v)

    bot._home_card = fake_home                      # the Lighter / arbitrage cards read their own state: not here
    settings.save(tmp_path / "state", "telegram_ui", "menu", app.sizing)
    return bot, api


# ------------------------------------------------------------------------------------------------ the setting
def test_telegram_ui_setting_parses() -> None:
    assert settings.parse("telegram_ui", "Menu") == "menu" and settings.parse("telegram_ui", "classic") == "classic"
    with pytest.raises(ValueError, match="one of: classic, menu"):
        settings.parse("telegram_ui", "fancy")


async def test_classic_stays_the_default(tmp_path: Path) -> None:
    app = _app(tmp_path)
    _running_paper(tmp_path, app)
    bot, api, _ = _bot(tmp_path, app)
    assert bot.ui() == "classic"
    await bot.handle(msg("/menu"))
    assert api.last_keyboard == views.menu_keyboard()
    await bot.handle(msg("hello"))
    assert "Send a Command" in api.sent[-1][1]


async def test_set_telegram_ui_goes_through_the_confirm_card(tmp_path: Path) -> None:
    app = _app(tmp_path)
    _running_paper(tmp_path, app)
    bot, api, _ = _bot(tmp_path, app)
    await bot.handle(msg("/set telegram_ui menu"))
    assert bot.ui() == "classic"                           # nothing changes before the tap
    ok = next(d for d in datas(api.last_keyboard) if d.startswith("ok "))
    await bot.handle(press(ok))
    assert bot.ui() == "menu"
    await bot.handle(msg("/set telegram_ui default"))
    await bot.handle(press(next(d for d in datas(api.last_keyboard) if d.startswith("ok "))))
    assert bot.ui() == "classic"


# ------------------------------------------------------------------------------------------------ Home
async def test_home_has_six_buttons_and_free_text_is_not_a_dead_end(tmp_path: Path) -> None:
    bot, api = await _menu_on(tmp_path)
    await bot.handle(msg("/menu"))
    kb = api.last_keyboard
    assert [len(r) for r in kb] == [2, 2, 2] and len(datas(kb)) == 6
    assert datas(kb) == ["dashboard", "m_start", "m_control", "m_money", "m_settings", "venue"]
    assert kb[-1][-1][0] == "🔀 Venue: Arcus"
    await bot.handle(msg("what now?"))
    assert api.sent[-1][1] == "HOME arcus" and api.last_keyboard == kb


async def test_destructive_buttons_live_only_on_the_control_screen() -> None:
    for v in menu.VENUES:
        assert not DESTRUCTIVE & {d.removeprefix(menu.PREFIX[v]) for d in datas(menu.home_keyboard(v))}
        for g in ("start", "money", "settings"):
            names = {d.removeprefix(menu.PREFIX[v]) for d in datas(menu.screen_keyboard(v, g))}
            assert not DESTRUCTIVE & names, (v, g)
        control = {d.removeprefix(menu.PREFIX[v]) for d in datas(menu.screen_keyboard(v, "control"))}
        assert {"stop"} <= control and (v == "arb" or {"closeall", "resumeaftersl"} <= control), (v, control)


async def test_venue_switch_cycles_and_the_buttons_follow(tmp_path: Path) -> None:
    bot, api = await _menu_on(tmp_path)
    await bot.handle(press("venue"))
    assert api.edits[-1][2] == "HOME lighter" and datas(api.last_keyboard)[0] == "l_dashboard"
    await bot.handle(press("m_control"))
    assert "l_closeall" in datas(api.last_keyboard) and "closeall" not in datas(api.last_keyboard)
    assert "Lighter · Control" in api.edits[-1][2]
    await bot.handle(press("home"))
    await bot.handle(press("venue"))
    assert datas(api.last_keyboard)[0] == "arb_status" and api.last_keyboard[-1][-1][0] == "🔀 Venue: Funding arb"
    await bot.handle(press("venue"))
    assert datas(api.last_keyboard)[0] == "dashboard"
    # typed commands never follow the switch: /status is Arcus's
    bot.venues[OWNER] = "lighter"
    await bot.handle(msg("/status"))
    assert "LIGHTER" not in api.sent[-1][1].upper()


# ------------------------------------------------------------------------------------------------ Back / Home
async def test_every_screen_ends_with_home_and_money_screens_with_back(tmp_path: Path) -> None:
    bot, api = await _menu_on(tmp_path)
    await bot.handle(press("m_money"))
    assert api.last_keyboard[-1] == [("☰ Home", "home")]
    await bot.handle(msg("/status"))                          # its classic ☰ Menu button gives way to Home
    assert "menu" not in datas(api.last_keyboard) and api.last_keyboard[-1] == [("☰ Home", "home")]
    await bot.handle(msg("/pnl"))
    assert api.last_keyboard == [[("🔄 Refresh", "pnl paper")], [("◀️ Back", "m_money"), ("☰ Home", "home")]]


async def test_back_goes_to_the_group_a_screen_came_from(tmp_path: Path) -> None:
    bot, api = await _menu_on(tmp_path)
    await bot.handle(msg("/balance"))
    assert api.last_keyboard[-1] == [("◀️ Back", "m_money"), ("☰ Home", "home")]
    await bot.handle(msg("/settings"))
    assert api.last_keyboard[-1] == [("◀️ Back", "m_settings"), ("☰ Home", "home")]


async def test_confirm_cards_and_the_dashboard_stay_untouched(tmp_path: Path) -> None:
    bot, api = await _menu_on(tmp_path)
    bot._spawn = lambda coro: coro.close()  # type: ignore[method-assign]
    await bot.handle(msg("/stop"))
    assert [d.split()[0] for d in datas(api.last_keyboard)] == ["ok", "no"]
    assert menu.with_nav(views.dashboard_keyboard(True)) == views.dashboard_keyboard(True)
    assert menu.with_nav(menu.home_keyboard("arcus")) == menu.home_keyboard("arcus")


async def test_read_only_control_screen_says_so(tmp_path: Path) -> None:
    app = _app(tmp_path)
    bot, api, _ = _bot(tmp_path, app, read_only=True)
    settings.save(tmp_path / "state", "telegram_ui", "menu", app.sizing)
    await bot.handle(press("m_control"))
    assert "Read-only" in api.edits[-1][2]


# ------------------------------------------------------------------------------------------------ the buttons are real
async def test_every_arcus_button_reaches_a_command(tmp_path: Path) -> None:
    app = _app(tmp_path)
    bot, _api, _ = _bot(tmp_path, app)
    read, write = bot._tables()
    arcus = {cmd for g in menu.GROUPS for _l, cmd in menu.ITEMS["arcus"][g]}
    arcus |= {menu.LIVE["arcus"], "m_start", "m_control", "m_money", "m_settings", "venue", "home", "set"}
    assert arcus <= set(read) | set(write), arcus - set(read) - set(write)


def test_lighter_buttons_are_commands_of_its_panel() -> None:
    embed = pytest.importorskip("lighter_bot.telegram.embed")
    names = {cmd for g in menu.GROUPS for _l, cmd in menu.ITEMS["lighter"][g]} | {menu.LIVE["lighter"]}
    assert names <= set(embed.COMMANDS), names - set(embed.COMMANDS)


def test_arbitrage_buttons_are_commands_of_its_telegram_part() -> None:
    tg = pytest.importorskip("arbitrage.telegram")
    handled_by_c_arb = ("start", "stop")                      # confirm button and typed code live in the bot itself
    for g in menu.GROUPS:
        for _label, cmd in menu.ITEMS["arb"][g]:
            if cmd.split()[0] not in handled_by_c_arb:
                assert tg.to_argv("/" + cmd, False) is not None, cmd
    assert tg.to_argv("/" + menu.LIVE["arb"], False) is not None


def test_callback_data_fits_telegrams_limit_and_labels_are_short() -> None:
    for v in menu.VENUES:
        for kb in [menu.home_keyboard(v), *(menu.screen_keyboard(v, g) for g in menu.GROUPS)]:
            for row in kb:
                assert 1 <= len(row) <= 3
                for label, data in row:
                    assert len(data.encode()) <= 64 and len(label) <= 24, (label, data)
    assert others.part_of("l_dashboard") == ("l", "dashboard") and others.part_of("arb_scan") == ("arb", "scan")


def test_arb_start_live_button_only_when_live_is_allowed() -> None:
    assert "arb_start live" not in datas(menu.screen_keyboard("arb", "start", live_allowed=False))
    assert "arb_start live" in datas(menu.screen_keyboard("arb", "start", live_allowed=True))
    assert "arb_start 120 120" in datas(menu.screen_keyboard("arb", "start"))
