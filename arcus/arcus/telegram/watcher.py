"""Alerts the Telegram bot sends on its own, from the same state the status screen reads:

- the trading bot stopped, went down, or started (a dead bot cannot alert about itself);
- safety stops cleared (the running bot itself alerts when one fires: a daily stop, the kill, safe mode);
- today's PnL reached half of the daily stop;
- fills: each one, an hourly summary, or nothing;
- a digest shortly after 00:00 UTC with yesterday's numbers.

Muting silences everything except critical alerts.
"""

from __future__ import annotations

import datetime as dt
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from html import escape
from pathlib import Path

from arcus.common.tgfmt import card, codes, section
from arcus.telegram.control import Control
from arcus.telegram.views import _risk, ago, day_pnl, fill_line, position_line, positions_of, usd

Notify = Callable[[str, bool], Awaitable[None]]  # (html text, critical)


@dataclass
class Prefs:
    mute_until: float = 0.0
    fills: str = "summary"        # each | summary | off
    summary_min: int = 60
    digest: bool = True
    pnl_alerts: bool = True

    @classmethod
    def load(cls, path: Path) -> Prefs:
        try:
            d = json.loads(path.read_text())
            return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
        except (OSError, ValueError, TypeError):
            return cls()

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self)))

    def muted(self, now: float | None = None) -> bool:
        return (now if now is not None else time.time()) < self.mute_until


@dataclass
class _Mem:
    running: bool | None = None
    risk: tuple[str, ...] = ()
    last_fill_ts: int = 0
    summary_start: float = field(default_factory=time.time)
    summary: dict[str, list[float]] = field(default_factory=dict)  # market -> [fills, maker volume]
    pnl_day: str = ""
    pnl_level: int = 0            # 0 none, 1 half the daily stop alerted, 2 past the stop (the bot alerts that)
    stop_requested_at: float = 0.0


