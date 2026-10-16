"""`deltaneutral run`: the engine's loop, paper or live.

- paper (the default): the venues' real books and funding, simulated orders. Nothing leaves the machine but public
  reads. The pretend money is moved between the venues by the bot itself after each close.
- live: real orders on both venues. Refused unless DN_LIVE=1 is in arcus/.env, this machine trades (BOT_ROLE all or
  trader) and LIVE is typed at the prompt (cli.py). The money is moved by the owner, as the bot says.

Files in deltaneutral/state/ (one set per mode): <mode>-state.json, <mode>-events.jsonl, <mode>-cycles.jsonl,
<mode>-control.json (commands from `deltaneutral close|pause|resume`), paper-venues.json, paper-funding.json.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import signal
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import aiohttp

from . import ROOT, STATE
from . import venues as V
from .engine import Engine, Settings, Store
from .series import hhmm

LOOP_S = {"opening": 3.0, "closing": 3.0, "open": 10.0, "flat": 15.0}
LABEL = "⇄ DELTA NEUTRAL"


def settings_path() -> Path:
    return ROOT / "settings.json"


def settings() -> Settings:
    return Settings.load(settings_path())


def telegram_alert(env: dict[str, str]) -> Callable[[str], None] | None:
    """Send-only alerts through the trading bot's one Telegram bot (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)."""
    tok, chat = env.get("TELEGRAM_BOT_TOKEN", ""), env.get("TELEGRAM_CHAT_ID", "")
    if not tok or not chat:
        return None

    async def post(text: str) -> None:
        with contextlib.suppress(aiohttp.ClientError, TimeoutError, OSError):
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s:
                await s.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                             json={"chat_id": chat, "text": f"{LABEL}\n{text}"})

    def send(text: str) -> None:
        with contextlib.suppress(RuntimeError):
            asyncio.get_running_loop().create_task(post(text))
    return send


def store(mode: str, echo: Callable[[str], None] | None = print, alert: Callable[[str], None] | None = None,
          root: Path | None = None) -> Store:
    return Store(root or STATE, mode, echo=echo, alert=alert)


def say(text: str) -> None:
    print(text, flush=True)          # a background run writes to a file: each line as it happens


async def run(mode: str, *, seconds: float | None = None, collateral: dict[str, float] | None = None,
              echo: Callable[[str], None] | None = say, now: bool = False) -> None:
    """Run until stopped (Ctrl-C, SIGTERM) or for `seconds`. A stop is not a close: an open position stays (and in
    paper its legs are kept on disk); `deltaneutral close` closes. `now`: when flat, the first cycle opens at once
    instead of at the next daily open time (the next ones follow every `hold_h` hours from it)."""
    env = V.read_env()
    names = V.account_names(env)
    st_ = store(mode, echo, telegram_alert(env))
    paper_sides: dict[str, V.PaperSide] = {}
    if mode == "live":
        venues, equity = V.live(env)
    else:
        paper_sides, equity = V.paper(collateral or {"arcus": 100.0, "lighter": 100.0})
        venues = paper_sides            # type: ignore[assignment]
        names = {"arcus": "Arcus (paper)", "lighter": "Lighter (paper)"}
    eng: Engine
    reader = V.Reader(equity, lambda: eng.symbols())

    def mover(amount: float, source: str, target: str) -> None:
        paper_sides[source].collateral -= amount
        paper_sides[target].collateral += amount

    eng = Engine(venues, reader, settings, st_, st_.load(), mover=mover if mode == "paper" else None, names=names)
    books = STATE / "paper-venues.json"
    funding = V.PaperFunding(STATE / "paper-funding.json") if mode == "paper" else None
    if mode == "paper":
        try:
            saved = json.loads(books.read_text())
            for name, side in paper_sides.items():
                if name in saved:
                    side.restore(saved[name])
        except (OSError, ValueError):
            pass
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(sig, stop.set)
    s = settings()
    if now and eng.st.phase == "flat":
        eng.st.next_open = time.time()
    st_.event("started", f"started ({mode}): pair {s.x}/{s.y}, cycle {s.hold_h:g} h from {hhmm(s.open_ny)} New York; "
              + (f"holding cycle {eng.st.cycle} ({eng.st.phase})" if eng.st.phase != "flat" else "flat"))
    t_end = time.time() + seconds if seconds else None
    last_funding = time.time()
    try:
        while not stop.is_set() and (t_end is None or time.time() < t_end):
            try:
                await eng.step()
            except (RuntimeError, aiohttp.ClientError, TimeoutError, OSError) as e:
                st_.event("venue_down", f"a read failed: {type(e).__name__} {str(e)[:160]}")
            if mode == "paper":
                books.write_text(json.dumps({n: v.dump() for n, v in paper_sides.items()}))
                if funding is not None and time.time() - last_funding > 600 and eng.st.phase != "flat":
                    last_funding = time.time()
                    with contextlib.suppress(RuntimeError, aiohttp.ClientError, TimeoutError, OSError, KeyError):
                        paid = await funding.apply(paper_sides, (eng.st.x, eng.st.y), eng.st.opened_at)
                        for v, x in paid.items():
                            eng.st.funding[v] = eng.st.funding.get(v, 0.0) + x
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), LOOP_S.get(eng.st.phase, 5.0))
    finally:
        st_.event("stopped", "stopped" + (f"; cycle {eng.st.cycle} is {eng.st.phase} and stays so until the bot runs "
                                          "again (`deltaneutral close` closes it)" if eng.st.phase != "flat" else ""))
        st_.save(eng.st)
        for v in venues.values():
            with contextlib.suppress(Exception):
                await v.stop()
        await reader.close()


def status(mode: str) -> dict[str, Any]:
    """What the status commands show: the state, the last events and cycles, the settings."""
    s = store(mode, echo=None)
    st = s.load()
    return {"state": st, "events": s.tail(s.events, 12), "cycles": s.tail(s.cycles, 30), "settings": settings()}
