"""Lighter auth tokens for private REST reads and account WebSocket channels.

Tokens from the API-key signer: `{expiry_unix}:{account_index}:{api_key_index}:{random_hex}`, max 8 h; we use
7 h and refresh 10 min before expiry. A read-only token (`ro:...`, up to 10 years) can be configured instead for
processes that must not be able to sign (guardian reads, recorder); it cannot place or cancel.
"""

from __future__ import annotations

import time
from typing import Protocol


class _TokenSigner(Protocol):
    def create_auth_token(self, deadline_unix_s: int) -> str: ...


class AuthTokenManager:
    def __init__(self, signer: _TokenSigner | None = None, *, readonly_token: str | None = None,
                 lifetime_s: int = 7 * 3600, refresh_before_s: int = 600) -> None:
        if signer is None and not readonly_token:
            raise ValueError("need a signer or a read-only token")
        self._signer = signer
        self._ro = readonly_token
        self._lifetime = min(lifetime_s, 8 * 3600)
        self._refresh_before = refresh_before_s
        self._token: str | None = None
        self._expiry = 0

    def token(self, now_s: int | None = None) -> str:
        if self._signer is None:
            assert self._ro
            return self._ro
        now = int(time.time()) if now_s is None else now_s
        if self._token is None or now >= self._expiry - self._refresh_before:
            self._expiry = now + self._lifetime
            self._token = self._signer.create_auth_token(self._expiry)
        return self._token

    @property
    def expiry(self) -> int:
        return self._expiry

    @property
    def readonly(self) -> bool:
        return self._signer is None
