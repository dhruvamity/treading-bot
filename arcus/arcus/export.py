"""`arcus export` and `arcus import`: carry one machine's recordings, trades and state to another in ONE file.

    arcus export                     everything new since the last export (the first time: everything)
    arcus export --full              everything again
    arcus export --days 2            a small one: all state and trades, the logs and tape of the last 2 UTC days
    arcus export --since 2026-10-01  the same, from that UTC day on
    arcus export --no-tape           state, trades and logs only (a quick error report)
    arcus import [FILE]              on the other machine: check it, merge the tape, unpack the rest

The file is treading-bot/exports/tb-<YYYYMMDD>-<HHMM>Z.tar (UTC). Inside it, under one folder of the same name:

    SUMMARY.md       read this first: what ran, the trades, problems in the logs, how complete the recording is
    records.tar.gz   arcus/ lighter/ arbitrage/ (state, logs, scans, reports, config, fills and orders as CSV), system/
    coverage.csv     rows and hours per venue, market, UTC day and kind, counted here before packing
    tape/<venue>/<MARKET>/<UTC day>/*.npz    the market tape, as recorded
    files.csv, MANIFEST.json    every tape file's size and SHA-256; written last, so a cut-off copy is noticed

Never in it: .env files, config/secrets.enc, pid files. Every secret value found in a .env is also blanked out of
every text file and database copy, in case a log line ever carried one.

The live folders are only read: databases through SQLite's backup, each tape file through one open handle (the
recorder replaces its current part every few minutes; a handle keeps the version it opened). So an export is safe
while the bot trades and records. Only a plain `arcus export` or `--full` moves the "since the last export" mark.

`arcus import` never touches this machine's own state or logs: the tape is merged into the tape folders (a file
already here is kept, unless the incoming one is the same recorder part with more rows), everything else goes to
arcus/data/server-export/<name>/.
"""

from __future__ import annotations

import contextlib
import csv
import datetime as dt
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import tarfile
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any

import numpy as np

PREFIX = "tb"
NAME_RE = re.compile(r"^tb(-[a-z0-9]{1,16})?-\d{8}-\d{4}Z$")
TAPE_RE = re.compile(r"^tape/(arcus|lighter)/([A-Za-z0-9][A-Za-z0-9._-]*)/(\d{4}-\d{2}-\d{2})/"
                     r"([A-Za-z0-9][A-Za-z0-9._-]*\.npz)$")
MARK_FILE = "export.json"          # in arcus/state: when the last regular export was made
MARGIN_S = 900.0                   # "new since the last export" reaches this far back, so nothing falls between two
OUT_TAIL = 20 * 2**20              # of a service's own output (logs/*.out), at most this much, from its end
RESERVE_GB = 6.0                   # the recorders pause under 5 GB free: an export never takes the disk below this
GAP_CAP_US = 600_000_000           # a gap over 10 minutes counts as not recorded (as DayTape.bbo_hours)
REDACTED = b"[redacted]"

BINARY = (".sqlite", ".npz", ".parquet", ".gz", ".zst", ".pkl", ".png", ".jpg", ".zip", ".db", ".tar")
SKIP_NAMES = {".env", "secrets.enc", ".DS_Store", MARK_FILE}
SKIP_SUFFIXES = (".pid", ".sqlite-wal", ".sqlite-shm", ".tmp", ".tmp.npz", ".partial", ".importing")
SKIP_DIRS = {"__pycache__", ".venv", ".git", "cache", "tape", "server-export"}
SECRET_NAME = re.compile(r"PRIVATE|SECRET|TOKEN|PASSWORD|PASSPHRASE|MNEMONIC|SEED|API_KEY", re.I)
TELEGRAM_TOKEN = re.compile(rb"\b\d{6,}:[A-Za-z0-9_-]{30,}")
COVERAGE_FIELDS = ["venue", "market", "day", "kind", "parts", "bytes", "rows", "unique_ts", "first_utc", "last_utc",
                   "hours"]

