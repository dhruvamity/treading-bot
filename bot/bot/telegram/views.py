"""Message text and keyboards for the Telegram bot. Pure functions of ModeView / dicts, so they are easy to test and
easy to change. Output is Telegram HTML (only <b>, <i>, <code>, <pre>)."""

from __future__ import annotations

import datetime as dt
import time
from html import escape
from typing import Any

from bot.telegram.api import Keyboard
from bot.telegram.control import ModeView

GREEN, YELLOW, RED, WHITE = "🟢", "🟡", "🔴", "⚪"


def usd(x: float | str | None, sign: bool = True) -> str:
    if x is None:
        return "—"
    v = float(x)
    s = "+" if sign and v > 0 else ("-" if v < 0 else "")
    return f"{s}${abs(v):,.2f}"


def ago(seconds: float | None) -> str:
    if seconds is None:
        return "never"
    s = max(0, int(seconds))
    if s < 90:
        return f"{s}s"
    if s < 5400:
        return f"{s // 60}m"
    if s < 172800:
        return f"{s // 3600}h{(s % 3600) // 60:02d}m"
    return f"{s // 86400}d"


def _risk(v: ModeView) -> list[str]:
    snap = v.snapshot or {}
    r = snap.get("risk") or {}
    out = []
    if r.get("all_stopped"):
        out.append(f"STOPPED: {r['all_stopped']}")
    for venue, why in (r.get("safe_mode") or {}).items():
        out.append(f"SAFE MODE {venue}: {why}")
    for venue, day in (r.get("venue_stopped_day") or {}).items():
        out.append(f"daily loss stop on {venue} ({day})")
    return out


def state_of(v: ModeView) -> tuple[str, str]:
    """(icon, one-line state) for a mode."""
    if not v.running:
        if v.heartbeat_age_s is None:
            return WHITE, "not running"
        return WHITE, f"stopped (last heartbeat {ago(v.heartbeat_age_s)} ago)"
    risk = _risk(v)
    if risk:
        return RED, "running, " + "; ".join(risk)
    if v.paused:
        return YELLOW, "running, paused: " + ", ".join(sorted(v.paused))
    snap = v.snapshot or {}
    blocked = [m for m in snap.get("markets", []) if not m.get("quoting") and m.get("why")]
    if blocked:
        return YELLOW, "running, not quoting: " + "; ".join(f"{m['market']} ({m['why']})" for m in blocked)
    return GREEN, "running"


def day_pnl(v: ModeView) -> float | None:
    vals = [float(s["day_pnl"]) for s in (v.snapshot or {}).get("sessions", []) if s.get("day_pnl") is not None]
    return sum(vals) if vals else None


