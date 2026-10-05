"""What `bot export` writes about the machine and the trading, next to the raw files (bot/export.py):

    system/status.txt         `bot status` at that moment
    system/host.txt           machine, load, memory, disk, clock
    system/network.txt        how long a request takes to each venue and back, and the clock against theirs
    system/processes.txt      the bot's processes
    system/git.txt, local-changes.diff, versions.txt, env-names.txt   the code and environment it ran on
    arcus/csv/                fills, orders and volume by market from each state database
    SUMMARY.md                all of it in one page: read first

Everything here is best effort: a part that cannot be read is left out and the export goes on.
"""

from __future__ import annotations

import contextlib
import csv
import datetime as dt
import email.utils
import http.client
import json
import os
import platform
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
from collections.abc import Iterator
from importlib import metadata
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

if TYPE_CHECKING:
    from bot.export import Cut, Roots

SHOWN = {"BOT_PILOT_LIVE", "LBOT_LIVE", "ARB_LIVE", "LIGHTER_ENV", "LIGHTER_API_KEY_INDEX", "LIGHTER_ACCOUNT_INDEX",
         "ARCUS_ACCOUNT_INDEX", "BOT_HOME"}          # switches and indexes: shown; everything else only set / empty
BAD_LEVELS = {"WARNING": 1, "WARN": 1, "ERROR": 2, "CRITICAL": 3, "CRIT": 3}
MODES = ("live", "paper", "testnet")


def _sh(cmd: list[str], cwd: Path | None = None, timeout: float = 15.0) -> str:
    try:
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _utc(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, dt.UTC).strftime("%Y-%m-%d %H:%M")


def _secs(v: Any) -> float:
    """A log or state timestamp in seconds, whether it was written in seconds or microseconds."""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return 0.0
    return x / 1e6 if x > 1e14 else x


def _json(p: Path) -> Any:
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


def _jsonl(p: Path) -> Iterator[dict[str, Any]]:
    try:
        fh = p.open("rb")
    except OSError:
        return
    with fh:
        for line in fh:
            if line[:1] == b"{":
                with contextlib.suppress(ValueError):
                    d = json.loads(line)
                    if isinstance(d, dict):
                        yield d


# ------------------------------------------------------------------------------------------------ the machine
def host_text(r: Roots, now: float) -> str:
    lines = [f"host      {socket.gethostname()}", f"system    {platform.platform()} ({platform.machine()})",
             f"python    {sys.version.split()[0]}", f"cpus      {os.cpu_count()}",
             f"time      {_utc(now)} UTC · local {time.strftime('%Y-%m-%d %H:%M %Z', time.localtime(now))}"]
    with contextlib.suppress(OSError, AttributeError):
        lines.append("load      " + " ".join(f"{x:.2f}" for x in os.getloadavg()) + " (1, 5, 15 min)")
    mem = ""
    with contextlib.suppress(OSError, ValueError):
        info = dict(x.split(":", 1) for x in Path("/proc/meminfo").read_text().splitlines() if ":" in x)
        mem = (f"{int(info['MemTotal'].split()[0]) / 1e6:.1f} GB, "
               f"{int(info['MemAvailable'].split()[0]) / 1e6:.1f} GB available")
    if not mem and (m := _sh(["sysctl", "-n", "hw.memsize"])).isdigit():
        mem = f"{int(m) / 1e9:.1f} GB"
    lines.append(f"memory    {mem or '?'}")
    lines.append(f"uptime    {_sh(['uptime']) or '?'}")
    for label, p in (("bot", r.bot), ("lighter", r.lighter), ("arb", r.arb)):
        with contextlib.suppress(OSError):
            u = shutil.disk_usage(p)
            lines.append(f"disk      {label}: {u.free / 1e9:.1f} GB free of {u.total / 1e9:.0f} GB ({p})")
    for label, p in r.tapes().items():
        size = _sh(["du", "-skL", str(p)], timeout=60).split("\t")[0]
        if size.isdigit():
            lines.append(f"tape      {label}: {int(size) / 1e6:.2f} GB")
    sync = _sh(["timedatectl", "show", "-p", "NTPSynchronized", "--value"])
    if sync:
        lines.append(f"clock     synchronised by the system: {sync}")
    return "\n".join(lines)


