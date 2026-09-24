"""Per-API-key nonces for Lighter RH.

Mode `time_skip` (default): SkipNonce attribute on, nonce = max(now_ms, last + 1), persisted after every
allocation so a restart never reuses one. Mode `sequential`: seeded from GET /api/v1/nextNonce, +1 per tx, and
hard-refreshed on an invalid-nonce error (costs REST budget).
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

MAX_SKIP_NONCE = 2**47 - 1


class NonceManager:
    def __init__(self, state_path: Path | str | None = None, *, mode: str = "time_skip", seed: int = 0) -> None:
        if mode not in ("time_skip", "sequential"):
            raise ValueError("mode must be time_skip or sequential")
        self.mode = mode
        self.path = Path(state_path) if state_path else None
        self._lock = threading.Lock()
        self._last = max(seed, self._read())

    def _read(self) -> int:
        if self.path and self.path.exists():
            try:
                return int(self.path.read_text().strip() or 0)
            except ValueError:
                return 0
        return 0

    def _write(self, v: int) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(str(v))
        os.replace(tmp, self.path)

    @property
    def last(self) -> int:
        return self._last

    def next(self, now_ms: int | None = None) -> int:
        with self._lock:
            if self.mode == "time_skip":
                n = max(now_ms if now_ms is not None else int(time.time() * 1000), self._last + 1)
                if n >= MAX_SKIP_NONCE:
                    raise OverflowError("nonce exceeds 2^47-1")
            else:
                n = self._last + 1
            self._last = n
            self._write(n)
            return n

    def reserve(self, k: int, now_ms: int | None = None) -> list[int]:
        """k strictly increasing nonces (a sendTxBatch must carry increasing nonces)."""
        return [self.next(now_ms) for _ in range(k)]

    def resync(self, next_nonce_from_api: int) -> None:
        """Sequential mode: the API's next nonce is the one to use next."""
        with self._lock:
            self._last = max(self._last if self.mode == "time_skip" else 0, next_nonce_from_api - 1)
            self._write(self._last)