def status_text(views: list[ModeView]) -> str:
    if not views:
        return "No bot has run on this machine yet. Start one with /run &lt;session&gt;."
    parts = []
    for v in views:
        icon, state = state_of(v)
        snap = v.snapshot or {}
        head = f"{icon} <b>{v.mode.upper()}</b> — {escape(state)}"
        if v.running and snap.get("started_us"):
            head += f"\nup {ago((snap.get('ts_us', 0) - snap['started_us']) / 1e6)} · pid {v.pid}"
        lines = [head]
        if v.running and v.snapshot_age_s is not None and v.snapshot_age_s > 30:
            lines.append(f"⚠️ status not updated for {ago(v.snapshot_age_s)}")
        fills = sum(int(d["fills"]) for d in v.today.values())
        vol = sum(d["maker_volume"] for d in v.today.values())
        dp = day_pnl(v)
        lines.append(f"Today (UTC): PnL <b>{usd(dp)}</b> · {fills} fills · {usd(vol, sign=False)} maker volume")
        rows = []
        for m in snap.get("markets", []):
            t = v.today.get(m["market"], {})
            pos = float(m.get("position") or 0)
            mark = float(m["mark"]) if m.get("mark") else 0.0
            q = "✓" if m.get("quoting") else "✗"
            rows.append(f"{m['market']:<6} pos {usd(pos * mark, sign=True):>9} fills {int(t.get('fills', 0)):>4} "
                        f"vol {usd(t.get('maker_volume', 0.0), sign=False):>9} {q}")
        if rows:
            lines.append("<pre>" + escape("\n".join(rows)) + "</pre>")
        if v.open_orders:
            lines.append(f"Open orders: {len(v.open_orders)}")
        if v.resume_pending:
            lines.append("Resume requested, applies on the next tick")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def pnl_text(v: ModeView) -> str:
    snap = v.snapshot or {}
    head = f"<b>PnL — {v.mode.upper()}</b> (since the bot started; today's fills from the database)"
    rows = [f"{'mkt':<6} {'net':>9} {'spread':>8} {'inv':>8} {'fees':>7} {'fills':>6} {'volume':>10}"]
    tot = 0.0
    for m in snap.get("markets", []):
        tot += float(m.get("net") or 0)
        rows.append(f"{m['market']:<6} {usd(m.get('net')):>9} {usd(m.get('spread_capture')):>8} "
                    f"{usd(m.get('inventory_mtm')):>8} {usd(m.get('fees'), sign=False):>7} {m.get('fills', 0):>6} "
                    f"{usd(m.get('maker_volume'), sign=False):>10}")
    if len(rows) == 1:
        return head + "\nNo PnL yet (the bot publishes it every 5 s while running)."
    dp = day_pnl(v)
    sess = [f"{escape(s['session'])}: day {usd(s.get('day_pnl'))}, since start {usd(s.get('pnl'))}"
            for s in snap.get("sessions", [])]
    return (f"{head}\n<pre>{escape(chr(10).join(rows))}</pre>\nTotal since start: <b>{usd(tot)}</b> · "
            f"today: <b>{usd(dp)}</b>\n" + "\n".join(sess))


def positions_text(v: ModeView) -> str:
    snap = v.snapshot or {}
    rows = []
    for m in snap.get("markets", []):
        pos = float(m.get("position") or 0)
        if pos == 0:
            continue
        mark = float(m["mark"]) if m.get("mark") else 0.0
        rows.append(f"{m['venue']}:{m['market']:<6} {pos:+.6g} ≈ {usd(pos * mark)} @ {m.get('mark') or '?'}")
    if not rows and v.positions:  # runner not publishing: fall back to the positions table
        rows = [f"{k:<16} {val}" for k, val in v.positions.items()]
    if not rows:
        return f"<b>Positions — {v.mode.upper()}</b>\nFlat."
    return f"<b>Positions — {v.mode.upper()}</b>\n<pre>{escape(chr(10).join(rows))}</pre>"


def orders_text(v: ModeView, limit: int = 30) -> str:
    if not v.open_orders:
        return f"<b>Open orders — {v.mode.upper()}</b>\nNone."
    rows = [f"{o['market']:<6} {o['side']:<4} {o['size']:>12} @ {o['price']:<12} {o['tag']}" for o in v.open_orders[:limit]]
    more = f"\n… and {len(v.open_orders) - limit} more" if len(v.open_orders) > limit else ""
    return f"<b>Open orders — {v.mode.upper()}</b> ({len(v.open_orders)})\n<pre>{escape(chr(10).join(rows))}</pre>{more}"


def sessions_text(sessions: list[dict[str, Any]], runs: list[dict[str, Any]]) -> str:
    rows = []
    for s in sessions:
        if "error" in s:
            rows.append(f"{s['name']}: ⚠️ {s['error']}")
            continue
        live = "live OK" if s.get("live_enabled") else "paper only"
        if s.get("kind") == "mm":
            rows.append(f"{s['name']}: {s['market']} {s['mode']} · ${s.get('order_usd')} orders, "
                        f"${s.get('cap_usd')} cap · sub {s.get('account')} · {live}")
        else:
            rows.append(f"{s['name']}: {s['market']} {s['mode']} (delta-neutral) · {live}")
    txt = "<b>Sessions</b> (config/sessions)\n" + escape("\n".join(rows) or "none")
    alive = [r for r in runs if r.get("alive")]
    if alive:
        txt += "\n\n<b>Started from Telegram</b>\n" + escape("\n".join(
            f"{r['name']} ({'LIVE' if r['live'] else 'paper'}) pid {r['pid']}" for r in alive))
    txt += "\n\nStart one: /run &lt;name&gt; (paper) or /run &lt;name&gt; live"
    return txt


