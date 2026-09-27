"""The account's figures as Arcus keeps them (/account in Telegram, `bot account`): perps volume, fees paid and earned,
the fee tier and what the next one needs, the realized result and the rank.

From Arcus (public reads by address; the referral one is signed when a key is available):
- /v1/account/stats: perps volume and fees paid, all-time and over the fee tier's 30 days, plus 24 h and 7 days;
- /v1/feetiers: the tier table (maker and taker fee by 30-day volume);
- /v1/leaderboard?address=: realized PnL (after fees, before funding) and the rank by volume, all-time;
- /v1/affiliate/info: referral commission earned (what traders who used your referral code paid you).

Fees earned are maker rebates (Arcus pays none below its VIP tier) and referral commission. Spot volume is not in
Arcus's API: stock tokens trade on-chain from the wallet, through Arcus's router, and the bot trades perps only.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import time
from collections.abc import Awaitable, Callable
from typing import Any

from bot.common.logging import Log
from bot.common.secrets import SecretStore
from bot.common.time import KOLKATA
from bot.core.creds import arcus_address, arcus_private_keys
from bot.venues.arcus.rest import ArcusRest
from bot.venues.arcus.signing import ArcusSigner

log = Log("account_stats")
Q = 1e9   # Arcus amounts are in quote quantums: 1e9 = $1


async def read(rest_url: str, secrets: SecretStore | None = None) -> dict[str, Any]:
    """Everything the card shows, as Arcus returns it. Raises when the address is not set or the stats cannot be
    read; the tier table, leaderboard row and referral figures are left out (None) when their read fails."""
    s = secrets or SecretStore()
    address = arcus_address(s)
    signer = None
    with contextlib.suppress(Exception):   # no key: the referral read goes unsigned
        signer = ArcusSigner(next(iter(arcus_private_keys(s).values())))
    rest = ArcusRest(rest_url, signer=signer)
    out: dict[str, Any] = {"read_at": time.time()}
    try:
        out["stats"] = await rest.account_stats(address)
        extra: dict[str, Callable[[], Awaitable[Any]]] = {
            "tiers": rest.feetiers, "board": lambda: rest.leaderboard_row(address),
            "affiliate": lambda: rest.affiliate_info(address)}
        for name, call in extra.items():
            try:
                out[name] = await call()
            except Exception as e:  # the card shows what it has
                log.info("account_stats_part_failed", part=name, reason=type(e).__name__)
                out[name] = None
    finally:
        await rest.close()
    return out


def _usd(x: Any) -> float:
    return float(x or 0) / Q


def _big(x: float) -> str:
    """$494.4k, $1.25M, $1B."""
    if abs(x) >= 1e9:
        return f"${x / 1e9:,.4g}B"
    return f"${x / 1e6:,.2f}M" if abs(x) >= 1e6 else f"${x / 1e3:,.1f}k" if abs(x) >= 1e3 else f"${x:,.2f}"


def _bp(ppm: float) -> str:
    return f"{ppm / 100:g} bp"


def lines(raw: dict[str, Any]) -> tuple[str, list[tuple[str, list[str]]]]:
    """(the heading line, [(section label, lines)]) for the card and the CLI."""
    st = raw.get("stats") or {}
    win = st.get("windowedStats") or {}
    w = {k: (_usd((win.get(k) or {}).get("volume")), _usd((win.get(k) or {}).get("feesPaid"))) for k in ("24h", "7d")}
    vol_all, vol_30 = _usd(st.get("lifetimeVolume")), _usd(st.get("rollingVolume"))
    fee_all, fee_30 = _usd(st.get("lifetimeFeesPaid")), _usd(st.get("rollingFeesPaid"))
    t = dt.datetime.fromtimestamp(float(raw.get("read_at") or time.time()), dt.UTC)
    head = f"Arcus · read {t:%H:%M} UTC · {t.astimezone(KOLKATA):%H:%M} IST"
    out: list[tuple[str, list[str]]] = []
    out.append(("Futures (perps) volume", [
        f"All-time {_big(vol_all)} · 30 days {_big(vol_30)}",
        f"24h {_big(w['24h'][0])} · 7 days {_big(w['7d'][0])}"]))
    per_k = f" ({fee_all / vol_all * 1e4:.2f} bp)" if vol_all > 0 else ""
    out.append(("Fees paid", [
        f"All-time ${fee_all:,.2f}{per_k} · 30 days ${fee_30:,.2f}",
        f"24h ${w['24h'][1]:,.2f} · 7 days ${w['7d'][1]:,.2f}"]))

    tier = st.get("tradingFeeTier") or (raw.get("affiliate") or {}).get("tradingFeeTier")
    tiers = sorted(((raw.get("tiers") or {}).get("tiers") or []), key=lambda x: int(x.get("level", 0)))
    level = int(tier["level"]) if tier else None
    cur = next((x for x in tiers if int(x["level"]) == level), None)
    nxt = next((x for x in tiers if level is not None and int(x["level"]) > level), None)
    rebate = next((x for x in tiers if float(x.get("maker_fee_ppm", 0)) < 0), None)
    earned: list[str] = []
    if tier and float(tier.get("makerFeePpm", 0)) < 0:
        earned.append(f"Maker rebates: your tier pays {_bp(-float(tier['makerFeePpm']))} on maker volume")
    else:
        name = cur["name"] if cur else f"tier {level}" if level is not None else "your tier"
        where = f" (from {rebate['name']}: {_big(_usd(rebate['volume_threshold']))} in 30 days)" if rebate else ""
        earned.append(f"Maker rebates $0.00 · none at {name}{where}")
    aff = raw.get("affiliate")
    if aff:
        earned.append(f"Referral commission ${_usd(aff.get('totalCommission')):,.2f} all-time · "
                      f"${_usd(aff.get('pendingCommission')):,.2f} not claimed yet")
    else:
        earned.append("Referral commission: not readable now")
    out.append(("Fees earned", earned))

    spot = st.get("lifetimeSpotVolume")
    out.append(("Spot volume", [f"All-time {_big(_usd(spot))}"] if spot is not None else [
        "Not in Arcus's API: spot trades settle on-chain from the wallet",
        "The bot trades perps only (its spot volume is $0)"]))

    if tier:
        tl = [f"{cur['name'] if cur else f'Level {level}'} · maker {_bp(float(tier['makerFeePpm']))} · "
              f"taker {_bp(float(tier['takerFeePpm']))}"]
        if nxt:
            need = max(0.0, _usd(nxt["volume_threshold"]) - vol_30)
            tl.append(f"{nxt['name']} at {_big(_usd(nxt['volume_threshold']))} in 30 days: {_big(need)} to go · "
                      f"taker {_bp(float(nxt['taker_fee_ppm']))}")
        out.append(("Fee tier", tl))
    board = raw.get("board")
    if board and int(board.get("pnl", 0)) > -(2 ** 62):   # Arcus writes the int64 minimum when it has no figure
        out.append(("All-time result", [f"Realized PnL {'-' if board['pnl'] < 0 else '+'}${abs(_usd(board['pnl'])):,.2f}"
                                        " (after fees, before funding)",
                                        f"Rank #{int(board['rank']):,} by volume"]))
    return head, out
