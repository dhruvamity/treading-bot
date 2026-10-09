"""`arcus sync`: a trader takes the lists from the machine that makes them.

Two machines (arcus/common/role.py): one records and ranks, the other runs the Telegram bot and trades. The trader
needs a few small files the other one writes: the last scan and its report, the autopilot's playbook and the markets'
usual volatility, the Lighter lists and order ceilings, and the recorders' health. Nothing else crosses.

How they are connected, and why it is safe enough to leave running:

- The TRADER asks (`pull`), over SSH, about every two minutes. Nothing ever logs in to the trader: it holds the
  trading keys, the other machine holds none.
- The key the trader uses can do ONE thing on the other machine: run `arcus sync serve` (a forced command in
  ~/.ssh/authorized_keys, with `restrict`: no shell, no forwarding, no file copy). `serve` writes out the files named
  in FILES below and nothing else, whatever it is asked.
- What arrives is treated as untrusted: only the names in FILES are taken, each at most MAX_FILE, each checked (JSON
  that parses and has the shape it should, a scan not dated in the future) before it replaces the file here.
- A machine that makes its own lists never takes someone else's (`may_receive`).

If the other machine were broken into, the attacker gets no way in here and no key; the worst they can do is hand
over wrong lists. The sizes and stops of a run are worked out here, from this machine's account and settings, the
leverage is capped by the market list this machine reads from Arcus itself, and a LIVE run still needs the owner.

    arcus sync key                    trader: make the key (once) and print its public half
    arcus sync allow 'ssh-ed25519 …'  other machine: let that key run `arcus sync serve`, and nothing else
    arcus sync pull                   trader: fetch now (the service does it by itself: BOT_SYNC_FROM in .env)
    arcus sync push user@trader       from a machine you scanned on by hand (your own computer), with your own SSH
    arcus sync status                 when it last worked, what it has

`serve` and `receive` are the two ends; you do not type them.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shlex
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import zlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from arcus.common import role as roles

FROM, KEY = "BOT_SYNC_FROM", "BOT_SYNC_KEY"
DEFAULT_KEY = "~/.ssh/treading_bot_sync"
# what crosses, per bot, relative to that bot's folder. `serve` sends nothing else; `unpack` takes nothing else.
FILES: dict[str, tuple[str, ...]] = {
    "arcus": ("data/scout/latest.json", "data/scout/report.txt", "data/scout/playbook.json", "data/scout/usual.json",
              "data/scout/scan_status.json", "data/scout/recorder.json"),
    "lighter": ("data/scout/latest.json", "data/scout/report.txt", "data/scout/ceilings.json", "data/recorder.json"),
}
# a JSON file must be an object with one of these keys, and its time must not be in the future
SHAPE: dict[str, tuple[str, ...]] = {"arcus/data/scout/latest.json": ("ts_us",),
                                     "lighter/data/scout/latest.json": ("t",),
                                     "arcus/data/scout/playbook.json": ("markets",)}
RECORDERS = {"arcus": ("arcus/data/scout/recorder.json", "ts"), "lighter": ("lighter/data/recorder.json", "t")}
MANIFEST = "handoff.json"
MAX_FILE = 32 * 2**20
MAX_TOTAL = 64 * 2**20
FUTURE_S = 600.0
STATE = "sync.json"
EVERY_S = 120.0
_DEST = re.compile(r"^([A-Za-z_][A-Za-z0-9_.-]{0,31})@([A-Za-z0-9][A-Za-z0-9.-]{0,252})(?::(\d{1,5}))?$")
_PUB = re.compile(r"^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(?:256|384|521)) ([A-Za-z0-9+/]{40,}={0,3})(?: .*)?$")
_SINCE = re.compile(r"^since=(\d{1,12}(?:\.\d{1,9})?)$")


class SyncError(Exception):
    """Something the owner can act on: the message says what."""


def roots(arcus: Path | None = None) -> dict[str, Path]:
    """The bots' folders, found by name next to treading-bot/arcus (the folder commands run from)."""
    a = (arcus or Path.cwd()).resolve()
    return {"arcus": a, "lighter": Path(os.environ.get("LBOT_ROOT") or a.parent / "lighter")}


