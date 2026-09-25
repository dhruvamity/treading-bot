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
from bot.telegram.views import ago, money, positions_of, state_of, usd

REFRESH_S = 10.0
FRESH_S = 60.0          # an account reading older than this makes the dashboard read the account itself
PACE_AFTER_S = 1800.0   # extrapolate today's volume to a full day only after 30 min of trading
SPARK = "▁▂▃▄▅▆▇█"
SPARK_POINTS = 16
BAR_CELLS = 12


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
    day_series: list[float] = field(default_factory=list)   # PnL today at each balance reading (for the sparkline)
    # now
    positions: list[dict[str, Any]] = field(default_factory=list)   # views.positions_of: market, size, mark, entry
    open_orders: int = 0
    stops: dict[str, float] = field(default_factory=dict)          # position / daily / kill, in dollars
    quotes: dict[str, Any] = field(default_factory=dict)            # engine QuoteStats of the day (first session)
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
        d.positions, d.open_orders = positions_of(v), len(v.open_orders)
        sess = snap.get("sessions") or []
        caps = [float(s["size_capital"]) for s in sess if s.get("size_capital")]
        d.size_capital = sum(caps) if caps else None
        for s in sess:
            for k, x in (s.get("stops") or {}).items():
                if x:
                    d.stops[k] = d.stops.get(k, 0.0) + float(x)
            if s.get("quotes") and not d.quotes:
                d.quotes = s["quotes"]
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
            d.day_pnl_note = f"since {_clock(base['ts'])}, the first reading today"
        if p_base is not None and d.day_pnl is not None:
            d.day_series = [0.0] + [float(pnl(r) or 0) - p_base for r in log.rows(since=d.day_start)
                                    if int(r.get("account", 0)) == account and pnl(r) is not None
                                    and r["ts"] > base["ts"]] + [d.day_pnl]
    elif v is not None:
        vals = [float(s["day_pnl"]) for s in snap.get("sessions") or [] if s.get("day_pnl") is not None]
        if vals:
            d.day_pnl = sum(vals)
            d.day_pnl_base = d.size_capital
            d.day_pnl_note = "the bot's own count" + (" since it started" if d.up_s and d.up_s < now - d.day_start
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
    if d.now - t_from >= PACE_AFTER_S:   # maker volume: what the backtest's $/day counts
        d.pace_day = d.maker_volume / (d.now - t_from) * DAY


def _deployed(d: Dash, state_dir: Path) -> None:
    try:
        a = (json.loads((state_dir / "pilot.json").read_text()) or {}).get("active")
    except (OSError, ValueError):
        return
    if a and (d.mode is None or a.get("mode") == d.mode):
        d.market, d.config, d.backtest = a.get("market"), a.get("config"), a.get("backtest") or {}


def quote_lines(q: dict[str, Any] | None) -> list[str]:
    """How much of the day the bot quoted, what blocked it, and how often it rested at the best price: the first
    things to look at when fills are fewer than the backtest's."""
    if not q or not q.get("seconds"):
        return []
    tot = float(q["seconds"])
    blocks = sorted((q.get("blocked") or {}).items(), key=lambda kv: -kv[1])
    out = [f"Quoting {q['quoting'] / tot * 100:.0f}% of {ago(tot)}"
           + "".join(f" · {k} {v / tot * 100:.0f}%" for k, v in blocks[:2] if v / tot >= 0.01)]
    out.append("On the book: " + " · ".join(f"{name} {float(q.get(side) or 0) / tot * 100:.0f}%"
                                            for side, name in (("bid", "buy"), ("ask", "sell"))))
    sides = []
    for side in ("bid", "ask"):
        rest = float(q.get(side) or 0)
        if rest >= 1:
            at = float(q.get(f"{side}_touch") or 0) / rest
            behind = float(q.get(f"{side}_ticks") or 0) / rest
            sides.append(f"{side} {at * 100:.0f}%" + (f" (avg {behind:.1f} ticks behind)" if at < 0.95 else ""))
    if sides:
        out.append("At the best price: " + " · ".join(sides))
    if q.get("refused"):
        out.append(f"⚠️ {q['refused']:,} orders refused by the bot's own checks: {q.get('refused_why', '')[:100]}")
    return out


def _clock(ts: float) -> str:
    """Local time with its zone, e.g. 20:02 IST (UTC when the machine runs on UTC)."""
    t = time.localtime(ts)
    return time.strftime("%H:%M ", t) + (t.tm_zone or "")


def _pct(x: float | None, of: float | None) -> str:
    return f"{x / of * 100:+.2f}%" if x is not None and of else ""


def spark(values: list[float], points: int = SPARK_POINTS) -> str:
    """A one-line chart, e.g. ▁▂▃▅▆▅▇, of at most `points` values (evenly picked, the last one always kept)."""
    if len(values) < 3:
        return ""
    if len(values) > points:
        step = (len(values) - 1) / (points - 1)
        values = [values[round(i * step)] for i in range(points)]
    lo, hi = min(values), max(values)
    if hi - lo < 0.005:
        return SPARK[0] * len(values)
    return "".join(SPARK[min(len(SPARK) - 1, int((x - lo) / (hi - lo) * len(SPARK)))] for x in values)


def _px(x: float) -> str:
    return f"{x:,.2f}" if x >= 10 else f"{x:.6g}"


def _k(x: float) -> str:
    """$22.4k above $10,000, else whole dollars."""
    return f"${x / 1000:,.1f}k" if x >= 10_000 else money(x)


def bar(frac: float, cells: int = BAR_CELLS) -> str:
    """━━━━──────── for a third."""
    n = max(0, min(cells, round(frac * cells)))
    return "━" * n + "─" * (cells - n)


def render(d: Dash, *, html: bool = True, frame: str = "live") -> str:
    """Three cards (today, position, capital) under a title. html=True: Telegram HTML, each card a blockquote; else
    plain text for a terminal, each card behind a bar. frame: "live" (updating), "stopped" (the last frame of a
    Telegram dashboard that stopped updating) or "once" (`bot dashboard --once`)."""
    def b(x: str) -> str:
        return f"<b>{escape(x)}</b>" if html else x

    def e(x: str) -> str:
        return escape(x) if html else x

    def i(x: str) -> str:
        return f"<i>{escape(x)}</i>" if html else x

    def c(x: str) -> str:   # monospace (Telegram draws it in the accent colour): the bar and the sparkline
        return f"<code>{escape(x)}</code>" if html else x

    def card(title: str, rows: list[str]) -> str:
        if html:
            return f"<blockquote><b>{escape(title)}</b>\n" + "\n".join(rows) + "</blockquote>"
        return "\n".join(["│ " + title.upper()] + ["│ " + r for r in rows])

    title = d.market or ("Dashboard" if d.mode is None else "No setup deployed")
    out = [f"📊 {b(title)}" + (f" · {b(d.mode.upper())}" if d.mode else ""),
           f"{d.icon} {e(d.state[:1].upper() + d.state[1:])}" + (f" · up {ago(d.up_s)}" if d.up_s is not None else "")]
    if d.config:
        out.append(i(d.config + (f" · sizing for {usd(d.size_capital, sign=False)}" if d.size_capital else "")))
    out += ["⚠️ " + e(w) for w in d.warnings]
    cards = []

    # today
    rows = [f"Volume {b(money(d.volume))} · {d.fills} fill{'' if d.fills == 1 else 's'}"
            + (e(f" · pace {_k(d.pace_day)}/day") if d.pace_day is not None else "")]
    bt_vol, bt_pnl = d.backtest.get("volume_day"), d.backtest.get("pnl_day")
    if bt_vol:
        bt = float(bt_vol)
        if d.pace_day is not None:
            rows.append(c(bar(d.pace_day / bt)) + e(f" pace {d.pace_day / bt * 100:.0f}% of the {_k(bt)}/day backtest"))
        else:
            rows.append(c(bar(d.maker_volume / bt)) + e(f" {d.maker_volume / bt * 100:.0f}% of the {_k(bt)}/day "
                                                         "backtest so far"))
    rows += [e(x) for x in quote_lines(d.quotes)]
    if d.fees >= 0.005:
        rows.append(e(f"Fees {usd(d.fees, sign=False)} (taker fills)"))
    if d.day_pnl is not None:
        extra = []
        if d.day_pnl_base:
            extra.append(_pct(d.day_pnl, d.day_pnl_base))
        if d.day_pnl < 0 and d.stops.get("daily"):
            extra.append(f"{-d.day_pnl / d.stops['daily'] * 100:.0f}% of day stop")
        elif bt_pnl is not None:
            extra.append(f"backtest {usd(bt_pnl)}/day")
        rows.append(f"PnL {b(usd(d.day_pnl))}" + e("".join(f" · {x}" for x in extra)))
        line = spark(d.day_series)
        if line or d.day_pnl_note:
            rows.append((c(line) if line else "") + (" " if line and d.day_pnl_note else "") + (i(d.day_pnl_note) if d.day_pnl_note
                                                                              else ""))
    else:
        rows.append(e("PnL — no balance reading yet today"))
    cards.append(card("Today", rows))

    # position
    rows = []
    for p in d.positions:
        side = "Long" if p["size"] > 0 else "Short"
        rows.append(f"{e(side)} {b(format(abs(p['size']), '.6g') + ' ' + p['market'])}"
                    + (e(f" ≈ {usd(abs(p['size'] * p['mark']), sign=False)}") if p.get("mark") else ""))
        if p.get("entry") and p.get("mark"):
            upnl = p["size"] * (p["mark"] - p["entry"])
            rows.append(e(f"{_px(p['entry'])} → {_px(p['mark'])} · ") + b(usd(upnl))
                        + (e(f" · stop {usd(-d.stops['position'])}") if d.stops.get("position") else ""))
    if not d.positions:
        rows.append("Flat")
    rows.append(e(f"{d.open_orders} open order{'' if d.open_orders == 1 else 's'}"))
    cards.append(card("Position", rows))

    # capital
    rows = []
    paper = d.mode == "paper"
    if d.equity is not None:
        dep = "" if d.net_deposits is None else f" · {'started with' if paper else 'deposited'} " \
            f"{usd(d.net_deposits, sign=False)}"
        rows.append(f"Equity {b(usd(d.equity, sign=False))}" + e(dep))
    if d.capital_pnl is not None:
        ch = "".join(f" · {k} {usd(x)}" for k, x in d.changes.items())
        rows.append(f"P/L {b(usd(d.capital_pnl))}" + e((f" · {_pct(d.capital_pnl, d.net_deposits)}"
                                                         if d.net_deposits else "") + ch))
    if not rows:
        rows.append(e("No balance reading yet (ARCUS_ADDRESS in .env lets the bot and the scout read it)"))
    cards.append(card("Capital" + (" (paper)" if paper else ""), rows))

    t = time.localtime(d.now)
    stamp = time.strftime("%H:%M:%S ", t) + (t.tm_zone or "")
    age = f" · balance {ago(d.account_age_s)} old" if d.account_age_s is not None else ""
    foot = {"live": f"↻ {stamp} · every {REFRESH_S:.0f} s{age}",
            "stopped": f"⏸ Stopped at {stamp} · tap ▶️ or send /dashboard to update it again"}.get(frame,
                                                                                               f"{stamp}{age}")
    sep = "" if html else "\n"
    return "\n".join(out) + "\n" + sep + (sep + "\n").join(cards) + "\n" + sep + i(foot)
