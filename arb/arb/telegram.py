"""`arb telegram`: the same commands from the phone, through a Telegram bot of its own.

arb/.env: ARB_TELEGRAM_TOKEN (a new bot from @BotFather: two programs cannot read one bot's messages),
ARB_TELEGRAM_USERS (the Telegram user ids allowed to send commands, comma-separated; /whoami shows yours) and
ARB_TELEGRAM_CHAT_ID (where the running bot sends its alerts).

    /status  /scan  /plan SPY  /settings  /set max_hold_h 72  /hold 72  /minhold 24  /stop 2  /stop auto
    /close  /closenow  /pause  /resume  /skip CASHCAT  /unskip CASHCAT

Each command runs the same code as the command line and replies with what it printed.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
from typing import Any

import aiohttp

API = "https://api.telegram.org/bot{}/{}"
# /hold 72 -> set max_hold_h 72, and the other short forms
SHORT = {"hold": ["set", "max_hold_h"], "minhold": ["set", "min_hold_h"], "stop": ["set", "stop_pct"],
         "closenow": ["close", "--now"]}
ALLOWED = ("status", "scan", "plan", "settings", "set", "close", "pause", "resume", "skip", "unskip", "feeds")


def to_argv(text: str, live: bool) -> list[str] | None:
    """The command-line arguments a message stands for, or None when it is not one of ours."""
    parts = text.strip().split()
    if not parts or not parts[0].startswith("/"):
        return None
    name = parts[0][1:].split("@")[0].lower()
    argv = SHORT[name] + parts[1:] if name in SHORT else [name, *parts[1:]]
    if argv[0] not in ALLOWED:
        return None
    if live and argv[0] in ("status", "close", "pause", "resume"):
        argv.append("--live")
    return argv


async def run_command(argv: list[str]) -> str:
    """Run one command as the command line would, in a thread, and return what it printed."""
    from arb import cli

    def call() -> str:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.suppress(SystemExit):
            cli.main(argv)
        return out.getvalue().strip() or "done"

    return await asyncio.to_thread(call)


async def serve(token: str, users: set[int], live: bool) -> None:
    offset = 0
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=70)) as s:

        async def say(chat: int, text: str) -> None:
            for i in range(0, len(text), 3800):
                await s.post(API.format(token, "sendMessage"), json={"chat_id": chat, "text": text[i:i + 3800]})

        while True:
            try:
                async with s.get(API.format(token, "getUpdates"), params={"timeout": 50, "offset": offset}) as r:
                    body: dict[str, Any] = await r.json(content_type=None)
            except (aiohttp.ClientError, TimeoutError, OSError):
                await asyncio.sleep(5)
                continue
            for u in body.get("result") or []:
                offset = max(offset, int(u["update_id"]) + 1)
                msg = u.get("message") or {}
                text, chat = str(msg.get("text") or ""), (msg.get("chat") or {}).get("id")
                sender = (msg.get("from") or {}).get("id")
                if chat is None:
                    continue
                if text.strip().lower().startswith("/whoami"):
                    await say(chat, f"your id is {sender}; this chat is {chat}")
                    continue
                if sender not in users:
                    continue                      # not the owner: no answer at all
                argv = to_argv(text, live)
                if argv is None:
                    await say(chat, (__doc__ or "").split("\n\n")[2])
                    continue
                try:
                    await say(chat, await run_command(argv))
                except Exception as e:  # noqa: BLE001  one bad command must not stop the listener
                    await say(chat, f"{type(e).__name__}: {e}")
