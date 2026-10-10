"""`tbot up`, `tbot down`, `tbot status`: run everything a machine needs with one command.

Background services (each a normal `bot` command, detached, logging to logs/<name>.out, pid in state/<name>.pid):
- scout:    `arcus scout run --depth` records every Arcus market and ranks setups (always);
- telegram: `tbot telegram`, the phone control bot (when TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are in .env);
- guardian: `arcus guardian`, which cancels everything if the LIVE bot goes silent (only while a live bot runs:
            with no live bot it would fire at once).
What "everything a machine needs" is depends on its role (BOT_ROLE in .env, arcus/common/role.py): a recorder runs
the scout with --record-only and no Telegram bot, a trader runs it with --follow (no recording, no scans: it takes
the lists from the other machine), a scout machine records and ranks without Telegram. The service keeps its name
in every role, so `tbot down` and `tbot status` are the same everywhere.
The trading bot itself is started by the pilot (`arcus pilot approve 1 [--live]`, or Telegram's /top3 -> Run) and is
not a service here; `tbot down --all` stops it too (quotes cancelled, positions kept).

The bot's other two parts run under the same three commands:
- Lighter (treading-bot/lighter): `tbot up` starts its scout too (it records every Lighter market and ranks setups;
  no keys, no orders). Its runs are started from Telegram (/l_run) or `lighter run ...`.
- The funding arbitrage (treading-bot/arbitrage): its executor is started on request (/arb_start, `arbitrage start`).
`tbot down --all` stops their runs too; positions are kept, with the venues' own stop orders.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from arcus.common import role as roles
from arcus.common.config import AppConfig
from arcus.common.proc import pid_alive


@dataclass(frozen=True)
class Service:
    name: str
    args: tuple[str, ...]
    what: str
    stop_wait_s: float


SERVICES = (
    Service("scout", ("scout", "run", "--depth"), "records every market, ranks setups every 30 min", 240.0),
    Service("telegram", ("telegram",), "phone control bot", 20.0),
    Service("guardian", ("guardian",), "cancels everything if the live bot goes silent", 20.0),
)
BY_NAME = {s.name: s for s in SERVICES}
# the scout service per role: the flag added to its command (the Lighter scout takes the same two) and what it
# then does. A trader records nothing, so --depth is dropped there.
SCOUT_FLAG: dict[str, tuple[str, ...]] = {"all": (), "scout": (), "recorder": ("--record-only",),
                                          "trader": ("--follow",)}
SCOUT_WHAT = {"scout": "records every market, ranks setups (a trader fetches the lists)",
              "recorder": "records every market (no scans on this machine)",
              "trader": "takes the lists from the other machine, watches the run"}


def service(name: str, role: str = "all") -> Service:
    """The service as this role runs it."""
    s = BY_NAME[name]
    if name != "scout" or role == "all":
        return s
    args = tuple(a for a in s.args if not (role == "trader" and a == "--depth")) + SCOUT_FLAG[role]
    return Service(s.name, args, SCOUT_WHAT[role], s.stop_wait_s)


def bot_bin() -> str:
    return str(Path(sys.executable).with_name("arcus"))


def pid_path(app: AppConfig, name: str) -> Path:
    return Path(app.state_dir) / f"{name}.pid"


def pid_of(app: AppConfig, name: str) -> int | None:
    """The service's pid if that process is alive (a zombie is not: arcus/common/proc.py)."""
    try:
        pid = int(pid_path(app, name).read_text().strip())
    except (OSError, ValueError):
        return None
    return pid if pid_alive(pid) else None


def uptime_s(app: AppConfig, name: str) -> float | None:
    try:
        return time.time() - pid_path(app, name).stat().st_mtime
    except OSError:
        return None


def wanted(app: AppConfig, env: dict[str, str], live_running: bool) -> dict[str, str]:
    """{service: why} for what should run here now; a missing key means skipped (the reason is in `skipped`).
    The machine's role is read from `env` (BOT_ROLE; ValueError when it is not a role)."""
    r = roles.role(env)
    out = {"scout": "always" if r == "all" else f"this machine is a {r}: {SCOUT_WHAT[r]}"}
    if not roles.trades(r):
        return out
    if env.get("TELEGRAM_BOT_TOKEN") and env.get("TELEGRAM_CHAT_ID"):
        out["telegram"] = "Telegram is set up in .env"
    if live_running:
        out["guardian"] = "a live bot is running"
    return out


