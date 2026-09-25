"""Alerts the Telegram bot sends on its own, from the same state the status screen reads:

- the trading bot stopped or started (a dead bot cannot alert about itself);
- safe mode, a drawdown stop or a daily loss stop appeared or cleared;
- today's PnL reached half, then all, of the daily loss limit;
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
from typing import Any

from bot.telegram.control import Control, ModeView
from bot.telegram.views import _risk, ago, day_pnl, fill_line, usd

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
    pnl_level: int = 0            # 0 none, 1 half the limit alerted, 2 limit alerted
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
        # ---- bot up / down
        if m.running and not v.running:
            if now - m.stop_requested_at < 300:
                await self._say(f"⏹ <b>{name}</b> bot stopped, as requested.")
            else:
                await self._say(f"🔴 <b>{name} bot is DOWN</b> — no heartbeat for {ago(v.heartbeat_age_s)}. Resting "
                                "quotes stay on the venue until the dead man's switch or the guardian cancels them. "
                                "Check the server; /status", critical=True)
        elif v.running and not m.running:
            await self._say(f"🟢 <b>{name}</b> bot started (pid {v.pid}).")
        m.running = v.running
        # ---- risk flags
        risk = tuple(_risk(v))
        for r in risk:
            if r not in m.risk:
                await self._say(f"🔴 <b>{name}</b>: {r}. Quoting stopped; /status for details, /resumeaftersl after you "
                                "have checked.", critical=r.startswith(("SAFE", "STOPPED")))
        if m.risk and not risk and v.running:
            await self._say(f"🟢 <b>{name}</b>: safety stops cleared, quoting allowed again.")
        m.risk = risk
        # ---- daily PnL against the loss limit
        day = dt.datetime.fromtimestamp(now, dt.UTC).strftime("%Y-%m-%d")
        if m.pnl_day != day:
            m.pnl_day, m.pnl_level = day, 0
        dp = day_pnl(v)
        cap = sum(float(s.get("capital") or 0) for s in (v.snapshot or {}).get("sessions", []))
        limit = cap * self.daily_loss_pct / 100
        if self.prefs.pnl_alerts and dp is not None and limit > 0:
            if dp <= -limit and m.pnl_level < 2:
                m.pnl_level = 2
                await self._say(f"🔴 <b>{name}</b> day PnL {usd(dp)} reached the daily loss limit ({usd(-limit)}). "
                                "The bot stops opening new positions until 00:00 UTC.", critical=True)
            elif dp <= -limit / 2 and m.pnl_level < 1:
                m.pnl_level = 1
                await self._say(f"🟡 <b>{name}</b> day PnL {usd(dp)}: half of the daily loss limit ({usd(-limit)}).")
        # ---- fills
        new = self.control.fills_since(mode, m.last_fill_ts)
        if new:
            m.last_fill_ts = max(f["ts_us"] for f in new)
            if self.prefs.fills == "each":
                for i in range(0, len(new), 15):
                    await self._say(f"💱 <b>{name}</b> fills\n<pre>" + "\n".join(fill_line(f) for f in new[i:i + 15])
                                    + "</pre>")
            for f in new:
                s = m.summary.setdefault(f["market"], [0.0, 0.0])
                s[0] += 1
                s[1] += f["price"] * f["size"] if f["maker"] else 0.0
        if self.prefs.fills == "summary" and now - m.summary_start >= self.prefs.summary_min * 60:
            if m.summary:
                rows = [f"{mk:<6} {int(n):>4} fills  {usd(vol, sign=False):>10} maker" for mk, (n, vol) in
                        sorted(m.summary.items())]
                await self._say(f"📈 <b>{name}</b> last {self.prefs.summary_min} min · day PnL {usd(dp)}\n<pre>"
                                + "\n".join(rows) + "</pre>")
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
                parts.append(f"<b>{mode.upper()}</b>\n<pre>{_trim(rep)}</pre>")
        if parts:
            await self._say(f"🗓 <b>Daily digest {y}</b>\n\n" + "\n\n".join(parts))


def _trim(md: str, n: int = 2500) -> str:

    return escape(md[:n] + ("\n…" if len(md) > n else ""))


def status_digest(views: list[ModeView]) -> dict[str, Any]:
    """Small machine-readable summary (used by tests and /ping)."""
    return {v.mode: {"running": v.running, "day_pnl": day_pnl(v)} for v in views}
