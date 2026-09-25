"""Cross-check the farm's replay against the live engine's paper mode.

For a few menu settings that have a live strategy (mid, anchor, rgrid), it writes one isolated bot home per setting
(its own config/sessions, state and logs, sharing the repository's app and venue config) with a pilot-style session
at the farm's sizing. `bot farm crosscheck` prints the commands that run each one in paper mode next to the farm:

    BOT_HOME=<home> .venv/bin/bot run xcheck --paper --seconds 54000

The paper venue fills orders with its own queue-aware model (bot/venues/paper/fills.py), not the replay's
trade-through rule, so the two bracket the fill-model uncertainty. Compare with `BOT_HOME=<home> bot report
--mode paper` and the farm's row for the same market, setting and leverage.

`prepare_scout` writes the session Telegram's `/run MARKET SETTING max` would deploy for a scout menu setting (for
example "touch 0bp"): the scout's config, the market's maximum leverage (BTC and ETH capped at 20x) and the scout's
stops, without needing a scan (a scan needs 3 recorded days). One home per market: `bot farm paperrun`.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import yaml

from bot.common.sizing import Pct, min_capital, venue_min_usd
from bot.farm.analyze import FARM_PCT, market_info
from bot.farm.menu import BY_NAME
from bot.scout import scan
from bot.scout.pilot import session_for
from bot.scout.sim import Config, Risk

LIVE_MODES = {"mid", "anchor", "rgrid"}
DEFAULT = ("join", "mid+1", "grid+3 r0.5")


def prepare(root: Path, repo_bot: Path, market: str, meta: dict[str, Any], *, settings: tuple[str, ...] = DEFAULT,
            capital: float = 100.0, leverage: float = 10.0, order_max: float | None = None) -> list[dict[str, Any]]:
    mi = market_info(meta)
    px = float(meta.get("markPrice") or meta.get("oraclePrice") or 0)
    vmin = venue_min_usd(mi.min_notional, mi.min_size, px)
    imf = float(meta.get("initialMarginFraction") or 0.2)
    off = float(meta.get("offHoursInitialMarginFraction") or imf)
    lev = min(leverage, 1 / imf)
    risk = Risk.for_capital(capital, lev, min(lev, 1 / off), pct=FARM_PCT, order_max=order_max,
                            min_capital=round(min_capital(vmin, min(lev, 1 / off)), 2))
    out = []
    for name in settings:
        cfg = BY_NAME[name].cfg
        if cfg.mode not in LIVE_MODES:
            continue
        home = root / name.replace(" ", "_").replace("+", "p")
        write_home(home, repo_bot, session_for(market, cfg, risk, live=False))
        out.append({"setting": name, "leverage": lev, "home": str(home),
                    "command": f"BOT_HOME={home} {repo_bot / '.venv' / 'bin' / 'bot'} run xcheck --paper"})
    (root / "crosscheck.json").write_text(json.dumps({"market": market, "capital": capital, "runs": out}, indent=1))
    return out


def write_home(home: Path, repo_bot: Path, session: dict[str, Any]) -> Path:
    """An isolated bot home: the repository's app, venue and calendar config (symlinked), its own sessions/, state/
    and logs/. The session is saved as config/sessions/xcheck.yaml."""
    (home / "config" / "sessions").mkdir(parents=True, exist_ok=True)
    for item in ("app.yaml", "venues", "calendars"):
        link = home / "config" / item
        if not link.exists():
            os.symlink((repo_bot / "config" / item).resolve(), link)
    session = {**session, "session_id": "xcheck"}
    path = home / "config" / "sessions" / "xcheck.yaml"
    path.write_text(yaml.safe_dump(session, sort_keys=False))
    return path


def prepare_scout(root: Path, repo_bot: Path, market: str, meta: dict[str, Any], *, setting: str = "touch 0bp",
                  leverage: float | str = "max", capital: float = 100.0, pct: Pct | None = None,
                  order_max: float | None = None) -> dict[str, Any]:
    """The session `/run MARKET SETTING LEVERAGE` would deploy, for a scout menu setting, in its own home."""
    cfg: Config = scan.BY_NAME[setting]
    ladder = scan.leverages(meta)                         # the maximum first (after the owner's caps), then 20x ...
    lev, off = ladder[0] if leverage == "max" else next(
        ((x, o) for x, o in ladder if abs(x - float(leverage)) < 0.01), (float(leverage), min(float(leverage),
                                                                                              ladder[0][1])))
    mi = market_info(meta)
    px = float(meta.get("markPrice") or meta.get("oraclePrice") or 0)
    vmin = venue_min_usd(mi.min_notional, mi.min_size, px)
    risk = Risk.for_capital(capital, lev, off, pct=pct or Pct(), order_max=order_max,
                            min_capital=round(min_capital(vmin, off), 2))
    home = root / f"{market}__{setting.replace(' ', '_').replace(',', '')}__{lev:g}x"
    write_home(home, repo_bot, session_for(market, cfg, risk, live=False))
    return {"market": market, "setting": setting, "leverage": lev, "leverage_off": off, "capital": capital,
            "order_usd": round(risk.order_usd, 2), "cap_usd": round(risk.cap_usd, 2),
            "stops_usd": [risk.pos_stop_usd, risk.daily_stop_usd, risk.kill_usd], "home": str(home),
            "min_capital_usd": risk.min_capital_usd}
