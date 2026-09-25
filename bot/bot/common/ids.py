"""Client order IDs.

Arcus clientId: charset [A-Za-z0-9_-], 1-36 chars, unique among live orders (docs: place-order).
Format (B3.4): `al{strategyCode}{sessionId base36}{seq base36}`.
"""

from __future__ import annotations

import re
import threading

_B36 = "0123456789abcdefghijklmnopqrstuvwxyz"
ARCUS_CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,36}$")

STRATEGY_CODES = {
    "mid": "m",
    "grid": "g",
    "rgrid": "r",
    "signal": "s",
    "anchor": "a",
    "manual": "x",
    "probe": "q",
    "guardian": "z",
}


def b36(n: int) -> str:
    if n < 0:
        raise ValueError("negative")
    if n == 0:
        return "0"
    out = []
    while n:
        n, r = divmod(n, 36)
        out.append(_B36[r])
    return "".join(reversed(out))


class ClientIdFactory:
    """Thread-safe, monotonic per session. `session_id` is a small integer (e.g. session start minute)."""

    def __init__(self, strategy: str, session_id: int, start_seq: int = 0) -> None:
        if strategy not in STRATEGY_CODES:
            raise ValueError(f"unknown strategy code for {strategy!r}")
        self.code = STRATEGY_CODES[strategy]
        self.session_id = session_id
        self._seq = start_seq
        self._lock = threading.Lock()

    def next_seq(self) -> int:
        with self._lock:
            self._seq += 1
            return self._seq

    def arcus(self, seq: int | None = None) -> str:
        s = self.next_seq() if seq is None else seq
        cid = f"al{self.code}{b36(self.session_id)}{b36(s)}"
        if not ARCUS_CLIENT_ID_RE.match(cid):
            raise ValueError(f"invalid Arcus clientId {cid!r}")
        return cid


def validate_arcus_client_id(cid: str) -> str:
    if not ARCUS_CLIENT_ID_RE.match(cid):
        raise ValueError(f"invalid Arcus clientId {cid!r}")
    return cid
