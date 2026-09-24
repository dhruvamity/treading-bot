"""The live lock: what it takes for a real (mainnet) order to leave this machine.

A live run needs `--live` on the command line AND one owner approval:
  * interactive: the owner types LIVE after reading the start-up summary (the default), or
  * unattended (systemd, `--yes`): every session file being started has `live_enabled: true`.
On top of that, `bot doctor` must report no FAIL for those sessions (checked by the CLI before the lock).
Anything less is refused with the reason; a live request is never silently downgraded to paper. Venue REST clients
get `writes_allowed` from this module only, so no other code path can open a mainnet write.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from bot.common.errors import LiveLockError


class RunMode(StrEnum):
    PAPER = "paper"
    TESTNET = "testnet"
    LIVE = "live"


@dataclass(frozen=True, slots=True)
class LockState:
    cli_live: bool
    session_live_enabled: bool
    typed_confirmation: bool = False

    @property
    def open(self) -> bool:
        return self.cli_live and (self.typed_confirmation or self.session_live_enabled)

    def why_closed(self) -> list[str]:
        out = []
        if not self.cli_live:
            out.append("not started with --live")
        if not (self.typed_confirmation or self.session_live_enabled):
            out.append("no approval: type LIVE at the prompt, or for unattended runs set live_enabled: true in "
                       "every session file and pass --yes")
        return out


def lock_state(*, cli_live: bool, session_live_enabled: bool, typed_confirmation: bool = False) -> LockState:
    return LockState(cli_live=cli_live, session_live_enabled=session_live_enabled,
                     typed_confirmation=typed_confirmation)


def resolve_mode(requested: RunMode, lock: LockState) -> RunMode:
    """LIVE only when the lock is open; a refused LIVE request is an error, never a silent downgrade (the operator
    must know nothing live is running)."""
    if requested is RunMode.LIVE:
        if not lock.open:
            raise LiveLockError("live mode refused: " + "; ".join(lock.why_closed()))
        return RunMode.LIVE
    return requested


def mainnet_writes_allowed(mode: RunMode, lock: LockState) -> bool:
    return mode is RunMode.LIVE and lock.open
