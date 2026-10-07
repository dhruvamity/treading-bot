"""Settings the owner changes from Telegram (/settings, /set) instead of editing files.

They are stored in state/settings.json and layered over config/app.yaml (`sizing`) and the scout's command-line
options. The scout reads them before every scan; the live bot applies the sizing ones at its next re-size (start or
00:00 UTC). Only names listed in SETTINGS can be set, each checked against its range.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from arcus.common.config import SizingDefaults

FILE = "settings.json"
DEFAULT_VOLUME_COST = 0.20   # dollars per $1,000 of volume the lists may cost in the backtest (2 bp): the backtest
                             # costs ~1.25x what live BTC runs cost (3 runs, 2026-09-26), so about 1.6 bp live
DEFAULT_SCAN_BUDGET_MIN = 30   # minutes of full-day backtests per scan; the rest continues in the next one
CRYPTO = ("BTC-USD", "ETH-USD")   # the markets /set crypto_lev caps


@dataclass(frozen=True)
class Setting:
    name: str
    kind: str          # money_auto | money_none | pct | pct_auto | minutes | workers | per1k | lev
    lo: float
    hi: float
    help: str
    applies: str       # when a change takes effect
    field: str = ""    # SizingDefaults field it overrides ("" = a scout option)


SETTINGS: dict[str, Setting] = {s.name: s for s in (
    Setting("capital", "money_auto", 1, 10_000_000,
            "Money the bot sizes for: auto = the Arcus account's balance, or a fixed dollar amount (never more than "
            "the balance)", "next scan; the running bot at its next re-size", "capital_usd"),
    Setting("trade_share", "pct", 1, 100, "Share of the balance to trade, in %; the rest is left untouched",
            "next scan; the running bot at its next re-size", "capital_frac"),
    Setting("max_capital", "money_none", 1, 10_000_000, "Never size for more than this many dollars (none = no cap)",
            "next scan; the running bot at its next re-size", "max_capital_usd"),
    Setting("position_stop", "pct_auto", 0.1, 10,
            "Close an open position once it is down this much: auto = it follows the market (2 hourly moves on a full "
            "position, between 1% and 5% of the capital), or a fixed % of the capital",
            "next scan (a full re-backtest); the running bot at its next re-size", "position_stop_pct"),
    Setting("daily_stop", "pct", 0.2, 20, "Stop for the UTC day once the day is down this % of the capital",
            "next scan (a full re-backtest); the running bot at its next re-size", "daily_stop_pct"),
    Setting("kill", "pct", 1, 50, "Flatten and stop for good once equity is this % below its peak",
            "next scan (a full re-backtest); the running bot at its next re-size", "kill_pct"),
    Setting("scan_every", "minutes", 10, 240, "Minutes between scout scans", "after the current wait"),
    Setting("scan_budget", "minutes", 5, 240,
            "Most minutes a scan spends backtesting full days (busiest markets first); the rest continues in the next "
            "scan", "next scan"),
    Setting("scan_workers", "workers", 1, 32,
            "CPU cores a scan may use: auto = all but one; all but two while a bot runs on this machine", "next scan"),
    Setting("volume_cost", "per1k", 0.01, 5,
            "The most the Most Volume and Cheapest lists may cost: dollars lost per $1,000 traded (0.20 = 2 bp)",
            "those lists at once; the next scan also re-checks the last 24 h of the settings it lets in"),
    Setting("crypto_lev", "lev", 1, 50,
            "Highest leverage the scan tests on BTC and ETH: max = what Arcus allows (BTC 40x, ETH 25x), or a number "
            "such as 20", "next scan (it backtests the new leverage on every recorded day)"),
)}


def path(state_dir: Path | str) -> Path:
    return Path(state_dir) / FILE


def load(state_dir: Path | str) -> dict[str, Any]:
    try:
        d = json.loads(path(state_dir).read_text())
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in d.items() if k in SETTINGS} if isinstance(d, dict) else {}


def parse(name: str, text: str) -> Any:
    """The stored value for `/set name text`, or ValueError with a message a person can act on."""
    s = SETTINGS.get(name)
    if s is None:
        raise ValueError(f"unknown setting {name!r}; one of: {', '.join(SETTINGS)}")
    t = text.strip().lower().removeprefix("$").removesuffix("%").replace(",", "")
    if s.kind in ("money_auto", "pct_auto") and t in ("auto", "dynamic"):
        return "auto"
    if s.kind == "money_none" and t in ("none", "off", "no"):
        return None
    if s.kind == "workers" and t == "auto":
        return "auto"
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
    return int(v) if s.kind in ("minutes", "workers") else v


def effective_sizing(base: SizingDefaults, overrides: dict[str, Any]) -> SizingDefaults:
    """config/app.yaml's sizing with the owner's overrides applied (validated together: position <= daily <= kill)."""
    upd: dict[str, Any] = {}
    for name, v in overrides.items():
        s = SETTINGS.get(name)
        if s is None or not s.field:
            continue
        if name == "position_stop":
            # a number fixes the stop; "auto" is the app's own (the market-following stop unless app.yaml fixed it)
            if v != "auto":
                upd.update(position_stop_pct=v, position_stop_k=0.0)
            continue
        upd[s.field] = v / 100 if name == "trade_share" else v
    return SizingDefaults.model_validate({**base.model_dump(), **upd})


def save(state_dir: Path | str, name: str, value: Any, base: SizingDefaults) -> dict[str, Any]:
    """Store one setting after checking the result is a valid whole (raises ValueError otherwise)."""
    cur = load(state_dir)
    new = {**cur, name: value}
    try:
        z = effective_sizing(base, {k: v for k, v in new.items() if k not in ("position_stop", "daily_stop", "kill")})
        ps = new.get("position_stop")
        pos = z.position_stop_pct if ps in (None, "auto") else float(ps)
        day, kill = float(new.get("daily_stop", z.daily_stop_pct)), float(new.get("kill", z.kill_pct))
        if not pos <= day <= kill:      # said in the owner's words, with the way out
            floor = " (the dynamic stop's floor)" if ps in (None, "auto") else ""
            fix = f"/set daily_stop {pos:g}" if pos > day else f"/set kill {day:g}"
            raise ValueError(f"the stops must stay position ≤ daily ≤ kill, and this would make them {pos:g}%{floor} "
                             f"/ {day:g}% / {kill:g}%. Change the other one first, for example {fix}")
        effective_sizing(base, new)
    except ValueError as e:
        errs = getattr(e, "errors", None)       # pydantic: its own text ends with a link, not with the reason
        msg = str(errs()[0].get("msg", "")).removeprefix("Value error, ") if callable(errs) and errs() else str(e)
        raise ValueError(f"not saved: {msg or 'invalid'}") from None
    p = path(state_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(new, indent=1))
    tmp.replace(p)
    return new


def reset(state_dir: Path | str, name: str) -> dict[str, Any]:
    new = {k: v for k, v in load(state_dir).items() if k != name}
    p = path(state_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(new, indent=1))
    return new


def scout_options(overrides: dict[str, Any]) -> tuple[float | None, int | str | None]:
    """(minutes between scans, workers) when the owner set them, else (None, None)."""
    return overrides.get("scan_every"), overrides.get("scan_workers")


def scan_budget_s(overrides: dict[str, Any]) -> float:
    """The owner's time budget for one scan's full-day backtests, in seconds (default 30 minutes)."""
    return 60.0 * float(overrides.get("scan_budget") or DEFAULT_SCAN_BUDGET_MIN)


def volume_cost(overrides: dict[str, Any]) -> float:
    """The owner's budget for the volume lists, in dollars per $1,000 of volume."""
    return float(overrides.get("volume_cost") or DEFAULT_VOLUME_COST)


