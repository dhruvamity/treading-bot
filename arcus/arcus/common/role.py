"""What this machine is for: `BOT_ROLE` in .env. One machine can do everything, or the work is split over two.

    all        records, ranks, runs the Telegram bot and trades (the default: one machine)
    trader     the Telegram bot, the runs and their guardian. It records and ranks nothing: it keeps its own market
               list, and takes the lists from another machine when one is set (BOT_SYNC_FROM, arcus/handoff.py)
    recorder   records the market tape and nothing else: no keys, no Telegram, no runs (fits a 1 GB server)
    scout      records and ranks (scans, the playbook) for a trader to fetch; no Telegram, no runs

Why a trader starts no recorder: the recorder and the scans take the memory and the processor a small server needs
for the run. Why a recorder starts no Telegram bot: Telegram hands each message to one poller only, so two machines
polling the same bot would each see half of them.

The Lighter bot and the funding arbitrage read the same variable (lighter_bot/config.py, arbitrage/config.py): the
three packages do not import each other.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Mapping
from pathlib import Path

ENV = "BOT_ROLE"
ROLES = ("all", "trader", "recorder", "scout")
WHAT = {"all": "records, ranks, Telegram and trading", "trader": "Telegram and trading",
        "recorder": "records only", "scout": "records and ranks"}
SMALL_MB = 3000        # under this much memory a machine cannot record, rank and trade at once


def role(env: Mapping[str, str] | None = None) -> str:
    """The machine's role (default "all"). ValueError for a word that is not a role: a typing mistake must not
    quietly turn a recorder into a machine that trades."""
    v = str((os.environ if env is None else env).get(ENV) or "all").strip().lower()
    if v not in ROLES:
        raise ValueError(f"{ENV}={v!r} in .env: must be one of {', '.join(ROLES)}")
    return v


def write(r: str, path: Path | str = ".env") -> Path:
    """Put BOT_ROLE=r in the .env file (`tbot role trader`): the line that is there is replaced and a second one is
    dropped; a file without the line (an .env made before the roles existed) gets it at the end. Every other line is
    kept as it is, and the file stays private."""
    if r not in ROLES:
        raise ValueError(f"{r!r} is not a role: one of {', '.join(ROLES)}")
    p = Path(path)
    out, done = [], False
    for ln in p.read_text().splitlines() if p.exists() else []:
        if ln.split("=", 1)[0].strip() == ENV and not ln.lstrip().startswith("#"):
            if not done:
                out.append(f"{ENV}={r}")
            done = True
        else:
            out.append(ln)
    if not done:
        out.append(f"{ENV}={r}")
    if not p.exists():
        os.close(os.open(p, os.O_WRONLY | os.O_CREAT, 0o600))
    p.write_text("\n".join(out) + "\n")
    os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)
    return p


def memory_mb() -> float | None:
    """This machine's memory in MB (None where the system does not say)."""
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1e6
    except (ValueError, OSError, AttributeError):
        return None


def note(r: str, mem_mb: float | None = None) -> list[str]:
    """What `tbot up` says first: what this machine is for, and a warning when a small machine is about to do
    everything because nobody told it its role."""
    out = [f"this machine: {r} ({WHAT[r]}). `tbot role trader|recorder|scout|all` changes it"]
    if r == "all" and mem_mb is not None and mem_mb < SMALL_MB:
        out.append(f"WARNING: {mem_mb:,.0f} MB of memory and no BOT_ROLE: this machine is about to record and rank as "
                   "well as trade, which needs about 8 GB. On a small server run `tbot role trader` (the keys are "
                   "here; no recording) or `tbot role recorder` (no keys), then `tbot down` and `tbot up`.")
    return out


# What to say on a trader where a list, a scan or a playbook is asked for: it never makes one, so "wait for the scan"
# would be wrong advice (2026-10-11: a trader answered /top3 with "NO SCAN YET, on the server: tbot up")
TRADER_NO_LISTS = ("This machine is a trader: it makes no lists and no scans",
                   "Lists come from the machine that scans (BOT_SYNC_FROM in .env)",
                   "or from your own computer: tbot sync push USER@THIS-MACHINE",
                   "No list is needed for your own pick: /run SPY mid 0 max paper")


def is_trader(env: Mapping[str, str] | None = None) -> bool:
    """True on a trader machine; False for every other role and for a BOT_ROLE that is not a role."""
    try:
        return role(env) == "trader"
    except ValueError:
        return False


def records(r: str) -> bool:
    return r in ("all", "recorder", "scout")


def ranks(r: str) -> bool:
    return r in ("all", "scout")


def trades(r: str) -> bool:
    return r in ("all", "trader")


def jobs(r: str, *, follow: bool = False, record_only: bool = False) -> tuple[bool, bool, bool]:
    """(record, rank, supervise) for the scout service: what the command's flag says, else what the role gives.
    supervise is the pilot's review of the run and the autopilot, which belong where the runs are."""
    if follow:
        return False, False, True
    if record_only:
        return True, False, False
    return records(r), ranks(r), trades(r)


def no_trading(env: Mapping[str, str] | None = None) -> str:
    """Why this machine must not start a run ("" when it may)."""
    try:
        r = role(env)
    except ValueError as e:
        return str(e)
    return "" if trades(r) else (f"this machine is a {r} ({ENV}={r} in .env): it {WHAT[r]}. Runs start on the "
                                 "trader machine")
