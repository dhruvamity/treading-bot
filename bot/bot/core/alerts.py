"""Alerts: INFO / WARN / CRIT to the log always, and to Telegram when TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID are
configured by the owner. Rate-limited per (level, key) so a flapping condition cannot spam; CRIT is never
suppressed for longer than 60 s. Message text passes through the log redactor first.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import IntEnum

import aiohttp

from bot.common.logging import Log, redact_str

log = Log("alerts")


class Level(IntEnum):
    INFO = 10
    WARN = 20
    CRIT = 30


@dataclass
class Alerter:
    bot_token: str | None = None
    chat_id: str | None = None
    min_interval_s: float = 30.0
    min_level_telegram: Level = Level.WARN
    prefix: str = "bot"
    _last: dict[str, float] = field(default_factory=dict)
    sent: list[tuple[Level, str, str]] = field(default_factory=list)  # kept for tests / daily report
    _session: aiohttp.ClientSession | None = None

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    def _allowed(self, level: Level, key: str) -> bool:
        interval = min(self.min_interval_s, 60.0) if level is Level.CRIT else self.min_interval_s
        now = time.monotonic()
        k = f"{level.name}:{key}"
        if now - self._last.get(k, -1e9) < interval:
            return False
        self._last[k] = now
        return True

    async def send(self, level: Level, key: str, text: str) -> bool:
        text = redact_str(text)
        if not self._allowed(level, key):
            return False
        self.sent.append((level, key, text))
        {Level.INFO: log.info, Level.WARN: log.warning, Level.CRIT: log.critical}[level](
            "alert", reason=key, data={"text": text})
        if self.telegram_enabled and level >= self.min_level_telegram:
            await self._telegram(f"[{self.prefix}][{level.name}] {text}")
        return True

    async def _telegram(self, text: str) -> None:
        try:
            if self._session is None or self._session.closed:
                self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            async with self._session.post(url, json={"chat_id": self.chat_id, "text": text[:4000]}) as r:
                if r.status != 200:
                    log.warning("telegram_failed", reason=f"HTTP {r.status}")
        except (TimeoutError, aiohttp.ClientError) as e:
            log.warning("telegram_failed", reason=type(e).__name__)

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    def info(self, key: str, text: str) -> asyncio.Task[bool]:
        return asyncio.ensure_future(self.send(Level.INFO, key, text))

    def warn(self, key: str, text: str) -> asyncio.Task[bool]:
        return asyncio.ensure_future(self.send(Level.WARN, key, text))

    def crit(self, key: str, text: str) -> asyncio.Task[bool]:
        return asyncio.ensure_future(self.send(Level.CRIT, key, text))


def alerter_from_secrets(store: object | None) -> Alerter:
    tok = chat = None
    if store is not None:
        tok = store.get("TELEGRAM_BOT_TOKEN")  # type: ignore[attr-defined]
        chat = store.get("TELEGRAM_CHAT_ID")  # type: ignore[attr-defined]
    return Alerter(bot_token=tok, chat_id=chat)