Say = Callable[[str], None]


class ExportError(Exception):
    """Something the owner can act on: the message says what."""


# ------------------------------------------------------------------------------------------------ where things are
@dataclass(frozen=True)
class Roots:
    """The three parts' folders. `arcus` is treading-bot/arcus (the folder commands run from)."""

    arcus: Path
    lighter: Path
    arbitrage: Path

    @property
    def repo(self) -> Path:
        return self.arcus.parent

    @classmethod
    def find(cls, arcus: Path | None = None) -> Roots:
        arcus = (arcus or Path.cwd()).resolve()
        lighter, arbitrage = arcus.parent / "lighter", arcus.parent / "arbitrage"
        with contextlib.suppress(Exception):      # a part installed elsewhere says where its data is
            from lighter_bot.config import ROOT as LROOT

            lighter = Path(LROOT)
        with contextlib.suppress(Exception):
            from arbitrage.config import ROOT as AROOT

            arbitrage = Path(AROOT)
        return cls(arcus, lighter, arbitrage)

    def tapes(self) -> dict[str, Path]:
        return {"arcus": self.arcus / "data" / "scout" / "tape", "lighter": self.lighter / "data" / "tape"}


@dataclass(frozen=True)
class Source:
    """One folder (or file) that goes into records.tar.gz under `label`."""

    label: str
    path: Path
    always: bool = False       # every file however old (small state); else only files changed since the cut
    flat: bool = False         # the files directly inside only


def sources(r: Roots) -> list[Source]:
    b, li, a = r.arcus, r.lighter, r.arbitrage
    return [
        Source("arcus/state", b / "state", always=True),
        Source("arcus/config", b / "config", always=True),
        Source("arcus/data/scout", b / "data" / "scout", always=True, flat=True),   # the latest scan, lists, health
        Source("arcus/data", b / "data"),                                           # scans, reports, playbook days
        Source("arcus/logs", b / "logs"),
        Source("arcus/reports", b / "reports"),
        Source("lighter/state", li / "state", always=True),
        Source("lighter/config", li / "config", always=True),
        Source("lighter/data", li / "data", always=True, flat=True),
        Source("lighter/data/scout", li / "data" / "scout", always=True, flat=True),
        Source("lighter/data", li / "data"),
        Source("lighter/logs", li / "logs"),
        Source("arbitrage/state", a / "state", always=True),
        Source("arbitrage/settings.json", a / "settings.json", always=True),
        Source("arbitrage/data", a / "data"),
    ]


@dataclass(frozen=True)
class Cut:
    """What an export takes. ts: files changed after it (None = all). day: tape day folders from it on."""

    mode: str                    # full | new | since | days
    ts: float | None = None
    day: str | None = None
    tape: bool = True

    @property
    def moves_mark(self) -> bool:
        return self.mode in ("full", "new") and self.tape

    def words(self) -> str:
        when = "" if self.ts is None else time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(self.ts))
        what = {"full": "everything", "new": f"what is new since the last export ({when})",
                "since": f"from {self.day} (UTC) on", "days": f"from {self.day} (UTC) on"}[self.mode]
        return what + ("" if self.tape else ", without the tape")


def read_mark(state: Path) -> dict[str, Any]:
    try:
        d = json.loads((state / MARK_FILE).read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def choose_cut(state: Path, *, full: bool = False, days: int | None = None, since: str | None = None,
               tape: bool = True, now: float | None = None) -> Cut:
    now = time.time() if now is None else now
    if since or days:
        if since:
            try:
                day = dt.datetime.strptime(since, "%Y-%m-%d").strftime("%Y-%m-%d")
            except ValueError:
                raise ExportError(f"--since takes a UTC day like 2026-10-01 (got {since!r})") from None
        else:
            day = time.strftime("%Y-%m-%d", time.gmtime(now - (max(1, int(days or 1)) - 1) * 86400))
        ts = dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=dt.UTC).timestamp()
        return Cut("since" if since else "days", ts, day, tape)
    last = read_mark(state).get("last_ts")
    if full or not last:
        return Cut("full", None, None, tape)
    return Cut("new", float(last) - MARGIN_S, None, tape)


