"""Dead man's switch refresher (P2 task 6).

Arcus: POST /v1/scheduleCancel every 20 s with a 60 s absolute deadline (lead 5 s-5 min); auto-fires are capped
at 10 per UTC day per subaccount, so the guardian must also be able to cancel directly.
Two consecutive refresh failures -> safe mode (callback).
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from bot.common.logging import Log
from bot.common.time import utc_date_str

log = Log("dms")


@dataclass
class DeadMansSwitch:
    name: str
    arm: Callable[[int | None], Awaitable[None]]  # adapter.arm_dead_mans_switch
    refresh_s: float = 20.0
    deadline_s: float = 60.0
    max_fires_per_day: int = 10
    on_failure: Callable[[str], Awaitable[None] | None] | None = None
    now_us: Callable[[], int] = field(default=lambda: time.time_ns() // 1000)
    consecutive_failures: int = 0
    last_ok_us: int = 0
    last_deadline_us: int = 0
    fires_today: dict[str, int] = field(default_factory=dict)
    refreshes: int = 0
    _task: asyncio.Task[None] | None = None
    _stop: asyncio.Event = field(default_factory=asyncio.Event)

    async def refresh_once(self) -> bool:
        now = self.now_us()
        # If we are past the previous deadline without having refreshed, the venue has fired it.
        if self.last_deadline_us and now > self.last_deadline_us:
            day = utc_date_str(now)
            self.fires_today[day] = self.fires_today.get(day, 0) + 1
            log.warning("dms_presumed_fired", reason="refresh late past deadline", data={"name": self.name,
                                                                                        "fires_today": self.fires_today[day]})
        deadline = now + int(self.deadline_s * 1_000_000)
        try:
            await self.arm(deadline)
        except Exception as e:
            self.consecutive_failures += 1
            log.error("dms_refresh_failed", reason=type(e).__name__,
                      data={"name": self.name, "consecutive": self.consecutive_failures, "err": str(e)[:200]})
            if self.consecutive_failures >= 2 and self.on_failure is not None:
                r = self.on_failure(f"{self.name}: dead man's switch refresh failed twice ({type(e).__name__})")
                if asyncio.iscoroutine(r):
                    await r
            return False
        self.consecutive_failures = 0
        self.last_ok_us = now
        self.last_deadline_us = deadline
        self.refreshes += 1
        return True

    def fires_remaining_today(self) -> int:
        return self.max_fires_per_day - self.fires_today.get(utc_date_str(self.now_us()), 0)

    async def run(self) -> None:
        while not self._stop.is_set():
            await self.refresh_once()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=self.refresh_s)

    def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self.run(), name=f"dms-{self.name}")

    async def stop(self, *, disarm: bool = True) -> None:
        self._stop.set()
        if self._task:
            await self._task
        if disarm:
            try:
                await self.arm(None)
                self.last_deadline_us = 0
            except Exception as e:
                log.warning("dms_disarm_failed", reason=type(e).__name__)