def lev_caps(overrides: dict[str, Any]) -> dict[str, float]:
    """The owner's leverage cap per market (/set crypto_lev): BTC and ETH at that number; none at "max" (Arcus's)."""
    v = overrides.get("crypto_lev")
    return {} if v in (None, "max") else {m: float(v) for m in CRYPTO}


def show(name: str, value: Any) -> str:
    s = SETTINGS[name]
    if value is None:
        return "none"
    if value == "auto":
        return "auto"
    if s.kind == "lev":
        return "Arcus max" if value == "max" else f"{float(value):g}x"
    if s.kind in ("money_auto", "money_none"):
        return f"${float(value):,.2f}"
    if s.kind in ("pct", "pct_auto"):
        return f"{float(value):g}%"
    if s.kind == "minutes":
        return f"{int(value)} min"
    if s.kind == "per1k":
        return f"${float(value):.2f} per $1,000"
    return str(value)


def defaults(base: SizingDefaults, every_min: float, workers: int | str | None) -> dict[str, Any]:
    """What each setting is when the owner has not changed it."""
    return {"capital": base.capital_usd, "trade_share": base.capital_frac * 100, "max_capital": base.max_capital_usd,
            "position_stop": "auto" if base.position_stop_k > 0 else base.position_stop_pct,
            "daily_stop": base.daily_stop_pct, "kill": base.kill_pct,
            "scan_every": every_min, "scan_workers": workers or "auto", "scan_budget": DEFAULT_SCAN_BUDGET_MIN,
            "volume_cost": DEFAULT_VOLUME_COST,
            "crypto_lev": "max"}