def names() -> list[str]:
    return [f"{bot}/{rel}" for bot, rels in FILES.items() for rel in rels]


def may_receive(env: Mapping[str, str] | None = None) -> str:
    """Why this machine must not take another machine's lists ("" when it may): only a trader does. A machine that
    ranks would have its own scan replaced by an older one."""
    r = roles.role(env)
    return "" if r == "trader" else (f"this machine is {'the only one' if r == 'all' else 'a ' + r} "
                                     f"({roles.ENV}={r}): it makes its own lists. Only a trader takes them "
                                     f"({roles.ENV}=trader in .env)")


# ------------------------------------------------------------------------------------------------ checking a file
def check(name: str, data: bytes, now: float | None = None) -> str:
    """Why this file must not be taken ("" when it is good)."""
    if len(data) > MAX_FILE:
        return f"larger than {MAX_FILE // 2**20} MB"
    if not name.endswith(".json"):
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            return "not text"
        return ""
    try:
        d = json.loads(data)
    except ValueError:
        return "not JSON (cut off while it was being written?)"
    if not isinstance(d, dict):
        return "not a JSON object"
    need = SHAPE.get(name)
    if need:
        if not any(k in d for k in need):
            return f"no {need[0]} in it"
        k = need[0]
        if k in ("ts_us", "t"):
            try:
                t = float(d[k]) / (1e6 if k == "ts_us" else 1.0)
            except (TypeError, ValueError):
                return f"{k} is not a time"
            if t > (now or time.time()) + FUTURE_S:
                return "dated in the future (is the other machine's clock right?)"
    return ""


# ------------------------------------------------------------------------------------------------ the sending end
def pack(where: Mapping[str, Path], since: float = 0.0, now: float | None = None) -> bytes:
    """A gzipped tar of the FILES changed after `since` (their modification time on this machine), with a manifest
    of all of them first. A file that does not pass `check` (half written) is left for the next time."""
    now = now or time.time()
    man: dict[str, Any] = {"t": now, "role": _role_word(), "files": {}, "left_out": {}, "recorder_age_s": {}}
    out: list[tuple[str, bytes, float]] = []
    for bot, rels in FILES.items():
        root = where.get(bot)
        if root is None:
            continue
        for rel in rels:
            p, name = root / rel, f"{bot}/{rel}"
            try:
                st = p.stat()
                data = p.read_bytes() if st.st_mtime > since and st.st_size <= MAX_FILE else b""
            except OSError:
                continue
            man["files"][name] = [st.st_mtime, st.st_size]
            if st.st_mtime <= since:
                continue
            why = check(name, data, now) if st.st_size <= MAX_FILE else f"larger than {MAX_FILE // 2**20} MB"
            if why:
                man["left_out"][name] = why
            else:
                out.append((name, data, st.st_mtime))
    for bot, (name, key) in RECORDERS.items():
        with contextlib.suppress(OSError, ValueError, TypeError, KeyError):
            root = where[bot]
            man["recorder_age_s"][bot] = round(now - float(json.loads((root / name.split("/", 1)[1]).read_text())[key]), 1)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", format=tarfile.PAX_FORMAT) as tar:
        for name, data, mtime in [(MANIFEST, json.dumps(man).encode(), now), *out]:
            ti = tarfile.TarInfo(name)
            ti.size, ti.mtime, ti.mode = len(data), mtime, 0o644
            tar.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


def _role_word() -> str:
    try:
        return roles.role()
    except ValueError:
        return "?"


def since_of(command: str | None) -> float:
    """The `since=<seconds>` the trader sent (SSH_ORIGINAL_COMMAND); anything else means everything."""
    m = _SINCE.match((command or "").strip())
    return float(m.group(1)) if m else 0.0


