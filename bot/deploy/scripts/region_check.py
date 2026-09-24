#!/usr/bin/env python3
"""Region check (P0 task 10, [HUMAN GATE] for choosing the VPS region). Read-only.

Prints: Arcus /v1/compliance for this IP (country + perps/spot restriction), Lighter RH read access (REST + WS),
and whether the reported country is on Lighter RH's restricted list. Run it FROM THE SERVER before deploying.
"""

from __future__ import annotations

import asyncio
import json

import aiohttp

LIGHTER_RESTRICTED = {"BY": "Belarus", "CA": "Canada", "CN": "China", "CU": "Cuba", "IR": "Iran", "MM": "Myanmar",
                      "KP": "North Korea", "RU": "Russia", "SG": "Singapore", "SS": "South Sudan", "SD": "Sudan",
                      "CH": "Switzerland", "SY": "Syria", "UA": "Ukraine", "AE": "UAE", "GB": "UK", "US": "US",
                      "VE": "Venezuela"}


async def main() -> None:
    out: dict[str, object] = {}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s:
        async with s.get("https://api.arcus.xyz/v1/compliance") as r:
            comp = await r.json()
        out["arcus_compliance"] = comp
        country = (comp.get("geo") or {}).get("country")
        restr = (comp.get("geo") or {}).get("restrictions") or {}
        out["arcus_perps_allowed"] = not restr.get("perpetuals", True)
        async with s.get("https://api.rh.lighter.xyz/api/v1/orderBooks") as r:
            out["lighter_rest_status"] = r.status
        try:
            async with s.ws_connect("wss://api.rh.lighter.xyz/stream") as ws:
                msg = await ws.receive(timeout=10)
                out["lighter_ws"] = json.loads(msg.data).get("type") if msg.data else str(msg.type)
        except Exception as e:  # restricted regions can still use ?readonly=true
            out["lighter_ws"] = f"error: {type(e).__name__}"
        out["country"] = country
        out["lighter_restricted_country"] = LIGHTER_RESTRICTED.get(country or "")
    ok = out["arcus_perps_allowed"] and not out["lighter_restricted_country"] and out["lighter_rest_status"] == 200
    out["verdict"] = "OK for both venues" if ok else "NOT OK: choose another region"
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    asyncio.run(main())
