"""Typed errors.

Venue rejections map to `OrderRejected(reason=...)` with the venue's own reason code kept verbatim, so the
decision log and risk engine can branch on it (e.g. SELF_TRADE and GEO_RESTRICTED are CRIT stops).
"""

from __future__ import annotations


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
CRITICAL_REJECTS = frozenset({"SELF_TRADE", "GEO_RESTRICTED"})