def _venue_urls() -> dict[str, str]:
    out: dict[str, str] = {}
    with contextlib.suppress(Exception):
        from bot.common.config import load_arcus_config

        out["arcus"] = load_arcus_config().rest.mainnet
    with contextlib.suppress(Exception):
        from lbot.config import load

        out["lighter"] = load().endpoints.rest
    return out


# A light public GET that each venue's own servers answer (never the CDN's cache), and how many are timed.
PROBES = {"arcus": ("/v1/time", 4), "lighter": ("/api/v1/nextNonce?account_index=1&api_key_index=0", 2)}


def lighter_trading(r: Roots) -> bool:
    """True while a live Lighter run or the live arbitrage is up: Lighter allows 60 requests a minute in all, and
    they need every one, so the export sends it none."""
    from bot.common.proc import pid_alive

    for pid_file in (r.lighter / "state" / "run-live.pid", r.arb / "state" / "run-live.pid"):
        with contextlib.suppress(OSError, ValueError):
            if pid_alive(int(pid_file.read_text().strip())):
                return True
    return False


def network_text(urls: dict[str, str] | None = None, skip: tuple[str, ...] = ()) -> str:
    """How long one request takes from here to each venue and back, and this clock against the venue's.

    Both venues sit behind a CDN, so a ping or a TCP connect only reaches the nearest edge (a few ms from anywhere).
    What is timed is a small request the venue itself must answer, on a connection that is already open: the first
    request carries the handshake and is not counted."""
    lines = []
    for venue, url in (urls if urls is not None else _venue_urls()).items():
        host = urlparse(url).hostname or ""
        path, n = PROBES.get(venue, ("/", 2))
        if venue in skip:
            lines.append(f"{venue:<8} {host}: not timed (a live run is using this venue's request allowance)")
            continue
        ms: list[float] = []
        line = f"{venue:<8} {host}: "
        try:
            c = http.client.HTTPSConnection(host, timeout=10)
            status, date, mid = 0, None, 0.0
            for i in range(n + 1):
                t0, w0 = time.perf_counter(), time.time()
                c.request("GET", path, headers={"User-Agent": "curl/8.7.1"})
                resp = c.getresponse()
                took = time.perf_counter() - t0
                resp.read()
                status, date, mid = resp.status, resp.getheader("Date"), w0 + took / 2
                if i:
                    ms.append(took * 1000)
            c.close()
            if status == 403:
                line += "refused (HTTP 403 for this server's address)"
            else:
                line += (f"a request takes {min(ms):.0f} ms at best, {sorted(ms)[len(ms) // 2]:.0f} ms typically "
                         f"({len(ms)} timed)")
            if date:
                skew = mid - email.utils.parsedate_to_datetime(date).timestamp()
                line += f" · this clock is {skew:+.1f} s against theirs (to the second)"
        except Exception as e:
            line += f"could not be reached ({type(e).__name__})"
        lines.append(line)
    return "\n".join(lines) or "no venue address known"


def processes_text() -> str:
    marks = ("bin/bot", "bin/lbot", "bin/arb", "bot.cli", "lbot.cli", "arb.cli")
    rows = []
    for x in _sh(["ps", "-eo", "pid,etime,rss,args"]).splitlines():
        f = x.split(None, 3)            # this export and the shell that started it are not the bot
        if len(f) == 4 and any(m in f[3] for m in marks) and f[0] != str(os.getpid()) \
                and not f[3].split()[0].endswith(("sh", "zsh", "bash")):
            rows.append(x)
    return "pid, running for, memory (KB), command\n" + ("\n".join(rows) or "none of the bot's processes is running")