# ------------------------------------------------------------------------------------------------ secrets
def _env_pairs(p: Path) -> Iterator[tuple[str, str]]:
    try:
        lines = p.read_text(errors="ignore").splitlines()
    except OSError:
        return
    for line in lines:
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            k, _, v = s.partition("=")
            yield k.strip(), v.strip().strip('"').strip("'")


def secret_values(r: Roots, environ: dict[str, str] | None = None) -> list[bytes]:
    """Every secret this machine holds (by the variable's name), longest first: blanked out of whatever is copied."""
    found: set[str] = set()
    pairs = list((environ if environ is not None else dict(os.environ)).items())
    for root in (r.arcus, r.lighter, r.arbitrage):
        pairs += list(_env_pairs(root / ".env"))
    for k, v in pairs:
        if SECRET_NAME.search(k) and not k.upper().endswith("_INDEX") and len(v) >= 8:
            found.add(v)
            if v.lower().startswith("0x"):
                found.add(v[2:])
    return sorted((s.encode() for s in found), key=len, reverse=True)


def scrub(data: bytes, secrets: list[bytes]) -> bytes:
    for s in secrets:
        if s in data:
            data = data.replace(s, REDACTED)
    return TELEGRAM_TOKEN.sub(REDACTED, data)


# ------------------------------------------------------------------------------------------------ copying records
def _wanted(p: Path) -> bool:
    n = p.name
    return n not in SKIP_NAMES and not n.endswith(SKIP_SUFFIXES) and not p.is_symlink()


def _walk(root: Path, flat: bool) -> Iterator[Path]:
    """Files under root, never into the tape, the backtest cache, earlier imports or virtual environments."""
    try:
        entries = sorted(os.scandir(root), key=lambda e: e.name)
    except OSError:
        return
    for e in entries:
        p = Path(e.path)
        if e.is_symlink():
            continue
        if e.is_dir():
            if not flat and e.name not in SKIP_DIRS:
                yield from _walk(p, False)
        elif _wanted(p):
            yield p


def pick_records(r: Roots, cut: Cut) -> dict[str, Path]:
    """{path inside records: file here}. Small state always; bulk folders only what changed since the cut."""
    out: dict[str, Path] = {}
    for s in sources(r):
        if s.path.is_file():
            if _wanted(s.path):
                out[s.label] = s.path
            continue
        for p in _walk(s.path, s.flat):
            rel = f"{s.label}/{p.relative_to(s.path).as_posix()}"
            if rel in out:
                continue
            try:
                if s.always or cut.ts is None or p.stat().st_mtime >= cut.ts:
                    out[rel] = p
            except OSError:
                continue
    return out


def copy_sqlite(src: Path, dst: Path, secrets: list[bytes]) -> None:
    """A consistent copy of a database that may be in use (SQLite's own backup), then every secret blanked in it."""
    a = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=30)
    b = sqlite3.connect(dst)
    try:
        a.backup(b)
        changed = 0
        for s in (x.decode() for x in secrets):
            for (table,) in b.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
                for col in b.execute(f'PRAGMA table_info("{table}")').fetchall():
                    cur = b.execute(f'UPDATE "{table}" SET "{col[1]}" = replace("{col[1]}", ?, ?) '
                                    f'WHERE typeof("{col[1]}") = \'text\' AND instr("{col[1]}", ?) > 0',
                                    (s, REDACTED.decode(), s))
                    changed += cur.rowcount
        b.commit()
        if changed:
            b.execute("VACUUM")     # the old text must not stay behind in freed pages
    finally:
        a.close()
        b.close()


