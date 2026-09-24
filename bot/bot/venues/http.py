"""Shared aiohttp session wrapper: timeouts, orjson, latency sampling, typed transport errors."""

from __future__ import annotations

from typing import Any

import aiohttp
import orjson

from bot.common.errors import VenueError
from bot.common.time import monotonic_us


class HttpClient:
    def __init__(self, venue: str, base_url: str, *, timeout_s: float = 10.0, user_agent: str = "bot/0.1") -> None:
        self.venue = venue
        self.base_url = base_url.rstrip("/")
        self._timeout = aiohttp.ClientTimeout(total=timeout_s)
        self._session: aiohttp.ClientSession | None = None
        self._ua = user_agent
        self.last_latency_us: int = 0
        self.errors = 0
        self.calls = 0

    async def session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout, headers={"User-Agent": self._ua},
                                                  json_serialize=lambda o: orjson.dumps(o).decode())
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        raw_body: bytes | None = None,
        form: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, Any, dict[str, str]]:
        """Returns (status, parsed_body_or_text, headers). Raises VenueError only on transport failure."""
        s = await self.session()
        url = self.base_url + path
        h = dict(headers or {})
        data: Any = None
        if raw_body is not None:
            data = raw_body
            h.setdefault("Content-Type", "application/json")
        elif json_body is not None:
            data = orjson.dumps(json_body)
            h.setdefault("Content-Type", "application/json")
        elif form is not None:
            data = aiohttp.FormData({k: str(v) for k, v in form.items()})
        t0 = monotonic_us()
        self.calls += 1
        try:
            async with s.request(method, url, params=_clean(params), data=data, headers=h) as r:
                raw = await r.read()
                self.last_latency_us = monotonic_us() - t0
                try:
                    body: Any = orjson.loads(raw) if raw else {}
                except orjson.JSONDecodeError:
                    body = raw.decode(errors="replace")
                if r.status >= 500:
                    self.errors += 1
                return r.status, body, dict(r.headers)
        except TimeoutError as e:
            self.errors += 1
            raise VenueError(self.venue, f"timeout {method} {path}", retryable=True) from e
        except aiohttp.ClientError as e:
            self.errors += 1
            raise VenueError(self.venue, f"transport {method} {path}: {type(e).__name__}", retryable=True) from e

    def error_rate(self) -> float:
        return self.errors / self.calls if self.calls else 0.0


def _clean(params: dict[str, Any] | None) -> dict[str, str] | None:
    if not params:
        return None
    out: dict[str, str] = {}
    for k, v in params.items():
        if v is None:
            continue
        out[k] = ("true" if v else "false") if isinstance(v, bool) else str(v)
    return out
