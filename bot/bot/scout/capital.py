"""The capital the scout backtests at, and so the capital the pilot's sizes are written for.

config/app.yaml `sizing.capital_usd`:
- auto (the default): the Arcus subaccount's equity x capital_frac, read before every scan (public read; needs only
  ARCUS_ADDRESS in .env). With no address, or an account that was never funded, the scout uses paper_capital_usd;
- a number: that amount (the Docker scout on a machine without keys, or to size for a deposit not made yet).
Either way it is capped at max_capital_usd and rounded down to the sizing series (bot/common/sizing.py), which is
exactly what the live engine does with the account's equity.
"""

from __future__ import annotations

from bot.common.config import SizingDefaults
from bot.common.logging import Log
from bot.common.secrets import SecretStore
from bot.common.sizing import bucket
from bot.core.creds import arcus_address
from bot.venues.arcus.rest import ArcusRest

log = Log("scout.capital")


async def account_equity(rest_url: str, account_index: int = 0, secrets: SecretStore | None = None) -> float | None:
    """The subaccount's equity, or None (no address in .env, never funded, or unreachable)."""
    s = secrets or SecretStore()
    try:
        address = arcus_address(s)
    except Exception:
        return None
    rest = ArcusRest(rest_url)
    try:
        eq = float((await rest.account(address, account_index)).get("equity") or 0)
        return eq if eq > 0 else None
    except Exception as e:  # "no activity yet" (unfunded), network: fall back to the paper capital
        log.info("scout_capital_no_equity", reason=str(e)[:200])
        return None
    finally:
        await rest.close()


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
