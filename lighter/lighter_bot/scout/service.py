"""The scout daemon (`lighter scout run`): the recorder around the clock, a scan every `scan_every` minutes, the pilot's
review after each scan. A scan runs in a thread next to the recorder; its workers run at the lowest CPU priority.

Three jobs, and a machine does the ones its role gives it (BOT_ROLE, lighter_bot/config.py; `tbot up` picks):
record (the tape), rank (the scans) and supervise (the pilot's review, the autopilot). A recorder machine only
records. A trader machine neither records nor ranks: it follows, reviewing its run each time a new scan arrives from
the machine that made it (the one `tbot sync` fetches Arcus's and Lighter's lists together), and keeps its own
market list for the run form.
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


def scan_once(cfg: Config, capital: float, supervise: bool = True, **kw: Any) -> dict[str, Any]:
    """One scan; with `supervise` (a machine that trades) the pilot then reviews the run and offers a new #1."""
    eff = settings.effective(cfg)
    out = Scanner(cfg).scan(capital, stops=settings.stops(cfg), volume_cost=float(eff["volume_cost"]),
                            lev_cap=settings.lev_cap(cfg), **kw)
    if supervise:
        pilot.review(cfg, out)
        pilot.offer(cfg, out)
    return out


MARKETS_EVERY_S = 600.0


def follow_scan(cfg: Config, seen: dict[str, Any], now: float | None = None) -> bool:
    """A trader's review: when a scan it has not seen has arrived (another machine made it), judge the run by it as
    `scan_once` does after its own. The scan already there when the service starts is reviewed only if it is fresh:
    an old one would pause a run for nothing. True when it reviewed."""
    now = now or time.time()
    scan = pilot.latest(cfg)
    t = (scan or {}).get("t")
    if not scan or not t or t == seen.get("t"):
        return False
    first, seen["t"] = "t" not in seen, t
    if first and (now - float(t)) / 60 > pilot.MAX_SCAN_AGE_MIN:
        return False
    pilot.review(cfg, scan)
    pilot.offer(cfg, scan)
    log.info("follow_review", scan_age_s=round(now - float(t)))
    return True


async def run(cfg: Config, *, record: bool = True, seconds: float | None = None, rank: bool = True,
              supervise: bool = True) -> None:
    """record / rank / supervise: the three jobs (the module's note). With rank off and supervise on it follows."""
    cfg.ensure_dirs()
    rest = Rest(cfg.endpoints.rest)
    rec = Recorder(cfg.data_dir, rest, cfg.endpoints.ws) if record else None
    rec_task = asyncio.create_task(rec.run(seconds)) if rec else None
    end = time.time() + seconds if seconds else None
    next_scan = time.time() + 10
    next_auto = next_markets = 0.0
    earn_day = ""
    seen: dict[str, Any] = {}
    try:
        while True:
            now = time.time()
            if supervise and not rec and now >= next_markets:      # no recorder here to keep the market list
                next_markets = now + MARKETS_EVERY_S
                try:
                    from lighter_bot.trade.runner import fetch_markets
                    await fetch_markets(cfg)
                except Exception as e:
                    log.warn("markets_failed", err=str(e))
            if supervise and now >= next_auto:
                next_auto = now + 60
                try:
                    scan = pilot.latest(cfg)
                    if not rank:
                        follow_scan(cfg, seen, now)
                        # lists that stopped arriving are not lists to start a run from (a run going is left alone)
                        if scan and (now - float(scan.get("t") or 0)) / 60 > pilot.MAX_SCAN_AGE_MIN:
                            scan = None
                    await autopilot.tick(cfg, scan, now)
                except Exception as e:
                    log.error("autopilot_failed", err=f"{type(e).__name__}: {e}")
            if (supervise or rank) and time.strftime("%Y-%m-%d", time.gmtime(now)) != earn_day:
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
            if not rank:
                asked.unlink(missing_ok=True)      # nothing scans here
            elif now >= next_scan or asked.exists():
                asked.unlink(missing_ok=True)
                try:
                    cap = await account_capital(cfg)
                    out = await asyncio.to_thread(scan_once, cfg, cap, supervise=supervise)
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
