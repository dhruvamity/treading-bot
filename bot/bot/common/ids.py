"""Client order IDs.

Arcus clientId: charset [A-Za-z0-9_-], 1-36 chars, unique among live orders (docs: place-order).
Format (B3.4): `al{strategyCode}{sessionId base36}{seq base36}`.

Lighter client_order_index: uint48, unique across all markets for the account. We derive it from the same
(session, seq) pair so one logical order has one identity on both representations.
"""

from __future__ import annotations

import re
import threading

_B36 = "0123456789abcdefghijklmnopqrstuvwxyz"
ARCUS_CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,36}$")
LIGHTER_COI_MAX = 2**48 - 1

STRATEGY_CODES = {
    "mid": "m",
    "grid": "g",
    "rgrid": "r",
    "dgrid": "d",
    "blend": "b",
    "signal": "s",
    "dn_hedged_mm": "h",
    "dn_carry": "c",
    "points_overlay": "p",
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

    def lighter(self, seq: int | None = None) -> int:
        """uint48: 24 bits of session, 24 bits of sequence (wraps sequence at 16.7M per session)."""
        s = self.next_seq() if seq is None else seq
        coi = ((self.session_id & 0xFFFFFF) << 24) | (s & 0xFFFFFF)
        if coi == 0:
            coi = 1  # 0 means "no client id" on Lighter
        assert 0 < coi <= LIGHTER_COI_MAX
        return coi


def validate_arcus_client_id(cid: str) -> str:
    if not ARCUS_CLIENT_ID_RE.match(cid):
        raise ValueError(f"invalid Arcus clientId {cid!r}")
    return cid
