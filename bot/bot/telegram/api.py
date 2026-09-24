"""Minimal async Telegram Bot API client: long polling, HTML messages, inline keyboards.

Only what the operator bot needs. The token is part of every URL, so URLs and raw exception text are never logged;
message text passes through the log redactor before it leaves the process.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any

import aiohttp

from bot.common.logging import Log, redact_str

log = Log("telegram")
MAX_LEN = 3900  # Telegram's limit is 4096 characters; leave room for closing tags

Keyboard = list[list[tuple[str, str]]]  # rows of (label, callback_data)


class TelegramError(Exception):
    pass


def inline_keyboard(rows: Keyboard) -> dict[str, Any]:
    return {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row] for row in rows]}


def split_html(text: str, limit: int = MAX_LEN) -> list[str]:
    """Split on line boundaries; a <pre> block cut in two is closed and reopened so every chunk is valid HTML."""
    pieces: list[str] = []
    for line in text.split("\n"):
        while len(line) > limit:  # one enormous line: hard cut
            pieces.append(line[:limit])
            line = line[limit:]
        pieces.append(line)
    chunks: list[str] = []
    cur = ""
    for line in pieces:
        if cur and len(cur) + 1 + len(line) > limit:
            chunks.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur or not chunks:
        chunks.append(cur)
    out = []
    carry = False
    for c in chunks:
        if carry:
            c = "<pre>" + c
        opened = c.count("<pre>") > c.count("</pre>")
        if opened:
            c += "</pre>"
        carry = opened
        out.append(c)
    return out or [""]


class TelegramAPI:
    def __init__(self, token: str, *, base_url: str = "https://api.telegram.org", min_gap_s: float = 1.05) -> None:
        self.token = token
        self.base_url = base_url.rstrip("/")
        self.min_gap_s = min_gap_s  # Telegram allows ~1 message/s per chat
        self._session: aiohttp.ClientSession | None = None
        self._last_send: dict[str, float] = {}
        self._pace_lock = asyncio.Lock()

    async def _sess(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=75))
        return self._session

    async def call(self, method: str, **params: Any) -> Any:
        url = f"{self.base_url}/bot{self.token}/{method}"
        payload = {k: v for k, v in params.items() if v is not None}
        for attempt in range(6):
            try:
                s = await self._sess()
                async with s.post(url, json=payload) as r:
                    data = await r.json(content_type=None)
            except (aiohttp.ClientError, TimeoutError, ValueError) as e:
                log.warning("telegram_network", reason=f"{method}: {type(e).__name__}")
                await asyncio.sleep(min(30.0, 2.0 ** attempt))
                continue
            if isinstance(data, dict) and data.get("ok"):
                return data.get("result")
            code = int((data or {}).get("error_code") or 0) if isinstance(data, dict) else 0
            desc = str((data or {}).get("description", "")) if isinstance(data, dict) else ""
            if code == 429:
                wait = float(((data or {}).get("parameters") or {}).get("retry_after", 2))
                await asyncio.sleep(wait)
                continue
            if 500 <= code < 600:
                await asyncio.sleep(min(30.0, 2.0 ** attempt))
                continue
            raise TelegramError(f"{method}: {code} {desc}")
        raise TelegramError(f"{method}: gave up after retries")

    async def _pace(self, chat_id: int | str) -> None:
        async with self._pace_lock:
            k = str(chat_id)
            wait = self._last_send.get(k, 0.0) + self.min_gap_s - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_send[k] = time.monotonic()

    async def get_updates(self, offset: int | None, timeout: int = 50) -> list[dict[str, Any]]:
        res = await self.call("getUpdates", offset=offset, timeout=timeout,
                              allowed_updates=["message", "callback_query"])
        return list(res or [])

    async def send(self, chat_id: int | str, text: str, *, keyboard: Keyboard | None = None,
                   silent: bool = False) -> dict[str, Any] | None:
        chunks = split_html(redact_str(text))
        last: dict[str, Any] | None = None
        for i, chunk in enumerate(chunks):
            await self._pace(chat_id)
            last = await self.call("sendMessage", chat_id=chat_id, text=chunk, parse_mode="HTML",
                                   disable_web_page_preview=True, disable_notification=silent,
                                   reply_markup=inline_keyboard(keyboard) if keyboard and i == len(chunks) - 1 else None)
        return last

    async def edit(self, chat_id: int | str, message_id: int, text: str, *, keyboard: Keyboard | None = None) -> None:
        text = split_html(redact_str(text))[0]
        try:
            await self.call("editMessageText", chat_id=chat_id, message_id=message_id, text=text, parse_mode="HTML",
                            disable_web_page_preview=True,
                            reply_markup=inline_keyboard(keyboard) if keyboard else None)
        except TelegramError as e:
            if "not modified" not in str(e):
                raise

    async def answer(self, callback_id: str, text: str = "") -> None:
        with contextlib.suppress(TelegramError):  # an expired button press is harmless
            await self.call("answerCallbackQuery", callback_query_id=callback_id, text=text[:190] or None)

    async def set_commands(self, commands: list[tuple[str, str]]) -> None:
        await self.call("setMyCommands", commands=[{"command": c, "description": d} for c, d in commands])

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
