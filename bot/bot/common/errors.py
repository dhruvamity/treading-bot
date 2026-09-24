"""Typed errors and retry policy.

Venue rejections map to `OrderRejected(reason=...)` with the venue's own reason code kept verbatim, so the
decision log and risk engine can branch on it (e.g. SELF_TRADE and GEO_RESTRICTED are CRIT stops).
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

T = TypeVar("T")


class BotError(Exception):
    pass


class ConfigError(BotError):
    pass


class SecretsError(BotError):
    pass


class LiveLockError(BotError):
    """A mainnet write was attempted without the triple lock open (prompt pack B4)."""


class VenueError(BotError):
    def __init__(self, venue: str, message: str, *, status: int | None = None, retryable: bool = False) -> None:
        super().__init__(f"[{venue}] {message}")
        self.venue = venue
        self.status = status
        self.retryable = retryable


class RateLimited(VenueError):
    def __init__(self, venue: str, message: str, *, reason: str | None, retry_after_ms: int) -> None:
        super().__init__(venue, message, status=429, retryable=True)
        self.reason = reason  # Arcus: None | ip | account_empty | account_partial
        self.retry_after_ms = retry_after_ms


class AuthError(VenueError):
    pass


class GeoRestricted(VenueError):
    pass


class TransmissionError(VenueError):
    """Arcus `Transmission`: validated but not delivered. Retry only after reconciliation."""


class OrderRejected(VenueError):
    def __init__(self, venue: str, reason: str, message: str = "", *, client_id: str | None = None) -> None:
        super().__init__(venue, f"{reason}: {message}".strip(": "), retryable=False)
        self.reason = reason
        self.client_id = client_id


class PreTradeReject(BotError):
    """Our own risk engine refused an order before it was sent."""

    def __init__(self, check: str, detail: str) -> None:
        super().__init__(f"{check}: {detail}")
        self.check = check
        self.detail = detail


# Reasons that must stop a venue immediately (prompt pack E7, last row).
CRITICAL_REJECTS = frozenset({"SELF_TRADE", "GEO_RESTRICTED", "canceled-self-trade"})

# Arcus engine rejection reasons (docs: create-api-key / RejectionReason schema).
ARCUS_REJECTIONS = frozenset(
    {
        "POST_ONLY_WOULD_CROSS", "SELF_TRADE", "UNDERCOLLATERALIZED", "COULD_NOT_FILL", "IOC_CANCELED",
        "FOK_FAILED", "REDUCE_ONLY_WOULD_INCREASE", "TOO_MANY_CLIENT_IDS", "DUPLICATE_CLIENT_ID",
        "POSITION_TPSL_ALREADY_EXISTS", "ENTRY_TPSL_CANNOT_BE_POSITION_TPSL", "OPEN_ORDER_CAP_EXCEEDED",
        "FILL_WILL_EXCEED_TRADING_BOUND", "OPEN_INTEREST_CAP_EXCEEDED", "ORDER_NOT_FOUND",
        "ORDER_NOT_FOUND_FOR_MODIFY", "MODIFY_CHANGED_IMMUTABLE_FIELD", "MODIFY_ZERO_SIZE",
        "POSITION_SIZE_CAP_EXCEEDED", "MODIFY_TPSL_NOT_SUPPORTED", "MODIFY_SUPERSEDED_BY_CANCEL",
        "MODIFY_SIZE_ALREADY_FILLED",
    }
)
ARCUS_ERROR_TYPES = frozenset(
    {"Tick", "InvalidRequest", "OracleDeviation", "ReduceOnly", "Unavailable", "Unauthorized", "Forbidden",
     "NotImplemented", "Transmission", "Internal"}
)


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    attempts: int = 4
    base_s: float = 0.25
    max_s: float = 8.0
    jitter: float = 0.3

    def delay(self, attempt: int) -> float:
        d = min(self.max_s, self.base_s * (2**attempt))
        return d * (1 + random.uniform(-self.jitter, self.jitter))


async def retry_async(
    fn: Callable[[], Awaitable[T]],
    *,
    policy: RetryPolicy = RetryPolicy(),
    retry_on: tuple[type[BaseException], ...] = (asyncio.TimeoutError, ConnectionError),
    is_idempotent: bool = True,
) -> T:
    """Bounded retry with exponential backoff and jitter.

    Non-idempotent writes are never retried here (B3.5): the caller must reconcile state first.
    RateLimited honours the venue's retry hint.
    """
    last: BaseException | None = None
    for attempt in range(policy.attempts if is_idempotent else 1):
        try:
            return await fn()
        except RateLimited as e:
            last = e
            await asyncio.sleep(max(e.retry_after_ms / 1000, policy.delay(attempt)))
        except VenueError as e:
            if not e.retryable:
                raise
            last = e
            await asyncio.sleep(policy.delay(attempt))
        except retry_on as e:
            last = e
            await asyncio.sleep(policy.delay(attempt))
    assert last is not None
    raise last
