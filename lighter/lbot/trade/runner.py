"""Build and run one run (paper or live) from a RunSpec.

Paper needs nothing but the public feed. Live needs LBOT_LIVE=1 in lighter/.env, the credentials, a passing doctor
(lbot/doctor.py) and the owner's typed confirmation (the CLI asks for LIVE, Telegram for a one-time code).
"""

from __future__ import annotations

import json
import time
from dataclasses import replace
from pathlib import Path

from lbot.config import Config
from lbot.log import Log
from lbot.scout.record import load_markets, save_markets
from lbot.trade.engine import Engine, RunSpec, RunState
from lbot.trade.exchange import Exchange, PaperExchange
from lbot.trade.feed import MarketFeed
from lbot.venue.market import Market, parse_markets
from lbot.venue.nonce import ClientIds, Nonces
from lbot.venue.rest import Rest

log = Log("runner")


class RunRefused(RuntimeError):
    pass


async def fetch_markets(cfg: Config) -> dict[str, Market]:
    rest = Rest(cfg.endpoints.rest)
    try:
        ms = parse_markets(await rest.markets())
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        save_markets(cfg.data_dir, ms)
        return ms
    except Exception as e:
        log.warn("markets_fetch_failed", err=str(e))
        ms = load_markets(cfg.data_dir)
        if not ms:
            raise
        return ms
    finally:
        await rest.close()


def order_ceiling(cfg: Config, market: str) -> float | None:
    """The market's liquidity ceiling for one order (the scout writes it after each scan)."""
    try:
        d = json.loads((cfg.data_dir / "scout" / "ceilings.json").read_text())
        v = d.get(market)
        return float(v) if v else None
    except (OSError, ValueError):
        return None


async def build(cfg: Config, spec: RunSpec, *, resume: bool = True) -> Engine:
    ms = await fetch_markets(cfg)
    m = ms.get(spec.market)
    if m is None:
        raise RunRefused(f"{spec.market} is not a Lighter perp (lbot markets lists them)")
    if not m.active or m.reduce_only:
        raise RunRefused(f"{spec.market} is not open for new positions on Lighter now")
    state = cfg.state_dir
    state.mkdir(parents=True, exist_ok=True)
    feed = MarketFeed(cfg.endpoints.ws, m)
    ids = ClientIds(state / f"client-ids-{spec.mode}.txt")
    ex: Exchange
    if spec.mode == "live":
        if not cfg.live_allowed:
            raise RunRefused("live trading is off: set LBOT_LIVE=1 in lighter/.env")
        if not cfg.creds.can_sign:
            raise RunRefused("LIGHTER_API_PRIVATE_KEY is not set in lighter/.env")
        from lbot.trade.live import LiveExchange, resolve_account
        rest = Rest(cfg.endpoints.rest)
        try:
            account = await resolve_account(rest, cfg)
        finally:
            await rest.close()
        nonces = Nonces(state / f"nonce-{account}-{cfg.creds.api_key_index}.txt")
        ex = LiveExchange(m, feed, cfg, account, ids, nonces)
    else:
        cap = spec.capital or cfg.sizing.paper_capital_usd
        ex = PaperExchange(m, feed, cfg.requests, cfg.latency, ids, cap)
    from lbot import settings
    eff = settings.effective(cfg)
    if spec.capital is None and eff.get("capital") not in (None, "auto"):
        spec = replace(spec, capital=float(eff["capital"]))       # the owner's fixed capital (never above equity)
    run_path = state / f"run-{spec.mode}.json"
    old = RunState.load(run_path) if resume else None
    same = old is not None and old.spec.market == spec.market and old.spec.setup == spec.setup \
        and old.spec.started == spec.started and not old.done
    run = old if (same and old is not None) else RunState(spec)
    if spec.mode == "paper" and run is not old:
        (state / "fills-paper.jsonl").unlink(missing_ok=True)
    return Engine(ex, run, state, frac=float(eff["trade_share"]) / 100, max_capital=eff.get("max_capital"),
                  order_max=order_ceiling(cfg, spec.market), period_s=cfg.loop_ms / 1000)


def spec_path(cfg: Config, mode: str) -> Path:
    return cfg.state_dir / f"spec-{mode}.json"


def save_spec(cfg: Config, spec: RunSpec) -> Path:
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    p = spec_path(cfg, spec.mode)
    d = spec.__dict__.copy()
    d["stops"] = list(spec.stops)
    p.write_text(json.dumps(d, indent=1))
    return p


def load_spec(p: Path) -> RunSpec:
    d = json.loads(p.read_text())
    d["stops"] = tuple(d.get("stops") or (2.0, 5.0, 25.0))
    d.setdefault("started", time.time())
    known = set(RunSpec.__dataclass_fields__)
    return RunSpec(**{k: v for k, v in d.items() if k in known})
