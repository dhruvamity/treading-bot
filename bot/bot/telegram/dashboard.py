"""The live dashboard: today's volume and PnL and the capital's profit or loss, refreshed every 10 s.

Two screens from the same numbers:
- Telegram: /dashboard posts one message and edits it every 10 s. It is pinned so it stays at the top of the chat,
  and it keeps updating until ⏹ Stop is tapped or a newer /dashboard replaces it. It survives a restart of the
  Telegram bot (state/telegram_dashboard.json).
- Terminal: `bot dashboard` redraws the same screen every 10 s (Ctrl-C leaves).

Where each number comes from:
- Volume, fills and fees today: the bot's fills table since 00:00 UTC. Exact, and it moves with every fill.
- Equity and net deposits: the running bot reads the account every 15 s and publishes it with its status. With no bot
  running (or a bot started before this existed), the dashboard reads the account itself at most once a minute (a
  public read by address), else it uses the last line of state/balances.jsonl.
- PnL today (live): trading PnL (equity - net deposits) now, minus the same at 00:00 UTC from the balance history.
  Deposits and withdrawals never count, and a bot restart during the day does not reset it. Paper: the bot's own
  day PnL.
- Capital P/L: equity - net deposits. Live: everything since the first deposit. Paper: since the paper bot started.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from html import escape
from pathlib import Path
from typing import Any

from bot.core.balances import DAY, BalanceLog, pnl
from bot.telegram.control import Control, ModeView, _today_start_us
from bot.telegram.views import ago, state_of, usd

REFRESH_S = 10.0
FRESH_S = 60.0          # an account reading older than this makes the dashboard read the account itself
PACE_AFTER_S = 1800.0   # extrapolate today's volume to a full day only after 30 min of trading


@dataclass
class Dash:
    now: float
    mode: str | None = None
    running: bool = False
    icon: str = "⚪"
    state: str = "no trading bot running"
    up_s: float | None = None
    market: str | None = None
    config: str | None = None
    backtest: dict[str, Any] = field(default_factory=dict)
    size_capital: float | None = None
    # today (UTC day)
    day_start: float = 0.0
    volume: float = 0.0
    maker_volume: float = 0.0
    fills: int = 0
    fees: float = 0.0
    by_market: dict[str, float] = field(default_factory=dict)
    pace_day: float | None = None
    day_pnl: float | None = None
    day_pnl_base: float | None = None     # equity the day's PnL is measured against (for the %)
    day_pnl_note: str = ""
    # now
    positions: list[tuple[str, float, float | None]] = field(default_factory=list)   # market, size, notional
    open_orders: int = 0
    # capital
    equity: float | None = None
    free: float | None = None
    net_deposits: float | None = None
    account_age_s: float | None = None
    account_from: str = ""
    capital_pnl: float | None = None
    changes: dict[str, float] = field(default_factory=dict)   # "7 d" -> trading PnL change
    warnings: list[str] = field(default_factory=list)


def pick_mode(control: Control) -> str | None:
    """The running bot (live first); with none running, live (its fills today and the real account), if it ever ran
    here. A stopped paper bot is not shown: its numbers are not money."""
    run = control.running_modes()
    if run:
        return run[0]
    return "live" if "live" in control.known_modes() else None


def published_account(snap: dict[str, Any] | None, now: float) -> dict[str, Any] | None:
    """The running bot's last account reading, as {equity, free, net_deposits, ts} (Arcus first)."""
    acc = (snap or {}).get("account") or {}
    a = acc.get("arcus") or next(iter(acc.values()), None)
    if not a or not a.get("ts_us"):
        return None
    return {"equity": a.get("equity"), "free": a.get("free"), "net_deposits": a.get("net_deposits"),
            "ts": a["ts_us"] / 1e6, "from": "the bot"}


def _day_base(log: BalanceLog, day_start: float, account: int) -> dict[str, Any] | None:
    """The balance at 00:00 UTC: the last reading before it, else the day's first reading."""
    rows = [r for r in log.rows(since=day_start - 2 * DAY) if int(r.get("account", 0)) == account]
    before = [r for r in rows if r["ts"] < day_start]
    if before:
        return before[-1]
    return rows[0] if rows else None


