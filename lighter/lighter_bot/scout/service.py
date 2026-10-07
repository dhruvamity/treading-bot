"""The scout daemon (`lighter scout run`): the recorder around the clock, a scan every `scan_every` minutes, the pilot's
review after each scan. A scan runs in a thread next to the recorder; its workers run at the lowest CPU priority.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from typing import Any

from lighter_bot import settings
from lighter_bot.config import Config
from lighter_bot.log import Log
from lighter_bot.scout import autopilot, calendar, pilot
from lighter_bot.scout.record import Recorder
from lighter_bot.scout.scan import Scanner
from lighter_bot.venue.rest import Rest

log = Log("scout")


async def account_capital(cfg: Config) -> float:
    """What to size the scan for: the owner's fixed capital, else the account's equity, else the paper capital."""
    eff = settings.effective(cfg)
    cap = eff.get("capital", "auto")
    if cap not in (None, "auto"):
        return float(cap)
    if cfg.creds.address or cfg.creds.account_index is not None:
        rest = Rest(cfg.endpoints.rest)
        try:
            from lighter_bot.trade.live import resolve_account
            idx = await resolve_account(rest, cfg)
            r = await rest.account(idx)
            acc = (r.get("accounts") or [{}])[0]
            eq = float(acc.get("total_asset_value") or acc.get("collateral") or 0)
            if eq > 0:
                from lighter_bot.trade.sizing import target_capital
                return target_capital(eq, frac=float(eff["trade_share"]) / 100, max_capital=eff.get("max_capital"))
        except Exception as e:
            log.warn("capital_read_failed", err=str(e))
        finally:
            await rest.close()
    return cfg.sizing.paper_capital_usd


def scan_once(cfg: Config, capital: float, **kw: Any) -> dict[str, Any]:
    eff = settings.effective(cfg)
    out = Scanner(cfg).scan(capital, stops=settings.stops(cfg), volume_cost=float(eff["volume_cost"]),
                            lev_cap=settings.lev_cap(cfg), **kw)
    pilot.review(cfg, out)
    pilot.offer(cfg, out)
    return out


async def run(cfg: Config, *, record: bool = True, seconds: float | None = None) -> None:
    cfg.ensure_dirs()
    rest = Rest(cfg.endpoints.rest)
    rec = Recorder(cfg.data_dir, rest, cfg.endpoints.ws) if record else None
    rec_task = asyncio.create_task(rec.run(seconds)) if rec else None
    end = time.time() + seconds if seconds else None
    next_scan = time.time() + 10
    next_auto = 0.0
    earn_day = ""
    try:
        while True:
            now = time.time()
            if now >= next_auto:
                next_auto = now + 60
                try:
                    await autopilot.tick(cfg, pilot.latest(cfg), now)
                except Exception as e:
                    log.error("autopilot_failed", err=f"{type(e).__name__}: {e}")
            if time.strftime("%Y-%m-%d", time.gmtime(now)) != earn_day:
                earn_day = time.strftime("%Y-%m-%d", time.gmtime(now))
                from lighter_bot.scout.record import load_markets
                syms = set(load_markets(cfg.data_dir))
                try:
                    n = await calendar.fetch_earnings(cfg, syms)
                    log.info("earnings", rows=n)
                except Exception as e:
                    log.warn("earnings_failed", err=str(e))
            if end and now >= end:
                break
            if rec_task and rec_task.done():
                break
            asked = cfg.state_dir / "scan_now"
            if now >= next_scan or asked.exists():
                asked.unlink(missing_ok=True)
                try:
                    cap = await account_capital(cfg)
                    out = await asyncio.to_thread(scan_once, cfg, cap)
                    log.info("scan_done", took_s=out["took_s"], lists={k: len(v) for k, v in out["lists"].items()})
                except Exception as e:
                    log.error("scan_failed", err=f"{type(e).__name__}: {e}")
                every = float(settings.effective(cfg)["scan_every"])
                next_scan = time.time() + every * 60
                (cfg.state_dir / "scan_status.json").write_text(json.dumps({"last": time.time(), "next": next_scan}))
            await asyncio.sleep(2)
    finally:
        if rec:
            rec.stop()
        if rec_task:
            with contextlib.suppress(Exception):
                await rec_task
        await rest.close()
