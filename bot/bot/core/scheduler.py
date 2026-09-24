"""Session scheduler (Tread-style duration / repeat / campaign time slots).

A session runs for `duration`, then its exit plan (maker unwind, IOC after T), then repeats up to `repeat` times.
Optional IST windows restrict when a session may be active (the owner's time-slot research plugs in here);
configured event kinds pause new quoting via the risk engine's event-window check.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from bot.common.config import SessionWindowCfg
from bot.common.time import US_PER_S
from bot.core.calendar import in_ist_windows


class SessionState(StrEnum):
    WAITING = "waiting"  # outside IST window
    RUNNING = "running"
    EXITING = "exiting"  # duration reached / SL / TP: running the exit plan
    DONE = "done"


@dataclass
class SessionClock:
    cfg: SessionWindowCfg
    state: SessionState = SessionState.WAITING
    run_index: int = 0
    started_us: int = 0
    exit_started_us: int = 0

    def update(self, now_us: int) -> SessionState:
        if self.state is SessionState.DONE:
            return self.state
        in_window = in_ist_windows(now_us, self.cfg.windows_ist)
        if self.state is SessionState.WAITING and in_window:
            self.state = SessionState.RUNNING
            self.started_us = now_us
            self.run_index += 1
        elif self.state is SessionState.RUNNING:
            if now_us - self.started_us >= self.cfg.duration_s() * US_PER_S or not in_window:
                self.begin_exit(now_us)
        return self.state

    def begin_exit(self, now_us: int) -> None:
        if self.state is SessionState.RUNNING:
            self.state = SessionState.EXITING
            self.exit_started_us = now_us

    def exit_done(self) -> SessionState:
        self.state = SessionState.DONE if self.run_index >= self.cfg.repeat else SessionState.WAITING
        return self.state

    def progress(self, now_us: int) -> float:
        """0..1 through the current run (drives the Tread bias path)."""
        if self.state is not SessionState.RUNNING:
            return 1.0 if self.state is SessionState.EXITING else 0.0
        return min(1.0, (now_us - self.started_us) / (self.cfg.duration_s() * US_PER_S))