def collect(control: Control, *, now: float | None = None, account: int = 0,
            extra: dict[str, Any] | None = None) -> Dash:
    """Everything the dashboard shows. `extra`: an account reading the caller made itself ({equity, free,
    net_deposits, ts}); the freshest of it, the bot's published reading and the balance history is used."""
    now = time.time() if now is None else now
    d = Dash(now=now, day_start=_today_start_us(now) / 1e6)
    state_dir = control.root / control.app.state_dir
    log = BalanceLog(state_dir / "balances.jsonl")
    d.mode = pick_mode(control)
    v: ModeView | None = control.view(d.mode, now) if d.mode else None
    snap = (v.snapshot or {}) if v else {}
    if v is not None:
        d.running = v.running
        d.icon, d.state = state_of(v)
        if v.running and snap.get("started_us"):
            d.up_s = now - snap["started_us"] / 1e6
            if v.snapshot_age_s is not None and v.snapshot_age_s > 30:
                d.warnings.append(f"the bot has not published its status for {ago(v.snapshot_age_s)}")
        _today(d, v, snap)
        _positions(d, v, snap)
        sess = snap.get("sessions") or []
        caps = [float(s["size_capital"]) for s in sess if s.get("size_capital")]
        d.size_capital = sum(caps) if caps else None
    _deployed(d, state_dir)

    # the account: live uses the real one (bot, own read, history); paper only what the paper bot publishes
    paper = d.mode == "paper"
    readings = [r for r in (published_account(snap, now), None if paper else extra,
                            None if paper else _latest(log, account)) if r and r.get("equity")]
    if readings:
        r = max(readings, key=lambda x: x["ts"])
        d.equity, d.free, d.net_deposits = float(r["equity"]), r.get("free"), r.get("net_deposits")
        d.account_age_s, d.account_from = max(0.0, now - r["ts"]), str(r.get("from", ""))
        if d.net_deposits is not None:
            d.net_deposits = float(d.net_deposits)
            d.capital_pnl = d.equity - d.net_deposits
    elif paper:
        nets = [float(m.get("net") or 0) for m in snap.get("markets") or []]
        d.capital_pnl = sum(nets) if nets else None

    # PnL today
    base = None if paper else _day_base(log, d.day_start, account)
    if base is not None and d.equity is not None:
        p_base, p_now = pnl(base), d.capital_pnl
        if p_base is not None and p_now is not None:
            d.day_pnl = p_now - p_base
        else:
            d.day_pnl = d.equity - float(base["equity"])
        d.day_pnl_base = float(base["equity"])
        if base["ts"] >= d.day_start:
            d.day_pnl_note = f"since the first reading today, {_clock(base['ts'])}"
    elif v is not None:
        vals = [float(s["day_pnl"]) for s in snap.get("sessions") or [] if s.get("day_pnl") is not None]
        if vals:
            d.day_pnl = sum(vals)
            d.day_pnl_base = d.size_capital
            d.day_pnl_note = "the bot's own count" + (" (since it started)" if d.up_s and d.up_s < now - d.day_start
                                                      else "")
    if not paper and d.capital_pnl is not None:
        # trading PnL over the last 7 / 30 days, only once the history is that long (before that it is the all-time
        # figure again)
        rows = [r for r in log.rows(since=now - 31 * DAY) if int(r.get("account", 0)) == account
                and pnl(r) is not None]
        for days, name in ((7, "7 d"), (30, "30 d")):
            older = [r for r in rows if r["ts"] < now - days * DAY]
            if older:   # the balance as it was N days ago
                d.changes[name] = d.capital_pnl - float(pnl(older[-1]) or 0)
    return d


def _latest(log: BalanceLog, account: int) -> dict[str, Any] | None:
    r = log.latest()
    if r is None or int(r.get("account", 0)) != account:
        return None
    return {**r, "from": f"balance history ({r.get('source', '?')})"}


def _today(d: Dash, v: ModeView, snap: dict[str, Any]) -> None:
    first: float | None = None
    for market, t in v.today.items():
        d.volume += t["volume"]
        d.maker_volume += t["maker_volume"]
        d.fills += int(t["fills"])
        d.fees += t["fees"]
        d.by_market[market] = t["volume"]
        if t.get("first_us"):
            first = t["first_us"] / 1e6 if first is None else min(first, t["first_us"] / 1e6)
    if not v.running or not d.volume:
        return
    started = snap.get("started_us", 0) / 1e6 or d.now
    t_from = max(d.day_start, min(started, first if first is not None else started))
    if d.now - t_from >= PACE_AFTER_S:
        d.pace_day = d.volume / (d.now - t_from) * DAY


def _positions(d: Dash, v: ModeView, snap: dict[str, Any]) -> None:
    d.open_orders = len(v.open_orders)
    for m in snap.get("markets") or []:
        size = float(m.get("position") or 0)
        if size:
            mark = float(m["mark"]) if m.get("mark") else None
            d.positions.append((str(m["market"]), size, size * mark if mark is not None else None))
    if not snap.get("markets"):
        for k, val in v.positions.items():   # the runner is not publishing: the positions table
            d.positions.append((k.split(":")[-1], float(val.split()[0]), None))


def _deployed(d: Dash, state_dir: Path) -> None:
    try:
        a = (json.loads((state_dir / "pilot.json").read_text()) or {}).get("active")
    except (OSError, ValueError):
        return
    if a and (d.mode is None or a.get("mode") == d.mode):
        d.market, d.config, d.backtest = a.get("market"), a.get("config"), a.get("backtest") or {}


def _clock(ts: float) -> str:
    """Local time with its zone, e.g. 20:02 IST (UTC when the machine runs on UTC)."""
    t = time.localtime(ts)
    return time.strftime("%H:%M ", t) + (t.tm_zone or "")


