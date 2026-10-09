"""Two machines: roles (arcus/common/role.py), what `arcus up` starts in each, the handoff a trader fetches
(arcus/handoff.py), the trader's follow turn and its alerts, and an autopilot with no tape of its own. No network."""

from __future__ import annotations

import asyncio
import io
import json
import os
import tarfile
import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from arcus import handoff, ops
from arcus.common import role as roles
from arcus.common.config import AppConfig
from arcus.core.calendar import TradingCalendar
from arcus.scout import autopilot as ap
from arcus.scout import playbook as pbk
from arcus.scout import regime as rg
from arcus.scout import service as svc

TG = {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "1"}
KEY = "ssh-ed25519 " + "A" * 68 + " someone@laptop"


# ------------------------------------------------------------------------------------------------ roles
def test_one_machine_is_the_default_and_a_typing_mistake_is_refused() -> None:
    assert roles.role({}) == "all" and roles.role({"BOT_ROLE": " Trader "}) == "trader"
    with pytest.raises(ValueError, match="must be one of all, trader, recorder, scout"):
        roles.role({"BOT_ROLE": "recoder"})
    assert roles.no_trading({}) == "" and roles.no_trading({"BOT_ROLE": "trader"}) == ""
    assert "recorder" in roles.no_trading({"BOT_ROLE": "recorder"}) and "scout" in roles.no_trading({"BOT_ROLE": "scout"})
    assert "must be one of" in roles.no_trading({"BOT_ROLE": "x"})      # unknown: never read as "may trade"


def test_each_role_does_its_jobs_and_no_other() -> None:
    #                                     record  rank   supervise
    assert roles.jobs("all") == (True, True, True)
    assert roles.jobs("scout") == (True, True, False)
    assert roles.jobs("recorder") == (True, False, False)
    assert roles.jobs("trader") == (False, False, True)
    assert roles.jobs("all", record_only=True) == (True, False, False)      # a flag decides over the role
    assert roles.jobs("all", follow=True) == (False, False, True)


def test_up_starts_what_the_role_needs() -> None:
    app = AppConfig()
    assert set(ops.wanted(app, {**TG, "BOT_ROLE": "recorder"}, live_running=True)) == {"scout"}   # no Telegram: one poller
    assert set(ops.wanted(app, {**TG, "BOT_ROLE": "scout"}, live_running=False)) == {"scout"}
    assert set(ops.wanted(app, {**TG, "BOT_ROLE": "trader"}, live_running=True)) == {"scout", "telegram", "guardian"}
    assert set(ops.skipped({**TG, "BOT_ROLE": "recorder"}, live_running=False)) == {"telegram", "guardian"}
    with pytest.raises(ValueError):
        ops.wanted(app, {"BOT_ROLE": "both"}, live_running=False)
    assert ops.service("scout", "all").args == ("scout", "run", "--depth")
    assert ops.service("scout", "recorder").args == ("scout", "run", "--depth", "--record-only")
    assert ops.service("scout", "trader").args == ("scout", "run", "--follow")          # nothing to record: no --depth
    assert ops.service("scout", "scout").args == ("scout", "run", "--depth")
    assert ops.service("telegram", "trader") is ops.BY_NAME["telegram"]


# ------------------------------------------------------------------------------------------------ the handoff
def _two(tmp_path: Path) -> tuple[dict[str, Path], dict[str, Path]]:
    there = {"arcus": tmp_path / "there" / "arcus", "lighter": tmp_path / "there" / "lighter"}
    here = {"arcus": tmp_path / "here" / "arcus", "lighter": tmp_path / "here" / "lighter"}
    for d in (*there.values(), *here.values()):
        (d / "data" / "scout").mkdir(parents=True)
    now = time.time()
    (there["arcus"] / "data/scout/latest.json").write_text(json.dumps({"ts_us": now * 1e6, "all": [], "top": []}))
    (there["arcus"] / "data/scout/report.txt").write_text("scan at 10:00\nBTC Mid 0\n")
    (there["arcus"] / "data/scout/recorder.json").write_text(json.dumps({"ts": now - 5, "markets": 60}))
    (there["lighter"] / "data/scout/latest.json").write_text(json.dumps({"t": now, "lists": {}, "table": []}))
    (there["lighter"] / "data/scout/ceilings.json").write_text(json.dumps({"SPY": 1200}))
    return there, here