def fill_line(f: dict[str, Any]) -> str:
    t = dt.datetime.fromtimestamp(f["ts_us"] / 1e6, dt.UTC).strftime("%H:%M:%S")
    return (f"{t} {f['market']} {f['side'].upper()} {f['size']:.6g} @ {f['price']:.6g} "
            f"({usd(f['price'] * f['size'], sign=False)}{'' if f['maker'] else ', TAKER'})")


HELP = """<b>What you can do</b> (or tap /menu)

<b>Choose and start</b>
/top3 — the 3 best setups right now; tap Run → Paper or LIVE
/openpositions — what is running, today vs its backtest; Close & stop

<b>Check</b>
/dashboard — live screen, updates itself every 10 s: today's volume and PnL, your capital's profit or loss
/status — running? today's profit, fills, volume
/balance — money in the account, deposits vs trading profit, change over time
/positions · /orders — what you hold, what is waiting on the book
/yesterdayreport — yesterday's full report

<b>Control</b>
/pauseneworders — stop new orders (closing orders keep working) · /unpause
/stop — shut the bot down (orders cancelled, position kept)
/resumeaftersl — trade again after a safety stop, once you know why

<b>Emergency</b> (asks you to confirm)
/cancelall — cancel every order · /closeall — close every position (<code>/closeall taker</code>: at once)

<b>Settings</b>
/settings — what you can change · /set &lt;name&gt; &lt;value&gt;, e.g. <code>/set trade_share 50</code>
/scannow — look for setups now

<b>More</b>
/pnl (profit by market) · /logs (the bot's recent decisions) · /sessions · /run &lt;session&gt; · /doctor
&lt;session&gt; · /alerts · /mute [minutes] · /unmute
Add paper or live to pick a bot, e.g. <code>/status paper</code>. Old names (/scout, /pilot, /pause, /flatten…) still
work."""

# Telegram's "/" list: the everyday commands (everything in HELP still works)
COMMANDS: list[tuple[str, str]] = [
    ("dashboard", "Live screen: today's volume, PnL, capital (every 10 s)"),
    ("top3", "Best 3 setups now, with Run buttons"), ("openpositions", "What is running and how it is doing"),
    ("status", "Running? today's profit, fills, volume"), ("balance", "Money in the account and its history"),
    ("positions", "What you hold"), ("orders", "Orders waiting on the book"),
    ("pauseneworders", "Stop new orders (closing orders keep working)"), ("unpause", "Place orders again"),
    ("stop", "Shut the bot down (position kept)"), ("closeall", "Close every position (confirm)"),
    ("cancelall", "Cancel every order (confirm)"), ("resumeaftersl", "Trade again after a safety stop"),
    ("yesterdayreport", "Yesterday's report"), ("settings", "What you can change"),
    ("set", "Change a setting: /set name value"), ("scannow", "Look for setups now"), ("menu", "Buttons"),
    ("help", "All commands"),
]


def menu_keyboard() -> Keyboard:
    return [
        [("📺 Live dashboard", "dashboard")],
        [("🏆 Top 3 now", "top3"), ("▶️ Running now", "openpositions")],
        [("📊 Status", "status"), ("💰 Balance", "balance")],
        [("📦 Positions", "positions"), ("📋 Orders", "orders")],
        [("⏸ Pause new orders", "pauseneworders"), ("▶️ Unpause", "unpause")],
        [("⚙️ Settings", "settings"), ("🔎 Scan now", "scannow")],
        [("❌ Cancel all orders", "cancelall"), ("🧯 Close all positions", "closeall")],
        [("🛑 Stop bot", "stop"), ("🔔 Alerts", "alerts")],
    ]


def refresh_keyboard(cmd: str) -> Keyboard:
    return [[("🔄 Refresh", cmd), ("☰ Menu", "menu")]]


def dashboard_keyboard(live: bool = True) -> Keyboard:
    # only dashboard buttons: any other button would turn this message into its own reply
    return [[("⏹ Stop updating", "dashstop")]] if live else [[("▶️ Update live again", "dashresume")]]


def confirm_keyboard(pid: str) -> Keyboard:
    return [[("✅ Confirm", f"ok {pid}"), ("✖️ Cancel", f"no {pid}")]]


