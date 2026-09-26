"""What the Telegram bot can see and do, without holding any trading state of its own.

Reads: each mode's heartbeat file and state DB (the runner publishes a status snapshot to kv "status" every 5 s;
fills, orders and positions come from their tables). Writes: kv "paused" / "control" / "resume", which the runner
applies on its next tick. Starting a run goes through the normal `bot run` command, so every live lock
(session live_enabled, doctor, --yes) still applies. Venue actions (cancel-all, flatten) reuse the CLI's code.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bot.common.config import AppConfig, load_session
from bot.core.heartbeat import heartbeat_process_alive, read_heartbeat_age_s

MODES = ("live", "testnet", "paper")


@dataclass
class ModeView:
    mode: str
    running: bool
    heartbeat_age_s: float | None
    pid: int | None
    snapshot: dict[str, Any] | None
    snapshot_age_s: float | None
    # market -> fills, volume, maker_volume, fees, first_us (the day's first fill)
    today: dict[str, dict[str, float]] = field(default_factory=dict)
    open_orders: list[dict[str, str]] = field(default_factory=list)
    positions: dict[str, str] = field(default_factory=dict)
    paused: dict[str, str] = field(default_factory=dict)
    resume_pending: bool = False


def _today_start_us(now: float | None = None) -> int:
    d = dt.datetime.fromtimestamp(now if now is not None else time.time(), dt.UTC).date()
    return int(dt.datetime(d.year, d.month, d.day, tzinfo=dt.UTC).timestamp() * 1e6)


class Control:
    def __init__(self, app: AppConfig, *, root: Path | str = ".", bot_bin: str | None = None) -> None:
        self.app = app
        self.root = Path(root)
        self.bot_bin = bot_bin or str(Path(sys.executable).with_name("bot"))
        self.runs_file = self.root / app.state_dir / "telegram_runs.json"

    # ------------------------------------------------------------------ paths / db
    def db_path(self, mode: str) -> Path:
        return self.root / self.app.state_db_for(mode)

    def hb_path(self, mode: str) -> Path:
        return self.root / self.app.heartbeat_for(mode)

    def _db(self, mode: str, write: bool = False) -> sqlite3.Connection | None:
        p = self.db_path(mode)
        if not p.exists():
            if not write:
                return None
            p.parent.mkdir(parents=True, exist_ok=True)
        uri = f"file:{p}?mode={'rwc' if write else 'ro'}"
        con = sqlite3.connect(uri, uri=True, timeout=5)
        if write:
            con.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT)")
        return con

    def _kv_get(self, mode: str, k: str) -> str | None:
        con = self._db(mode)
        if con is None:
            return None
        try:
            row = con.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
            return row[0] if row else None
        except sqlite3.Error:
            return None
        finally:
            con.close()

    def _kv_set(self, mode: str, k: str, v: str) -> None:
        con = self._db(mode, write=True)
        assert con is not None
        try:
            with con:
                con.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", (k, v))
        finally:
            con.close()

    # ------------------------------------------------------------------ reading
    def known_modes(self) -> list[str]:
        return [m for m in MODES if self.db_path(m).exists() or self.hb_path(m).exists()]

    def running_modes(self) -> list[str]:
        return [m for m in MODES if self.is_running(m)]

    def is_running(self, mode: str) -> bool:
        age = read_heartbeat_age_s(self.hb_path(mode))
        return age < 15 and heartbeat_process_alive(self.hb_path(mode))

    def heartbeat_pid(self, mode: str) -> int | None:
        try:
            return int(json.loads(self.hb_path(mode).read_text())["pid"])
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def default_mode(self) -> str | None:
        """The mode a command means when none is given: the running one (live first), else the newest state."""
        run = self.running_modes()
        if run:
            return run[0]
        known = self.known_modes()
        return known[0] if known else None

    def view(self, mode: str, now: float | None = None) -> ModeView:
        now = now if now is not None else time.time()
        age = read_heartbeat_age_s(self.hb_path(mode), int(now * 1e6))
        v = ModeView(mode=mode, running=age < 15 and heartbeat_process_alive(self.hb_path(mode)),
                     heartbeat_age_s=None if age == float("inf") else age, pid=self.heartbeat_pid(mode),
                     snapshot=None, snapshot_age_s=None)
        con = self._db(mode)
        if con is None:
            return v
        try:
            row = con.execute("SELECT v FROM kv WHERE k='status'").fetchone()
            if row and row[0]:
                v.snapshot = json.loads(row[0])
                v.snapshot_age_s = now - int(v.snapshot.get("ts_us", 0)) / 1e6
            row = con.execute("SELECT v FROM kv WHERE k='paused'").fetchone()
            v.paused = json.loads(row[0]) if row and row[0] else {}
            row = con.execute("SELECT v FROM kv WHERE k='resume'").fetchone()
            v.resume_pending = bool(row and row[0])
            t0 = _today_start_us(now)
            for ts, base, price, size, fee, maker in con.execute(
                    "SELECT ts_us, base, price, size, fee, is_maker FROM fills WHERE ts_us >= ?", (t0,)):
                d = v.today.setdefault(base, {"fills": 0.0, "volume": 0.0, "maker_volume": 0.0, "fees": 0.0,
                                              "first_us": float(ts)})
                d["first_us"] = min(d["first_us"], float(ts))
                n = float(price) * float(size)
                d["fills"] += 1
                d["volume"] += n
                d["maker_volume"] += n if maker else 0.0
                d["fees"] += float(fee or 0)
            for cid, venue, base, side, price, size, status, tag in con.execute(
                    "SELECT client_id, venue, base, side, price, size, status, tag FROM orders "
                    "WHERE status IN ('PENDING_NEW','OPEN','PARTIALLY_FILLED') ORDER BY base, side, price"):
                v.open_orders.append({"cid": cid, "venue": venue, "market": base, "side": side, "price": price,
                                      "size": size, "status": status, "tag": tag or ""})
            for venue, base, size, entry in con.execute("SELECT venue, base, size, entry FROM positions"):
                if size and float(size) != 0:
                    v.positions[f"{venue}:{base}"] = f"{size} @ {entry}"
        except (sqlite3.Error, ValueError):
            pass
        finally:
            con.close()
        return v

    def fills_since(self, mode: str, ts_us: int, limit: int = 200) -> list[dict[str, Any]]:
        con = self._db(mode)
        if con is None:
            return []
        try:
            rows = con.execute("SELECT ts_us, venue, base, side, price, size, fee, is_maker, tag FROM fills "
                               "WHERE ts_us > ? ORDER BY ts_us LIMIT ?", (ts_us, limit)).fetchall()
        except sqlite3.Error:
            return []
        finally:
            con.close()
        return [{"ts_us": r[0], "venue": r[1], "market": r[2], "side": r[3], "price": float(r[4]), "size": float(r[5]),
                 "fee": float(r[6] or 0), "maker": bool(r[7]), "tag": r[8] or ""} for r in rows]

    def last_fill_ts(self, mode: str) -> int:
        con = self._db(mode)
        if con is None:
            return 0
        try:
            row = con.execute("SELECT MAX(ts_us) FROM fills").fetchone()
            return int(row[0] or 0)
        except sqlite3.Error:
            return 0
        finally:
            con.close()

    # ------------------------------------------------------------------ operator controls
    def set_pause(self, mode: str, market: str | None, reason: str) -> dict[str, str]:
        cur: dict[str, str] = json.loads(self._kv_get(mode, "paused") or "{}")
        cur[(market or "*").upper()] = reason
        self._kv_set(mode, "paused", json.dumps(cur))
        return cur

    def clear_pause(self, mode: str, market: str | None) -> dict[str, str]:
        cur: dict[str, str] = json.loads(self._kv_get(mode, "paused") or "{}")
        if market is None:
            cur = {}
        else:
            cur.pop(market.upper(), None)
        self._kv_set(mode, "paused", json.dumps(cur) if cur else "")
        return cur

    def set_sizing_ok(self, mode: str, capital_usd: float, market: str) -> None:
        """The scout found the running setup (on `market`, a base like QQQ) still GO at this capital; the engine may
        size up to 1.25x it."""
        self._kv_set(mode, "sizing_ok", f"{capital_usd:.2f}:{market}")

    def clear_sizing_ok(self, mode: str) -> None:
        """A new deployment starts from its own backtested capital, never from an earlier one's."""
        self._kv_set(mode, "sizing_ok", "")

    def request_stop(self, mode: str, by: str) -> None:
        self._kv_set(mode, "control", json.dumps({"cmd": "stop", "by": by, "ts": time.time()}))

    def request_close(self, mode: str, by: str) -> None:
        """Close every position (maker, then taker), then stop the run."""
        self._kv_set(mode, "control", json.dumps({"cmd": "close", "by": by, "ts": time.time()}))

    def signal_stop(self, mode: str) -> bool:
        """Fallback when the runner did not pick up the stop flag: SIGINT the heartbeat's process (the runner's
        signal handler shuts down the same way: cancel quotes, keep positions)."""
        pid = self.heartbeat_pid(mode)
        if pid is None or not self.is_running(mode):
            return False
        try:
            os.kill(pid, signal.SIGINT)
            return True
        except OSError:
            return False

    def request_resume(self, mode: str, venue: str | None) -> None:
        self._kv_set(mode, "resume", json.dumps({"venue": venue, "all": True, "ts": time.time()}))

    # ------------------------------------------------------------------ sessions and runs
    def session_path(self, name: str) -> Path:
        """A session file: a path, or a name in config/sessions. ValueError when there is none (not the CLI's exit,
        which would end the Telegram bot on a mistyped /doctor)."""
        for p in (Path(name), self.root / "config" / "sessions" / name, self.root / "config" / "sessions" / f"{name}.yaml"):
            if p.is_file():
                return p
        have = ", ".join(x["name"] for x in self.sessions()) or "none"
        raise ValueError(f"no session called {name!r} (have: {have})")

    def sessions(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for p in sorted((self.root / "config" / "sessions").glob("*.yaml")):
            try:
                s = load_session(p)
            except Exception as e:  # show broken files instead of hiding them
                out.append({"name": p.stem, "error": str(e)[:120]})
                continue
            out.append({"name": p.stem, "market": s.market, "live_enabled": s.live_enabled, "kind": "mm",
                        "mode": s.mode, "venue": s.venue, "account": s.account_index, "order_usd": s.order_size_usd,
                        "cap_usd": s.inventory_cap_usd, "capital": s.capital_usd})
        return out

    def _runs(self) -> list[dict[str, Any]]:
        try:
            runs: list[dict[str, Any]] = json.loads(self.runs_file.read_text())
        except (OSError, ValueError):
            return []
        return runs

    def runs(self) -> list[dict[str, Any]]:
        """Runs started from Telegram, with whether each process is still alive."""
        out = []
        for r in self._runs():
            try:
                os.kill(int(r["pid"]), 0)
                alive = True
            except (OSError, ValueError, KeyError):
                alive = False
            out.append({**r, "alive": alive})
        return out

    def start_run(self, name: str, *, live: bool) -> dict[str, Any]:
        log_dir = self.root / self.app.logs_dir / "runs"
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
        log_path = log_dir / f"{name}-{'live' if live else 'paper'}-{stamp}.log"
        cmd = [self.bot_bin, "run", name] + (["--live", "--yes"] if live else [])
        with open(log_path, "ab") as fh:
            p = subprocess.Popen(cmd, cwd=self.root, stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                 start_new_session=True)
        rec = {"pid": p.pid, "name": name, "live": live, "started": time.time(), "log": str(log_path)}
        runs = [r for r in self.runs() if r.get("alive")] + [rec]
        self.runs_file.parent.mkdir(parents=True, exist_ok=True)
        self.runs_file.write_text(json.dumps([{k: v for k, v in r.items() if k != "alive"} for r in runs]))
        return rec

    def log_tail(self, path: str, lines: int = 25) -> str:
        try:
            return "\n".join(Path(path).read_text(errors="replace").splitlines()[-lines:])
        except OSError:
            return ""

    # ------------------------------------------------------------------ venue actions (real accounts)
    async def doctor(self, name: str, *, replacing: bool = False) -> tuple[bool, str]:
        """The live pre-start checks for a session (a name in config/sessions, or a path). replacing: the running live
        bot is closed before this one starts (Telegram's /run), so it is not a failure here."""
        from bot.cli import _doctor
        from bot.core.livelock import RunMode

        s = load_session(self.session_path(name))
        rep = await _doctor([s], RunMode.LIVE, replacing=replacing)
        return (not rep.failed), rep.render()

    async def cancel_all(self, mode: str, venue: str) -> str:
        from bot.cli import venue_cancel_all

        if mode == "paper":
            return "paper orders live inside the paper bot: use Pause or Stop instead"
        return await venue_cancel_all(venue, None, mode == "live")

    async def flatten(self, mode: str, venue: str, taker: bool) -> str:
        from bot.cli import venue_flatten

        if mode == "paper":
            return "paper positions live inside the paper bot: Pause lets its exit orders work them off"
        return await venue_flatten(venue, None, mode == "live", taker)

    # ------------------------------------------------------------------ reports and logs
    def report(self, mode: str, date: str) -> str | None:
        p = self.root / self.app.reports_for(mode) / "daily" / f"{date}.md"
        return p.read_text() if p.exists() else None

    def recent_decisions(self, n: int = 15, kinds: set[str] | None = None) -> list[dict[str, Any]]:
        p = self.root / self.app.logs_dir / "decisions.jsonl"
        if not p.exists():
            return []
        with open(p, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - 400_000))
            lines = fh.read().decode(errors="replace").splitlines()[1:]
        out = []
        for line in reversed(lines):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if kinds and r.get("event") not in kinds:
                continue
            out.append(r)
            if len(out) >= n:
                break
        return list(reversed(out))