def _tar(members: list[tuple[str, bytes]], *, kind: bytes = tarfile.REGTYPE) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, body in members:
            ti = tarfile.TarInfo(name)
            ti.size, ti.mtime, ti.type = len(body), time.time(), kind
            tar.addfile(ti, io.BytesIO(body))
    return buf.getvalue()


def test_a_handoff_carries_the_named_files_and_only_what_changed(tmp_path: Path) -> None:
    there, here = _two(tmp_path)
    got = handoff.unpack(here, handoff.pack(there))
    assert set(got.files) == {"arcus/data/scout/latest.json", "arcus/data/scout/report.txt",
                              "arcus/data/scout/recorder.json", "lighter/data/scout/latest.json",
                              "lighter/data/scout/ceilings.json"} and not got.refused
    assert (here["arcus"] / "data/scout/report.txt").read_text().startswith("scan at 10:00")
    src, dst = there["arcus"] / "data/scout/latest.json", here["arcus"] / "data/scout/latest.json"
    assert abs(dst.stat().st_mtime - src.stat().st_mtime) < 0.01          # the other machine's time is kept
    assert got.remote["recorder_age_s"]["arcus"] < 60 and got.since == max(got.files.values())
    again = handoff.unpack(here, handoff.pack(there, since=got.since))    # nothing changed: only the manifest comes
    assert not again.files and set(again.remote["files"]) == set(got.files)
    os.utime(there["arcus"] / "data/scout/report.txt", (got.since + 5, got.since + 5))
    assert set(handoff.unpack(here, handoff.pack(there, since=got.since)).files) == {"arcus/data/scout/report.txt"}


def test_a_file_cut_off_while_being_written_waits_for_the_next_time(tmp_path: Path) -> None:
    there, here = _two(tmp_path)
    (there["arcus"] / "data/scout/latest.json").write_text('{"ts_us": 17, "all": [')
    blob = handoff.pack(there)
    got = handoff.unpack(here, blob)
    assert "arcus/data/scout/latest.json" not in got.files
    assert "not JSON" in got.remote["left_out"]["arcus/data/scout/latest.json"]
    assert not (here["arcus"] / "data/scout/latest.json").exists()


def test_what_arrives_is_untrusted(tmp_path: Path) -> None:
    _there, here = _two(tmp_path)
    good = json.dumps({"ts_us": time.time() * 1e6}).encode()
    evil = tmp_path / "here" / "evil.txt"
    got = handoff.unpack(here, _tar([
        ("arcus/../evil.txt", b"x"),                      # out of the folder
        ("/etc/cron.d/x", b"x"),                          # an absolute path
        ("arcus/.env", b"ARCUS_API_PRIVATE_KEY=1"),       # not a file a handoff carries
        ("arcus/config/sessions/pilot.yaml", b"x"),
        ("arcus/data/scout/latest.json", b"[1, 2]"),      # not an object
        ("arcus/data/scout/playbook.json", b"{}"),        # not a playbook
        ("lighter/data/scout/latest.json", json.dumps({"t": time.time() + 86400}).encode()),   # tomorrow's scan
        ("arcus/data/scout/report.txt", b"\xff\xfe\x00"),                                       # not text
    ]))
    assert not got.files and len(got.refused) == 8 and not evil.exists()
    assert not (here["arcus"] / ".env").exists() and not (here["arcus"] / "config").exists()
    assert "future" in got.refused["lighter/data/scout/latest.json"]
    link = handoff.unpack(here, _tar([("arcus/data/scout/latest.json", b"")], kind=tarfile.SYMTYPE))
    assert not link.files and "not a plain file" in link.refused["arcus/data/scout/latest.json"]
    assert handoff.unpack(here, _tar([("arcus/data/scout/latest.json", good)])).files       # a good one is taken
    for junk in (b"", b"Welcome to Ubuntu 24.04\n", b"\x1f\x8b" + b"\x00" * 40):
        with pytest.raises(handoff.SyncError, match="not a handoff"):
            handoff.unpack(here, junk)
    with pytest.raises(handoff.SyncError, match="refused"):
        handoff.unpack(here, b"0" * (handoff.MAX_TOTAL + 1))
    with pytest.raises(handoff.SyncError, match="more files"):
        handoff.unpack(here, _tar([(f"x{i}", b"") for i in range(40)]))