class Watcher:
    def __init__(self, control: Control, notify: Notify, prefs: Prefs, *, daily_loss_pct: float = 3.0) -> None:
        self.control = control
        self.notify = notify
        self.prefs = prefs
        self.daily_loss_pct = daily_loss_pct
        self.mem: dict[str, _Mem] = {}
        self.last_digest_day = dt.datetime.now(dt.UTC).strftime("%Y-%m-%d")

    def note_stop_requested(self, mode: str) -> None:
        self.mem.setdefault(mode, _Mem()).stop_requested_at = time.time()

    async def _say(self, text: str, critical: bool = False) -> None:
        if critical or not self.prefs.muted():
            await self.notify(text, critical)

    async def tick(self, now: float | None = None) -> None:
        now = now if now is not None else time.time()
        for mode in self.control.known_modes():
            await self._check(mode, now)
        await self._digest(now)

    async def _check(self, mode: str, now: float) -> None:
        v = self.control.view(mode, now)
        m = self.mem.get(mode)
        if m is None:  # first look: remember, never replay history
            m = self.mem[mode] = _Mem(running=v.running, risk=tuple(_risk(v)),
                                      last_fill_ts=self.control.last_fill_ts(mode), summary_start=now)
            return
        name = mode.upper()
        # ---- tbot up / down
        if m.running and not v.running:
            # a stop on purpose: /stop here, a close or a replace by the pilot (up to 10 min of closing), or a bot
            # whose last heartbeat says it pulled its quotes and stopped (2026-09-26: a replace said "LIVE DOWN")
            asked = max(m.stop_requested_at, self.control.stop_asked.get(mode, 0.0))
            if now - asked < 900 or v.stopped:
                pos = positions_of(v)
                held = " · ".join(position_line(p) for p in pos) or "Flat"
                if self.control.stop_kind.get(mode) == "close" and now - asked < 900:
                    await self._say(card("⏹", f"{name} STOPPED", codes("Orders cancelled"), codes(f"Position: {held}")))
                else:
                    await self._say(card("⏹", f"{name} STOPPED", codes("Quotes cancelled"),
                                         codes("Positions unchanged" + (f": {held}" if pos else ""))))
            else:
                await self._say(card("🔴", f"{name} DOWN", codes(f"Heartbeat lost · {ago(v.heartbeat_age_s)}"),
                                     codes("Guardian / dead-man switch protecting orders")), critical=True)
        elif v.running and not m.running and now - self.control.start_asked.get(mode, 0.0) > 300:
            await self._say(card("🟢", f"{name} STARTED", codes("Heartbeat OK")))   # a start from here says so itself
        m.running = v.running
        # ---- risk flags: the running bot alerts each stop when it fires (with its numbers); here only the all-clear
        risk = tuple(_risk(v))
        if m.risk and not risk and v.running:
            await self._say(card("🟢", "SAFETY STOPS CLEARED", codes(f"{name} · quoting again")))
        m.risk = risk
        # ---- daily PnL against the day stop in force (the bot's own, lifted by a run's sl=)
        day = dt.datetime.fromtimestamp(now, dt.UTC).strftime("%Y-%m-%d")
        if m.pnl_day != day:
            m.pnl_day, m.pnl_level = day, 0
        dp = day_pnl(v)
        sess = (v.snapshot or {}).get("sessions", [])
        limit = sum(float((s.get("stops") or {}).get("daily") or 0) for s in sess) or \
            sum(float(s.get("capital") or 0) for s in sess) * self.daily_loss_pct / 100
        if self.prefs.pnl_alerts and dp is not None and limit > 0:
            if dp <= -limit:
                m.pnl_level = 2
            elif dp <= -limit / 2 and m.pnl_level < 1:
                m.pnl_level = 1
                await self._say(card("⚠️", "HALF THE DAY STOP", codes(f"{name} · PnL {usd(dp)}",
                                                                      f"Day stop {usd(-limit)}")))
        # ---- fills
        new = self.control.fills_since(mode, m.last_fill_ts)
        if new:
            m.last_fill_ts = max(f["ts_us"] for f in new)
            if self.prefs.fills == "each":
                for i in range(0, len(new), 15):
                    await self._say(card("💱", f"{name} FILLS", codes(*(fill_line(f) for f in new[i:i + 15]))))
            for f in new:
                s = m.summary.setdefault(f["market"], [0.0, 0.0])
                s[0] += 1
                s[1] += f["price"] * f["size"] if f["maker"] else 0.0
        if self.prefs.fills == "summary" and now - m.summary_start >= self.prefs.summary_min * 60:
            if m.summary:
                rows = [f"{mk} · {int(n)} fills · {usd(vol, sign=False)} maker" for mk, (n, vol) in
                        sorted(m.summary.items())]
                await self._say(card("📈", f"{name} · LAST {self.prefs.summary_min} MIN", codes(*rows),
                                     codes(f"Day PnL {usd(dp)}")))
            m.summary, m.summary_start = {}, now
        elif self.prefs.fills != "summary":
            m.summary, m.summary_start = {}, now

    async def _digest(self, now: float) -> None:
        today = dt.datetime.fromtimestamp(now, dt.UTC)
        key = today.strftime("%Y-%m-%d")
        if key == self.last_digest_day or today.hour != 0 or today.minute < 5:
            return
        self.last_digest_day = key
        if not self.prefs.digest:
            return
        y = (today - dt.timedelta(days=1)).strftime("%Y-%m-%d")
        parts = []
        for mode in self.control.known_modes():
            rep = self.control.report(mode, y)
            if rep:
                parts.append(section(mode.upper(), [f"<pre>{_trim(rep)}</pre>"]))
        if parts:
            await self._say(card("🗓", f"DAILY DIGEST {y}", *parts))


def _trim(md: str, n: int = 2500) -> str:

    return escape(md[:n] + ("\n…" if len(md) > n else ""))