# ------------------------------------------------------------------ scout / pilot
def candidate_lines(top: list[dict[str, Any]]) -> str:
    if not top:
        return "Nothing passes all checks right now."
    rows = []
    for i, c in enumerate(top, 1):
        uses = (f" · uses {usd(c['used_usd'], sign=False)} of {usd(c['capital_usd'], sign=False)} (liquidity)"
                if c.get("used_usd") and c.get("capital_usd") and c["used_usd"] < c["capital_usd"] - 0.01 else "")
        size = (f"   {usd(c['order_usd'], sign=False)} orders · max position {usd(1.25 * c['cap_usd'], sign=False)}"
                f"{' (max leverage)' if c.get('at_max') else ''}{uses}\n") if c.get("order_usd") else ""
        rows.append(f"<b>{i}. {escape(c['market'])}</b> — {escape(c['config'])}\n{size}"
                    f"   {c['fills_day']:.0f} fills/day · {usd(c['volume_day'], sign=False)} maker volume/day\n"
                    f"   PnL {usd(c['pnl_day'])}/day (worst day {usd(c['worst_day'])}, {c['days']} days) · "
                    f"last 24 h {usd(c['recent_pnl'])}")
    return "\n".join(rows)


def pick_keyboard(top: list[dict[str, Any]]) -> Keyboard:
    row = [(f"Run #{i}", f"pick {i}") for i in range(1, len(top) + 1)]
    return [row, [("Deployed", "openpositions"), ("☰ Menu", "menu")]] if row else [[("☰ Menu", "menu")]]


def scout_text(scan: dict[str, Any] | None, now: float) -> str:
    if not scan:
        return "No scan yet. Start the scout on the server: <code>bot scout run</code> (it scans every 30 min)."
    age = now - scan["ts_us"] / 1e6
    r = scan.get("risk", {})
    lev = scan.get("leverage")
    cap = scan.get("capital") or {"usd": r.get("capital_usd", 100), "source": "fixed",
                                  "pct": {"position_stop": 1, "daily_stop": 2, "kill": 10}}
    p = cap["pct"]
    sizing = (f"sizes from each market's leverage ({escape(lev['policy'])}; BTC/ETH at most 20x)" if lev else
              f"${r.get('order_usd', 25):g} orders, ${r.get('cap_usd', 50):g} max position")
    head = (f"<b>Best setups right now</b> (scan {ago(age)} ago, {scan.get('markets')} markets × "
            f"{scan.get('configs')} settings)\n"
            f"Backtested at ${cap['usd']:,.2f} of capital ({escape(str(cap['source']))}): {sizing}; stops "
            f"{p['position_stop']:g}% position, {p['daily_stop']:g}% day, {p['kill']:g}% kill.\n\n")
    body = candidate_lines(scan.get("top") or [])
    near = [c for c in scan.get("ranked", []) if not c["go"] and c.get("days")][:3]
    if near:
        body += "\n\n<i>Closest that failed:</i>\n" + "\n".join(
            f"· {escape(c['market'])} {escape(c['config'])}: {escape('; '.join(c['reasons'])[:120])}" for c in near)
    at_max = [c for c in scan.get("at_max", []) if c.get("days")][:3]
    if at_max:
        body += "\n\n<i>At maximum leverage:</i>\n" + "\n".join(
            f"· {escape(c['market'])} {escape(c['config'])}: {usd(c['volume_day'], sign=False)}/day, "
            f"PnL {usd(c['pnl_day'])}/day · {'GO' if c['go'] else escape('; '.join(c['reasons'])[:90])}"
            for c in at_max)
    return head + body


