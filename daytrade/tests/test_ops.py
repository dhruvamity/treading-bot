"""`tbot up daytrade` / `tbot down daytrade` (daytrade/ops.py) and the /dt_ replies (daytrade/telegram.py). No real
process is started: Popen and the liveness check are replaced."""

import json
import signal

import pytest

from daytrade import ops, telegram


class FakePopen:
    exit_code = None                                        # set: the program exited at once with it

    def __init__(self, argv, **kw):
        self.argv, self.kw, self.pid, self.returncode = argv, kw, 4242, FakePopen.exit_code
        FakePopen.made.append(self)

    def poll(self):
        return self.returncode


@pytest.fixture
def fake(tmp_path, monkeypatch):
    """A state folder, a recorder instead of Popen, and processes that are 'alive' while their pid is in `live`."""
    FakePopen.made = []
    live: set[int] = set()
    killed: list[tuple[int, int]] = []

    def kill(pid, sig):
        killed.append((pid, sig))
        live.discard(pid)                                   # it stops at once

    monkeypatch.setattr(ops, "STATE", tmp_path)
    monkeypatch.setattr(ops.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(ops, "alive", lambda pid: pid in live)
    monkeypatch.setattr(ops.os, "kill", kill)
    monkeypatch.setattr(ops.time, "sleep", lambda s: None)
    return tmp_path, live, killed


def test_tests_never_start_the_paper_bot(tmp_path, monkeypatch):
    monkeypatch.setattr(ops, "STATE", tmp_path)
    with pytest.raises(RuntimeError, match="refusing"):     # conftest.py sets DAYTRADE_NO_SPAWN=1
        ops.start()
    assert not (tmp_path / "paper.pid").exists()


def test_start_runs_the_paper_bot_in_the_background_once(fake, monkeypatch):
    state, live, _ = fake
    monkeypatch.delenv("DAYTRADE_NO_SPAWN")
    ok, msg = ops.start()
    p = FakePopen.made[0]
    assert ok and msg.startswith("started (pid 4242)") and p.argv[-1] == "run" and "daytrade" in " ".join(p.argv)
    assert p.kw["start_new_session"] and p.kw["cwd"] == ops.ROOT
    assert (state / "paper.pid").read_text() == "4242" and (state / "paper.out").exists()
    live.add(4242)
    assert ops.running() == 4242
    assert ops.start() == (False, "already running (pid 4242)") and len(FakePopen.made) == 1


def test_a_bot_that_exits_at_once_says_so_and_leaves_no_pid(fake, monkeypatch):
    state, _, _ = fake
    monkeypatch.delenv("DAYTRADE_NO_SPAWN")
    monkeypatch.setattr(FakePopen, "exit_code", 2)
    (state / "paper.out").write_text("Traceback\nValueError: boom\n")
    ok, msg = ops.start()
    assert not ok and "exited at once (2)" in msg and "boom" in msg and not (state / "paper.pid").exists()


def test_stop_sends_sigterm_and_clears_the_pid(fake):
    state, live, killed = fake
    assert ops.stop() == (False, "not running")
    (state / "paper.pid").write_text("4242")
    assert ops.stop() == (False, "not running") and not (state / "paper.pid").exists()   # a stale pid is removed
    (state / "paper.pid").write_text("4242")
    live.add(4242)
    assert ops.stop() == (True, "stopped") and killed == [(4242, signal.SIGTERM)] and not (state / "paper.pid").exists()


def test_dt_trades_shows_the_last_ten_two_lines_each(tmp_path):
    assert telegram.trades(state_dir=tmp_path) == "no paper trades yet"
    rows = [{"slot": "xau-sweep-asia@lighter", "side": "short", "entry_utc": f"2026-10-{d:02d} 07:30",
             "entry": 4000 + d, "exit": 3990 + d, "reason": "target", "r": 3.0 - d / 10} for d in range(1, 13)]
    text = "\n".join(json.dumps(r) for r in rows) + "\nnot json\n"
    (tmp_path / "paper_trades.jsonl").write_text(text)
    out = telegram.trades(state_dir=tmp_path).splitlines()
    assert out[0].startswith("last 10 of 12 paper trades") and len(out) == 21
    assert out[1:3] == ["2026-10-03 07:30 UTC XAU short", "  4003 -> 3993 target +2.70R"]
    assert out[-1] == "  4012 -> 4002 target +1.80R"


def test_dt_replies_only_to_its_reads(tmp_path):
    assert telegram.reply("status", tmp_path) == "PAPER: never started"
    assert telegram.reply("trades", tmp_path) == "no paper trades yet"
    for name in ("run", "start", "stop", "close", ""):
        assert telegram.reply(name, tmp_path) is None
