"""`bot export` and `bot import` (bot/export.py): one file carries a machine's tape, trades, state and logs to
another, never a key, and the receiving side merges it without touching its own state."""

from __future__ import annotations

import gzip
import io
import json
import os
import shutil
import sqlite3
import tarfile
import time
from pathlib import Path

import numpy as np
import pytest

from bot import export as ex
from bot.core.state import SCHEMA

KEY = "ab" * 32
TOKEN = "123456789:" + "A" * 35


def part(path: Path, start_us: int, rows: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ts = start_us + np.arange(rows, dtype=np.int64) * 1_000_000
    np.savez_compressed(path, ts=ts, bid=np.full(rows, 100.0), ask=np.full(rows, 100.1), bid_sz=np.ones(rows),
                        ask_sz=np.ones(rows))


def machine(root: Path) -> ex.Roots:
    """A server as `bot up` leaves it: three parts with state, logs, tape, and secrets that must stay behind."""
    bot, lighter, arb = root / "bot", root / "lighter", root / "arb"
    for d in (bot / "state", bot / "logs" / "runs", bot / "config", bot / "data" / "scout" / "scans",
              bot / "data" / "scout" / "cache" / "v10", lighter / "state", lighter / "logs", lighter / "data" / "scout",
              arb / "state"):
        d.mkdir(parents=True)
    (bot / ".env").write_text(f"ARCUS_API_PRIVATE_KEY=0x{KEY}\nTELEGRAM_BOT_TOKEN={TOKEN}\nBOT_PILOT_LIVE=1\n"
                              "ARCUS_ADDRESS=0x1111111111111111111111111111111111111111\n")
    (bot / "config" / "app.yaml").write_text("state_dir: state\n")
    (bot / "config" / "secrets.enc").write_text("encrypted")
    db = sqlite3.connect(bot / "state" / "live.sqlite")
    db.executescript(SCHEMA)
    day = 1_791_000_000_000_000
    db.execute("INSERT INTO fills VALUES ('t1','arcus','SPY','c1','buy','770.10','2','0',1,0,?,'b0','pilot')", (day,))
    db.execute("INSERT INTO fills VALUES ('t2','arcus','SPY','c2','sell','770.20','2','0.35',0,0,?,'exit','pilot')",
               (day + 60_000_000,))
    db.execute("INSERT INTO orders (client_id, venue, base, side, price, size, status, reject_reason, created_us, "
               "updated_us) VALUES ('c3','arcus','SPY','buy','770','1','REJECTED','POST_ONLY_WOULD_CROSS',?,?)",
               (day, day))
    db.execute("INSERT INTO events (ts_us, venue, kind, payload) VALUES (?, 'arcus', 'update', ?)",
               (day, f"signed with {KEY} by mistake"))
    db.execute("INSERT INTO kv VALUES ('run_vol:SPY-USD-1791000000000000', '3080.60')")
    db.execute("INSERT INTO kv VALUES ('run_pnl:SPY-USD-1791000000000000', '-0.15')")
    db.commit()
    db.close()
    (bot / "state" / "scout.pid").write_text("123")
    (bot / "state" / "balances.jsonl").write_text('{"ts": 1791000000, "equity": 240.0, "net_deposits": 250.0}\n')
    (bot / "logs" / "bot.jsonl").write_text(
        '{"ts":1791000000000000,"level":"INFO","component":"engine","event":"resize","reason":null}\n'
        '{"ts":1791000001000000,"level":"WARNING","component":"ws","event":"ws_degraded","reason":"book stale"}\n'
        f'{{"ts":1791000002000000,"level":"ERROR","component":"telegram","event":"send","reason":"bot{TOKEN} 502"}}\n')
    (bot / "logs" / "scout.out").write_text("started\nTraceback (most recent call last):\n  File x\nKeyError: 'bid'\n")
    scout = bot / "data" / "scout"
    (scout / "recorder.json").write_text('{"ts": 1791000000, "markets": 60, "rows_total": 5, "free_gb": 40.0}')
    (scout / "latest.json").write_text("{}")
    (scout / "scans" / "20261005-1200.json").write_text("{}")
    (scout / "cache" / "v10" / "big.json").write_text("{}")
    part(scout / "tape" / "BTC-USD" / "2026-10-05" / "bbo-rec1-497500.npz", day, 50)
    part(scout / "tape" / "BTC-USD" / "2026-10-05" / "bbo-rec1-497501.npz", day + 3_600_000_000, 20)
    (scout / "tape" / "BTC-USD" / "2026-10-05" / "bbo-rec1-497502.npz.tmp.npz").write_text("half written")
    part(lighter / "data" / "tape" / "BTC" / "2026-10-05" / "bbo-rec152037803-00001.npz", day, 30)
    (lighter / "data" / "recorder.json").write_text('{"t": 1791000000, "markets": 57, "rows_total": 9}')
    (lighter / "state" / "fills-paper.jsonl").write_text(
        '{"t": 1791000000, "market": "BTC", "usd": 254.45, "maker": true}\n')
    (lighter / "logs" / "run-paper-2026-10-05.jsonl").write_text(
        '{"t":1791000000,"lvl":"warning","c":"rest","ev":"rate_limited"}\n')
    (arb / "state" / "position-paper.json").write_text('{"phase": "flat", "paused": true}')
    (arb / "state" / "run-paper.pid").write_text("5")
    (arb / "settings.json").write_text('{"max_hold_h": 72}')
    return ex.Roots(bot, lighter, arb)


def empty(root: Path) -> ex.Roots:
    for d in ("bot/state", "lighter", "arb"):
        (root / d).mkdir(parents=True)
    return ex.Roots(root / "bot", root / "lighter", root / "arb")


def run(r: ex.Roots, **kw: object) -> ex.Result:
    return ex.run_export(r, probe=False, say=lambda s: None, **kw)   # type: ignore[arg-type]


def everything_in(path: Path) -> tuple[list[str], bytes, dict[str, bytes]]:
    """(member names, every byte of the file with the records unpacked, {record path: content})."""
    blob = path.read_bytes()
    records: dict[str, bytes] = {}
    with tarfile.open(path) as tar:
        names = tar.getnames()
        inner = tar.extractfile(next(n for n in names if n.endswith("records.tar.gz")))
        assert inner is not None
        raw = gzip.decompress(inner.read())
        with tarfile.open(fileobj=io.BytesIO(raw)) as tz:
            for m in tz.getmembers():
                if m.isfile():
                    records[m.name] = tz.extractfile(m).read()   # type: ignore[union-attr]
    return names, blob + raw, records


def test_one_file_holds_the_trades_the_logs_and_the_tape_but_no_secret(tmp_path: Path) -> None:
    r = machine(tmp_path / "srv")
    res = run(r)
    assert ex.NAME_RE.match(res.name) and res.path == tmp_path / "srv" / "exports" / f"{res.name}.tar"
    assert res.cut.mode == "full" and res.tape_files == 3 and not res.warnings
    names, data, rec = everything_in(res.path)
    assert names[-1] == f"{res.name}/MANIFEST.json"            # written last: a cut-off copy has none
    assert KEY.encode() not in data and TOKEN.encode() not in data
    assert not any(n.endswith((".env", "secrets.enc", ".pid", ".tmp.npz")) or "/cache/" in n for n in [*names, *rec])
    assert f"{res.name}/tape/arcus/BTC-USD/2026-10-05/bbo-rec1-497500.npz" in names
    assert f"{res.name}/tape/lighter/BTC/2026-10-05/bbo-rec152037803-00001.npz" in names
    for want in ("records/arcus/state/live.sqlite", "records/arcus/state/balances.jsonl", "records/arcus/logs/bot.jsonl",
                 "records/arcus/config/app.yaml", "records/arcus/data/scout/latest.json",
                 "records/arcus/data/scout/scans/20261005-1200.json", "records/arcus/csv/fills-live.csv",
                 "records/lighter/state/fills-paper.jsonl", "records/arb/state/position-paper.json",
                 "records/arb/settings.json", "records/system/host.txt", "records/system/env-names.txt"):
        assert want in rec, want
    assert b"770.10" in rec["records/arcus/csv/fills-live.csv"]
    env = rec["records/system/env-names.txt"].decode()
    assert "ARCUS_API_PRIVATE_KEY = set" in env and "BOT_PILOT_LIVE = 1" in env and "ARCUS_ADDRESS = set" in env
    with tarfile.open(res.path) as tar:
        summary = tar.extractfile(f"{res.name}/SUMMARY.md").read().decode()   # type: ignore[union-attr]
    for said in ("2 fills", "50% as maker", "POST_ONLY_WOULD_CROSS", "SPY-USD", "0.49 bp", "ws_degraded", "rate_limited",
                 "KeyError: 'bid'", "trading PnL since the first deposit -10.00", "arcus: 1 markets"):
        assert said in summary, said


def test_the_database_copy_is_whole_and_the_secret_in_it_is_blanked(tmp_path: Path) -> None:
    r = machine(tmp_path / "srv")
    out = tmp_path / "copy.sqlite"
    ex.copy_sqlite(r.bot / "state" / "live.sqlite", out, ex.secret_values(r, {}))
    db = sqlite3.connect(out)
    assert db.execute("SELECT count(*) FROM fills").fetchone() == (2,)
    assert db.execute("SELECT payload FROM events").fetchone() == ("signed with [redacted] by mistake",)
    db.close()
    assert KEY.encode() not in out.read_bytes()
    assert KEY in sqlite3.connect(r.bot / "state" / "live.sqlite").execute("SELECT payload FROM events").fetchone()[0]


def test_import_merges_the_tape_and_never_touches_this_machines_state(tmp_path: Path) -> None:
    src = machine(tmp_path / "srv")
    res = run(src)
    dst = empty(tmp_path / "mac")
    said: list[str] = []
    got = ex.run_import(dst, ex.find_export(dst, str(res.path)), say=said.append)
    assert got["ok"] and got["added"] == 3 and got["short"] == 0 and got["damaged"] == 0
    for venue, root in src.tapes().items():
        for f in root.rglob("*.npz"):
            if not f.name.endswith(".tmp.npz"):
                assert (dst.tapes()[venue] / f.relative_to(root)).read_bytes() == f.read_bytes()
    home = dst.bot / "data" / "server-export" / res.name
    assert (home / "SUMMARY.md").exists() and (home / "records" / "arcus" / "state" / "live.sqlite").exists()
    assert (home.parent / "LATEST").read_text().strip() == res.name
    assert list((dst.bot / "state").iterdir()) == []            # its own state: not a file added
    assert any("2 complete, 0 short" in s for s in said)       # arcus BTC-USD and lighter BTC, one day of bbo each
    again = ex.run_import(dst, res.path, say=lambda s: None)    # a second time changes nothing
    assert again["ok"] and again["same"] == 3 and again["added"] == 0


def test_the_next_export_sends_only_what_is_new_and_a_longer_part_replaces_the_shorter(tmp_path: Path) -> None:
    src = machine(tmp_path / "srv")
    t0 = time.time()
    first = run(src, now=t0)
    dst = empty(tmp_path / "mac")
    assert ex.run_import(dst, first.path, say=lambda s: None)["ok"]
    for f in [*src.tapes()["arcus"].rglob("*.npz"), *src.tapes()["lighter"].rglob("*.npz"),
              src.bot / "data" / "scout" / "scans" / "20261005-1200.json"]:
        os.utime(f, (t0 - 7200, t0 - 7200))                     # recorded long before the first export
    day = 1_791_000_000_000_000
    grown = src.tapes()["arcus"] / "BTC-USD" / "2026-10-05" / "bbo-rec1-497501.npz"
    part(grown, day + 3_600_000_000, 45)                        # the recorder wrote its current part again, longer
    part(src.tapes()["arcus"] / "ETH-USD" / "2026-10-06" / "bbo-rec1-497524.npz", day + 86_400_000_000, 10)
    second = run(src, now=t0 + 3600)
    assert second.cut.mode == "new" and second.tape_files == 2
    _, _, rec = everything_in(second.path)
    assert "records/arcus/data/scout/scans/20261005-1200.json" not in rec     # old bulk: not sent again
    assert "records/arcus/state/live.sqlite" in rec                           # state: always whole
    got = ex.run_import(dst, second.path, say=lambda s: None)
    assert got["ok"] and got["grown"] == 1 and got["added"] == 1 and got["chain_ok"]
    assert (dst.tapes()["arcus"] / "BTC-USD" / "2026-10-05" / "bbo-rec1-497501.npz").read_bytes() == grown.read_bytes()


def test_a_small_export_does_not_move_the_mark(tmp_path: Path) -> None:
    r = machine(tmp_path / "srv")
    small = run(r, days=1, now=1_791_300_000.0)                 # 2026-10-06: the tape here is of 2026-10-05
    assert small.cut.mode == "days" and small.tape_files == 0 and small.record_files > 5
    assert "last_ts" not in ex.read_mark(r.bot / "state")
    notape = run(r, tape=False, now=1_791_300_100.0)
    assert notape.tape_files == 0 and "last_ts" not in ex.read_mark(r.bot / "state")
    assert run(r, since="2026-10-05", now=1_791_300_200.0).tape_files == 3
    full = run(r, now=1_791_300_300.0)
    assert full.cut.mode == "full" and ex.read_mark(r.bot / "state")["last_name"] == full.name
    assert len(list((r.repo / "exports").glob("tb-*.tar"))) == 3       # --keep 3: the oldest file was removed
    with pytest.raises(ex.ExportError, match="2026-10-01"):
        run(r, since="yesterday")


def test_import_keeps_a_longer_part_it_already_has_and_reports_damage(tmp_path: Path) -> None:
    src = machine(tmp_path / "srv")
    res = run(src)
    dst = empty(tmp_path / "mac")
    mine = dst.tapes()["arcus"] / "BTC-USD" / "2026-10-05" / "bbo-rec1-497500.npz"
    part(mine, 1_791_000_000_000_000, 80)                       # this machine already has more of that part
    before = mine.read_bytes()
    got = ex.run_import(dst, res.path, say=lambda s: None)
    assert got["kept"] == 1 and got["added"] == 2 and got["ok"] and mine.read_bytes() == before

    broken = tmp_path / f"{res.name}.tar"
    data = bytearray(res.path.read_bytes())
    with tarfile.open(res.path) as tar:
        m = tar.getmember(f"{res.name}/tape/lighter/BTC/2026-10-05/bbo-rec152037803-00001.npz")
    data[m.offset_data + 10] ^= 0xFF                             # one flipped byte on the way
    broken.write_bytes(bytes(data))
    other = empty(tmp_path / "mac2")
    said: list[str] = []
    got = ex.run_import(other, broken, say=said.append)
    assert not got["ok"] and got["damaged"] == 1 and any("DAMAGED" in s for s in said)
    assert not list(other.tapes()["lighter"].rglob("*.npz"))    # the damaged file was not kept

    cut = tmp_path / "cut" / f"{res.name}.tar"
    cut.parent.mkdir()
    cut.write_bytes(res.path.read_bytes()[: m.offset_data + 512])
    with pytest.raises(ex.ExportError, match="incomplete"):
        ex.run_import(empty(tmp_path / "mac3"), cut, say=lambda s: None)


def test_an_export_never_fills_the_disk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    r = machine(tmp_path / "srv")
    monkeypatch.setattr(shutil, "disk_usage", lambda p: shutil._ntuple_diskusage(100 * 10**9, 96 * 10**9, 4 * 10**9))
    with pytest.raises(ex.ExportError, match="not enough room"):
        run(r)
    assert not list((r.repo / "exports").glob("*"))             # nothing half-made left behind
    assert run(r, force=True).tape_files == 3


def test_an_incremental_export_says_when_the_one_before_it_was_never_imported(tmp_path: Path) -> None:
    src = machine(tmp_path / "srv")
    t0 = time.time()
    run(src, now=t0)
    second = run(src, now=t0 + 120)
    said: list[str] = []
    got = ex.run_import(empty(tmp_path / "mac"), second.path, say=said.append)
    assert not got["chain_ok"] and any("bot export --full" in s for s in said)


def test_secrets_are_found_by_name_and_indexes_are_not(tmp_path: Path) -> None:
    r = machine(tmp_path / "srv")
    vals = ex.secret_values(r, {"LIGHTER_API_KEY_INDEX": "4", "PROFUNDING_API_KEY": "pf_live_12345678",
                                "TELEGRAM_CHAT_ID": "123456789"})
    assert set(vals) == {KEY.encode(), ("0x" + KEY).encode(), TOKEN.encode(), b"pf_live_12345678"}
    assert ex.scrub(f"GET /bot{TOKEN}/send x-api-key: pf_live_12345678".encode(), vals) == \
        b"GET /bot[redacted]/send x-api-key: [redacted]"
    assert json.loads(ex.scrub(b'{"chat": 123456789}', vals)) == {"chat": 123456789}


def test_a_services_endless_output_is_sent_from_its_end_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    r = machine(tmp_path / "srv")
    (r.bot / "logs" / "scout.out").write_text("".join(f"line {i:05d}\n" for i in range(5000)))
    monkeypatch.setattr(ex, "OUT_TAIL", 1024)
    res = run(r)
    _, _, rec = everything_in(res.path)
    out = rec["records/arcus/logs/scout.out"]
    assert len(out) <= 1024 and out.endswith(b"line 04999\n") and out.startswith(b"line ")
    assert res.warnings == ["arcus/logs/scout.out: the last 0 MB of 0 MB"]
    assert len(rec["records/arcus/logs/bot.jsonl"]) > 100        # the dated logs always go whole


def test_a_part_the_recorder_starts_during_the_export_waits_for_the_next_one(tmp_path: Path,
                                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    """The recorders write a new part every few minutes. One that appears after the files were chosen must not be
    counted as sent, or the receiving side would call the day short."""
    src = machine(tmp_path / "srv")
    late = src.tapes()["lighter"] / "BTC" / "2026-10-05" / "bbo-rec152037803-00002.npz"
    real = ex.copy_file

    def copy_and_meanwhile(a: Path, b: Path, secrets: list[bytes]) -> str:
        if not late.exists():
            part(late, 1_791_000_300_000_000, 40)
        return real(a, b, secrets)

    monkeypatch.setattr(ex, "copy_file", copy_and_meanwhile)
    t0 = time.time()
    first = run(src, now=t0)
    assert first.tape_files == 3 and late.exists()
    dst = empty(tmp_path / "mac")
    assert ex.run_import(dst, first.path, say=lambda s: None)["ok"]
    second = run(src, now=t0 + 60)
    names, _, _ = everything_in(second.path)
    assert f"{second.name}/tape/lighter/BTC/2026-10-05/{late.name}" in names
    got = ex.run_import(dst, second.path, say=lambda s: None)
    assert got["ok"] and got["added"] == 1


def test_tape_names_cannot_leave_the_tape_folder(tmp_path: Path) -> None:
    assert ex.TAPE_RE.match("tape/arcus/BTC-USD/2026-10-05/bbo-rec1-497500.npz")
    assert ex.TAPE_RE.match("tape/lighter/1000PEPE/2026-10-05/stats-rec152037803-00001.npz")
    for bad in ("tape/arcus/../2026-10-05/x.npz", "tape/arcus/BTC-USD/2026-10-05/../x.npz", "tape/other/BTC/2026-10-05/x.npz",
                "tape/arcus/BTC-USD/2026-10-05/.x.npz", "/tape/arcus/BTC-USD/2026-10-05/x.npz",
                "tape/arcus/BTC-USD/20261005/x.npz", "tape/arcus/BTC-USD/2026-10-05/x.npz.tmp"):
        assert not ex.TAPE_RE.match(bad), bad
    r = machine(tmp_path / "srv")
    part(r.tapes()["arcus"] / ".odd" / "2026-10-05" / "bbo-rec1-1.npz", 1_791_000_000_000_000, 5)
    res = run(r)
    assert res.tape_files == 3 and any(".odd" in w and "left out" in w for w in res.warnings)


def test_lighter_is_not_timed_while_it_trades_live(tmp_path: Path) -> None:
    from bot import export_report as rp

    r = machine(tmp_path / "srv")
    assert not rp.lighter_trading(r)
    (r.lighter / "state" / "run-live.pid").write_text(str(os.getpid()))      # a live Lighter run is up
    assert rp.lighter_trading(r)
    text = rp.network_text({"lighter": "https://api.rh.lighter.xyz"}, skip=("lighter",))   # sends nothing
    assert "not timed" in text and "api.rh.lighter.xyz" in text
