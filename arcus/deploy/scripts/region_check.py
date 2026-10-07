#!/usr/bin/env python3
"""Region check: may this server's IP trade Arcus perps? Read-only. Run it FROM THE SERVER before deploying.

Prints Arcus /v1/compliance for this IP: the country it sees and whether perps are restricted there.
"""

from __future__ import annotations

import asyncio
import json

import aiohttp


async def main() -> None:
    out: dict[str, object] = {}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s:
        async with s.get("https://api.arcus.xyz/v1/compliance") as r:
            comp = await r.json()
        out["arcus_compliance"] = comp
        out["country"] = (comp.get("geo") or {}).get("country")
        restr = (comp.get("geo") or {}).get("restrictions") or {}
        out["arcus_perps_allowed"] = not restr.get("perpetuals", True)
    out["verdict"] = "OK for Arcus perps" if out["arcus_perps_allowed"] else "NOT OK: choose another region"
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    asyncio.run(main())
