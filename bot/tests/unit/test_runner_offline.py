"""BotRunner end-to-end in PAPER mode against an in-process fake venue server (no network).

The fake server answers the public REST reads LiveParams needs (markets, fee tiers) from the captured fixtures and
replays the captured LIVE Arcus WebSocket frames. The runner must build, sync the book into the hub, run its loops,
quote into the paper venue, write a heartbeat and shut down cleanly.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from aiohttp import WSMsgType, web

from bot.common.config import load_app, load_arcus_config, load_session
from bot.common.secrets import SecretStore
from bot.core.calendar import TradingCalendar
from bot.core.livelock import RunMode
from bot.core.runner import BotRunner
from bot.venues.base import Venue
from bot.venues.paper.adapter import PaperVenue

ROOT = Path(__file__).parents[2]
FIX = ROOT / "tests" / "fixtures" / "live"


class FakeVenues:
    def __init__(self) -> None:
        self.app = web.Application()
        self.rest = {
            "/v1/markets": json.loads((FIX / "arcus_markets.json").read_text()),
            "/v1/feetiers": json.loads((FIX / "arcus_feetiers.json").read_text()),
        }
        self.frames = {"arcus": [f["raw"] for f in json.loads((FIX / "arcus_ws_frames.json").read_text())]}
        self.subs: dict[str, list[Any]] = {"arcus": []}
        self.app.router.add_get("/arcus-ws", lambda r: self.ws(r, "arcus"))
        self.app.router.add_route("*", "/{tail:.*}", self.handle)
        self.runner: web.AppRunner | None = None
        self.url = ""

    async def start(self) -> None:
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.url = f"127.0.0.1:{site._server.sockets[0].getsockname()[1]}"  # type: ignore[union-attr]

    async def handle(self, req: web.Request) -> web.StreamResponse:
        return web.json_response(self.rest.get(req.path, {}))

    async def ws(self, req: web.Request, name: str) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=None)
        await ws.prepare(req)
        frames = self.frames[name]

        async def pump() -> None:
            head = 12  # connected + snapshots first, then a live-like trickle
            for f in frames[:head]:
                await ws.send_str(json.dumps(f))
            for f in frames[head:]:
                await asyncio.sleep(0.01)
                if ws.closed:
                    return
                await ws.send_str(json.dumps(f))

        task = asyncio.create_task(pump())
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                self.subs[name].append(json.loads(msg.data))
        task.cancel()
        return ws


async def test_paper_runner_end_to_end(tmp_path: Path) -> None:
    srv = FakeVenues()
    await srv.start()
    acfg = load_arcus_config(ROOT / "config/venues/arcus.yaml")
    acfg.rest.mainnet, acfg.ws.mainnet = f"http://{srv.url}", f"ws://{srv.url}/arcus-ws"
    app = load_app(ROOT / "config/app.yaml")
    app.data_dir, app.reports_dir = str(tmp_path / "data"), str(tmp_path / "reports")
    app.state_dir = str(tmp_path / "state")
    sessions = [load_session(ROOT / "config/sessions/arcus_btc_mm.yaml")]
    runner = BotRunner(sessions, mode=RunMode.PAPER, cli_live=False, app=app, arcus_cfg=acfg,
                       secrets=SecretStore(tmp_path / "none.enc", password=""),
                       calendar=TradingCalendar.load(ROOT / "config/calendars"), state_db=str(tmp_path / "s.sqlite"))
    assert runner.mode is RunMode.PAPER and not runner.writes_mainnet
    try:
        await runner.run(duration_s=4)
    finally:
        if srv.runner:
            await srv.runner.cleanup()
    assert all(isinstance(a, PaperVenue) for a in runner.adapters.values())
    assert set(runner.adapters) == {Venue.ARCUS}
    view = runner.hub.get(Venue.ARCUS, "BTC")
    assert view is not None and view.mid() is not None
    assert runner.hub.view(Venue.ARCUS, "BTC").predicted_funding_h is not None
    assert Path(app.heartbeat_for("paper")).exists() and not Path(app.heartbeat_for("live")).exists()
    # a clean stop leaves a last heartbeat saying so: the guardian stands down instead of alarming
    assert "stopped" in json.loads(Path(app.heartbeat_for("paper")).read_text())
    kinds = {d["kind"] for d in runner.decisions.records}
    assert {"mode", "place"} <= kinds, kinds
    assert runner.state.orders and all(o.req.venue in runner.adapters for o in runner.state.orders.values())
    assert any(s.get("channel") or s.get("type") for s in srv.subs["arcus"])  # it subscribed
    st = runner.status()
    assert st["mode"] == "paper" and not st["risk"]["safe_mode"], st
