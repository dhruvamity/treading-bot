"""The Lighter controls as a panel of the trading bot's ONE Telegram bot (treading-bot/bot, `bot telegram`).

There is no Lighter Telegram bot of its own any more: the one bot receives every message and hands this panel the
Lighter ones. So that nothing can be mistaken for an Arcus command:

- a Lighter command is `/l_<name>` (`/l_status`, `/l_run SPY smart +1`, `/l_closeall`); `/l <name>` works too;
- a Lighter button comes back as `l <data>`;
- everything this panel sends says LIGHTER first, and the commands it mentions are written as `/l_<name>`, so
  tapping one comes back here and never to the Arcus command of the same name.

The one bot checks who may send commands; this panel never polls Telegram.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import replace
from typing import Any

from lbot.config import Config
from lbot.log import Log
from lbot.telegram.api import Api
from lbot.telegram.bot import Bot

log = Log("telegram")
PREFIX = "l"
LABEL = "<b>LIGHTER</b> · "
# every command the Lighter panel answers (lbot/telegram/bot.py: on_text)
COMMANDS = ("top3", "mostvolume", "cheapest", "maxvolume", "run", "status", "openpositions", "positions", "orders",
            "dashboard", "pauseneworders", "pause", "unpause", "stop", "closeall", "resumeaftersl", "balance",
            "account", "settings", "set", "auto", "scannow", "help", "menu", "start")
# what a read-only bot still answers: nothing here starts, stops or changes anything
READ_COMMANDS = frozenset(("top3", "mostvolume", "cheapest", "maxvolume", "status", "openpositions", "positions",
                           "orders", "dashboard", "balance", "account", "settings", "help", "menu", "start"))
READ_BUTTONS = ("list:", "status", "dash", "nop")
_CMD = re.compile(r"(?<![\w/<])/(" + "|".join(sorted(COMMANDS, key=len, reverse=True)) + r")\b")


def relabel(text: str) -> str:
    """A Lighter message as the one bot sends it: LIGHTER first, its commands as /l_<name>."""
    return LABEL + _CMD.sub(lambda m: f"/{PREFIX}_{m.group(1)}", text)


def reroute(markup: dict[str, Any] | None) -> dict[str, Any] | None:
    """The same keyboard with every button's data behind `l `, so the one bot hands the press back to this panel."""
    if not markup:
        return markup
    return {"inline_keyboard": [[{**btn, "callback_data": f"{PREFIX} {btn['callback_data']}"[:64]} for btn in row]
                                for row in markup.get("inline_keyboard", [])]}


class PanelApi(Api):
    async def send(self, chat: str | int, text: str, markup: dict[str, Any] | None = None) -> Any:
        return await super().send(chat, relabel(text), reroute(markup))

    async def edit(self, chat: str | int, msg_id: int, text: str, markup: dict[str, Any] | None = None) -> Any:
        return await super().edit(chat, msg_id, relabel(text), reroute(markup))


class Panel:
    def __init__(self, cfg: Config, token: str, chat: int | str, users: set[int] | None = None,
                 api: Api | None = None) -> None:
        self.api = api or PanelApi(token)
        self.bot = Bot(replace(cfg, telegram_token=token, telegram_chat=str(chat),
                               telegram_users=tuple(sorted(users or ()))), self.api)

    def waits_for_code(self, chat: int) -> bool:
        """True while a LIVE start waits for its code to be typed back in this chat."""
        return chat in self.bot.pending

    async def text(self, chat: int, user: int, text: str, *, read_only: bool = False) -> None:
        """One message for Lighter: `/status`, `/run SPY smart +1`, or the digits of a code."""
        name = text.strip()[1:].split(" ")[0].split("@")[0].lower() if text.strip().startswith("/") else ""
        if read_only and name not in READ_COMMANDS:
            await self.api.send(chat, "🔒 <b>Read-only</b>\n\n<code>Controls are off on this bot</code>")
            return
        await self.bot.on_text(chat, user, text)

    async def button(self, chat: int, msg_id: int, data: str, *, read_only: bool = False) -> str:
        """One button press; returns the short note to show on the button ("" = none)."""
        if read_only and not data.startswith(READ_BUTTONS):
            return "read-only: controls are off on this bot"
        return _CMD.sub(lambda m: f"/{PREFIX}_{m.group(1)}", await self.bot.on_button(chat, msg_id, data))

    async def run(self) -> None:
        """The panel's own loops: the live dashboard message and the alerts of Lighter runs. Never returns; one
        failing loop is started again, so a Lighter problem cannot stop the bot it lives in."""
        async def keep(name: str, loop: Any) -> None:
            while True:
                try:
                    await loop()
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    log.error("panel_loop_failed", loop=name, err=f"{type(e).__name__}: {e}")
                    await asyncio.sleep(10)
        await asyncio.gather(keep("dashboard", self.bot.refresh), keep("alerts", self.bot.alerts))

    async def close(self) -> None:
        await self.api.close()