def git_text(repo: Path) -> tuple[str, str, dict[str, Any]]:
    """(git.txt, the local changes as a diff, {commit, branch, changed})."""
    commit = _sh(["git", "rev-parse", "HEAD"], repo)
    branch = _sh(["git", "rev-parse", "--abbrev-ref", "HEAD"], repo)
    status = _sh(["git", "status", "--short"], repo)
    log = _sh(["git", "log", "-8", "--format=%h %ad %s", "--date=short"], repo)
    diff = _sh(["git", "diff", "HEAD"], repo, timeout=30)[:2_000_000]
    text = f"commit {commit or '?'}\nbranch {branch or '?'}\n\nnot committed:\n{status or '(nothing)'}\n\nlast commits:\n{log}"
    return text, diff, {"commit": commit[:12], "branch": branch, "changed": len(status.splitlines())}


def env_names_text(r: Roots) -> str:
    from bot.export import _env_pairs

    lines = ["Which settings each .env has. Values are never exported, except switches and account indexes."]
    for label, root in (("bot", r.bot), ("lighter", r.lighter), ("arb", r.arb)):
        pairs = list(_env_pairs(root / ".env"))
        lines.append(f"\n{label}/.env" + ("" if pairs else ": none"))
        lines += [f"  {k} = {(v if k in SHOWN else 'set') if v else '(empty)'}" for k, v in pairs]
    return "\n".join(lines)


def versions_text() -> str:
    dists = sorted({f"{d.metadata['Name']}=={d.version}" for d in metadata.distributions() if d.metadata["Name"]},
                   key=str.lower)
    return f"python {sys.version}\n\n" + "\n".join(dists)


def status_text(r: Roots) -> str:
    if Path.cwd().resolve() != r.bot:
        return "not available (the export did not run from the bot folder)"
    try:
        from bot import ops
        from bot.common.config import load_app

        return ops.dashboard(load_app(), dict(os.environ), r.bot)
    except Exception as e:
        return f"not available ({type(e).__name__}: {e})"


# ------------------------------------------------------------------------------------------------ trades as CSV
def _query(db: Path, sql: str) -> list[tuple[Any, ...]]:
    try:
        c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    except sqlite3.Error:
        return []
    try:
        return c.execute(sql).fetchall()
    except sqlite3.Error:
        return []
    finally:
        c.close()


NOTIONAL = "CAST(price AS REAL) * CAST(size AS REAL)"
WHEN = "strftime('%Y-%m-%d %H:%M:%S', {col} / 1000000, 'unixepoch')"


def write_csvs(rec: Path) -> None:
    for mode in MODES:
        db = rec / "arcus" / "state" / f"{mode}.sqlite"
        if not db.exists():
            continue
        tables = {
            "fills": (["time_utc", "base", "side", "price", "size", "notional", "fee", "is_maker", "liquidation",
                       "session", "tag", "client_id", "trade_id"],
                      f"SELECT {WHEN.format(col='ts_us')}, base, side, price, size, round({NOTIONAL}, 2), fee, is_maker, "
                      "liquidation, session, tag, client_id, trade_id FROM fills ORDER BY ts_us"),
            "orders": (["created_utc", "updated_utc", "base", "side", "price", "size", "tif", "reduce_only", "tag",
                        "status", "filled", "avg_px", "reject_reason", "reason", "session", "client_id"],
                       f"SELECT {WHEN.format(col='created_us')}, {WHEN.format(col='updated_us')}, base, side, price, "
                       "size, tif, reduce_only, tag, status, filled, avg_px, reject_reason, reason, session, client_id "
                       "FROM orders ORDER BY created_us"),
            "volume_by_market": (["base", "fills", "volume", "maker_volume", "fees"],
                                 f"SELECT base, count(*), round(sum({NOTIONAL}), 2), "
                                 f"round(sum(is_maker * {NOTIONAL}), 2), round(sum(CAST(fee AS REAL)), 4) FROM fills "
                                 "GROUP BY base ORDER BY 3 DESC"),
        }
        for name, (head, sql) in tables.items():
            rows = _query(db, sql)
            if rows:
                p = rec / "arcus" / "csv" / f"{name}-{mode}.csv"
                p.parent.mkdir(parents=True, exist_ok=True)
                with p.open("w", newline="") as fh:
                    w = csv.writer(fh)
                    w.writerow(head)
                    w.writerows(rows)


