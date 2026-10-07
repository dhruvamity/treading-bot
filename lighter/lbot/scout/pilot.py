"""The pilot: turn a list's pick (or the owner's own choice) into a run, and keep a list's pick honest.

- start(): write the run spec and start `lbot run` in the background. A run already going in that mode is closed
  first (its position closed, maker then taker) so one market and one setup trade at a time.
- review(), after every scan: a run that came from a list and no longer passes the checks is paused (orders that
  close the position keep working) with the reason; after two scans that pass again it resumes. The owner's own
  picks are never paused for their numbers.
Every event is a line in state/pilot_events.jsonl, which the Telegram bot posts.
"""

from __future__ import annotations

import json
import time
from typing import Any

from lbot import ops
from lbot.config import Config
from lbot.log import Log
from lbot.trade.engine import RunSpec, send_control
from lbot.trade.runner import save_spec

log = Log("pilot")
MAX_SCAN_AGE_MIN = 90


def event(cfg: Config, kind: str, text: str, **kw: Any) -> None:
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    with open(cfg.state_dir / "pilot_events.jsonl", "a") as fh:
        fh.write(json.dumps({"t": time.time(), "kind": kind, "text": text, **kw}) + "\n")


def latest(cfg: Config) -> dict[str, Any] | None:
    return ops.read_json(cfg.data_dir / "scout" / "latest.json")


def pick(cfg: Config, lst: str, n: int) -> dict[str, Any]:
    scan = latest(cfg)
    if scan is None:
        raise ValueError("no scan yet: is the scout running? (lbot up)")
    age = (time.time() - scan["t"]) / 60
    if age > MAX_SCAN_AGE_MIN:
        raise ValueError(f"the last scan is {age:.0f} minutes old: is the scout running?")
    rows = scan["lists"].get(lst) or []
    if not 1 <= n <= len(rows):
        raise ValueError(f"the {lst} list has {len(rows)} entries")
    return rows[n - 1]


def start(cfg: Config, spec: RunSpec, *, wait_close_s: float = 600.0, confirmed: bool = False) -> int:
    """Start `spec` in the background (closing a run already going in the same mode first). A live spec needs
    `confirmed` (the owner typed LIVE or the Telegram code): the run checks the confirmation is fresh."""
    name = f"run-{spec.mode}"
    if ops.running(cfg, name):
        send_control(cfg.state_dir, spec.mode, "close")
        end = time.time() + wait_close_s
        while ops.running(cfg, name) and time.time() < end:
            time.sleep(1)
        ops.stop(cfg, name)
    p = save_spec(cfg, spec)
    if spec.mode == "live":
        if not confirmed:
            raise ValueError("a live run needs your confirmation")
        raw = json.loads(p.read_text())
        raw["confirmed_at"] = time.time()
        p.write_text(json.dumps(raw, indent=1))
    args = ["run", "--spec", str(p)]
    if spec.mode == "live":
        args += ["--live", "--confirmed"]
    pid = ops.start(cfg, name, args)
    event(cfg, "started", f"{spec.mode.upper()} {spec.market} {spec.setup} @ {spec.leverage:g}x ({spec.source})",
          mode=spec.mode, market=spec.market, setup=spec.setup)
    return pid


def review(cfg: Config, scan: dict[str, Any]) -> None:
    """After a scan: pause or resume a running list pick."""
    state_p = cfg.state_dir / "pilot.json"
    st = ops.read_json(state_p) or {}
    for mode in ("paper", "live"):
        if not ops.running(cfg, f"run-{mode}"):
            continue
        status = ops.read_json(cfg.state_dir / f"status-{mode}.json") or {}
        src = str(status.get("source", "you"))
        if src == "you":
            continue
        row = next((r for r in scan["table"] if r["market"] == status.get("market")
                    and r["setup"] == status.get("setup")), None)
        why = row["why"] if row else ["not in the scan"]
        s = st.setdefault(mode, {"paused": False, "good": 0})
        if why and not s["paused"]:
            send_control(cfg.state_dir, mode, "pause")
            s.update(paused=True, good=0)
            event(cfg, "paused", f"{status.get('market')} {status.get('setup')} paused: {'; '.join(why)}", mode=mode)
        elif not why and s["paused"]:
            s["good"] += 1
            if s["good"] >= 2:
                send_control(cfg.state_dir, mode, "unpause")
                s.update(paused=False, good=0)
                event(cfg, "resumed", f"{status.get('market')} {status.get('setup')} passes again: resumed", mode=mode)
    state_p.write_text(json.dumps(st))


def offer(cfg: Config, scan: dict[str, Any]) -> None:
    """When the Most Volume #1 changes, say so (the owner decides)."""
    top = (scan["lists"].get("most") or [None])[0]
    key = f"{top['market']} {top['setup']}" if top else ""
    p = cfg.state_dir / "pilot_offer.txt"
    old = p.read_text() if p.exists() else ""
    if key and key != old:
        p.write_text(key)
        event(cfg, "offer", f"New #1 Most Volume: {key} @ {top['leverage']:g}x, ${top['volume_d']:,.0f} a day at "
                            f"${top['cost_1k']:.3f} per $1,000 (backtest, {top['days']} day(s))")
