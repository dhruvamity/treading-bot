"""Outside views of the same funding rates, as a cross-check on the venues' own numbers. Never the source of a
trade: both aggregators refresh every few minutes, and the venues publish their rates themselves.

- ProFunding (profunding.pro): GET /opportunities with the X-API-Key header. Read-only use: the free key allows 100
  requests a day, so the answer is kept on disk and asked for again at most every CACHE_S. Its other endpoints place
  orders with exchange keys stored on its servers; this bot never sends it a key.
- arb.sh: GET /api/fresh says when its data last changed; its ranked list (/funding?view=list, a React server
  payload) shows the 50 widest spreads across some 60 venues. Undocumented, so only the pair links are read from it.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

import aiohttp

PROFUNDING = "https://profunding.pro/api/api"
ARBSH = "https://arb.sh"
CACHE_S = 900
NAMES = {"arcus": "arcus", "lighterrh": "lighter"}      # ProFunding's exchange names -> ours
UA = {"User-Agent": "Mozilla/5.0"}


def profunding_pairs(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """{symbol: {"long": venue, "short": venue, "net_apr": %, "breakeven_days": d}} for Arcus <-> Lighter RH rows."""
    out = {}
    for o in rows:
        lo, sh = str(o.get("long_exchange", "")).lower(), str(o.get("short_exchange", "")).lower()
        if {lo, sh} != set(NAMES):
            continue
        sym = str(o.get("symbol", "")).split("/")[0]
        out[sym] = {"long": NAMES[lo], "short": NAMES[sh], "net_apr": float(o.get("net_apr") or 0),
                    "breakeven_days": o.get("breakeven_days"), "at": o.get("timestamp")}
    return out


async def profunding(api_key: str, cache: Path, *, max_age_s: float = CACHE_S) -> dict[str, dict[str, Any]]:
    """ProFunding's Arcus <-> Lighter RH opportunities, from the cache when it is fresh enough. {} without a key."""
    try:
        saved = json.loads(cache.read_text())
        if time.time() - saved["ts"] < max_age_s:
            return dict(saved["pairs"])
    except (OSError, ValueError, KeyError):
        pass
    if not api_key:
        return {}
    session = aiohttp.ClientSession(headers={**UA, "X-API-Key": api_key}, timeout=aiohttp.ClientTimeout(total=60))
    async with session as s, s.get(f"{PROFUNDING}/opportunities") as r:
        if r.status != 200:
            raise RuntimeError(f"ProFunding: HTTP {r.status} {(await r.text())[:200]}")
        pairs = profunding_pairs(await r.json(content_type=None))
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"ts": time.time(), "pairs": pairs}))
    return pairs


def arbsh_pairs(payload: str) -> list[tuple[str, str, str]]:
    """(asset, venue a, venue b) for every pair link in arb.sh's list payload."""
    found = re.findall(r"/arbitrage/[a-z_]+/([a-z0-9_]+)/([a-z0-9_]+)-vs-([a-z0-9_]+)", payload)
    return sorted(set(found))


async def arbsh(top_only: bool = True) -> dict[str, Any]:
    """{"updated": ms, "pairs": [...]}: arb.sh's last refresh and the Arcus / Lighter RH pairs in its top list."""
    async with aiohttp.ClientSession(headers=UA, timeout=aiohttp.ClientTimeout(total=40)) as s:
        async with s.get(f"{ARBSH}/api/fresh") as r:
            updated = int((await r.json(content_type=None)).get("updated") or 0) if r.status == 200 else 0
        async with s.get(f"{ARBSH}/funding", params={"view": "list", "_rsc": "1"}, headers={"RSC": "1"}) as r:
            text = await r.text() if r.status == 200 else ""
    ours = {"arcus", "lighter_rh"}
    pairs = [p for p in arbsh_pairs(text) if (ours & {p[1], p[2]}) and (not top_only or {p[1], p[2]} == ours)]
    return {"updated": updated, "listed": len(arbsh_pairs(text)), "pairs": pairs}
