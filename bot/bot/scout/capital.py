"""The capital the scout backtests at, and so the capital the pilot's sizes are written for.

config/app.yaml `sizing.capital_usd`:
- auto (the default): the Arcus subaccount's equity x capital_frac, read before every scan (public read; needs only
  ARCUS_ADDRESS in .env). With no address, or an account that was never funded, the scout uses paper_capital_usd;
- a number: that amount (the Docker scout on a machine without keys, or to size for a deposit not made yet).
Either way it is capped at max_capital_usd and rounded down to the sizing series (bot/common/sizing.py), which is
exactly what the live engine does with the account's equity.

Every new capital means backtesting the whole week again, so settle() only moves the scan's capital when it matters:
a fixed amount, or a change of kind (paper -> a funded account), applies at once; the account's equity moving applies
only once it is 25% or more away from the capital in use, and at most once per UTC day (the live bot re-sizes daily
too), unless money came in or went out (a deposit or withdrawal) or the equity is half or twice the capital in use:
then at once. The capital in use is kept in state/scout_capital.json.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from bot.common.config import SizingDefaults
from bot.common.logging import Log
from bot.common.secrets import SecretStore
from bot.common.sizing import bucket
from bot.core.creds import arcus_address
from bot.venues.arcus.rest import ArcusRest

log = Log("scout.capital")


BAND = 1.25        # the scan's capital follows the equity only past this ratio (either way)
STATE = "scout_capital.json"


async def account_snapshot(rest_url: str, account_index: int = 0, secrets: SecretStore | None = None
                           ) -> dict[str, float] | None:
    """{equity, free, net_deposits} of the subaccount (a public read by address), or None: no ARCUS_ADDRESS in .env,
    never funded, or unreachable."""
    s = secrets or SecretStore()
    try:
        address = arcus_address(s)
    except Exception:
        return None
    rest = ArcusRest(rest_url)
    try:
        a = await rest.account(address, account_index)
        return {"equity": float(a.get("equity") or 0), "free": float(a.get("freeCollateral") or 0),
                "net_deposits": float(a.get("netDeposits") or 0)}
    except Exception as e:  # "no activity yet" (unfunded), network: fall back to the paper capital
        log.info("scout_capital_no_equity", reason=str(e)[:200])
        return None
    finally:
        await rest.close()


async def account_equity(rest_url: str, account_index: int = 0, secrets: SecretStore | None = None) -> float | None:
    """The subaccount's equity, or None (no address in .env, never funded, or unreachable)."""
    snap = await account_snapshot(rest_url, account_index, secrets)
    return snap["equity"] if snap and snap["equity"] > 0 else None


def kind_of(source: str) -> str:
    return "fixed" if source.startswith("fixed") else "account" if source.startswith("account") else "paper"


def settle(state_dir: Path | str, capital: float, source: str, *, today: str, band: float = BAND,
           net_deposits: float | None = None) -> tuple[float, str, bool]:
    """(capital to scan at, its source, whether it changed) given this scan's candidate (see the module docstring).
    net_deposits: the account's deposits less withdrawals now; a change since the capital was set moves it today."""
    p = Path(state_dir) / STATE
    try:
        prev: dict[str, Any] | None = json.loads(p.read_text())
    except (OSError, ValueError):
        prev = None
    if prev and prev.get("kind") == kind_of(source) and prev.get("usd"):
        held = float(prev["usd"])
        near = held / band <= capital <= held * band
        was = prev.get("net_deposits")
        moved = net_deposits is not None and was is not None and abs(net_deposits - float(was)) >= 1
        far = not held / 2 <= capital <= held * 2
        if kind_of(source) != "fixed" and (near or (prev.get("day") == today and not moved and not far)):
            if net_deposits is not None and was is None:   # an older file: remember the deposits from now on
                p.write_text(json.dumps({**prev, "net_deposits": net_deposits}))
            return held, str(prev.get("source") or source), False
        if kind_of(source) == "fixed" and abs(capital - held) < 1e-9:
            return held, source, False
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"usd": capital, "source": source, "kind": kind_of(source), "day": today,
                             "ts": time.time(), "net_deposits": net_deposits}))
    return capital, source, True


def forget(state_dir: Path | str) -> None:
    """Drop the held capital so the next scan settles afresh (after the owner changes a sizing setting)."""
    (Path(state_dir) / STATE).unlink(missing_ok=True)


def choose(spec: str | float | None, equity: float | None, z: SizingDefaults) -> tuple[float, str]:
    """(capital to scan at, where it came from). spec: "auto", a number, or None for app.yaml's sizing.capital_usd."""
    spec = z.capital_usd if spec in (None, "") else spec
    if str(spec).lower() != "auto":
        raw, src, frac = float(spec), "fixed", 1.0
    elif equity:
        raw, src, frac = equity, "account equity", z.capital_frac
    else:
        raw, src, frac = z.paper_capital_usd, "paper capital: no funded account", 1.0
    c = raw * frac
    if z.max_capital_usd:
        c = min(c, z.max_capital_usd)
    note = f"{src} ${raw:,.2f}" + (f" x {frac:g}" if frac != 1 else "") + \
        (f", capped at ${z.max_capital_usd:,.0f}" if z.max_capital_usd and raw * frac > z.max_capital_usd else "")
    return bucket(c), note