# ------------------------------------------------------------------------------------------------ everything
def write_all(rec: Path, r: Roots, *, name: str, cut: Cut, now: float, secrets: list[bytes], probe: bool, prev: str,
              warnings: list[str]) -> dict[str, Any]:
    """Write system/ and the CSVs into the records folder; return what the manifest and the summary say."""
    from bot.export import scrub

    sysdir = rec / "system"
    sysdir.mkdir(parents=True, exist_ok=True)
    git, diff, gitmeta = git_text(r.repo)
    texts = {"status.txt": status_text(r), "host.txt": host_text(r, now), "processes.txt": processes_text(),
             "git.txt": git, "local-changes.diff": diff, "env-names.txt": env_names_text(r),
             "versions.txt": versions_text(), "crontab.txt": _sh(["crontab", "-l"]) or "(none)",
             "network.txt": network_text(skip=("lighter",) if lighter_trading(r) else ()) if probe
             else "skipped (--no-probe)"}
    for fname, text in texts.items():
        (sysdir / fname).write_bytes(scrub(text.encode(errors="replace"), secrets) + b"\n")
    try:
        write_csvs(rec)
    except Exception as e:
        warnings.append(f"csv: {type(e).__name__}: {e}")
    return {"name": name, "made_utc": _utc(now) + " UTC", "made_ts": now, "host": socket.gethostname(),
            "system": platform.platform(), "mode": cut.mode, "tape": cut.tape, "cut_ts": cut.ts, "cut_day": cut.day,
            "contents": cut.words(), "prev": prev, **gitmeta}


# ------------------------------------------------------------------------------------------------ the summary
def _money(x: float) -> str:
    return f"${x:,.0f}" if abs(x) >= 1000 else f"${x:,.2f}"


def _table(head: list[str], rows: list[list[Any]]) -> list[str]:
    if not rows:
        return ["(none)"]
    return ["| " + " | ".join(head) + " |", "|" + "---|" * len(head),
            *("| " + " | ".join(str(c) for c in row) + " |" for row in rows)]


