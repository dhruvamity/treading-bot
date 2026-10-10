"""Is a process still running? `os.kill(pid, 0)` alone says yes for a zombie: a child that has exited but was never
reaped by the process that started it. The Telegram process starts the guardian and the runs (subprocess.Popen) and
lives on, so a guardian that stood down stayed "running" in `tbot status` for as long as Telegram ran (2026-10-03)."""

from __future__ import annotations

import os
import subprocess


def pid_alive(pid: int) -> bool:
    """True if `pid` is a live process on this host. Reaps it first when it is this process's own exited child."""
    if pid <= 0:
        return False
    try:
        done, _ = os.waitpid(pid, os.WNOHANG)
        if done == pid:
            return False          # our child, exited: now reaped
        if done == 0:
            return True           # our child, still running
    except ChildProcessError:
        pass                      # someone else's child
    except OSError:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True               # exists, owned by another user
    try:                          # exists: a zombie (state Z) waiting for its parent is not running
        state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True,
                               timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return True
    return not state.startswith("Z")