def skipped(env: dict[str, str], live_running: bool) -> dict[str, str]:
    r = roles.role(env)
    if not roles.trades(r):
        why = f"this machine is a {r}: the trader runs it"
        return {"telegram": why, "guardian": why}
    out = {}
    if not (env.get("TELEGRAM_BOT_TOKEN") and env.get("TELEGRAM_CHAT_ID")):
        out["telegram"] = "no TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID in .env"
    if not live_running:
        out["guardian"] = "no live bot running (it starts with one)"
    return out


def start(app: AppConfig, name: str, role: str = "all") -> tuple[bool, str]:
    """Start one service in the background unless it already runs. (started?, message)."""
    s = service(name, role)
    pid = pid_of(app, name)
    if pid:
        return False, f"{name}: already running (pid {pid})"
    Path(app.logs_dir).mkdir(parents=True, exist_ok=True)
    Path(app.state_dir).mkdir(parents=True, exist_ok=True)
    log = Path(app.logs_dir) / f"{name}.out"
    with log.open("ab") as fh:
        p = subprocess.Popen([bot_bin(), *s.args], stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                             start_new_session=True)
    pid_path(app, name).write_text(str(p.pid))
    time.sleep(1.0)
    if p.poll() is not None:
        tail = log.read_text(errors="ignore").splitlines()[-5:]
        return False, f"{name}: exited at once ({p.returncode}); last lines of {log}:\n  " + "\n  ".join(tail)
    return True, f"{name}: started (pid {p.pid}), log {log}"


def stop(app: AppConfig, name: str) -> tuple[bool, str]:
    """SIGTERM, then wait for a clean exit (the scout finishes its scan and writes out its buffers)."""
    pid = pid_of(app, name)
    if not pid:
        with contextlib.suppress(OSError):
            pid_path(app, name).unlink()
        return False, f"{name}: not running"
    os.kill(pid, signal.SIGTERM)
    t0 = time.time()
    while time.time() - t0 < BY_NAME[name].stop_wait_s:
        with contextlib.suppress(ChildProcessError, OSError):
            os.waitpid(pid, os.WNOHANG)   # reap it if this process started it (else it lingers as a zombie)
        if pid_of(app, name) is None:
            with contextlib.suppress(OSError):
                pid_path(app, name).unlink()
            return True, f"{name}: stopped"
        time.sleep(1.0)
    return False, (f"{name}: still stopping after {BY_NAME[name].stop_wait_s:.0f} s (pid {pid}); it exits when its "
                   "current work is done")


class _Skip(Exception):
    pass


# ------------------------------------------------------------------ the bot's other two parts
def lighter_up(role: str = "all") -> str:
    """Start the Lighter scout unless it runs: recorder and scans (no keys, no orders), or what this machine's role
    leaves of them (recording only on a recorder; neither on a trader, where it follows the other machine's lists)."""
    try:
        from lighter_bot import ops as lops
        from lighter_bot.config import load

        cfg = load()
        was = lops.running(cfg, "scout")
        pid = lops.start(cfg, "scout", ["scout", "run", *SCOUT_FLAG[role]])
    except Exception as e:   # not installed, or its config does not load: the Arcus services are not affected
        return f"lighter scout: not started ({type(e).__name__}: {e}). In treading-bot/arcus: make install"
    return (f"lighter scout: {'already running' if was else 'started'} (pid {pid}), "
            f"log {cfg.logs_dir / 'scout.out'}")


def others_down(everything: bool, *, lighter: bool = True, arbitrage: bool = True) -> list[str]:
    """Stop the Lighter scout; with `everything` also the Lighter runs and the arbitrage executors (positions kept)."""
    out: list[str] = []
    try:
        if not lighter:
            raise _Skip
        from lighter_bot import ops as lops
        from lighter_bot.config import load

        cfg = load()
        for name in ("scout",) + (("run-paper", "run-live") if everything else ()):
            if lops.stop(cfg, name):
                out.append(f"lighter {name.replace('run-', '') + ' run' if name != 'scout' else 'scout'}: stopped")
    except _Skip:
        pass
    except Exception as e:
        out.append(f"lighter: {type(e).__name__}: {e}")
    if everything and arbitrage:
        try:
            from arbitrage import ops as aops

            for mode in aops.MODES:
                if aops.running(mode):
                    out.append("funding arb " + aops.stop(mode)[1])
        except Exception as e:
            out.append(f"funding arb: {type(e).__name__}: {e}")
    return out