# ------------------------------------------------------------------------------------------------ the taking end
@dataclass
class Taken:
    files: dict[str, float] = field(default_factory=dict)       # name -> its time on the other machine
    refused: dict[str, str] = field(default_factory=dict)        # name -> why
    remote: dict[str, Any] = field(default_factory=dict)         # the other machine's manifest
    since: float = 0.0


def unpack(where: Mapping[str, Path], data: bytes, now: float | None = None) -> Taken:
    """Check what arrived and put the good files in place (each replaced whole, keeping the other machine's time).
    SyncError when it is not a handoff at all."""
    if len(data) > MAX_TOTAL:
        raise SyncError(f"more than {MAX_TOTAL // 2**20} MB arrived: refused")
    allowed = set(names())
    got = Taken()
    room = 3 * MAX_FILE          # of unpacked bytes in all: a small download must not unpack into gigabytes
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            for n, m in enumerate(tar):
                room -= max(0, m.size)
                if n > len(allowed) + 4:
                    raise SyncError("more files than a handoff has: refused")
                if m.size > MAX_FILE or room < 0:      # stop here: stepping over it would unpack all of it
                    raise SyncError(f"a file larger than a handoff carries ({m.name[:80]}): refused")
                if not m.isreg():
                    got.refused[m.name[:120]] = "not a plain file"
                    continue
                fh = tar.extractfile(m)
                body = fh.read(MAX_FILE + 1) if fh is not None else b""
                if m.name == MANIFEST:
                    with contextlib.suppress(ValueError):
                        d = json.loads(body)
                        got.remote = d if isinstance(d, dict) else {}
                    continue
                if m.name not in allowed:
                    got.refused[m.name[:120]] = "not a file a handoff carries"
                    continue
                why = check(m.name, body, now)
                if why:
                    got.refused[m.name] = why
                    continue
                bot, rel = m.name.split("/", 1)
                root = where.get(bot)
                if root is None or not root.is_dir():
                    got.refused[m.name] = f"no {bot} folder on this machine"
                    continue
                _install(root / rel, body, float(m.mtime))
                got.files[m.name] = float(m.mtime)
                got.since = max(got.since, float(m.mtime))
    except (tarfile.TarError, EOFError, zlib.error) as e:
        raise SyncError("what arrived is not a handoff (the other machine printed something else: is `arcus sync "
                        f"allow` set up there?): {type(e).__name__}") from e
    except OSError as e:
        raise SyncError(f"could not write a file here: {e}") from e
    return got


def _install(dest: Path, body: bytes, mtime: float) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".sync-tmp")
    tmp.write_bytes(body)
    os.utime(tmp, (mtime, mtime))
    os.replace(tmp, dest)


# ------------------------------------------------------------------------------------------------ over SSH
@dataclass(frozen=True)
class Dest:
    user: str
    host: str
    port: int = 22

    @classmethod
    def parse(cls, text: str | None) -> Dest:
        m = _DEST.match((text or "").strip())
        if not m or not 1 <= int(m.group(3) or 22) <= 65535:
            raise SyncError(f"{FROM} must look like user@host or user@host:port (got {(text or '')[:60]!r})")
        return cls(m.group(1), m.group(2), int(m.group(3) or 22))

    def __str__(self) -> str:
        return f"{self.user}@{self.host}" + ("" if self.port == 22 else f":{self.port}")


def key_path(env: Mapping[str, str] | None = None) -> Path:
    return Path((os.environ if env is None else env).get(KEY) or DEFAULT_KEY).expanduser()


