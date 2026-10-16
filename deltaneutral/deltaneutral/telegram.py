"""The delta-neutral bot's commands as the trading bot's ONE Telegram bot understands them (treading-bot/arcus).

There is no Telegram bot of its own: the one bot receives `/dn_<name>` (or `/dn <name>`) and calls `reply`. A
command acts on the paper bot unless `live` is its last word (`/dn_status live`).

    /dn_status      what it holds, the last cycle, the transfer asked for
    /dn_cycles      the last 10 cycles: what each venue made, what was traded, the transfer
    /dn_transfer    the last transfer asked for (how much, from which venue to which)
    /dn_settings    the settings      /dn_set NAME VALUE      /dn_pair USO ORCL
    /dn_skip        the skip dates    /dn_skip 2026-10-22 (an earnings date: nothing is held over it)
    /dn_close  /dn_pause  /dn_resume
    /dn_start  /dn_stop             the PAPER bot (live starts only from a terminal: deltaneutral start --live)
"""

from __future__ import annotations

import contextlib
import io

READ = ("status", "cycles", "transfer", "settings")
WRITE = ("set", "pair", "skip", "close", "pause", "resume", "start", "stop")
HELP = (__doc__ or "").split("\n\n")[2]


def _run(argv: list[str]) -> str:
    from . import cli

    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out), contextlib.suppress(SystemExit):
        cli.main(argv)
    return out.getvalue().strip() or "done"


def transfer_text(mode: str) -> str:
    from . import bot

    st = bot.store(mode, echo=None).load()
    tr = (st.transfer if st else {}) or {}
    if not tr:
        return "No cycle has closed yet: nothing to move."
    if not tr.get("amount"):
        return "After the last cycle the money was even: nothing to move."
    from .venues import account_names, read_env

    names = account_names(read_env())
    return (f"MOVE ${tr['amount']:,.2f} from {names.get(tr['from'], tr['from'])} to {names.get(tr['to'], tr['to'])}. "
            "Both venues are on Robinhood Chain in USDG: withdraw it from the first to your wallet, deposit it on "
            "the second.")


def changes_something(name: str, rest: list[str]) -> bool:
    return name in WRITE and not (name == "skip" and not rest)


def reply(name: str, rest: list[str]) -> str | None:
    """What /dn_<name> answers, or None when it is not one of ours."""
    from . import cli, ops

    live = bool(rest) and rest[-1].lower() == "live"
    args = rest[:-1] if live else list(rest)
    mode = "live" if live else "paper"
    if name == "status":
        return cli.status_text(mode)
    if name == "cycles":
        return cli.cycles_text(mode)
    if name == "transfer":
        return transfer_text(mode)
    if name == "settings":
        return cli.settings_text()
    if name == "skip":
        return cli.skip_text([a for a in args if a != "clear"], clear="clear" in args)
    if name == "set":
        return _run(["set", *args]) if len(args) == 2 else "Usage: /dn_set NAME VALUE (/dn_settings lists them)"
    if name == "pair":
        return _run(["pair", *args]) if len(args) == 2 else "Usage: /dn_pair USO ORCL"
    if name in ("close", "pause", "resume"):
        return _run([name] + (["--live"] if live else []))
    if name == "start":
        if live:
            return "Live starts only from a terminal on the trader machine: deltaneutral start --live (type LIVE)."
        return ops.start("paper")[1]
    if name == "stop":
        return ops.stop(mode)[1]
    return None