def others_status(role: str = "all") -> list[str]:
    """The Lighter and funding-arbitrage lines of `tbot status`."""
    lines = ["", "LIGHTER"]
    does = {"all": "records every Lighter market, ranks setups", "scout": "records every Lighter market, ranks setups",
            "recorder": "records every Lighter market", "trader": "follows the other machine's Lighter lists"}[role]
    try:
        from lighter_bot import ops as lops
        from lighter_bot.config import load

        st = lops.status(load())
        pid = st["services"].get("scout")
        rec = st.get("recorder") or {}
        age = time.time() - float(rec.get("t") or 0) if rec else None
        lines.append(f"  scout     {'running (pid ' + str(pid) + ')' if pid else 'STOPPED (lighter up starts it)':<46} "
                     f"{does}")
        if rec:
            lines.append(f"  recorder  {rec.get('markets', 0)} markets · {int(rec.get('rows_total') or 0):,} rows · last "
                         f"written {ago(age)} ago" + (" · PAUSED: disk nearly full" if rec.get("paused_for_disk") else "")
                         + (" (on the other machine, as last fetched)" if role == "trader" else ""))
        runs = [m for m in ("live", "paper") if st["services"].get(f"run-{m}")]
        for m in runs:
            r = st.get(m) or {}
            lines.append(f"  {m.upper():<6} {r.get('market', '?')} {r.get('setup', '')} · {r.get('state', '?')}")
        if not runs:
            lines.append("  no run. Start one: /l_run in Telegram, or lighter run SPY 'smart +1'" if roles.trades(role)
                         else "  no runs here: they start on the trader machine")
    except Exception as e:
        lines.append(f"  not available ({type(e).__name__}: {e}). In treading-bot/arcus: make install")
    lines += ["", "FUNDING ARB"]
    try:
        from arbitrage import ops as aops

        any_ = False
        for mode in aops.MODES:
            pid, ph = aops.running(mode), aops.phase(mode)
            if pid or ph not in ("", "flat"):
                any_ = True
                lines.append(f"  {mode.upper():<6} {'running (pid ' + str(pid) + ')' if pid else 'NOT RUNNING'} · "
                             f"{ph or 'never run'}")
        if not any_:
            lines.append("  not running. /arb_scan and /arb_start in Telegram, or arbitrage scan" if roles.trades(role)
                         else "  not here: it runs on the trader machine")
    except Exception as e:
        lines.append(f"  not available ({type(e).__name__}: {e}). In treading-bot/arcus: make install")
    return lines


PARTS = ("arcus", "lighter", "telegram")
HINT = {"scout": "arcus up", "telegram": "tbot up telegram", "guardian": "arcus up"}
PART_WHAT = {"arcus": "the Arcus scout (and the guardian while a live Arcus run exists)",
             "lighter": "the Lighter scout", "telegram": "the one Telegram bot (all three bots)"}


def up_parts(app: AppConfig, env: dict[str, str], live_running: bool, parts: tuple[str, ...]) -> list[str]:
    """Start what these parts are, as far as this machine's role allows. Returns the lines to print."""
    r = roles.role(env)
    out: list[str] = []
    want = wanted(app, env, live_running)
    skip = skipped(env, live_running)
    if "arcus" in parts:
        for name in ("scout", "guardian"):
            if name in want:
                out.append(f"{start(app, name, r)[1]}  [{want[name]}]")
            elif name in skip:
                out.append(f"{name}: not started ({skip[name]})")
    if "lighter" in parts:
        out.append(lighter_up(r) + {"trader": "  [it follows the other machine's lists; no recording here]",
                                    "recorder": "  [it records, with no keys and no orders; no scans here]"
                                    }.get(r, "  [it records, with no keys and no orders]"))
    if "telegram" in parts:
        if "telegram" in want:
            out.append(f"{start(app, 'telegram', r)[1]}  [{want['telegram']}]")
        else:
            out.append(f"telegram: not started ({skip.get('telegram', 'not set up')})")
    return out


def down_parts(app: AppConfig, parts: tuple[str, ...], *, everything: bool, live_running: bool) -> list[str]:
    """Stop these parts. `everything` also stops the runs of the parts named (positions kept); the arbitrage's executors
    stop only when all three parts are named."""
    out: list[str] = []
    if "telegram" in parts:
        out.append(stop(app, "telegram")[1])
    if "arcus" in parts:
        out.append(stop(app, "scout")[1])
        if everything or not live_running:
            out.append(stop(app, "guardian")[1])
        else:
            out.append("guardian: left running, it watches the live bot (`--all` stops both)")
    if "lighter" in parts:
        out += others_down(everything and "lighter" in parts, lighter="lighter" in parts,
                           arbitrage=everything and set(parts) >= set(PARTS))
    return out


