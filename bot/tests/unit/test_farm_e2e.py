"""The farm's live path end to end, offline: a local stand-in for Arcus (REST market list and a WebSocket that
streams best bid/offer and trade frames in Arcus's format), the real recorder, the analysis and the git commit."""

from __future__ import annotations

import asyncio
import json
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest
from aiohttp import WSMsgType, web

from bot.farm import service

FIXTURE = Path(__file__).parents[1] / "fixtures" / "live" / "arcus_markets.json"
MARKETS = ("BTC-USD", "SPY-USD")


def fake_arcus() -> web.Application:
    ms = [m for m in json.loads(FIXTURE.read_text())["markets"] if m["marketDisplayName"] in MARKETS]
    for m in ms:
        m["status"] = "ONLINE"
    price = {m["marketDisplayName"]: float(m["markPrice"]) for m in ms}

    async def markets(_: web.Request) -> web.Response:
        return web.json_response({"markets": ms})

    async def ws(req: web.Request) -> web.WebSocketResponse:
        sock = web.WebSocketResponse()
        await sock.prepare(req)
        subs: set[tuple[str, str]] = set()

        async def stream() -> None:
            k = 0
            while not sock.closed:
                await asyncio.sleep(0.05)
                k += 1
                now = int(time.time() * 1e6)
                for ch, mid in list(subs):
                    p = price[mid] * (1 + 1e-4 * ((k % 20) - 10) / 10)
                    tick = 0.1 if mid == "BTC-USD" else 0.01
                    bid, ask = round(p / tick) * tick, round(p / tick) * tick + tick
                    if ch == "bbo":
                        c: Any = {"bestBid": {"price": f"{bid:.2f}", "size": "1"},
                                  "bestAsk": {"price": f"{ask:.2f}", "size": "1"}, "timestamp": now}
                    else:
                        c = [{"marketDisplayName": mid, "side": "BUY" if k % 2 else "SELL", "price": f"{ask + tick:.2f}",
                              "size": "0.5", "tradeId": str(k * 10 + len(mid)), "timestamp": now,
                              "sequenceNumber": k}]
                    await sock.send_json({"type": "channel_data", "channel": ch, "id": mid, "contents": c})

        task = asyncio.create_task(stream())
        await sock.send_json({"type": "connected", "connection_id": "1"})
        async for msg in sock:
            if msg.type != WSMsgType.TEXT:
                continue
            m = json.loads(msg.data)
            if m.get("type") == "subscribe" and m.get("channel") in ("bbo", "trades"):
                subs.add((m["channel"], m["id"]))
                await sock.send_json({"type": "subscribed", "channel": m["channel"], "id": m["id"], "contents": {}})
        task.cancel()
        return sock

    app = web.Application()
    app.router.add_get("/v1/markets", markets)
    app.router.add_get("/v1/ws", ws)
    return app


def test_farm_records_a_fake_arcus_and_analyses_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    for cmd in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", "-C", str(repo), *cmd], check=True)
    run_dir = repo / "research" / "runs" / "e2e"
    monkeypatch.setattr(service, "WARMUP_S", 0)

    async def main() -> None:
        runner = web.AppRunner(fake_arcus())
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = runner.addresses[0][1]
        farm = service.Farm(run_dir, hours=6 / 3600, every_min=3 / 60, capital=100, workers=1, max_markets=5,
                            rest_url=f"http://127.0.0.1:{port}", ws_url=f"ws://127.0.0.1:{port}/v1/ws", git=True,
                            push=False)
        await farm.run()
        await runner.cleanup()

    asyncio.run(main())
    tape = run_dir / "scout" / "tape"
    assert {p.name for p in tape.iterdir()} == set(MARKETS)
    assert list(tape.rglob("bbo-*.npz")) and list(tape.rglob("trades-*.npz"))
    rows = json.loads((run_dir / "results" / "latest.json").read_text())
    assert {r["market"] for r in rows} == set(MARKETS)
    assert (run_dir / "LEADERBOARD.md").read_text().startswith("# Paper farm leaderboard")
    assert (run_dir / "candles" / "SPY-USD.csv.gz").exists() and (run_dir / "paper" / "SPY-USD.npz").exists()
    log = subprocess.run(["git", "-C", str(repo), "log", "--oneline"], capture_output=True, text=True).stdout
    assert "Paper farm e2e: final" in log
