"""The autopilot (`/auto`, `lighter auto`): trades by itself within a daily budget.

Every minute, inside the scout:
- The pot: `budget` dollars are added at 00:00 UTC; unspent money carries over, up to 7 days of it. Each run's result
  comes out of it (a profit goes back in).
- Nothing running: it starts the Most Volume list's best setup whose backtested cost is within the ceiling (the list's
  own budget by default), whose market is calm now, with no US release within 45 minutes and no earnings within 24
  hours for that stock. The run's loss limit (sl=) is what is left of the pot, at most 3 days of budget.
- Running: it closes the run 15 minutes before a release, before earnings, when the market stops passing the scan's
  checks, or when the list's best becomes another setup with 1.5x the volume (after 20 minutes). It rests 10 minutes
  after each stop.
- You take over: your own run, or a stop or close you send, turns it off.
It is off by default. LIVE needs LBOT_LIVE=1, your typed confirmation when you turn it on, and a passing doctor
before every start.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from lighter_bot import ops, settings
from lighter_bot.config import Config
from lighter_bot.log import Log
from lighter_bot.scout import calendar, pilot
from lighter_bot.trade.engine import RunSpec, send_control

log = Log("auto")
MAX_POT_DAYS = 7
RUN_MAX_DAYS = 3
REST_S = 600
SWITCH_X = 1.5
SWITCH_AFTER_S = 1200
MIN_POT = 0.5


@dataclass
class Auto:
    on: bool = False
    mode: str = "paper"
    budget: float = 5.0
    ceiling: float | None = None          # $ per $1,000 (None: the list's budget, /set volume_cost)
    pot: float = 0.0
    day: str = ""
    rest_until: float = 0.0
    run: dict[str, Any] = field(default_factory=dict)
    skip: dict[str, float] = field(default_factory=dict)   # market -> until (a failed doctor)
    last: str = ""
    log: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def load(cls, cfg: Config) -> Auto:
        d = ops.read_json(cfg.state_dir / "autopilot.json") or {}
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in known})

    def save(self, cfg: Config) -> None:
        self.log = self.log[-200:]
        (cfg.state_dir / "autopilot.json").write_text(json.dumps(asdict(self), indent=1))

    def note(self, cfg: Config, kind: str, text: str) -> None:
        self.last = text
        self.log.append({"t": time.time(), "kind": kind, "text": text})
        pilot.event(cfg, f"auto {kind}", text)


def utc_day(t: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(t))


def turn_on(cfg: Config, mode: str, budget: float | None = None, ceiling: float | None = None) -> Auto:
    a = Auto.load(cfg)
    a.on, a.mode = True, mode
    if budget is not None:
        a.budget = budget
    if ceiling is not None:
        a.ceiling = ceiling
    if a.pot <= 0:
        a.pot, a.day = a.budget, utc_day(time.time())
    a.note(cfg, "on", f"Autopilot ON ({mode}), budget ${a.budget:g} a day, pot ${a.pot:.2f}")
    a.save(cfg)
    return a


def turn_off(cfg: Config, why: str = "turned off") -> Auto:
    a = Auto.load(cfg)
    if a.on and a.run and ops.running(cfg, f"run-{a.mode}"):
        send_control(cfg.state_dir, a.mode, "close")
    a.on = False
    a.note(cfg, "off", f"Autopilot OFF: {why}")
    a.save(cfg)
    return a


def pick(cfg: Config, scan: dict[str, Any], a: Auto, now: float) -> dict[str, Any] | None:
    ceiling = a.ceiling if a.ceiling is not None else float(settings.effective(cfg)["volume_cost"])
    for r in scan["lists"].get("most") or []:
        if r["cost_1k"] > ceiling or a.skip.get(r["market"], 0) > now:
            continue
        if calendar.earnings_block(cfg, r["market"], now):
            continue
        return r
    return None


def close(cfg: Config, a: Auto, why: str, now: float) -> None:
    send_control(cfg.state_dir, a.mode, "close")
    a.rest_until = now + REST_S
    a.note(cfg, "stop", f"Closing {a.run.get('market')} {a.run.get('setup')}: {why}")


async def tick(cfg: Config, scan: dict[str, Any] | None, now: float | None = None) -> Auto:
    now = now or time.time()
    a = Auto.load(cfg)
    if not a.on:
        return a
    day = utc_day(now)
    if day != a.day:
        a.pot = min(a.pot + a.budget, MAX_POT_DAYS * a.budget)
        a.day = day
    name = f"run-{a.mode}"
    alive = ops.running(cfg, name)
    status = ops.read_json(cfg.state_dir / f"status-{a.mode}.json") or {}
    if alive and status.get("source") not in (None, "auto") and status.get("started") != a.run.get("started"):
        a.save(cfg)
        return turn_off(cfg, "you started your own run")
    if a.run and not alive:                                      # its run ended: settle the pot
        pnl = float(status.get("run_pnl") or 0) if status.get("started") == a.run.get("started") else 0.0
        a.pot = max(0.0, a.pot + pnl)
        a.note(cfg, "end", f"{a.run.get('market')} {a.run.get('setup')} ended: ${status.get('run_volume', 0):,.0f} "
                           f"traded, PnL {pnl:+.2f}; pot ${a.pot:.2f}")
        a.run = {}
        a.rest_until = max(a.rest_until, now + REST_S)
    if a.run and alive:
        why = calendar.event_block(cfg, now, closing=True) or calendar.earnings_block(cfg, a.run["market"], now)
        if not why and scan:
            row = next((r for r in scan["table"] if r["market"] == a.run["market"] and r["setup"] == a.run["setup"]), None)
            if row is None or row["why"]:
                why = "; ".join(row["why"]) if row else "no longer in the scan"
            else:
                best = pick(cfg, scan, a, now)
                if best and best["volume_d"] >= SWITCH_X * row["volume_d"] and \
                        now - float(a.run.get("started", now)) > SWITCH_AFTER_S:
                    why = f"{best['market']} {best['setup']} trades {best['volume_d'] / row['volume_d']:.1f}x as much"
        if why:
            close(cfg, a, why, now)
        a.save(cfg)
        return a
    if alive or now < a.rest_until or scan is None:
        a.save(cfg)
        return a
    if a.pot < MIN_POT:
        a.last = f"pot spent (${a.pot:.2f}): waiting for 00:00 UTC"
        a.save(cfg)
        return a
    ev = calendar.event_block(cfg, now)
    if ev:
        a.last = f"waiting: {ev}"
        a.save(cfg)
        return a
    r = pick(cfg, scan, a, now)
    if r is None:
        a.last = "nothing within the ceiling passes the checks now"
        a.save(cfg)
        return a
    sl = round(min(a.pot, RUN_MAX_DAYS * a.budget), 2)
    spec = RunSpec(market=r["market"], setup=r["setup"], leverage=float(r["leverage"]), mode=a.mode,
                   capital=float(r["capital"]) if a.mode == "paper" else None, stops=settings.stops(cfg), sl=sl,
                   source="auto")
    if a.mode == "live":
        from lighter_bot import doctor
        ok, lines = await doctor.check(cfg, spec.market, spec.leverage, spec.stops)
        if not ok:
            a.skip[spec.market] = now + 1800
            a.note(cfg, "error", f"{spec.market}: the doctor failed, skipped for 30 min: "
                                 + "; ".join(t for lv, _, t in lines if lv == "FAIL"))
            a.save(cfg)
            return a
    import asyncio
    await asyncio.to_thread(pilot.start, cfg, spec, confirmed=a.mode == "live")
    a.run = {"market": spec.market, "setup": spec.setup, "started": spec.started, "sl": sl}
    a.note(cfg, "start", f"{a.mode.upper()} {spec.market} {spec.setup} @ {spec.leverage:g}x, run stop ${sl:.2f} "
                         f"(backtest ${r['volume_d']:,.0f} a day at ${r['cost_1k']:.3f}/1k)")
    a.save(cfg)
    return a