def ago(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    s = int(seconds)
    return f"{s // 86400}d {s % 86400 // 3600}h" if s >= 86400 else f"{s // 3600}h {s % 3600 // 60}m" \
        if s >= 3600 else f"{s // 60}m {s % 60}s"


def dashboard(app: AppConfig, env: dict[str, str], root: Path, scope: str = "all") -> str:
    """One screen: the services, the trading bot, what is deployed, the last scan, the balance. scope "arcus": the Arcus
    part only (`tbot status`); "all": plus the Telegram bot, Lighter and the arbitrage (`tbot status`)."""
    from arcus.core.balances import BalanceLog, pnl
    from arcus.telegram.control import Control

    ctl = Control(app, root=root)
    live = ctl.is_running("live")
    r = roles.role(env)
    lines = [] if r == "all" else [f"THIS MACHINE: {r} ({roles.WHAT[r]}; BOT_ROLE in .env)", ""]
    lines.append("SERVICES")
    skip = skipped(env, live)
    for base in SERVICES:
        if scope == "arcus" and base.name == "telegram":
            continue            # the Telegram bot belongs to no one bot: `tbot status` shows it
        s = service(base.name, r)
        pid = pid_of(app, s.name)
        state = f"running {ago(uptime_s(app, s.name))} (pid {pid})" if pid else \
            f"off: {skip[s.name]}" if s.name in skip else f"STOPPED ({HINT.get(s.name, 'tbot up')} starts it)"
        lines.append(f"  {s.name:<9} {state:<46} {s.what}")
    if r == "trader":
        from arcus import handoff

        lines += ["", "THE OTHER MACHINE (tbot sync)"] + ["  " + x for x in handoff.status_lines(app.state_dir, env)]
    lines += ["", "TRADING BOT"]
    modes = [m for m in ("live", "paper") if ctl.is_running(m)]
    if not modes:
        lines.append("  not running. Start one: arcus pilot approve 1 (paper) or arcus pilot approve 1 --live"
                     if roles.trades(r) else "  no runs here: they start on the trader machine")
    for m in modes:
        v = ctl.view(m)
        snap = v.snapshot or {}
        for sess in snap.get("sessions") or []:
            lines.append(f"  {m.upper():<6} {sess.get('market', '?'):<6} session PnL ${float(sess.get('pnl') or 0):+.2f}"
                         f" · sizing for ${float(sess.get('size_capital') or sess.get('capital') or 0):,.2f}")
        pos = ", ".join(f"{k.split(':')[-1]} {p}" for k, p in v.positions.items()) or "flat"
        lines.append(f"         position: {pos} · open orders: {len(v.open_orders)}"
                     + (f" · paused: {', '.join(v.paused)}" if v.paused else ""))
    try:
        st = json.loads((Path(app.state_dir) / "pilot.json").read_text())
    except (OSError, ValueError):
        st = {}
    a = st.get("active")
    lines.append(f"  deployed: {a['market']} {a['config']} ({a['mode']})" + (
        f" · PAUSED by the scout: {st['paused_by_scout']}" if st.get("paused_by_scout") else "") if a else
        "  deployed: nothing")
    lines += ["", "SCOUT"]
    try:
        scan: dict[str, Any] = json.loads((root / "data" / "scout" / "latest.json").read_text())
        cap = scan.get("capital") or {}
        usd = cap.get("usd") or (scan.get("risk") or {}).get("capital_usd") or 100
        from arcus.common import settings
        from arcus.scout import profiles

        top = profiles.top(scan, profiles.DEFAULT, settings.volume_cost(settings.load(app.state_dir)))
        lines.append(f"  last scan {ago(time.time() - scan['ts_us'] / 1e6)} ago at ${usd:,.2f} "
                     f"({cap.get('source', 'older scan')}); Most Volume top 3:")
        for i, c in enumerate(top, 1):
            lines.append(f"   {i}. {c['market']} {c['config']}: ${c['volume_day']:,.0f}/day, PnL {c['pnl_day']:+.2f}/day, "
                         f"${profiles.cost_1k(c) or 0:.2f} per $1,000")
        if not top:
            lines.append("   nothing within the budget (/top3 in Telegram shows the closest)")
    except (OSError, ValueError, KeyError):
        lines.append({"recorder": "  this machine does not scan (it records; scan where the tape is brought to)",
                      "trader": "  no scan here yet: the lists come from the other machine (tbot sync status)"
                      }.get(r, "  no scan yet"))
    lines += ["", "BALANCE"]
    last = BalanceLog(Path(app.state_dir) / "balances.jsonl").latest()
    if last:
        p = pnl(last)
        lines.append(f"  ${last['equity']:,.2f} ({ago(time.time() - last['ts'])} ago, from {last['source']})"
                     + (f" · trading PnL ${p:+,.2f}" if p is not None else ""))
    else:
        lines.append("  no reading yet (the scout reads it before each scan once ARCUS_ADDRESS is in .env)")
    return "\n".join(lines + (others_status(r) if scope == "all" else []))
