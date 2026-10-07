"""Lighter and the funding arbitrage inside this ONE Telegram bot.

The trading bot is one bot with three parts: Arcus market making (this package), Lighter market making
(treading-bot/lighter, package `lighter`) and the funding arbitrage between the two (treading-bot/arbitrage, package `arb`).
`make install` puts all three into this one environment, and this one Telegram bot controls all of them:

    /status, /run, /closeall ...          Arcus, as always
    /l_status, /l_run, /l_closeall ...    the same commands for Lighter (`/l status` works too; /l lists them)
    /arb_status, /arb_scan, /arb_hold 72  the funding arbitrage (/arb lists them)

Every Lighter message starts with LIGHTER and every arbitrage message with FUNDING ARB, so a reply can never be
taken for another part's. A part that is not installed, or fails to load, says so when its command is sent; the
rest of the bot is not affected.
"""

from __future__ import annotations

from html import escape
from typing import Any

from arcus.common.logging import Log
from arcus.common.tgfmt import card, codes

log = Log("telegram_parts")
LIGHTER, ARB = "l", "arb"
INSTALL_HINT = "In treading-bot/arcus: make install, then arcus down and arcus up"


def part_of(cmd: str) -> tuple[str, str] | None:
    """("l" | "arb", the command inside that part) for /l, /l_status, /lighter, /arb, /arb_hold ...; else None.
    "" means the part's own menu."""
    for part, names in ((LIGHTER, ("l", "lighter")), (ARB, ("arb", "arbitrage"))):
        if cmd in names:
            return part, ""
        for n in names:
            if cmd.startswith(n + "_"):
                return part, cmd[len(n) + 1:]
    return None


class Lighter:
    """The Lighter panel (lighter_bot/telegram/embed.py), loaded the first time it is needed."""

    def __init__(self, token: str, chat: int, users: set[int]) -> None:
        self.token, self.chat, self.users = token, chat, users
        self.panel: Any = None
        self.why = ""

    def load(self) -> Any:
        if self.panel is None:
            try:
                from lighter_bot.config import load
                from lighter_bot.telegram.embed import Panel

                self.panel = Panel(load(), self.token, self.chat, self.users)
                self.why = ""
            except Exception as e:   # not installed, or its config does not load: this bot keeps working
                self.why = f"{type(e).__name__}: {e}"
                log.warning("lighter_unavailable", reason=self.why[:200])
        return self.panel

    def missing(self) -> str:
        return card("⚠️", "LIGHTER IS NOT AVAILABLE", codes(self.why[:300] or "not installed"), codes(INSTALL_HINT))

    def waits_for_code(self, chat: int) -> bool:
        return self.panel is not None and bool(self.panel.waits_for_code(chat))


class Arb:
    """The funding arbitrage's commands (arbitrage/telegram.py) and its background executor (arbitrage/ops.py)."""

    def __init__(self) -> None:
        self.why = ""

    def load(self) -> tuple[Any, Any] | None:
        try:
            from arbitrage import ops, telegram

            self.why = ""
            return telegram, ops
        except Exception as e:
            self.why = f"{type(e).__name__}: {e}"
            log.warning("arb_unavailable", reason=self.why[:200])
            return None

    def missing(self) -> str:
        return card("⚠️", "FUNDING ARB IS NOT AVAILABLE", codes(self.why[:300] or "not installed"), codes(INSTALL_HINT))

    @staticmethod
    def live_allowed() -> bool:
        from arbitrage.config import read_env

        return str(read_env().get("ARB_LIVE", "")).strip() == "1"


def arb_card(title: str, out: str) -> str:
    """What an arbitrage command printed, as one message."""
    out = out.strip() or "done"
    if len(out) > 3400:
        out = out[:3400].rsplit("\n", 1)[0] + "\n…"
    return card("⚖️", f"FUNDING ARB · {title.upper()}", f"<pre>{escape(out)}</pre>")


def arb_menu(mode: str, running: dict[str, int | None], live_allowed: bool) -> str:
    state = [f"{m.upper()}: {'running' if running.get(m) else 'not running'}" for m in ("paper", "live")]
    return card(
        "⚖️", "FUNDING ARB",
        codes(*state, f"Commands act on: {mode.upper()}", "LIVE allowed" if live_allowed else
              "LIVE off (ARB_LIVE=1 in arcus/.env allows it)"),
        "\n".join(("/arb_status · /arb_scan · /arb_settings · /arb_feeds",
                   "/arb_plan SPY",
                   "/arb_hold 72 · /arb_minhold 24 · /arb_sl 2 · /arb_sl auto",
                   "/arb_set name value",
                   "/arb_pause · /arb_resume · /arb_skip CASHCAT · /arb_unskip CASHCAT",
                   "/arb_close · /arb_closenow",
                   "/arb_start 120 120 (paper) · /arb_start live · /arb_stop")),
        codes("Add paper or live to pick one: /arb_status live"))


def arb_keyboard() -> list[list[tuple[str, str]]]:
    return [[("📊 Status", "arb_status"), ("🔎 Scan", "arb_scan")],
            [("⚙️ Settings", "arb_settings"), ("☰ Menu", "menu")]]
