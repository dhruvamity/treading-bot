"""`bot scout run`: record every Arcus perp, scan every `every_min` minutes, review the running deployment.

Writes data/scout/latest.json (and a copy per scan under data/scout/scans/) and hands the result to the pilot, which
pauses a deployment whose conditions changed and offers the top 3 when nothing is running.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import signal
import time
from pathlib import Path
from typing import Any

from bot.common.logging import Log
from bot.scout.pilot import Pilot
from bot.scout.record import ScoutRecorder
from bot.scout.scan import scan, table

log = Log("scout")


def save_scan(root: Path, res: dict[str, Any]) -> Path:
    """latest.json (everything; the pilot reads it), a slim copy per scan under scans/ (no per-setting list, so weeks
    of scans stay small), and report.txt plus reports/<day>.txt (the last scan of each UTC day) to read by eye."""
    d = root / "data" / "scout"
    (d / "scans").mkdir(parents=True, exist_ok=True)
    (d / "reports").mkdir(parents=True, exist_ok=True)
    t = time.gmtime(res["ts_us"] / 1e6)
    slim = {k: v for k, v in res.items() if k != "all"}
    (d / "scans" / time.strftime("%Y%m%d-%H%M.json", t)).write_text(json.dumps(slim, default=str))
    tmp = d / "latest.json.tmp"
    tmp.write_text(json.dumps(res, default=str))
    tmp.replace(d / "latest.json")
    text = f"scan at {time.strftime('%Y-%m-%d %H:%M UTC', t)}\n{table(res, 60)}\n"
    (d / "report.txt").write_text(text)
    (d / "reports" / time.strftime("%Y-%m-%d.txt", t)).write_text(text)
    return d / "latest.json"


async def run_service(root: Path, pilot: Pilot, *, rest_url: str, ws_url: str, every_min: float = 30.0,
                      workers: int = 4, record: bool = True, ladder: bool = True, depth: bool = False) -> None:
    rec = ScoutRecorder(root / "data" / "scout", rest_url=rest_url, ws_url=ws_url, depth=depth) if record else None
    rec_task = asyncio.create_task(rec.run()) if rec else None
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)
    try:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), 10)
        while not stop.is_set():
            t0 = time.time()
            if rec:
                rec.flush()   # scan on data up to now
            try:
                res = await loop.run_in_executor(None, lambda: scan(root / "data" / "scout", workers=workers,
                                                                    ladder=ladder))
                save_scan(root, res)
                events = pilot.review(res)
                log.info("scout_scan", data={"took_s": res["took_s"], "go": len(res["top"]),
                                             "events": [e["kind"] for e in events]})
            except Exception as e:  # a failed scan must not stop the recorder
                log.error("scout_scan_failed", reason=type(e).__name__, data={"err": str(e)[:300]}, exc_info=True)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), max(60.0, every_min * 60 - (time.time() - t0)))
    finally:
        if rec and rec_task:
            rec.stop()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(rec_task, 30)
        log.info("scout_stopped")
