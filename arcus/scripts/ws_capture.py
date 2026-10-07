"""Capture raw public Arcus WebSocket frames for format discovery and test fixtures.

Read-only: subscribes to public market channels only. No keys, no orders.

    .venv/bin/python scripts/ws_capture.py --seconds 20 --out tests/fixtures/live
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

import aiohttp

ARCUS_WS = "wss://api.arcus.xyz/v1/ws"


async def capture_arcus(seconds: float, out: Path) -> None:
    subs = [
        # Per-market channels: `id` is the market display name (docs: market-data/*).
        {"type": "subscribe", "channel": "l2OrderbookUpdates", "id": "BTC-USD", "nLevels": 5},
        {"type": "subscribe", "channel": "bbo", "id": "BTC-USD"},
        {"type": "subscribe", "channel": "trades", "id": "BTC-USD"},
        {"type": "subscribe", "channel": "oraclePrices"},
        {"type": "subscribe", "channel": "predictedFunding", "id": "BTC-USD"},
        {"type": "subscribe", "channel": "markets"},
        {"type": "subscribe", "channel": "l2OrderbookUpdates", "id": "SPY-USD", "nLevels": 5},
    ]
    frames: list[dict[str, object]] = []
    async with aiohttp.ClientSession() as s, s.ws_connect(ARCUS_WS, heartbeat=20) as ws:
        for m in subs:
            await ws.send_str(json.dumps(m))
        end = time.time() + seconds
        while time.time() < end:
            try:
                msg = await asyncio.wait_for(ws.receive(), timeout=max(0.1, end - time.time()))
            except TimeoutError:
                break
            if msg.type == aiohttp.WSMsgType.TEXT:
                frames.append({"recv_ts_us": time.time_ns() // 1000, "raw": json.loads(msg.data)})
    (out / "arcus_ws_frames.json").write_text(json.dumps(frames, indent=1))
    print(f"arcus: {len(frames)} frames")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=20)
    ap.add_argument("--out", type=Path, default=Path("tests/fixtures/live"))
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    await capture_arcus(a.seconds, a.out)


if __name__ == "__main__":
    asyncio.run(main())