def _ssh(cmd: list[str], *, stdin: bytes | None = None, timeout_s: float = 90.0) -> bytes:
    """Run ssh, returning at most MAX_TOTAL of what it printed. SyncError with ssh's own words when it fails."""
    with tempfile.TemporaryFile() as err:
        try:
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err,
                                 stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL)
        except OSError as e:
            raise SyncError(f"cannot run ssh: {e}") from e
        timer = threading.Timer(timeout_s, p.kill)
        timer.start()
        try:
            if stdin is not None and p.stdin is not None:
                with contextlib.suppress(BrokenPipeError):
                    p.stdin.write(stdin)
                p.stdin.close()
            assert p.stdout is not None
            data = p.stdout.read(MAX_TOTAL + 1)
            if len(data) > MAX_TOTAL:
                p.kill()
                raise SyncError(f"more than {MAX_TOTAL // 2**20} MB arrived: refused")
            code = p.wait()
        finally:
            timer.cancel()
        err.seek(0)
        said = err.read(4000).decode(errors="replace").strip().splitlines()
    if code != 0:
        why = said[-1][:200] if said else "no message"
        raise SyncError(f"ssh failed ({'timed out' if code < 0 else code}): {why}")
    return data


def ssh_fetch(dest: Dest, key: Path, since: float) -> bytes:
    if not key.is_file():
        raise SyncError(f"no key at {key}: run `arcus sync key` on this machine, then `arcus sync allow` on the other")
    return _ssh(["ssh", "-i", str(key), "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-o", "ConnectTimeout=10",
                 "-o", "ServerAliveInterval=10", "-o", "ServerAliveCountMax=3",
                 "-o", "StrictHostKeyChecking=accept-new", "-p", str(dest.port), f"{dest.user}@{dest.host}",
                 f"since={since:.6f}"])


def ssh_push(dest: Dest, remote_arcus: str, data: bytes) -> str:
    """Hand `data` to `arcus sync receive` on the trader, with the owner's own SSH access (no special key)."""
    out = _ssh(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-p", str(dest.port),
                f"{dest.user}@{dest.host}", f"cd {shlex.quote(remote_arcus)} && .venv/bin/arcus sync receive"],
               stdin=data, timeout_s=180.0)
    return out.decode(errors="replace").strip()


# ------------------------------------------------------------------------------------------------ the trader's side
def load_state(state_dir: Path | str) -> dict[str, Any]:
    try:
        d = json.loads((Path(state_dir) / STATE).read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(state_dir: Path | str, st: dict[str, Any]) -> None:
    p = Path(state_dir) / STATE
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1))
    tmp.replace(p)


def take(where: Mapping[str, Path], state_dir: Path | str, data: bytes, source: str, now: float | None = None
         ) -> dict[str, Any]:
    """Unpack what arrived and note it in state/sync.json. Raises SyncError (the state then keeps the reason)."""
    now = now or time.time()
    st = load_state(state_dir)
    st.update(last_try=now, source=source)
    try:
        got = unpack(where, data, now)
    except SyncError as e:
        st["error"] = str(e)
        save_state(state_dir, st)
        raise
    st.update(last_ok=now, error="", since=max(float(st.get("since") or 0), got.since), refused=got.refused,
              files={**(st.get("files") or {}), **got.files}, remote=got.remote, took=sorted(got.files))
    save_state(state_dir, st)
    return st


def pull(where: Mapping[str, Path], state_dir: Path | str, env: Mapping[str, str] | None = None, *,
         fetch: Callable[[Dest, Path, float], bytes] = ssh_fetch, now: float | None = None) -> dict[str, Any]:
    """One fetch from BOT_SYNC_FROM. Never raises: the state says what happened (`error` is "" when it worked)."""
    e = os.environ if env is None else env
    now = now or time.time()
    try:
        why = may_receive(e)
        if why:
            raise SyncError(why)
        dest = Dest.parse(e.get(FROM))
        data = fetch(dest, key_path(e), float(load_state(state_dir).get("since") or 0))
        return take(where, state_dir, data, str(dest), now)
    except (SyncError, ValueError) as err:
        st = load_state(state_dir)
        st.update(last_try=now, error=str(err), source=str(e.get(FROM) or ""))
        save_state(state_dir, st)
        return st


