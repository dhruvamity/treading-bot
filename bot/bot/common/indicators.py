"""Price indicators shared by the live Signal strategy and the scout's backtest of it (one copy, same numbers)."""

from __future__ import annotations


def rsi(closes: list[float], n: int = 14) -> float | None:
    """Wilder RSI over the last n+ closes."""
    if len(closes) < n + 1:
        return None
    gains = losses = 0.0
    for a, b in zip(closes[-n - 1:-1], closes[-n:], strict=True):
        d = b - a
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    if losses == 0:
        return 100.0 if gains > 0 else 50.0
    return 100 - 100 / (1 + gains / losses)


def ema(values: list[float], n: int) -> float | None:
    if len(values) < n:
        return None
    a = 2 / (n + 1)
    e = values[0]
    for v in values[1:]:
        e = a * v + (1 - a) * e
    return e
