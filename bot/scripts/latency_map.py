#!/usr/bin/env python3
"""Latency map: where is each perp venue hosted, and how far is this machine from it? Read-only, no keys, stdlib only.

  python3 latency_map.py                 from ~8 cloud regions worldwide, through Globalping's free probes
  python3 latency_map.py --local         from the machine it runs on (run it on the Mac, then on a Tokyo server)
  python3 latency_map.py --venues arcus,hyperliquid --from "Tokyo,Osaka,Seoul"

Every venue here sits behind a CDN, so ping and the TCP/TLS handshake only reach the nearest CDN edge (1-10 ms from
anywhere). What is timed instead is one uncached request: sent -> first byte back, which crosses edge -> origin and
back. The region with the lowest figure is the region the origin is in. Answers the CDN served from its cache are
dropped. Globalping allows 250 probe-tests an hour without an account; one run of the defaults uses about 80.
"""

from __future__ import annotations

import argparse
import http.client
import json
import statistics
import time
import urllib.request

# Lighter caches its public reads for a few seconds, so --local uses nextNonce, which is never cached. Lighter's CDN
# answers nextNonce with 403 to Globalping's probes (measured 2026-10-05), so the worldwide run keeps orderBooks and
# drops the cached answers: repeat it, or raise --limit, when a city is missing.
NONCE = "/api/v1/nextNonce?account_index=1&api_key_index=0"
VENUES: dict[str, tuple[str, ...]] = {  # name: (host, path of a light public GET that the origin answers; a 404/405 from the origin counts too)
    "arcus": ("api.arcus.xyz", "/v1/time"),
    "lighter-rh": ("api.rh.lighter.xyz", "/api/v1/orderBooks", NONCE),
    "lighter": ("mainnet.zklighter.elliot.ai", "/api/v1/orderBooks", NONCE),
    "hyperliquid": ("api.hyperliquid.xyz", "/info"),
    "txflow": ("api.txflow.com", "/info"),
    "aster": ("fapi.asterdex.com", "/fapi/v1/time"),
    "edgex": ("pro.edgex.exchange", "/api/v1/public/meta/getServerTime"),
    "paradex": ("api.prod.paradex.trade", "/v1/system/time"),
    "extended": ("api.starknet.extended.exchange", "/api/v1/info/markets/BTC-USD/stats"),
    "orderly": ("api.orderly.org", "/v1/public/system_info"),
}
REGIONS = ("aws-ap-northeast-1,aws-ap-northeast-2,aws-ap-east-1,aws-ap-southeast-1,aws-ap-south-1,"
           "aws-eu-central-1,aws-eu-west-2,aws-us-east-1")  # Tokyo, Seoul, Hong Kong, Singapore, Mumbai, Frankfurt, London, Virginia
GLOBALPING = "https://api.globalping.io/v1/measurements"


def cached(headers: dict[str, str]) -> bool:
    h = {k.lower(): v for k, v in headers.items()}
    return (h.get("x-cache") or h.get("cf-cache-status") or "").lower().startswith("hit")


def local(host: str, path: str, n: int) -> str:
    """Requests on one kept-alive connection; the first (it carries the handshake) is not counted."""
    conn = http.client.HTTPSConnection(host, timeout=15)
    ms = []
    for i in range(n + 1):
        t0 = time.perf_counter()
        conn.request("GET", path, headers={"User-Agent": "curl/8.7.1"})
        r = conn.getresponse()
        dt = (time.perf_counter() - t0) * 1000
        r.read()
        if r.status == 403:
            return "refused: HTTP 403 for this IP"
        if i and not cached(dict(r.getheaders())):
            ms.append(dt)
    conn.close()
    return f"min {min(ms):.0f} ms, median {statistics.median(ms):.0f} ms ({len(ms)} requests)" if ms else "every answer was cached"


def worldwide(host: str, path: str, locations: list[str], limit: int) -> str:
    body = {"type": "http", "target": host, "locations": [{"magic": loc, "limit": limit} for loc in locations],
            "measurementOptions": {"protocol": "HTTPS", "request": {"path": path, "method": "GET"}}}
    req = urllib.request.Request(GLOBALPING, json.dumps(body).encode(),
                                 {"content-type": "application/json", "user-agent": "latency-map"})
    mid = json.load(urllib.request.urlopen(req, timeout=30))["id"]
    while True:
        time.sleep(1)
        d = json.load(urllib.request.urlopen(f"{GLOBALPING}/{mid}", timeout=30))
        if d["status"] != "in-progress":
            break
    best: dict[str, float] = {}
    for r in d["results"]:
        res, city = r["result"], r["probe"]["city"]
        fb = (res.get("timings") or {}).get("firstByte")
        if fb is None or cached(res.get("headers") or {}) or res.get("statusCode") == 403:  # 403: the CDN refused the probe
            continue
        best[city] = min(fb, best.get(city, fb))
    return " · ".join(f"{c} {v:.0f}" for c, v in sorted(best.items(), key=lambda kv: kv[1])) or "no uncached answer"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--local", action="store_true", help="measure from this machine instead of Globalping's probes")
    ap.add_argument("--venues", default=",".join(VENUES), help="comma-separated, from: " + ", ".join(VENUES))
    ap.add_argument("--from", dest="regions", default=REGIONS, help="Globalping locations: cities, countries, or cloud regions")
    ap.add_argument("--limit", type=int, default=1, help="probes per location (the lowest is kept)")
    ap.add_argument("-n", type=int, default=10, help="--local: requests per venue")
    a = ap.parse_args()
    print("request -> first byte, ms" + ("" if a.local else " (lowest first)"))
    for name in a.venues.split(","):
        host, path, *local_path = VENUES[name]
        try:
            line = local(host, local_path[0] if local_path else path, a.n) if a.local else worldwide(host, path, a.regions.split(","), a.limit)
        except OSError as e:
            line = f"failed: {e}"
        print(f"{name:12} {line}")


if __name__ == "__main__":
    main()
