"""Nonces and client order ids, both restart-safe without asking Lighter.

Nonces: every transaction carries SkipNonce, which lets a nonce jump as long as it rises (and stays below 2^47 - 1).
The nonce is the time in ms, or one more than the last one used, written to disk before it is used: a restart never
reuses one, and no `nextNonce` request (a share of the 60 a minute) is ever needed.

Client order ids (1 .. 2^48 - 1, unique over every market while the order lives): time in ms x 100 + a counter, also
persisted. They stay below 2^48 until 2058, and never collide with Lighter's own order indexes (2^48 and up), so the
bot cancels and modifies its orders by its own ids.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from lbot.venue import consts as C


class _Counter:
    def __init__(self, path: Path | None, scale: int, limit: int) -> None:
        self.path = path
        self.scale = scale
        self.limit = limit
        self._lock = threading.Lock()
        self._last = self._read()

    def _read(self) -> int:
        if self.path is None:
            return 0
        try:
            return int(self.path.read_text().strip() or 0)
        except (OSError, ValueError):
            return 0

    def _write(self, v: int) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(str(v))
        os.replace(tmp, self.path)

    def take(self, n: int = 1, now_ms: int | None = None) -> list[int]:
        """n strictly increasing values, persisted before they are handed out."""
        with self._lock:
            base = (int(time.time() * 1000) if now_ms is None else now_ms) * self.scale
            first = max(base, self._last + 1)
            last = first + n - 1
            if last >= self.limit:
                raise OverflowError("counter past its limit")
            self._last = last
            self._write(last)
            return list(range(first, last + 1))

    @property
    def last(self) -> int:
        return self._last


class Nonces(_Counter):
    """Per API key: state/nonce-<account>-<key>.txt."""

    def __init__(self, path: Path | None) -> None:
        super().__init__(path, 1, C.MAX_SKIP_NONCE)


class ClientIds(_Counter):
    def __init__(self, path: Path | None) -> None:
        super().__init__(path, 100, C.MAX_CLIENT_ORDER_INDEX)
