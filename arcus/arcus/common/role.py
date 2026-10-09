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
from collections.abc import Mapping

ENV = "BOT_ROLE"
ROLES = ("all", "trader", "recorder", "scout")
WHAT = {"all": "records, ranks, Telegram and trading", "trader": "Telegram and trading",
        "recorder": "records only", "scout": "records and ranks"}


def role(env: Mapping[str, str] | None = None) -> str:
    """The machine's role (default "all"). ValueError for a word that is not a role: a typing mistake must not
    quietly turn a recorder into a machine that trades."""
    v = str((os.environ if env is None else env).get(ENV) or "all").strip().lower()
    if v not in ROLES:
        raise ValueError(f"{ENV}={v!r} in .env: must be one of {', '.join(ROLES)}")
    return v


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
