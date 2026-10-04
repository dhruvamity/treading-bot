"""A small Telegram Bot API client that only SENDS (the trading bot's one Telegram bot does the receiving), and the
message layout: one emoji and a bold title, a blank line, then short monospace lines in groups (Telegram HTML)."""

from __future__ import annotations

import asyncio
from html import escape
from typing import Any

import aiohttp

from lbot.log import Log

log = Log("tg")


def b(x: object) -> str:
    return f"<b>{escape(str(x))}</b>"


def code(x: object) -> str:
    return f"<code>{escape(str(x))}</code>"


def card(emoji: str, title: str, *blocks: str | list[str] | None) -> str:
    parts = [f"{emoji} {b(title)}" if emoji else b(title)]
    for bl in blocks:
        text = bl if isinstance(bl, str) else "\n".join(x for x in (bl or []) if x)
        if text:
            parts.append(text)
    return "\n\n".join(parts)


def lines(*xs: object) -> list[str]:
    return [code(x) for x in xs if x not in (None, "")]


def buttons(rows: list[list[tuple[str, str]]]) -> dict[str, Any]:
    return {"inline_keyboard": [[{"text": t, "callback_data": d[:64]} for t, d in row] for row in rows]}


class Api:
    def __init__(self, token: str) -> None:
        self.base = f"https://api.telegram.org/bot{token}"
        self._s: aiohttp.ClientSession | None = None

    async def s(self) -> aiohttp.ClientSession:
        if self._s is None or self._s.closed:
            self._s = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=40))
        return self._s

    async def call(self, method: str, **params: Any) -> Any:
        s = await self.s()
        for attempt in range(3):
            try:
                async with s.post(f"{self.base}/{method}", json={k: v for k, v in params.items() if v is not None}) as r:
                    d = await r.json(content_type=None)
                    if not d.get("ok"):
                        desc = d.get("description", "")
                        if "message is not modified" in desc:
                            return None
                        if r.status == 429:
                            await asyncio.sleep(float((d.get("parameters") or {}).get("retry_after", 3)))
                            continue
                        log.warn("tg_error", method=method, desc=desc[:200])
                        return None
                    return d.get("result")
            except (aiohttp.ClientError, TimeoutError) as e:
                if attempt == 2:
                    log.warn("tg_unreachable", method=method, err=str(e))
                await asyncio.sleep(2)
        return None

    async def send(self, chat: str | int, text: str, markup: dict[str, Any] | None = None) -> Any:
        return await self.call("sendMessage", chat_id=chat, text=text[:4000], parse_mode="HTML",
                               disable_web_page_preview=True, reply_markup=markup)

    async def edit(self, chat: str | int, msg_id: int, text: str, markup: dict[str, Any] | None = None) -> Any:
        return await self.call("editMessageText", chat_id=chat, message_id=msg_id, text=text[:4000], parse_mode="HTML",
                               disable_web_page_preview=True, reply_markup=markup)

    async def close(self) -> None:
        if self._s is not None:
            await self._s.close()
