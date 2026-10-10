"""The funding arbitrage's commands as the trading bot's ONE Telegram bot understands them (treading-bot/arcus).

There is no arbitrage Telegram bot of its own: the one bot receives `/arb_<name>` (or `/arb <name>`), asks for a
confirmation where real money is involved, and calls this module. Each command runs the same code as the command
line and returns what it printed.

    /arb_status  /arb_scan  /arb_plan SPY  /arb_settings  /arb_feeds
    /arb_set max_hold_h 72   /arb_hold 72   /arb_minhold 24   /arb_sl 2   /arb_sl auto
    /arb_close  /arb_closenow  /arb_pause  /arb_resume  /arb_skip CASHCAT  /arb_unskip CASHCAT
    /arb_only SPY (that market and no other; /arb_only all = any)
    /arb_cycle 3 (close and reopen every 3 funding payments = 3 hours; /arb_cycle off)
    /arb_side profunding (ProFunding decides which venue is short; /arb_side venues = their own rates)
    /arb_lev 10 (a lower leverage than the venues' highest; /arb_lev max = the highest again)
    /arb_start 120 120 (paper, with that much pretend money on Arcus and on Lighter)   /arb_start live   /arb_stop
"""

from __future__ import annotations

import asyncio
import contextlib
import io

# /arb_hold 72 -> set max_hold_h 72, and the other short forms
SHORT = {"hold": ["set", "max_hold_h"], "minhold": ["set", "min_hold_h"], "sl": ["set", "stop_pct"],
         "lev": ["set", "max_leverage"], "closenow": ["close", "--now"]}
READ = ("status", "scan", "plan", "settings", "feeds")                   # change nothing
WRITE = ("set", "close", "pause", "resume", "skip", "unskip", "only", "cycle", "side")   # change the bot
MODE_AWARE = ("status", "close", "pause", "resume")                      # act on the paper or on the live bot
HELP = (__doc__ or "").split("\n\n")[2]


def to_argv(text: str, live: bool) -> list[str] | None:
    """The command-line arguments a message stands for ("/hold 72", "/status"), or None when it is not one of ours.
    `live`: the mode-aware ones act on the live bot."""
    parts = text.strip().split()
    if not parts or not parts[0].startswith("/"):
        return None
    name = parts[0][1:].split("@")[0].lower()
    argv = SHORT[name] + parts[1:] if name in SHORT else [name, *parts[1:]]
    if argv[0] not in READ + WRITE:
        return None
    if live and argv[0] in MODE_AWARE:
        argv.append("--live")
    return argv


def changes_something(argv: list[str]) -> bool:
    return bool(argv) and argv[0] in WRITE


async def run_command(argv: list[str]) -> str:
    """Run one command as the command line would, in a thread, and return what it printed."""
    from arbitrage import cli

    def call() -> str:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out), contextlib.suppress(SystemExit):
            cli.main(argv)
        return out.getvalue().strip() or "done"

    return await asyncio.to_thread(call)
