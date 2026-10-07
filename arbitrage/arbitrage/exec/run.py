"""`arbitrage run`: the executor's loop, its files and its two modes.

- paper (the default): real prices and real funding rates, simulated orders (venue.PaperVenue). Nothing leaves the
  machine but public reads.
- live: real orders on both venues. Refused unless ARB_LIVE=1 is in arbitrage/.env and LIVE is typed at the prompt.

Files in arbitrage/state/ (one set per mode, so a paper position is never mistaken for a real one):
    position-<mode>.json   everything the engine knows (written after every step; read at start)
    control-<mode>.json    commands from `arbitrage close`, `pause`, `resume` (read once, then removed)
    events-<mode>.jsonl    what happened, one line each
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import time
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

import aiohttp

from arbitrage import scan as scanner
from arbitrage.config import ROOT, STATE, Config, load, read_env
from arbitrage.exec.engine import Engine, State
from arbitrage.exec.venue import PaperVenue, Spec, TradeVenue
from arbitrage.rank import Plan
from arbitrage.venues import Arcus, Lighter

LOOP_S = {"entering": 1.0, "exiting": 1.0, "open": 10.0, "flat": 15.0}
ALERT_KINDS = ("entering", "open", "exiting", "closed", "cross", "unequal", "refused", "not_opened", "stop_moved",
               "pause", "stopped", "started", "rebalance")


class FileStore:
    def __init__(self, root: Path, mode: str, *, echo: Callable[[str], None] | None = print,
                 alert: Callable[[str], None] | None = None) -> None:
        self.root, self.mode, self.echo, self.alert = root, mode, echo, alert
        root.mkdir(parents=True, exist_ok=True)
        self.position = root / f"position-{mode}.json"
        self.control = root / f"control-{mode}.json"
        self.events = root / f"events-{mode}.jsonl"
        self._last_down = 0.0

    def save(self, st: State) -> None:
        tmp = self.position.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(st), indent=1))
        os.replace(tmp, self.position)

    def load(self) -> State | None:
        try:
            return State.load(json.loads(self.position.read_text()))
        except (OSError, ValueError, TypeError):
            return None

    def commands(self) -> dict[str, Any]:
        try:
            cmd = json.loads(self.control.read_text())
            self.control.unlink()
            return cmd if isinstance(cmd, dict) else {}
        except (OSError, ValueError):
            return {}

    def send(self, **cmd: Any) -> None:
        """Leave a command for the running bot (merged with one not read yet)."""
        try:
            cur = json.loads(self.control.read_text())
        except (OSError, ValueError):
            cur = {}
        self.control.write_text(json.dumps({**cur, **cmd}))

    def event(self, kind: str, text: str, **data: Any) -> None:
        now = time.time()
        if kind == "venue_down":             # one line a minute is enough while a venue is away
            if now - self._last_down < 60:
                return
            self._last_down = now
        with self.events.open("a") as fh:
            fh.write(json.dumps({"ts": now, "kind": kind, "text": text, **data}, default=str) + "\n")
        if self.echo:
            self.echo(f"{time.strftime('%H:%M:%S')} {'PAPER ' if self.mode == 'paper' else ''}{text}")
        if self.alert and kind in ALERT_KINDS:
            self.alert(("PAPER · " if self.mode == "paper" else "") + text)

    def tail(self, n: int = 12) -> list[dict[str, Any]]:
        try:
            return [json.loads(x) for x in self.events.read_text().splitlines()[-n:]]
        except (OSError, ValueError):
            return []


class LivePlanner:
    """The engine's eyes: the scanner's best plan now, and how an open position's difference is doing."""

    def __init__(self, cfg: Callable[[], Config], skip: Callable[[], set[str]] = set) -> None:
        self.cfg, self.skip = cfg, skip

    async def best(self, collateral: dict[str, float]) -> Plan | None:
        res = await scanner.run(self.cfg(), top=12, collateral=collateral)
        skip = self.skip()
        return next((p for p in res.plans if p.go and p.symbol not in skip), None)

    async def edges(self, symbol: str, short_venue: str) -> tuple[float, float] | None:
        res = await scanner.run(self.cfg(), symbols=[symbol], collateral={"arcus": 1.0, "lighter": 1.0})
        if not res.plans:
            return None
        p = res.plans[0]
        sign = 1.0 if p.short_venue == short_venue else -1.0     # the plan may now point the other way
        return sign * p.edge_next_h, sign * p.edge_24h


def skipped(root: Path = ROOT) -> set[str]:
    try:
        return {str(x).upper() for x in json.loads((root / "settings.json").read_text()).get("skip", [])}
    except (OSError, ValueError, AttributeError):
        return set()


def telegram_alert(env: dict[str, str]) -> Callable[[str], None] | None:
    """Send-only alerts through the trading bot's one Telegram bot (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID in
    arcus/.env). Sending needs no listener, so it does not disturb the process that reads the commands."""
    tok, chat = env.get("TELEGRAM_BOT_TOKEN", ""), env.get("TELEGRAM_CHAT_ID", "")
    if not tok or not chat:
        return None

    async def post(text: str) -> None:
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s:
                await s.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                             json={"chat_id": chat, "text": f"⚖️ FUNDING ARB\n{text}"})
        except (aiohttp.ClientError, TimeoutError, OSError):
            pass

    def send(text: str) -> None:
        try:
            asyncio.get_running_loop().create_task(post(text))
        except RuntimeError:
            pass
    return send