def arcus_lines(rec: Path) -> list[str]:
    out: list[str] = []
    state = rec / "arcus" / "state"
    for mode in MODES:
        db = state / f"{mode}.sqlite"
        total = _query(db, f"SELECT count(*), sum({NOTIONAL}), sum(is_maker * {NOTIONAL}), sum(CAST(fee AS REAL)), "
                           "min(ts_us), max(ts_us) FROM fills")
        if not total or not total[0][0]:
            continue
        n, vol, mk, fee, t0, t1 = total[0]
        out += [f"### Arcus, {mode}", "",
                f"{n:,} fills, {_money(vol)} traded ({100 * mk / vol:.0f}% as maker), fees {_money(fee or 0)}; "
                f"first {_utc(t0 / 1e6)}, last {_utc(t1 / 1e6)} UTC.", ""]
        days = _query(db, f"SELECT date(ts_us / 1000000, 'unixepoch'), count(*), sum({NOTIONAL}), "
                          f"sum(is_maker * {NOTIONAL}), sum(CAST(fee AS REAL)) FROM fills GROUP BY 1 ORDER BY 1 DESC "
                          "LIMIT 14")
        out += _table(["UTC day", "fills", "volume", "maker", "fees"],
                      [[d, f"{c:,}", _money(v), f"{100 * m / v:.0f}%", _money(f or 0)] for d, c, v, m, f in days])
        mk_rows = _query(db, f"SELECT base, count(*), sum({NOTIONAL}) FROM fills GROUP BY 1 ORDER BY 3 DESC LIMIT 8")
        out += ["", "By market: " + ", ".join(f"{b} {_money(v)} ({c:,} fills)" for b, c, v in mk_rows) + "."]
        st = _query(db, "SELECT status, count(*) FROM orders GROUP BY 1 ORDER BY 2 DESC")
        rj = _query(db, "SELECT reject_reason, count(*) FROM orders WHERE reject_reason IS NOT NULL AND reject_reason "
                        "!= '' GROUP BY 1 ORDER BY 2 DESC LIMIT 8")
        out += ["", "Orders: " + ", ".join(f"{s} {c:,}" for s, c in st) + ".",
                "Why orders ended without filling: " + (", ".join(f"{s} {c:,}" for s, c in rj) or "nothing recorded")
                + "."]
        runs: dict[str, dict[str, float]] = {}
        for k, v in _query(db, "SELECT k, v FROM kv WHERE k LIKE 'run_vol:%' OR k LIKE 'run_pnl:%'"):
            with contextlib.suppress(ValueError):
                runs.setdefault(k.split(":", 1)[1], {})[k[4:7]] = float(v)
        rows = []
        for rid, d in sorted(runs.items(), key=lambda kv: kv[0].rsplit("-", 1)[-1], reverse=True)[:15]:
            market, _, start = rid.rpartition("-")
            vol_, pnl_ = d.get("vol", 0.0), d.get("pnl", 0.0)
            rows.append([_utc(_secs(start)), market, _money(vol_), f"{pnl_:+.2f}",
                         f"{-pnl_ / vol_ * 1e4:.2f} bp" if vol_ > 0 else "-"])
        out += ["", "Runs (newest first; cost = loss per dollar traded):", "",
                *_table(["started UTC", "market", "volume", "PnL $", "cost"], rows), ""]
    events = [e for e in _jsonl(state / "pilot_events.jsonl") if e.get("kind") != "offer"][-12:]
    if events:
        out += ["### What the pilot and the autopilot did (last 12)", ""]
        out += [f"- {_utc(_secs(e.get('ts')))} {e.get('kind')}: "
                + " · ".join(x for x in str(e.get("text") or "").splitlines()[:4] if x.strip())[:200] for e in events]
        out.append("")
    readings = list(_jsonl(state / "balances.jsonl"))
    last = readings[-1] if readings else None
    if last:
        nd = last.get("net_deposits")
        out.append(f"Balance: equity {_money(float(last.get('equity') or 0))} at {_utc(_secs(last.get('ts')))} UTC"
                   + (f", net deposits {_money(float(nd))}, so trading PnL since the first deposit "
                      f"{float(last.get('equity') or 0) - float(nd):+,.2f}." if nd is not None else "."))
    if not any(x.startswith("### Arcus") for x in out):
        out.insert(0, "No Arcus fills recorded on this machine.\n")
    return out


def lighter_lines(rec: Path) -> list[str]:
    out: list[str] = []
    state = rec / "lighter" / "state"
    for mode in ("live", "paper"):
        fills = list(_jsonl(state / f"fills-{mode}.jsonl"))
        run, st = _json(state / f"run-{mode}.json") or {}, _json(state / f"status-{mode}.json") or {}
        if not fills and not run:
            continue
        usd = sum(float(f.get("usd") or 0) for f in fills)
        mk = sum(float(f.get("usd") or 0) for f in fills if f.get("maker"))
        spec = run.get("spec") or {}
        out.append(f"- **{mode}:** {len(fills):,} fills, {_money(usd)} traded"
                   + (f" ({100 * mk / usd:.0f}% as maker)" if usd else "")
                   + (f", {_utc(_secs(fills[0].get('t')))} to {_utc(_secs(fills[-1].get('t')))} UTC" if fills else "")
                   + (f". Last run: {spec.get('market')} {spec.get('setup')} at {spec.get('leverage'):g}x, "
                      f"volume {_money(float(run.get('volume') or 0))}, state {(run.get('guard') or {}).get('state')}"
                      f"{', ended: ' + str(run.get('done')) if run.get('done') else ''}" if spec else "")
                   + (f"; run PnL {float(st.get('run_pnl') or 0):+.2f}, equity {_money(float(st.get('equity') or 0))}"
                      if st else "") + ".")
    return out or ["No Lighter run recorded on this machine."]