def ago(seconds: float | None) -> str:
    if seconds is None:
        return "never"
    s = int(max(0.0, seconds))
    return f"{s // 3600} h {s % 3600 // 60} min" if s >= 3600 else f"{s // 60} min" if s >= 60 else f"{s} s"


def status_lines(state_dir: Path | str, env: Mapping[str, str] | None = None, now: float | None = None) -> list[str]:
    """What `arcus sync status` and `arcus status` say on a trader."""
    e = os.environ if env is None else env
    now = now or time.time()
    st = load_state(state_dir)
    src = str(e.get(FROM) or "")
    if not src and not st.get("last_ok"):
        return [f"not set up: this trader runs on its own. To take lists from another machine, put {FROM}=user@host "
                "in .env (the README, \"Two machines\")"]
    ok = float(st["last_ok"]) if st.get("last_ok") else None
    out = [f"from {src or st.get('source') or '?'} · last worked {ago(now - ok) + ' ago' if ok else 'never'}"]
    if st.get("error"):
        out.append(f"last try failed: {st['error']}")
    for name, t in sorted((st.get("files") or {}).items()):
        out.append(f"  {name:<34} {ago(float(st.get('remote', {}).get('t') or now) - float(t))} old there")
    for name, why in sorted((st.get("refused") or {}).items()):
        out.append(f"  {name:<34} NOT TAKEN: {why}")
    return out


# ------------------------------------------------------------------------------------------------ setting it up
def make_key(path: Path) -> str:
    """Create the trader's key pair if there is none (no passphrase: a service uses it) and return the public half."""
    pub = path.with_name(path.name + ".pub")
    if not path.exists():
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "treading-bot-sync", "-f", str(path)],
                           check=True, capture_output=True, timeout=30)
        except (OSError, subprocess.SubprocessError) as e:
            raise SyncError(f"ssh-keygen failed: {e}") from e
    try:
        return pub.read_text().strip()
    except OSError as e:
        raise SyncError(f"{pub} is missing: delete {path} and run this again") from e


def _public(public_key: str) -> tuple[str, str]:
    """(key type, the key itself) of a public key line."""
    m = _PUB.match(public_key.strip())
    if not m:
        raise SyncError("that is not a public key: paste the whole line `arcus sync key` printed, in quotes "
                        "(it starts with ssh-ed25519)")
    return m.group(1), m.group(2)


def allow_line(public_key: str, arcus_root: Path, arcus_bin: Path | None = None) -> str:
    """The authorized_keys line that lets `public_key` run `arcus sync serve` here and nothing else."""
    kind, blob = _public(public_key)
    exe = arcus_bin or Path(sys.executable).with_name("arcus")
    for p in (arcus_root, exe):
        if any(c in str(p) for c in '"\\\n'):
            raise SyncError(f"cannot use a folder with a quote or a backslash in its name: {p}")
    cmd = f"BOT_HOME={shlex.quote(str(arcus_root))} {shlex.quote(str(exe))} sync serve"
    return f'command="{cmd}",restrict {kind} {blob} treading-bot-sync'


def allow(public_key: str, arcus_root: Path, authorized_keys: Path | None = None, arcus_bin: Path | None = None
          ) -> tuple[str, bool]:
    """Add that line to ~/.ssh/authorized_keys (kept as it is otherwise). (the line, added now?)"""
    line = allow_line(public_key, arcus_root, arcus_bin)
    ak = authorized_keys or Path("~/.ssh/authorized_keys").expanduser()
    blob = _public(public_key)[1]
    try:
        old = ak.read_text()
    except OSError:
        old = ""
    if any(blob in ln and "sync serve" in ln for ln in old.splitlines()):
        return line, False
    ak.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with ak.open("a") as f:
        f.write(("" if not old or old.endswith("\n") else "\n") + line + "\n")
    os.chmod(ak, 0o600)
    return line, True