async def paper_venues(collateral: dict[str, float]) -> tuple[dict[str, TradeVenue], Callable[[], Any]]:
    """Two PaperVenues on the real books. Their market rules are read once per market, when the engine starts it."""
    arcus, lighter = Arcus(), Lighter()

    class _Paper(PaperVenue):
        def __init__(self, name: str, client: Any, taker_bp: float) -> None:
            super().__init__(name, client.top, Spec(0.01, 0.0001, 0.0001, 5.0, taker_bp), collateral.get(name, 0.0))
            self.client = client

        async def start(self, symbol: str) -> Spec:
            legs = await self.client.legs()           # also fills Lighter's market ids
            leg = legs[symbol]
            self.spec = Spec(leg.tick, leg.step, leg.min_size, leg.min_notional, self.spec.taker_bp)
            return self.spec

        async def stop(self) -> None:
            await self.client.http.close()

    venues: dict[str, TradeVenue] = {"arcus": _Paper("arcus", arcus, 2.25), "lighter": _Paper("lighter", lighter, 0.0)}

    async def close() -> None:
        for v in venues.values():
            await v.stop()
    return venues, close


def live_venues() -> dict[str, TradeVenue]:
    from arbitrage.exec.arcus import ArcusTrade
    from arbitrage.exec.lighter import LighterTrade

    return {"arcus": ArcusTrade(ROOT.parent / "arcus"), "lighter": LighterTrade(STATE)}


class PaperFunding:
    """Credits a paper position the funding the venues really paid: each published hour once, to each paper venue."""

    def __init__(self, path: Path) -> None:
        self.path = path
        try:
            self.until = float(json.loads(path.read_text())["until"])
        except (OSError, ValueError, KeyError):
            self.until = 0.0

    async def apply(self, eng: Engine, venues: dict[str, Any], now: float) -> float:
        st = eng.st
        if st.phase != "open":
            return 0.0
        arcus, lighter = Arcus(), Lighter()
        hist = scanner.History(STATE)
        try:
            await lighter.legs()
            rates = {"arcus": await hist.fresh("arcus", st.symbol, arcus, now),
                     "lighter": await hist.fresh("lighter", st.symbol, lighter, now)}
        finally:
            await arcus.http.close()
            await lighter.http.close()
        start = max(self.until, st.opened_at)
        hours = [h for h in sorted(set(rates["arcus"]) & set(rates["lighter"])) if start < h <= now]
        paid = 0.0
        for h in hours:
            for name in ("arcus", "lighter"):
                side = 1.0 if name == st.long_venue else -1.0
                x = -side * rates[name][h] * st.size * st.entry[name]     # longs pay a positive rate
                venues[name].collateral += x
                paid += x
            self.until = float(h)
        if hours:
            self.path.write_text(json.dumps({"until": self.until}))
            eng.store.event("funding", f"{st.symbol}: {len(hours)} funding payment(s), ${paid:+.4f}", paid=paid)
        return paid


async def run(mode: str, *, seconds: float | None = None, collateral: dict[str, float] | None = None,
              echo: Callable[[str], None] | None = print) -> State:
    """Run the executor until stopped (Ctrl-C, SIGTERM) or for `seconds`. The position, if any, is kept: a stop is
    not a close (`arbitrage close` closes)."""
    env = read_env()
    store = FileStore(STATE, mode, echo=echo, alert=telegram_alert(env))
    closer: Callable[[], Any] | None = None
    if mode == "live":
        venues = live_venues()
    else:
        venues, closer = await paper_venues(collateral or {})
    eng = Engine(venues, LivePlanner(load, skipped), lambda: load().settings, store, state=store.load(),
                 require_native_stops=mode == "live")
    funding = PaperFunding(STATE / "paper-funding.json") if mode == "paper" else None
    books = STATE / "paper-venues.json"         # the paper venues' positions and money, kept across restarts
    if mode == "paper":
        try:
            saved = json.loads(books.read_text())
            for name, v in venues.items():
                if name in saved and eng.st.phase != "flat":
                    v.restore(saved[name])      # type: ignore[attr-defined]
        except (OSError, ValueError):
            pass
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            pass
    store.event("started", f"started ({mode}); " + (f"holding {eng.st.symbol}" if eng.st.phase != "flat"
                                                    else "flat, looking for a position"))
    t_end = time.time() + seconds if seconds else None
    last_funding = 0.0
    try:
        while not stop.is_set() and (t_end is None or time.time() < t_end):
            await eng.step()
            if mode == "paper":
                books.write_text(json.dumps({name: v.dump() for name, v in venues.items()}))  # type: ignore[attr-defined]
            if funding is not None and time.time() - last_funding > 300:
                last_funding = time.time()
                try:
                    await funding.apply(eng, venues, time.time())
                except (RuntimeError, aiohttp.ClientError, TimeoutError, OSError):
                    pass
            try:
                await asyncio.wait_for(stop.wait(), LOOP_S.get(eng.st.phase, 5.0))
            except TimeoutError:
                pass
    finally:
        store.event("stopped", "stopped" + (f"; {eng.st.symbol} is still {eng.st.phase}: its orders and stops stay on "
                                            "the venues" if eng.st.phase != "flat" else ""))
        for v in venues.values():
            try:
                await v.stop()
            except Exception:   # noqa: BLE001  a venue that will not close cleanly must not hide the stop
                pass
        if closer is not None:
            await asyncio.sleep(0)
    return eng.st