def _pct(x: float | None, of: float | None) -> str:
    return f" ({x / of * 100:+.2f}%)" if x is not None and of else ""


def render(d: Dash, *, html: bool = True, frame: str = "live") -> str:
    """Telegram HTML (html=True) or plain terminal text. frame: "live" (updating), "stopped" (the last frame of a
    Telegram dashboard that stopped updating) or "once" (`bot dashboard --once`)."""
    def b(x: str) -> str:
        return f"<b>{escape(x)}</b>" if html else x

    def e(x: str) -> str:
        return escape(x) if html else x

    def i(x: str) -> str:
        return f"<i>{escape(x)}</i>" if html else x

    def head(x: str) -> str:
        return f"<b>{escape(x)}</b>" if html else x.upper()

    what = f"{d.market} · {d.config}" if d.market else "no setup deployed"
    lines = [f"📊 {b((d.mode or 'no bot').upper())} · {e(what)}",
             f"{d.icon} {e(d.state)}" + (f" · up {ago(d.up_s)}" if d.up_s is not None else "")]
    stamp = time.strftime("%H:%M:%S ", time.localtime(d.now)) + (time.localtime(d.now).tm_zone or "")
    lines.append(i({"live": f"Updated {stamp} · refreshes every {REFRESH_S:.0f} s",
                    "stopped": f"Stopped updating at {stamp} · tap ▶️ or send /dashboard to update it again"}
                   .get(frame, f"At {stamp}")))

    utc0 = _clock(d.day_start)
    since = "00:00 UTC" + (f" = {utc0}" if not utc0.endswith("UTC") else "")
    lines += ["", head("Today") + " " + e(f"(since {since}, {ago(d.now - d.day_start)} in)")]
    maker = "all maker" if d.volume and abs(d.maker_volume - d.volume) < 0.005 else \
        f"maker {usd(d.maker_volume, sign=False)}" if d.volume else ""
    lines.append(f"Volume {b(usd(d.volume, sign=False))} · {d.fills} fills" + (f" ({maker})" if maker else "")
                 + f" · fees {usd(d.fees, sign=False)}")
    if len(d.by_market) > 1:
        lines.append(e("  " + " · ".join(f"{m} {usd(x, sign=False)}" for m, x in sorted(d.by_market.items()))))
    bt_vol, bt_pnl = d.backtest.get("volume_day"), d.backtest.get("pnl_day")
    pace = []
    if d.pace_day is not None:
        pace.append(f"${d.pace_day:,.0f}/day at today's rate")
    if bt_vol is not None:
        pace.append(f"backtest ${float(bt_vol):,.0f}/day")
    if pace:
        lines.append(e("Pace: " + " · ".join(pace)))
    if d.day_pnl is not None:
        lines.append(f"PnL {b(usd(d.day_pnl))}{e(_pct(d.day_pnl, d.day_pnl_base))}"
                     + (e(f" · backtest {usd(bt_pnl)}/day") if bt_pnl is not None else "")
                     + (" · " + i(d.day_pnl_note) if d.day_pnl_note else ""))
    else:
        lines.append(e("PnL — (no balance reading yet today)"))

    lines += ["", head("Now")]
    if d.positions:
        for m, size, notional in d.positions:
            lines.append(e(f"Position {m} {size:+.6g}" + (f" ≈ {usd(notional)}" if notional is not None else "")))
    else:
        lines.append(e("Position: flat"))
    lines.append(e(f"Open orders: {d.open_orders}" + (f" · sizing for {usd(d.size_capital, sign=False)}"
                                                      if d.size_capital else "")))

    lines += ["", head("Capital") + " " + e("(all time)" if d.mode != "paper" else "(paper, since it started)")]
    if d.equity is not None:
        dep = f" · deposited {usd(d.net_deposits, sign=False)}" if d.net_deposits is not None and d.mode != "paper" \
            else f" · started with {usd(d.net_deposits, sign=False)}" if d.net_deposits is not None else ""
        lines.append(f"Equity {b(usd(d.equity, sign=False))}{e(dep)}"
                     + (e(f" · free {usd(d.free, sign=False)}") if d.free is not None else ""))
    if d.capital_pnl is not None:
        ch = " · ".join(f"{k} {usd(x)}" for k, x in d.changes.items())
        lines.append(f"P/L {b(usd(d.capital_pnl))}{e(_pct(d.capital_pnl, d.net_deposits))}" + (e(f" · {ch}") if ch
                                                                                                 else ""))
    if d.equity is None and d.capital_pnl is None:
        lines.append(e("No balance reading yet (ARCUS_ADDRESS in .env lets the bot and the scout read it)."))
    elif d.account_age_s is not None:
        lines.append(i(f"account read {ago(d.account_age_s)} ago by {d.account_from}"))
    for w in d.warnings:
        lines.append("⚠️ " + e(w))
    return "\n".join(lines)