def test_the_serving_end_takes_one_number_and_nothing_else() -> None:
    assert handoff.since_of("since=1728400000.123456") == 1728400000.123456
    for text in (None, "", "since=", "since=1; rm -rf ~", "since=1 && cat .env", "cat /etc/passwd", "since=-5",
                 "scp -f .env"):
        assert handoff.since_of(text) == 0.0


def test_the_address_cannot_smuggle_an_ssh_option() -> None:
    d = handoff.Dest.parse("azureuser@20.1.2.3:2222")
    assert (d.user, d.host, d.port) == ("azureuser", "20.1.2.3", 2222) and str(d) == "azureuser@20.1.2.3:2222"
    assert handoff.Dest.parse("bot@recorder.example.com").port == 22
    for bad in ("", "20.1.2.3", "-oProxyCommand=sh@x", "u@-oProxyCommand=sh", "u@h:0", "u@h:99999", "u@h x",
                "u@h;id", "u @h", "u@h:22:1"):
        with pytest.raises(handoff.SyncError, match="user@host"):
            handoff.Dest.parse(bad)


def test_allow_adds_one_line_that_can_only_serve(tmp_path: Path) -> None:
    root, exe = Path("/home/u/treading-bot/arcus"), Path("/home/u/treading-bot/arcus/.venv/bin/arcus")
    line = handoff.allow_line(KEY, root, exe)
    assert line == (f'command="BOT_HOME={root} {exe} sync serve",restrict ssh-ed25519 {"A" * 68} treading-bot-sync')
    ak = tmp_path / ".ssh" / "authorized_keys"
    ak.parent.mkdir()
    ak.write_text("ssh-ed25519 OWNERSKEY me@home")                       # the owner's own key, no newline at the end
    assert handoff.allow(KEY, root, ak, exe) == (line, True)
    assert ak.read_text() == f"ssh-ed25519 OWNERSKEY me@home\n{line}\n" and oct(ak.stat().st_mode)[-3:] == "600"
    assert handoff.allow(KEY, root, ak, exe) == (line, False) and ak.read_text().count("sync serve") == 1
    for bad in ("", "hello", "ssh-ed25519", "ssh-ed25519 short", 'command="sh" ssh-ed25519 ' + "A" * 68):
        with pytest.raises(handoff.SyncError, match="not a public key"):
            handoff.allow_line(bad, root, exe)
    with pytest.raises(handoff.SyncError, match="quote"):
        handoff.allow_line(KEY, Path('/home/u/tre"ad'), exe)


