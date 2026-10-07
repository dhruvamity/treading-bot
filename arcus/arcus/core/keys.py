"""API key expiry monitor (P1 task 2). Arcus keys carry `validUntil` (epoch ms, max 180 days); alert 72 h before
expiry and CRIT once expired. Rotation re-registers with the same apiWalletName (which revokes the old key) on the
owner's machine: `python scripts/arcus_register_key.py --account N --rotate`.
"""

from __future__ import annotations

from dataclasses import dataclass

WARN_BEFORE_MS = 72 * 3600 * 1000


@dataclass(frozen=True, slots=True)
class KeyStatus:
    name: str
    valid_until_ms: int
    remaining_h: float
    level: str  # ok | warn | expired


def key_status(name: str, valid_until_ms: int, now_ms: int, warn_before_ms: int = WARN_BEFORE_MS) -> KeyStatus:
    rem = valid_until_ms - now_ms
    level = "expired" if rem <= 0 else "warn" if rem <= warn_before_ms else "ok"
    return KeyStatus(name, valid_until_ms, rem / 3_600_000, level)


def check_keys(keys: dict[str, int], now_ms: int) -> list[KeyStatus]:
    return [key_status(n, v, now_ms) for n, v in sorted(keys.items())]