def arb_lines(rec: Path) -> list[str]:
    out: list[str] = []
    state = rec / "arb" / "state"
    for mode in ("live", "paper"):
        pos = _json(state / f"position-{mode}.json")
        if not pos:
            continue
        out.append(f"- **{mode}:** {pos.get('phase') or 'flat'}"
                   + (f" {pos.get('symbol')} size {pos.get('size')}" if pos.get("symbol") else "")
                   + (", paused" if pos.get("paused") else "") + ".")
        out += [f"  - {_utc(_secs(e.get('ts')))} {e.get('kind')}: {str(e.get('text') or '')[:160]}"
                for e in list(_jsonl(state / f"events-{mode}.jsonl"))[-6:]]
    return out or ["The funding arbitrage has not run on this machine."]


def _crashes(p: Path) -> Iterator[tuple[str, list[str]]]:
    """(how it ended, the traceback) for each Python traceback printed in a service's or a run's own output."""
    block: list[str] | None = None
    try:
        fh = p.open(errors="replace")
    except OSError:
        return
    with fh:
        for raw in fh:
            line = raw.rstrip("\n")
            if block is None:
                if line.startswith("Traceback (most recent call last)"):
                    block = [line]
            elif (line.startswith((" ", "\t")) or not line.strip()) and len(block) < 80:
                block.append(line[:200])
            else:                           # the first line that is not indented names the error
                block.append(line[:200])
                yield line.strip()[:160], block
                block = None


def log_problems(rec: Path, limit: int = 30) -> list[str]:
    """Warnings and errors in the exported logs, grouped: how often, when last, what it said. Then the crashes."""
    seen: dict[tuple[str, str, str, str], list[Any]] = {}
    crashes: dict[str, list[Any]] = {}
    files = [p for part in ("arcus", "lighter", "arb") for p in sorted((rec / part).rglob("*"))
             if p.is_file() and ("logs" in p.parts or p.suffix == ".out")]
    for p in files:
        part = p.relative_to(rec).parts[0]
        if ".jsonl" not in p.name:      # a service's or a run's own output: it repeats the .jsonl lines, so only
            for ended, block in _crashes(p):                                   # what Python itself printed counts
                row = crashes.setdefault(ended, [0, "", []])
                row[0] += 1
                row[1], row[2] = str(p.relative_to(rec)), block
            continue
        for d in _jsonl(p):
            level = str(d.get("level") or d.get("lvl") or "").upper()
            if level not in BAD_LEVELS:
                continue
            key = (part, level, str(d.get("component") or d.get("c") or ""), str(d.get("event") or d.get("ev") or ""))
            row = seen.setdefault(key, [0, 0.0, ""])
            row[0] += 1
            row[1] = max(row[1], _secs(d.get("ts") or d.get("t")))
            said = d.get("reason") or d.get("msg") or d.get("err") or d.get("error") or d.get("data") or ""
            row[2] = (said if isinstance(said, str) else json.dumps(said, default=str))[:140]
    rows = sorted(seen.items(), key=lambda kv: (-BAD_LEVELS[kv[0][1]], -kv[1][0]))[:limit]
    out = _table(["part", "level", "where", "what", "times", "last UTC", "it said"],
                 [[k[0], k[1], k[2], k[3], f"{v[0]:,}", _utc(v[1]) if v[1] else "", v[2].replace("|", "/")]
                  for k, v in rows])
    if not rows:
        out = ["No warnings or errors in the exported logs."]
    if not crashes:
        return [*out, "", "No Python traceback in the services' or the runs' own output."]
    out += ["", "Python tracebacks in the services' and the runs' own output (an exception nothing handled):", "",
            *_table(["ended with", "times", "last in"],
                    [[k.replace("|", "/"), v[0], f"`{v[1]}`"] for k, v in sorted(crashes.items(), key=lambda kv: -kv[1][0])])]
    ended, last = max(crashes.items(), key=lambda kv: kv[1][1])     # the newest file's (names carry the date)
    return [*out, "", f"The last `{ended}`:", "", "```", *last[2][-25:], "```"]


