"""Order-budget governor (P2 task 4, A6.8).

Arcus per-subaccount pools: cap = 20,000 + lifetime fill notional / $0.10 (order pool; cancel pool starts at
40,000). The governor keeps "order actions per filled dollar" below 8 and escalates:
  pool < 20% of cap, or ratio above target after warm-up  -> WIDE (double requote hysteresis)
  pool < 5% of cap                                         -> CANCELS_ONLY (freeze requotes)
Lighter standard: 60 sendTx per minute (conservatively shared with REST reads), design caps of <= 10 pending
and <= 30 active orders per market; any 429/405 freezes requotes for the 60 s firewall cooldown.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum

from bot.venues.base import Venue


class BudgetMode(StrEnum):
    NORMAL = "normal"
    WIDE = "wide"
    CANCELS_ONLY = "cancels_only"


ActionKind = str  # "place" | "modify" | "cancel"


@dataclass
class ArcusGovernor:
    target_actions_per_usd: float = 8.0
    widen_frac: float = 0.20
    freeze_frac: float = 0.05
    warmup_actions: int = 200
    window_s: float = 6 * 3600
    order_remaining: int | None = None
    order_cap: int | None = None
    cancel_remaining: int | None = None
    cancel_cap: int | None = None
    _actions: deque[tuple[float, int]] = field(default_factory=deque)
    _filled: deque[tuple[float, float]] = field(default_factory=deque)
    total_actions: int = 0
    total_filled_usd: float = 0.0

    def update_pool(self, order_remaining: int | None, order_cap: int | None, cancel_remaining: int | None,
                    cancel_cap: int | None) -> None:
        if order_remaining is not None:
            self.order_remaining = order_remaining
        if order_cap is not None:
            self.order_cap = order_cap
        if cancel_remaining is not None:
            self.cancel_remaining = cancel_remaining
        if cancel_cap is not None:
            self.cancel_cap = cancel_cap

    def _trim(self, now: float) -> None:
        cut = now - self.window_s
        while self._actions and self._actions[0][0] < cut:
            self._actions.popleft()
        while self._filled and self._filled[0][0] < cut:
            self._filled.popleft()

    def record_actions(self, n: int, kind: ActionKind = "place", now: float | None = None) -> None:
        t = time.monotonic() if now is None else now
        if kind != "cancel":
            self._actions.append((t, n))
            self.total_actions += n
        if kind == "cancel" and self.cancel_remaining is not None:
            self.cancel_remaining -= n
        elif kind != "cancel" and self.order_remaining is not None:
            self.order_remaining -= n

    def record_fill(self, notional_usd: float, now: float | None = None) -> None:
        t = time.monotonic() if now is None else now
        self._filled.append((t, notional_usd))
        self.total_filled_usd += notional_usd
        # Every $0.10 filled adds one unit to both pools (never decays).
        units = int(notional_usd / 0.10)
        for attr in ("order_remaining", "order_cap", "cancel_remaining", "cancel_cap"):
            v = getattr(self, attr)
            if v is not None:
                setattr(self, attr, v + units)

    def actions_per_filled_usd(self, now: float | None = None) -> float:
        t = time.monotonic() if now is None else now
        self._trim(t)
        a = sum(n for _, n in self._actions)
        f = sum(x for _, x in self._filled)
        return a / f if f > 0 else float("inf") if a else 0.0

    def mode(self, now: float | None = None) -> BudgetMode:
        if self.order_cap and self.order_remaining is not None:
            frac = self.order_remaining / self.order_cap
            if frac < self.freeze_frac:
                return BudgetMode.CANCELS_ONLY
            if frac < self.widen_frac:
                return BudgetMode.WIDE
        if self.total_actions >= self.warmup_actions and \
                self.actions_per_filled_usd(now) > self.target_actions_per_usd:
            return BudgetMode.WIDE
        return BudgetMode.NORMAL

    def can_act(self, kind: ActionKind, n: int = 1, now: float | None = None) -> bool:
        if kind == "cancel":
            return True  # never block a cancel; an empty pool still drips 1 action / 10 s
        m = self.mode(now)
        if m is BudgetMode.CANCELS_ONLY:
            return False
        return self.order_remaining is None or self.order_remaining >= n

    def hysteresis_mult(self, now: float | None = None) -> float:
        return {BudgetMode.NORMAL: 1.0, BudgetMode.WIDE: 2.0, BudgetMode.CANCELS_ONLY: float("inf")}[self.mode(now)]


@dataclass
class LighterGovernor:
    sendtx_per_min: int = 60
    pending_per_market: int = 10
    active_per_market: int = 30
    freeze_s: float = 60.0
    _sent: deque[float] = field(default_factory=deque)
    _frozen_until: float = 0.0
    limit_hits: int = 0

    def _trim(self, now: float) -> None:
        while self._sent and self._sent[0] < now - 60:
            self._sent.popleft()

    def remaining(self, now: float | None = None) -> int:
        t = time.monotonic() if now is None else now
        self._trim(t)
        return max(0, self.sendtx_per_min - len(self._sent))

    def record_tx(self, n: int = 1, now: float | None = None) -> None:
        t = time.monotonic() if now is None else now
        self._sent.extend([t] * n)

    def on_rate_limited(self, now: float | None = None) -> None:
        t = time.monotonic() if now is None else now
        self._frozen_until = t + self.freeze_s
        self.limit_hits += 1

    def mode(self, now: float | None = None) -> BudgetMode:
        t = time.monotonic() if now is None else now
        if t < self._frozen_until:
            return BudgetMode.CANCELS_ONLY
        if self.remaining(t) < self.sendtx_per_min * 0.2:
            return BudgetMode.WIDE
        return BudgetMode.NORMAL

    def can_act(self, kind: ActionKind, n: int = 1, *, pending_in_market: int = 0, active_in_market: int = 0,
                now: float | None = None) -> bool:
        t = time.monotonic() if now is None else now
        # sendTxBatch counts as one request against the per-minute limit, but we still keep headroom for cancels.
        if kind == "cancel":
            return self.remaining(t) >= 1
        if self.mode(t) is BudgetMode.CANCELS_ONLY:
            return False
        if kind == "place" and (pending_in_market + n > self.pending_per_market
                                or active_in_market + n > self.active_per_market):
            return False
        return self.remaining(t) >= 2  # always keep one slot for an emergency cancel

    def hysteresis_mult(self, now: float | None = None) -> float:
        return {BudgetMode.NORMAL: 1.0, BudgetMode.WIDE: 2.0, BudgetMode.CANCELS_ONLY: float("inf")}[self.mode(now)]


@dataclass
class BudgetGovernor:
    arcus: dict[int, ArcusGovernor] = field(default_factory=dict)  # per subaccount
    lighter: LighterGovernor = field(default_factory=LighterGovernor)

    def for_arcus(self, account_index: int) -> ArcusGovernor:
        return self.arcus.setdefault(account_index, ArcusGovernor())

    def mode(self, venue: Venue, account_index: int = 0) -> BudgetMode:
        return self.for_arcus(account_index).mode() if venue is Venue.ARCUS else self.lighter.mode()

    def hysteresis_mult(self, venue: Venue, account_index: int = 0) -> float:
        return (self.for_arcus(account_index).hysteresis_mult() if venue is Venue.ARCUS
                else self.lighter.hysteresis_mult())

    def state(self) -> dict[str, object]:
        return {
            "arcus": {i: {"mode": g.mode().value, "order_remaining": g.order_remaining, "order_cap": g.order_cap,
                          "actions_per_filled_usd": round(g.actions_per_filled_usd(), 2)} for i, g in self.arcus.items()},
            "lighter": {"mode": self.lighter.mode().value, "sendtx_remaining": self.lighter.remaining(),
                        "limit_hits": self.lighter.limit_hits},
        }