def test_only_a_trader_takes_lists_and_a_failed_fetch_never_raises(tmp_path: Path) -> None:
    there, here = _two(tmp_path)
    state = tmp_path / "here" / "arcus" / "state"
    env = {"BOT_ROLE": "trader", "BOT_SYNC_FROM": "bot@10.0.0.4"}
    asked: list[float] = []

    def fetch(dest: handoff.Dest, key: Path, since: float) -> bytes:
        asked.append(since)
        return handoff.pack(there, since)

    st = handoff.pull(here, state, env, fetch=fetch)
    assert st["error"] == "" and len(st["took"]) == 5 and st["source"] == "bot@10.0.0.4" and asked == [0.0]
    st2 = handoff.pull(here, state, env, fetch=fetch)                      # the next asks only for what is newer
    assert asked[1] == st["since"] and st2["took"] == [] and st2["error"] == ""

    def down(dest: handoff.Dest, key: Path, since: float) -> bytes:
        raise handoff.SyncError("ssh failed (255): Connection timed out")

    st3 = handoff.pull(here, state, env, fetch=down)
    assert "timed out" in st3["error"] and st3["last_ok"] == st2["last_ok"] and st3["since"] == st["since"]
    for r in ("all", "scout", "recorder"):                                # a machine with its own lists keeps them
        refused = handoff.pull(here, tmp_path / r, {"BOT_ROLE": r, "BOT_SYNC_FROM": "bot@10.0.0.4"}, fetch=fetch)
        assert "makes its own lists" in refused["error"] or "Only a trader" in refused["error"]
    assert "user@host" in handoff.pull(here, tmp_path / "s", {"BOT_ROLE": "trader"}, fetch=fetch)["error"]
    lines = handoff.status_lines(state, env)
    assert lines[0].startswith("from bot@10.0.0.4 · last worked") and "last try failed" in lines[1]
    assert "not set up" in handoff.status_lines(tmp_path / "none", {"BOT_ROLE": "trader"})[0]


# ------------------------------------------------------------------------------------------------ the follow turn
class _Control:
    class app:
        state_dir = "state"

    def __init__(self) -> None:
        self.up = False

    def is_running(self, mode: str) -> bool:
        return self.up


class _Pilot:
    account_index = 0

    def __init__(self) -> None:
        self.control = _Control()
        self.scan: dict[str, Any] | None = None
        self.reviewed: list[int] = []
        self.events: list[tuple[str, str]] = []
        self._active: dict[str, Any] | None = None

    def latest_scan(self) -> dict[str, Any] | None:
        return self.scan

    def review(self, scan: dict[str, Any]) -> list[dict[str, Any]]:
        self.reviewed.append(scan["ts_us"])
        return []

    def active(self) -> dict[str, Any] | None:
        return self._active

    def event(self, kind: str, text: str, **k: Any) -> dict[str, Any]:
        self.events.append((kind, text))
        return {"kind": kind, "text": text}