def copy_file(src: Path, dst: Path, secrets: list[bytes]) -> str:
    """Copy one record file with the secrets blanked. Returns a note when it was not copied whole."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    note = ""
    if src.suffix == ".sqlite":
        copy_sqlite(src, dst, secrets)
    elif src.name.endswith(BINARY):
        shutil.copyfile(src, dst)
    else:                           # text: line by line, so a large log never sits in memory
        with src.open("rb") as fi, dst.open("wb") as fo:
            size = os.fstat(fi.fileno()).st_size
            if src.suffix == ".out" and size > OUT_TAIL:     # a service's own output is never rotated: its end
                fi.seek(size - OUT_TAIL)
                fi.readline()                                # (the .jsonl logs hold every line of it, by day)
                note = f"the last {OUT_TAIL // 2**20} MB of {size / 2**20:,.0f} MB"
            for line in fi:
                fo.write(scrub(line, secrets))
    return note


# ------------------------------------------------------------------------------------------------ the tape
def _size(p: Path) -> int:
    try:
        return p.stat().st_size
    except OSError:
        return 0


def _is_part(name: str) -> bool:
    return name.endswith(".npz") and not name.endswith(".tmp.npz")


@dataclass
class TapePick:
    send: list[tuple[str, str, Path]] = field(default_factory=list)                  # (market, day, file)
    present: dict[tuple[str, str], list[Path]] = field(default_factory=dict)         # every part of those days


def pick_tape(root: Path, cut: Cut) -> TapePick:
    """The tape parts the cut takes, and for each of their days every part that is there now. The count sent with
    the export covers exactly those: a part the recorder starts a minute later belongs to the next export."""
    pick = TapePick()
    try:
        markets = sorted((e for e in os.scandir(root) if e.is_dir()), key=lambda e: e.name)
    except OSError:
        return pick
    for m in markets:
        for d in sorted((e for e in os.scandir(m.path) if e.is_dir()), key=lambda e: e.name):
            if cut.day and d.name < cut.day:
                continue
            here = sorted(Path(f.path) for f in os.scandir(d.path) if _is_part(f.name))
            chosen = []
            for f in here:
                with contextlib.suppress(OSError):
                    if cut.day or cut.ts is None or f.stat().st_mtime >= cut.ts:
                        chosen.append((m.name, d.name, f))
            if chosen:
                pick.send += chosen
                pick.present[(m.name, d.name)] = here
    return pick


def _stamps(p: Path) -> np.ndarray | None:
    try:
        with np.load(p) as z:
            return np.asarray(z["ts"], np.int64)
    except Exception:
        return None


def _utc(us: int) -> str:
    return dt.datetime.fromtimestamp(us / 1e6, dt.UTC).strftime("%Y-%m-%dT%H:%M:%S")


def count_parts(files: list[Path]) -> dict[str, dict[str, Any]]:
    """Per kind (bbo, depth, trades, stats) over these part files: parts, bytes, rows, distinct timestamps, first
    and last time, hours covered (gaps over 10 minutes count as missing)."""
    by_kind: dict[str, list[Path]] = {}
    for f in files:
        by_kind.setdefault(f.name.split("-", 1)[0], []).append(f)
    out: dict[str, dict[str, Any]] = {}
    for kind, group in sorted(by_kind.items()):
        stamps = [t for t in (_stamps(f) for f in group) if t is not None and len(t)]
        ts = np.unique(np.concatenate(stamps)) if stamps else np.zeros(0, np.int64)
        hours = float(np.minimum(np.diff(ts), GAP_CAP_US).sum()) / 3.6e9 if len(ts) > 1 else 0.0
        out[kind] = {"parts": len(group), "bytes": sum(_size(f) for f in group),
                     "rows": int(sum(len(t) for t in stamps)), "unique_ts": len(ts),
                     "first_utc": _utc(int(ts[0])) if len(ts) else "", "last_utc": _utc(int(ts[-1])) if len(ts) else "",
                     "hours": round(hours, 2)}
    return out


def count_day(day_dir: Path) -> dict[str, dict[str, Any]]:
    """The same over every part in a day folder."""
    try:
        return count_parts([Path(f.path) for f in os.scandir(day_dir) if _is_part(f.name)])
    except OSError:
        return {}


def coverage(picks: dict[str, TapePick]) -> list[dict[str, Any]]:
    rows = []
    for venue, pick in picks.items():
        for (market, day), files in sorted(pick.present.items()):
            for kind, c in count_parts(files).items():
                rows.append({"venue": venue, "market": market, "day": day, "kind": kind, **c})
    return rows


def coverage_text(rows: list[dict[str, Any]]) -> str:
    """Per venue and UTC day: markets, rows of each kind, how many markets have 20 h or more of book."""
    lines = []
    for venue in sorted({r["venue"] for r in rows}):
        vr = [r for r in rows if r["venue"] == venue]
        lines += [f"{venue}: {len({r['market'] for r in vr})} markets, {len({r['day'] for r in vr})} UTC days",
                  "UTC day     markets     bbo rows   depth rows  trade rows  markets>=20h  median bbo h"]
        for d in sorted({r["day"] for r in vr}):
            dr = [r for r in vr if r["day"] == d]
            n = {k: sum(int(r["rows"]) for r in dr if r["kind"] == k) for k in ("bbo", "depth", "trades")}
            hs = sorted(float(r["hours"]) for r in dr if r["kind"] == "bbo")
            lines.append(f"{d}  {len({r['market'] for r in dr}):7}  {n['bbo']:11,}  {n['depth']:11,}  "
                         f"{n['trades']:10,}  {sum(h >= 20 for h in hs):12}  {hs[len(hs) // 2] if hs else 0:12}")
        lines.append("")
    return "\n".join(lines).rstrip() or "no tape in this export"


class _Hashing:
    """A file handle that hashes what tarfile reads from it."""

    def __init__(self, fh: IO[bytes]) -> None:
        self.fh, self.sha = fh, hashlib.sha256()

    def read(self, n: int = -1) -> bytes:
        b = self.fh.read(n)
        self.sha.update(b)
        return b


def _add_bytes(tar: tarfile.TarFile, name: str, data: bytes, mtime: float) -> None:
    ti = tarfile.TarInfo(name)
    ti.size, ti.mtime, ti.mode = len(data), int(mtime), 0o644
    tar.addfile(ti, io.BytesIO(data))


def _add_file(tar: tarfile.TarFile, name: str, src: Path) -> tuple[int, str]:
    """Add one file through a single handle, so a file replaced meanwhile goes in whole. (size, sha256)."""
    with src.open("rb") as fh:
        st = os.fstat(fh.fileno())
        ti = tarfile.TarInfo(name)
        ti.size, ti.mtime, ti.mode = st.st_size, int(st.st_mtime), 0o644
        h = _Hashing(fh)
        tar.addfile(ti, h)
        return st.st_size, h.sha.hexdigest()


# ------------------------------------------------------------------------------------------------ export
@dataclass
class Result:
    name: str
    path: Path
    bytes: int
    tape_files: int
    record_files: int
    cut: Cut
    warnings: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)


def export_name(now: float, tag: str = "") -> str:
    tag = re.sub(r"[^a-z0-9]", "", tag.lower())[:16]
    return f"{PREFIX}{'-' + tag if tag else ''}-{time.strftime('%Y%m%d-%H%M', time.gmtime(now))}Z"


def run_export(r: Roots, out_dir: Path | None = None, *, full: bool = False, days: int | None = None,
               since: str | None = None, tape: bool = True, keep: int = 3, tag: str = "", probe: bool = True,
               force: bool = False, say: Say = print, now: float | None = None) -> Result:
    from arcus import export_report as report

    now = time.time() if now is None else now
    with contextlib.suppress(OSError, AttributeError):
        os.nice(10)                                   # never compete with a trading bot for the CPU
    state = r.arcus / "state"
    cut = choose_cut(state, full=full, days=days, since=since, tape=tape, now=now)
    out_dir = (out_dir or r.repo / "exports").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    name = export_name(now, tag)
    final = out_dir / f"{name}.tar"
    if final.exists():
        raise ExportError(f"{final} already exists: wait a minute, or remove it")
    say(f"export {name}: {cut.words()}")

    records = pick_records(r, cut)
    picks = {v: pick_tape(root, cut) if cut.tape else TapePick() for v, root in r.tapes().items()}
    parts = [(v, m, d, p) for v, pick in picks.items() for m, d, p in pick.send]
    need = sum(_size(p) for p in records.values()) * 1.3 + sum(_size(p) for *_, p in parts)
    free = shutil.disk_usage(out_dir).free
    say(f"  {len(records):,} record files and {len(parts):,} tape files, about {need / 1e9:.2f} GB "
        f"({free / 1e9:.1f} GB free on that disk)")
    if free - need < RESERVE_GB * 1e9 and not force:
        raise ExportError(
            f"not enough room: it needs about {need / 1e9:.1f} GB and must leave {RESERVE_GB:g} GB free (the "
            f"recorders pause under 5 GB), but only {free / 1e9:.1f} GB is free. Use --out on another disk, a "
            "smaller export (--days 2, --no-tape), or --force.")

    secrets = secret_values(r)
    warnings: list[str] = []
    work = out_dir / f".{name}.partial"
    shutil.rmtree(work, ignore_errors=True)
    rec = work / "records"
    try:
        for rel, src in records.items():
            try:
                if note := copy_file(src, rec / rel, secrets):
                    warnings.append(f"{rel}: {note}")
            except Exception as e:       # one unreadable file never stops the export; the summary says which
                warnings.append(f"{rel}: {type(e).__name__}: {e}")
        meta = report.write_all(rec, r, name=name, cut=cut, now=now, secrets=secrets, probe=probe,
                                prev=str(read_mark(state).get("last_name") or ""), warnings=warnings)
        if parts:
            say("  counting the tape (rows and hours per market and day)...")
        cov = coverage(picks)
        summary = report.summary(rec, meta, coverage_text(cov), warnings)
        (work / "records.tar.gz").parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(work / "records.tar.gz", "w:gz", compresslevel=6) as tz:
            tz.add(rec, arcname="records")
        cov_csv = io.StringIO()
        w = csv.DictWriter(cov_csv, fieldnames=COVERAGE_FIELDS)
        w.writeheader()
        w.writerows(cov)

        files: list[tuple[str, int, str]] = []
        with tarfile.open(str(final) + ".partial", "w") as tar:
            _add_bytes(tar, f"{name}/SUMMARY.md", summary.encode(), now)
            files.append(("records.tar.gz", *_add_file(tar, f"{name}/records.tar.gz", work / "records.tar.gz")))
            _add_bytes(tar, f"{name}/coverage.csv", cov_csv.getvalue().encode(), now)
            for i, (venue, market, day, p) in enumerate(parts, 1):
                rel = f"tape/{venue}/{market}/{day}/{p.name}"
                if not TAPE_RE.match(rel):
                    warnings.append(f"{rel}: a name `arcus import` would refuse; left out")
                    continue
                try:
                    files.append((rel, *_add_file(tar, f"{name}/{rel}", p)))
                except OSError as e:
                    warnings.append(f"{rel}: {type(e).__name__}: {e}")
                if i % 5000 == 0:
                    say(f"  tape: {i:,} of {len(parts):,} files")
            listing = "path,bytes,sha256\n" + "".join(f"{a},{b},{c}\n" for a, b, c in files)
            _add_bytes(tar, f"{name}/files.csv", listing.encode(), now)
            manifest = {**meta, "tape_files": len(files) - 1, "record_files": len(records), "warnings": warnings,
                        "tape_bytes": sum(b for a, b, _ in files if a.startswith("tape/"))}
            _add_bytes(tar, f"{name}/MANIFEST.json", json.dumps(manifest, indent=1).encode(), now)
        os.replace(str(final) + ".partial", final)
    finally:
        shutil.rmtree(work, ignore_errors=True)
        with contextlib.suppress(OSError):
            os.unlink(str(final) + ".partial")

    size = final.stat().st_size
    mark = read_mark(state)
    hist = [*list(mark.get("history") or []), {"name": name, "ts": now, "mode": cut.mode, "tape": cut.tape,
                                                 "bytes": size}][-30:]
    if cut.moves_mark:
        mark.update(last_ts=now, last_name=name)
    mark["history"] = hist
    state.mkdir(parents=True, exist_ok=True)
    (state / MARK_FILE).write_text(json.dumps(mark, indent=1))
    removed = []
    if keep > 0:                                       # older files: their data is still in the live folders
        old = sorted(out_dir.glob(f"{PREFIX}-*.tar"), key=lambda p: p.stat().st_mtime)[:-keep]
        for p in old:
            with contextlib.suppress(OSError):
                p.unlink()
                removed.append(p.name)
    return Result(name, final, size, len(files) - 1, len(records), cut, warnings, removed)


# ------------------------------------------------------------------------------------------------ import
def find_export(r: Roots, given: str | None) -> Path:
    """The file to import: the one given, else the newest tb-*.tar in a folder given, treading-bot/exports/,
    ~/Downloads or here."""
    places = [r.repo / "exports", Path.home() / "Downloads", Path.cwd()]
    if given:
        p = Path(given).expanduser()
        if p.is_file():
            return p.resolve()
        if not p.is_dir():
            raise ExportError(f"no such file: {p}")
        places = [p]
    found = [f for d in places if d.is_dir() for f in d.glob(f"{PREFIX}-*.tar")]
    if not found:
        raise ExportError("no export file found: give its path, or put it in treading-bot/exports/ or ~/Downloads")
    return max(found, key=lambda f: f.stat().st_mtime).resolve()


def _rows(p: Path) -> int:
    t = _stamps(p)
    return -1 if t is None else len(t)


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run_import(r: Roots, path: Path, *, say: Say = print) -> dict[str, Any]:
    """Check an export file and take it in. Returns the counts; `ok` is False when anything is damaged or short."""
    try:
        tar = tarfile.open(path, "r:")       # noqa: SIM115 - closed by the `with` below
    except (tarfile.TarError, OSError) as err:
        raise ExportError(f"{path.name} is not an export file ({err})") from None
    with tar:
        try:
            members = {m.name: m for m in tar.getmembers() if m.isfile()}
        except (tarfile.TarError, EOFError):
            raise ExportError(f"{path.name} is incomplete: the copy was cut off; copy it again") from None
        tops = {n.split("/", 1)[0] for n in members}
        if len(tops) != 1 or not NAME_RE.match(next(iter(tops))):
            raise ExportError(f"{path.name} is not an export made by `arcus export`")
        name = next(iter(tops))

        def read(rel: str) -> bytes:
            m = members.get(f"{name}/{rel}")
            fh = tar.extractfile(m) if m else None
            if fh is None:
                raise ExportError(f"{path.name} is incomplete ({rel} is missing): the copy was cut off; copy it again")
            return fh.read()

        manifest = json.loads(read("MANIFEST.json"))
        listed = {row["path"]: row for row in csv.DictReader(io.StringIO(read("files.csv").decode()))}
        out = r.arcus / "data" / "server-export" / name
        out.mkdir(parents=True, exist_ok=True)
        say(f"import {name}: made {manifest.get('made_utc', '?')} on {manifest.get('host', '?')}, "
            f"{manifest.get('contents', '')}")
        for rel in ("SUMMARY.md", "coverage.csv", "MANIFEST.json", "files.csv"):
            (out / rel).write_bytes(read(rel))
        bad: list[str] = []
        packed = out / "records.tar.gz.importing"
        inner = tar.extractfile(members[f"{name}/records.tar.gz"]) if f"{name}/records.tar.gz" in members else None
        if inner is None:
            raise ExportError(f"{path.name} is incomplete (records.tar.gz is missing): copy it again")
        digest = hashlib.sha256()
        with packed.open("wb") as fo:
            while chunk := inner.read(1 << 20):
                digest.update(chunk)
                fo.write(chunk)
        if digest.hexdigest() != listed.get("records.tar.gz", {}).get("sha256"):
            bad.append("records.tar.gz")
        else:
            with tarfile.open(packed, mode="r:gz") as tz:
                tz.extractall(out, filter="data")           # never outside `out`, no links, no devices
        packed.unlink()

        tapes = r.tapes()
        counts = {"added": 0, "same": 0, "grown": 0, "kept": 0}
        for full_name, m in members.items():
            rel = full_name.split("/", 1)[1] if "/" in full_name else ""
            if not rel.startswith("tape/"):
                continue
            hit = TAPE_RE.match(rel)
            if not hit or rel not in listed:
                bad.append(rel)
                continue
            venue, market, day, fname = hit.groups()
            dest = tapes[venue] / market / day / fname
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name(dest.name + ".importing")
            src = tar.extractfile(m)
            h = hashlib.sha256()
            with tmp.open("wb") as fo:
                while src is not None and (chunk := src.read(1 << 20)):
                    h.update(chunk)
                    fo.write(chunk)
            if h.hexdigest() != listed[rel]["sha256"]:
                bad.append(rel)
                tmp.unlink()
            elif not dest.exists():
                os.replace(tmp, dest)
                counts["added"] += 1
            elif dest.stat().st_size == tmp.stat().st_size and _sha(dest) == h.hexdigest():
                tmp.unlink()
                counts["same"] += 1
            elif _rows(tmp) > _rows(dest):      # the same recorder part, written again with more rows
                os.replace(tmp, dest)
                counts["grown"] += 1
            else:
                tmp.unlink()
                counts["kept"] += 1

    say(f"  tape: {counts['added']:,} new files, {counts['same']:,} already here, {counts['grown']:,} replaced by a "
        f"longer version, {counts['kept']:,} kept as they were")
    expected = list(csv.DictReader(io.StringIO((out / "coverage.csv").read_text())))
    short = []
    cache: dict[tuple[str, str, str], dict[str, dict[str, Any]]] = {}
    for want in expected:
        key = (want["venue"], want["market"], want["day"])
        if key not in cache:
            cache[key] = count_day(tapes[want["venue"]] / want["market"] / want["day"])
        have = cache[key].get(want["kind"])
        if have is None or int(have["unique_ts"]) < int(want["unique_ts"]):
            short.append((*key, want["kind"], want["unique_ts"], have["unique_ts"] if have else "missing"))
    say(f"  checked {len(expected):,} market-day-kinds against the sender's count: "
        f"{len(expected) - len(short):,} complete, {len(short)} short")
    for s in short[:20]:
        say("    SHORT " + " ".join(str(x) for x in s))
    for b in bad[:20]:
        say(f"    DAMAGED {b}")
    prev = manifest.get("prev")
    chain_ok = not (manifest.get("mode") == "new" and prev) or (out.parent / prev).is_dir()
    if not chain_ok:
        say(f"  NOTE: this export holds only what was new since {prev}, which was never imported here. Import that "
            "one too, or make a complete one on the sender: arcus export --full")
    (out.parent / "LATEST").write_text(name + "\n")
    say(f"  records, logs and scans: {out}")
    say(f"  read first: {out / 'SUMMARY.md'}")
    return {"name": name, "out": out, **counts, "short": len(short), "damaged": len(bad), "chain_ok": chain_ok,
            "ok": not short and not bad}
