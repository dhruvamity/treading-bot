"""Settings the owner changes from Telegram (/settings, /set) or `lbot set`, stored in state/settings.json and layered
over config/app.yaml. The scout reads them before each scan; a running bot at its next re-size (start, 00:00 UTC)."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lbot.config import Config

DEFAULT_VOLUME_COST = 0.10     # $ lost per $1,000 traded the Most Volume and Cheapest lists may cost (1 bp)


@dataclass(frozen=True)
class Setting:
    name: str
    kind: str       # money_auto | money_none | pct | minutes | per1k | lev
    lo: float
    hi: float
    help: str


SETTINGS: dict[str, Setting] = {s.name: s for s in (
    Setting("capital", "money_auto", 1, 10_000_000, "Money the bot sizes for: auto = the Lighter account's balance, "
            "or a fixed dollar amount (never more than the balance)"),
    Setting("trade_share", "pct", 1, 100, "Share of the balance to trade, in %"),
    Setting("max_capital", "money_none", 1, 10_000_000, "Never size for more than this (none = no cap)"),
    Setting("position_stop", "pct", 0.1, 20, "Close an open position once it is down this % of the capital"),
    Setting("daily_stop", "pct", 0.2, 50, "Stop for the UTC day once the day is down this % of the capital"),
    Setting("kill", "pct", 1, 80, "Flatten and stop once equity is this % below its peak"),
    Setting("volume_cost", "per1k", 0.0, 5, "The most the Most Volume and Cheapest lists may cost: $ lost per $1,000 "
            "traded in the backtest (0.10 = 1 bp)"),
    Setting("scan_every", "minutes", 10, 240, "Minutes between scans"),
    Setting("max_lev", "lev", 1, 50, "Highest leverage the scan and /run use on any market: max = Lighter's (50x on "
            "BTC, ETH, SPY, QQQ), or a number such as 20"),
)}


def _path(cfg: Config) -> Path:
    return cfg.state_dir / "settings.json"


def load(cfg: Config) -> dict[str, Any]:
    try:
        d = json.loads(_path(cfg).read_text())
        return {k: v for k, v in d.items() if k in SETTINGS} if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def parse(name: str, text: str) -> Any:
    s = SETTINGS.get(name)
    if s is None:
        raise ValueError(f"unknown setting {name!r}; one of: {', '.join(SETTINGS)}")
    t = text.strip().lower().removeprefix("$").removesuffix("%").replace(",", "")
    if s.kind == "money_auto" and t == "auto":
        return "auto"
    if s.kind == "money_none" and t in ("none", "off", "no"):
        return None
    if s.kind == "lev":
        if t == "max":
            return "max"
        t = t.removesuffix("x")
    try:
        v = float(t)
    except ValueError:
        raise ValueError(f"{name}: {text!r} is not a number") from None
    if not math.isfinite(v) or not s.lo <= v <= s.hi:
        raise ValueError(f"{name} must be between {s.lo:g} and {s.hi:g}")
    return int(v) if s.kind == "minutes" else v


def effective(cfg: Config) -> dict[str, Any]:
    """Every setting's value now: the owner's, else the config's default."""
    o = load(cfg)
    z = cfg.sizing
    out = {"capital": z.capital_usd, "trade_share": z.capital_frac * 100, "max_capital": z.max_capital_usd,
           "position_stop": z.position_stop_pct, "daily_stop": z.daily_stop_pct, "kill": z.kill_pct,
           "volume_cost": DEFAULT_VOLUME_COST, "scan_every": cfg.scout.every_min, "max_lev": "max"}
    out.update(o)
    return out


def save(cfg: Config, name: str, value: Any) -> dict[str, Any]:
    cur = load(cfg)
    new = {**cur, name: value}
    e = {**effective(cfg), **new}
    if not float(e["position_stop"]) <= float(e["daily_stop"]) <= float(e["kill"]):
        raise ValueError("not saved: the stops must be position <= daily <= kill")
    p = _path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(new, indent=1))
    tmp.replace(p)
    return new


def reset(cfg: Config, name: str) -> dict[str, Any]:
    new = {k: v for k, v in load(cfg).items() if k != name}
    p = _path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(new, indent=1))
    return new


def show(name: str, value: Any) -> str:
    s = SETTINGS[name]
    if value is None:
        return "none"
    if value in ("auto", "max"):
        return "Lighter max" if value == "max" else "auto"
    if s.kind in ("money_auto", "money_none"):
        return f"${float(value):,.2f}"
    if s.kind == "pct":
        return f"{float(value):g}%"
    if s.kind == "minutes":
        return f"{int(value)} min"
    if s.kind == "per1k":
        return f"${float(value):.2f} per $1,000"
    return f"{float(value):g}x"


def stops(cfg: Config) -> tuple[float, float, float]:
    e = effective(cfg)
    return float(e["position_stop"]), float(e["daily_stop"]), float(e["kill"])


def lev_cap(cfg: Config) -> float | None:
    v = effective(cfg).get("max_lev")
    return None if v in (None, "max") else float(v)