async def _turn(tmp_path: Path, pilot: _Pilot, fst: dict[str, Any], monkeypatch: Any, now: float,
                sync: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    async def no_snapshot(*a: Any, **k: Any) -> None:
        return None

    async def no_earnings(*a: Any, **k: Any) -> None:
        return None

    monkeypatch.setattr(svc, "account_snapshot", no_snapshot)
    monkeypatch.setattr(svc, "_earnings", no_earnings)
    from arcus.core.balances import BalanceLog

    return await svc.follow_turn(tmp_path, pilot, fst, rest_url="http://x",  # type: ignore[arg-type]
                                 balances=BalanceLog(tmp_path / "state" / "balances.jsonl"), cal=TradingCalendar(),
                                 pull=(lambda: sync) if sync is not None else None, now=now)


async def test_a_trader_reviews_each_new_scan_once_and_never_an_old_one_at_start(tmp_path: Path, monkeypatch: Any) -> None:
    now = 1_760_000_000.0
    pilot, fst = _Pilot(), {}
    pilot.scan = {"ts_us": int((now - 5 * 3600) * 1e6), "top": []}      # on disk when the service starts: hours old
    await _turn(tmp_path, pilot, fst, monkeypatch, now)
    assert pilot.reviewed == []                                          # judging a run by it would pause it for nothing
    await _turn(tmp_path, pilot, fst, monkeypatch, now + 120)
    assert pilot.reviewed == []
    pilot.scan = {"ts_us": int((now + 200) * 1e6), "top": []}            # a new one arrives
    await _turn(tmp_path, pilot, fst, monkeypatch, now + 240)
    await _turn(tmp_path, pilot, fst, monkeypatch, now + 360)
    assert pilot.reviewed == [pilot.scan["ts_us"]]
    fresh, f2 = _Pilot(), {}
    fresh.scan = {"ts_us": int((now - 600) * 1e6), "top": []}           # a fresh one at start is reviewed
    await _turn(tmp_path, fresh, f2, monkeypatch, now)
    assert fresh.reviewed == [fresh.scan["ts_us"]]


async def test_a_trader_says_once_when_the_other_machine_goes_quiet_and_once_when_it_is_back(
        tmp_path: Path, monkeypatch: Any) -> None:
    now = 1_760_000_000.0
    pilot, fst = _Pilot(), {}
    ok = {"last_ok": now, "error": "", "source": "bot@10.0.0.4", "remote": {"recorder_age_s": {"arcus": 4.0}}}
    assert await _turn(tmp_path, pilot, fst, monkeypatch, now, ok) == []
    down = {**ok, "error": "ssh failed (255): Connection timed out"}
    assert await _turn(tmp_path, pilot, fst, monkeypatch, now + 600, down) == []           # ten minutes: not yet
    out = await _turn(tmp_path, pilot, fst, monkeypatch, now + svc.QUIET_S + 60, down)
    assert [e["kind"] for e in out] == ["alert"] and "Cannot fetch the lists from bot@10.0.0.4" in out[0]["text"]
    assert "timed out" in out[0]["text"]
    assert await _turn(tmp_path, pilot, fst, monkeypatch, now + svc.QUIET_S + 180, down) == []   # said once
    back = {**ok, "last_ok": now + svc.QUIET_S + 300}
    out = await _turn(tmp_path, pilot, fst, monkeypatch, now + svc.QUIET_S + 300, back)
    assert len(out) == 1 and "arriving again" in out[0]["text"]
    silent = {**back, "remote": {"recorder_age_s": {"arcus": 3000.0, "lighter": 5.0}}}
    out = await _turn(tmp_path, pilot, fst, monkeypatch, now + svc.QUIET_S + 420, silent)
    assert len(out) == 1 and "recorder is silent: arcus 50 min" in out[0]["text"]
    # a list's pick with no new scan for hours runs unchecked: say so (the owner's own pick is never checked anyway)
    p2, f2 = _Pilot(), {}
    p2.control.up = True
    p2.scan = {"ts_us": int((now - 4 * 3600) * 1e6), "top": []}
    p2._active = {"market": "SPY-USD", "config": "Smart 0 @ 50x", "mode": "paper", "profile": "manual"}
    assert await _turn(tmp_path, p2, f2, monkeypatch, now) == []
    p2._active["profile"] = "volume"
    out = await _turn(tmp_path, p2, f2, monkeypatch, now + 120)
    assert len(out) == 1 and "running unchecked" in out[0]["text"]


async def test_a_recorder_never_scans_and_a_trader_follows(tmp_path: Path, monkeypatch: Any) -> None:
    from arcus.scout.pilot import Pilot
    from tests.unit.test_telegram import _app, _bot

    app = _app(tmp_path)
    _b, _api, ctl = _bot(tmp_path, app)
    pilot = Pilot(tmp_path, ctl)
    made: list[str] = []
    turns: list[float] = []

    def no_scan(*a: Any, **k: Any) -> Any:
        raise AssertionError("a machine that does not rank must never scan")

    class FakeWatch:
        def __init__(self, root: Path, **kw: Any) -> None:
            made.append("watch")
            self._stop = asyncio.Event()

        def recent(self, market: str) -> list[tuple[int, float]]:
            return []

        async def run(self) -> None:
            await self._stop.wait()

        def stop(self) -> None:
            self._stop.set()

    class FakeAuto:
        def __init__(self, root: Path, pilot: Any, **kw: Any) -> None:
            made.append(f"autopilot tapeless={kw.get('tapeless')}")

        async def run(self, stop: asyncio.Event) -> None:
            await stop.wait()

    async def turn(*a: Any, **k: Any) -> list[Any]:
        turns.append(time.time())
        asyncio.get_running_loop().call_later(0.1, os.kill, os.getpid(), __import__("signal").SIGTERM)
        return []

    monkeypatch.setattr(svc, "FIRST_SCAN_S", 0.0)
    monkeypatch.setattr(svc, "scan", no_scan)
    monkeypatch.setattr(svc, "MidWatch", FakeWatch)
    monkeypatch.setattr(svc, "Autopilot", FakeAuto)
    monkeypatch.setattr(svc, "follow_turn", turn)
    await asyncio.wait_for(svc.run_service(tmp_path, pilot, rest_url="http://x", ws_url="ws://x", record=False,
                                           rank=False, supervise=True), 20)
    assert made == ["watch", "autopilot tapeless=True"] and len(turns) == 1     # a trader: its own eyes, no scan

    made.clear()
    task = asyncio.create_task(svc.run_service(tmp_path, pilot, rest_url="http://x", ws_url="ws://x", record=False,
                                               rank=False, supervise=False))       # the scout's jobs all off
    await asyncio.sleep(0.3)
    os.kill(os.getpid(), __import__("signal").SIGTERM)
    await asyncio.wait_for(task, 20)
    assert made == [] and len(turns) == 1                                       # no autopilot, no review, no scan


# ------------------------------------------------------------------------------------------------ no tape of its own
def test_the_usual_levels_travel_in_a_file(tmp_path: Path) -> None:
    u = rg.Usual({(False, 14): 21.5, (True, 3): 9.25}, "2026-10-09")
    p = tmp_path / rg.USUAL_FILE
    rg.save_usual(p, {"BTC-USD": u, "NEW-USD": rg.Usual({}, "2026-10-09")})
    back = rg.load_usual(p, "BTC-USD")
    assert back is not None and back.rv == u.rv and back.day == "2026-10-09"
    assert rg.load_usual(p, "NEW-USD") is None and rg.load_usual(p, "ETH-USD") is None     # nothing known: not "calm"
    assert rg.load_usual(tmp_path / "missing.json", "BTC-USD") is None
    p.write_text("{not json")
    assert rg.load_usual(p, "BTC-USD") is None
    assert handoff.check("arcus/data/scout/usual.json", json.dumps({"ts": 1, "markets": {}}).encode()) == ""


def test_write_usual_is_daily_and_needs_a_playbook(tmp_path: Path, monkeypatch: Any) -> None:
    asked: list[str] = []
    monkeypatch.setattr(rg, "usual", lambda store, m, now_us: (asked.append(m), rg.Usual({(False, 1): 5.0}, "d"))[1])
    monkeypatch.setattr(pbk, "load", lambda root: {})
    assert svc.write_usual(tmp_path) is False and not asked
    monkeypatch.setattr(pbk, "load", lambda root: {"markets": {"SPY-USD": {}, "BTC-USD": {}}})
    assert svc.write_usual(tmp_path) is True and asked == ["BTC-USD", "SPY-USD"]
    assert rg.load_usual(tmp_path / rg.USUAL_FILE, "SPY-USD") is not None
    assert svc.write_usual(tmp_path) is False and len(asked) == 2                 # written today already


class _APilot:
    def __init__(self, tmp_path: Path) -> None:
        self.control = _Control()

    def latest_scan(self) -> dict[str, Any]:
        return {}


def _auto(tmp_path: Path, monkeypatch: Any, minutes: int, now: float) -> ap.Autopilot:
    (tmp_path / "state").mkdir(exist_ok=True)
    mids = [(int((now - (minutes - i) * 60) // 60 * 60 * 1e6), 100.0 + 0.01 * (i % 3)) for i in range(minutes)]
    a = ap.Autopilot(tmp_path, _APilot(tmp_path), recent=lambda m: mids, clock=lambda: now, tapeless=True)
    a._st = {}
    return a


def test_a_trader_judges_a_market_from_the_usual_file_and_its_own_prices(tmp_path: Path, monkeypatch: Any) -> None:
    now = 1_760_000_000.0
    pb = {"markets": {"BTC-USD": {}}}
    monkeypatch.setattr(pbk, "options", lambda pb, m, sess, bucket: [{"market": m, "bucket": bucket}])
    a = _auto(tmp_path, monkeypatch, 120, now)
    look = a.look(pb, now, set())[0]
    assert look.regime is None and "no usual level yet" in (look.blocked or "")      # the file has not arrived
    hours = {(w, h): 4.0 for w in (False, True) for h in range(24)}
    rg.save_usual(tmp_path / "data" / "scout" / rg.USUAL_FILE, {"BTC-USD": rg.Usual(hours, "d")})
    look = a.look(pb, now, set())[0]
    assert look.regime is not None and look.blocked is None and np.isfinite(look.regime.ratio) and look.options
    cold = _auto(tmp_path, monkeypatch, 20, now)                                      # just started: 20 minutes
    look = cold.look(pb, now, set())[0]
    assert look.regime is None and look.blocked == "collecting prices (20 of 61 minutes)" and not look.options


async def test_a_trader_starts_nothing_on_a_playbook_that_stopped_arriving(tmp_path: Path, monkeypatch: Any) -> None:
    now = 1_760_000_000.0
    a = _auto(tmp_path, monkeypatch, 120, now)
    a.pilot.active = lambda: None  # type: ignore[attr-defined]
    ap.configure(tmp_path / "state", on=True, budget_day=5.0)
    monkeypatch.setattr(pbk, "load", lambda root: {"markets": {"BTC-USD": {}}, "ts": now - 5 * 86400})
    d = await a.tick()
    assert d is not None and d.action == "idle" and "5 days old" in d.reason


# ------------------------------------------------------------------------------------------------ guards
async def test_a_machine_that_does_not_trade_starts_no_run(tmp_path: Path, monkeypatch: Any) -> None:
    from arcus.scout.pilot import Pilot
    from tests.unit.test_telegram import _app, _bot

    app = _app(tmp_path)
    _b, _api, ctl = _bot(tmp_path, app)
    closed: list[str] = []
    monkeypatch.setattr(ctl, "request_close", lambda mode, by: closed.append(mode))
    for r in ("recorder", "scout"):
        monkeypatch.setenv("BOT_ROLE", r)
        with pytest.raises(ValueError, match=f"this machine is a {r}"):
            await Pilot(tmp_path, ctl).deploy({"market": "SPY-USD", "config": "Smart 0 @ 50x"}, live=False, by="test")
        with pytest.raises(RuntimeError, match="Runs start on the trader machine"):
            ctl.start_run("pilot", live=False)
    assert closed == [] and not (tmp_path / "config" / "sessions" / "pilot.yaml").exists()


# ------------------------------------------------------------------------------------------------ the ssh call
def test_the_fetch_calls_ssh_strictly_and_reads_what_it_prints(tmp_path: Path, monkeypatch: Any) -> None:
    """A stand-in `ssh` on the PATH: the real one is never run by a test."""
    there, here = _two(tmp_path)
    blob, args_file = tmp_path / "blob.bin", tmp_path / "args.json"
    blob.write_bytes(handoff.pack(there))
    fake = tmp_path / "bin" / "ssh"
    fake.parent.mkdir()
    fake.write_text(f"#!{os.sys.executable}\nimport json, sys\n"
                    f"json.dump(sys.argv[1:], open({str(args_file)!r}, 'w'))\n"
                    f"mode = open({str(tmp_path / 'mode')!r}).read()\n"
                    "if mode == 'down':\n    sys.stderr.write('banner\\nssh: connect to host: Connection refused\\n')\n"
                    "    sys.exit(255)\n"
                    f"sys.stdout.buffer.write(open({str(blob)!r}, 'rb').read())\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake.parent}{os.pathsep}{os.environ['PATH']}")
    key = tmp_path / "key"
    with pytest.raises(handoff.SyncError, match="arcus sync key"):          # no key yet: says what to run
        handoff.ssh_fetch(handoff.Dest("bot", "10.0.0.4", 2222), key, 12.5)
    key.write_text("k")
    (tmp_path / "mode").write_text("up")
    got = handoff.unpack(here, handoff.ssh_fetch(handoff.Dest("bot", "10.0.0.4", 2222), key, 12.5))
    assert len(got.files) == 5
    argv = json.loads(args_file.read_text())
    assert argv[-2:] == ["bot@10.0.0.4", "since=12.500000"] and argv[:2] == ["-i", str(key)]
    for opt in ("BatchMode=yes", "IdentitiesOnly=yes", "StrictHostKeyChecking=accept-new", "ConnectTimeout=10"):
        assert opt in argv
    assert argv[argv.index("-p") + 1] == "2222"
    (tmp_path / "mode").write_text("down")
    with pytest.raises(handoff.SyncError, match=r"ssh failed \(255\): ssh: connect to host: Connection refused"):
        handoff.ssh_fetch(handoff.Dest("bot", "10.0.0.4"), key, 0.0)


def test_an_oversized_file_stops_the_handoff_at_once(tmp_path: Path) -> None:
    _there, here = _two(tmp_path)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        ti = tarfile.TarInfo("arcus/data/scout/latest.json")
        ti.size = handoff.MAX_FILE + 1
        tar.addfile(ti, io.BytesIO(b"0" * ti.size))               # zeros: a few kB to send, 32 MB to unpack
        ok = tarfile.TarInfo("arcus/data/scout/report.txt")
        ok.size = 2
        tar.addfile(ok, io.BytesIO(b"hi"))
    assert len(buf.getvalue()) < 2**20
    with pytest.raises(handoff.SyncError, match="larger than a handoff carries"):
        handoff.unpack(here, buf.getvalue())
    assert not (here["arcus"] / "data/scout/report.txt").exists()      # nothing after it is taken either
    many = _tar([(n, b"0" * (handoff.MAX_FILE - 1)) for n in handoff.names()[:4]])
    with pytest.raises(handoff.SyncError, match="larger than a handoff carries"):
        handoff.unpack(here, many)                                      # each fits, together they do not


def test_a_scan_row_is_advice_never_an_instruction(tmp_path: Path, monkeypatch: Any) -> None:
    """A trader's scan comes from another machine. A row must not set the run's loss limit (which lifts the daily
    stop and the kill), and with no market list of its own a trader starts nothing sized by that scan."""
    from arcus.scout.pilot import RUN_KEYS, Pilot
    from tests.unit.test_telegram import _app, _bot

    app = _app(tmp_path)
    _b, _api, ctl = _bot(tmp_path, app)
    pilot = Pilot(tmp_path, ctl)
    row = {"market": "BTC-USD", "config": "Mid 0 @ 40x", "setting": "Mid 0", "leverage": 40.0, "at_max": True,
           "capital_usd": 100.0, "volume_day": 1e6, "pnl_day": -1.0, "worst_day": -2.0, "days": 5,
           "max_loss_usd": 1e9, "take_profit_usd": 1e9, "volume_target_usd": 1.0, "run_id": "x", "profile": "volume",
           "risk": {"capital_usd": 100.0, "order_usd": 1600.0, "cap_usd": 3200.0, "leverage": 40.0}}
    (tmp_path / "data" / "scout").mkdir(parents=True)
    (tmp_path / "data" / "scout" / "latest.json").write_text(json.dumps({"ts_us": time.time() * 1e6, "all": [row]}))
    got = pilot.find("BTC-USD", "Mid 0", "max")                       # one machine, no market list: the scan's row
    assert got["profile"] == "manual" and not [k for k in RUN_KEYS if k in got and k not in ("profile", "lev")]
    monkeypatch.setenv("BOT_ROLE", "trader")
    with pytest.raises(ValueError, match="no market list yet"):
        pilot.find("BTC-USD", "Mid 0", "max")