def recorder_lines(rec: Path, now: float) -> list[str]:
    out = []
    for label, p in (("Arcus", rec / "arcus" / "data" / "scout" / "recorder.json"),
                     ("Lighter", rec / "lighter" / "data" / "recorder.json")):
        d = _json(p)
        if d:
            age = now - _secs(d.get("ts") or d.get("t"))
            out.append(f"- **{label} recorder:** {d.get('markets')} markets, {int(d.get('rows_total') or 0):,} rows "
                       f"since it started, last written {age / 60:.0f} min before the export"
                       + (f", {d.get('free_gb')} GB free" if d.get("free_gb") is not None else "")
                       + (f", {d.get('reconnects')} reconnects, {d.get('book_gaps')} book gaps"
                          if "reconnects" in d else "")
                       + (", **PAUSED: disk nearly full**" if d.get("paused_for_disk") else "") + ".")
    return out or ["- No recorder has written on this machine."]


def summary(rec: Path, meta: dict[str, Any], coverage: str, warnings: list[str]) -> str:
    def text(name: str) -> str:
        try:
            return (rec / "system" / name).read_text(errors="replace").strip()
        except OSError:
            return "not available"

    now = float(meta["made_ts"])
    lines = [
        f"# Export {meta['name']}", "",
        f"Made {meta['made_utc']} on `{meta['host']}` ({meta['system']}), from commit `{meta.get('commit') or '?'}` "
        f"on branch `{meta.get('branch') or '?'}`"
        + (f" with {meta['changed']} files not committed (system/git.txt, system/local-changes.diff)"
           if meta.get("changed") else "") + ".", "",
        f"**Holds:** {meta['contents']}."
        + (f" The export before it was `{meta['prev']}`." if meta.get("prev") else ""), "",
        "Take it in on another machine with `bot import <this file>` (from `treading-bot/bot`): the tape is merged "
        "into the tape folders, everything else lands in `bot/data/server-export/" + meta["name"] + "/`.", "",
        "## 1. The bot at that moment", "", "```", text("status.txt"), "```", "",
        "## 2. The machine", "", "```", text("host.txt"), "", text("network.txt"), "```", "",
        "Processes:", "", "```", text("processes.txt"), "```", "",
        "## 3. Trading", "", *arcus_lines(rec), "", "### Lighter", "", *lighter_lines(rec), "",
        "### Funding arbitrage", "", *arb_lines(rec), "",
        "## 4. Problems in the logs", "", *log_problems(rec), "",
        "## 5. The recording", "", *recorder_lines(rec, now), "",
        "Tape in this export, counted before packing (`coverage.csv` has every market, day and kind):", "",
        "```", coverage, "```", "",
        "## 6. Where things are (after `bot import`)", "",
        *_table(["Folder under records/", "What"], [
            ["`arcus/state/`", "`live.sqlite` and `paper.sqlite` (orders, fills, events, funding), balances, the pilot's "
                               "and autopilot's state, your settings"],
            ["`arcus/csv/`", "fills, orders and volume by market as CSV"],
            ["`arcus/logs/`", "`bot.jsonl*` (everything), `decisions.jsonl*` (why each order), `runs/` (one file a "
                              "run), `*.out` (each service's own output)"],
            ["`arcus/data/scout/`", "the latest scan and lists, `scans/`, `reports/`, the playbook, recorder health"],
            ["`arcus/config/`", "app.yaml, the session files, calendars"],
            ["`lighter/`", "the same for the Lighter part: `state/` (runs, `fills-<mode>.jsonl`), `logs/`, `data/`"],
            ["`arb/`", "the funding arbitrage: `state/` (position, events), `settings.json`, the funding history"],
            ["`system/`", "status, host, network, processes, git, versions, which .env names are set"],
        ]),
    ]
    if warnings:
        lines += ["", "## 7. Files not copied whole", "", *(f"- {w}" for w in warnings[:50])]
    return "\n".join(lines) + "\n"