def pilot_text(pilot: Any) -> str:
    st = pilot.state()
    a = st.get("active")
    if not a:
        return "<b>Deployed:</b> nothing. /top3 to pick one."
    running = pilot.control.is_running(a["mode"])
    rev = st.get("last_review") or {}
    state = ("paused by the scout: " + escape(st["paused_by_scout"])) if st.get("paused_by_scout") else \
        ("running" if running else "NOT running")
    since = dt.datetime.fromtimestamp(a["since"], dt.UTC).strftime("%m-%d %H:%M")
    bt = a.get("backtest") or {}
    view = pilot.control.view(a["mode"]) if running else None
    today = day_pnl(view) if view is not None else None
    checks = "passing" if rev.get("go") else "failing: " + escape("; ".join(rev.get("reasons") or [])[:200])
    return (f"<b>Deployed:</b> {escape(a['market'])} — {escape(a['config'])} ({a['mode'].upper()}), since {since} UTC\n"
            f"State: {state}\n"
            f"Today: {usd(today)} · backtest expected {usd(bt.get('pnl_day'))}/day, "
            f"{usd(bt.get('volume_day'), sign=False)} volume/day\n"
            f"Last check ({ago(time.time() - rev['ts']) if rev else 'never'} ago): {checks}")


def pilot_keyboard() -> Keyboard:
    return [[("Top 3 now", "top3"), ("Close & stop", "pilotclose")], [("🔄 Refresh", "openpositions"), ("☰ Menu", "menu")]]


# ------------------------------------------------------------------ balance and settings
def balance_text(snap: dict[str, float] | None, summary: dict[str, Any], held: dict[str, Any] | None,
                 live_capital: str | None, account: int) -> str:
    """/balance: the account now (or the last reading), deposits vs trading PnL, the change over 1/7/30 days, and the
    capital the scout and the live bot size for."""
    last = summary.get("latest") or {}
    if snap and snap.get("equity"):
        head = f"<b>💰 Balance</b> (subaccount {account}, read now)"
        eq, free, nd = snap["equity"], snap.get("free"), snap.get("net_deposits")
    elif last:
        when = dt.datetime.fromtimestamp(last["ts"], dt.UTC).strftime("%m-%d %H:%M")
        head = f"<b>💰 Balance</b> (subaccount {account}; could not read it now, last reading {when} UTC)"
        eq, free, nd = last["equity"], last.get("free"), last.get("net_deposits")
    else:
        return ("<b>💰 Balance</b>: no reading yet. The account could not be read (is ARCUS_ADDRESS in .env, and has "
                "it been funded?), and no balance has been logged.")
    lines = [head, f"Equity <b>{usd(eq, sign=False)}</b>" + (f" · free {usd(free, sign=False)}" if free else "")]
    if nd is not None:
        lines.append(f"Deposited, net of withdrawals {usd(nd, sign=False)} → trading PnL <b>{usd(eq - nd)}</b>")
    changes = []
    for key, name in (("1d", "24 h"), ("7d", "7 d"), ("30d", "30 d")):
        c = summary.get(key)
        if c:
            t = f"{name} {usd(c['equity_change'])}"
            if c.get("pnl_change") is not None and abs(c["pnl_change"] - c["equity_change"]) > 0.005:
                t += f" (trading {usd(c['pnl_change'])})"
            changes.append(t)
    if changes:
        lines.append("Change: " + " · ".join(changes))
    if held:
        since = dt.datetime.fromtimestamp(held.get("ts", 0), dt.UTC).strftime("%m-%d %H:%M")
        lines.append(f"Scans size for {usd(held['usd'], sign=False)} ({escape(str(held.get('source')))}, since "
                     f"{since} UTC); it follows the balance once it moves 25% or more, at most once a day")
    if live_capital:
        lines.append(f"The live bot sizes for {usd(float(live_capital), sign=False)} (re-read at 00:00 UTC)")
    lines.append(f"<i>{summary.get('rows_30d', 0)} readings in the last 30 days (state/balances.jsonl)</i>")
    return "\n".join(lines)


def settings_text(over: dict[str, Any], defaults: dict[str, Any]) -> str:
    from bot.common.settings import SETTINGS, show

    rows = ["<b>⚙️ Settings</b> — change with <code>/set name value</code>, undo with <code>/set name default</code>"]
    for name, s in SETTINGS.items():
        cur = over.get(name, defaults.get(name))
        mark = " <i>(changed)</i>" if name in over else ""
        rows.append(f"\n<b>{name}</b> = {escape(show(name, cur))}{mark}\n{escape(s.help)}. "
                    f"<i>Applies: {escape(s.applies)}.</i>")
    rows.append("\nLive trading itself stays switched on or off in .env on the server (BOT_PILOT_LIVE), not here.")
    return "\n".join(rows)
