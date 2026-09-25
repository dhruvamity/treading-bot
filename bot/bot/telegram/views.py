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


def money(x: float | None) -> str:
    """Whole dollars for volumes: $5,369."""
    return "—" if x is None else f"${float(x):,.0f}"


def positions_of(v: ModeView) -> list[dict[str, Any]]:
    """Open positions, one per venue and market: size, mark (the bot's), entry (the positions table). A market the
    status lists twice is shown once; with no status from the runner, the positions table alone."""
    entries: dict[tuple[str, str], tuple[float, float | None]] = {}
    for k, val in v.positions.items():               # "arcus:QQQ" -> "-0.15 @ 745.69"
        venue, _, base = k.partition(":")
        size_s, _, entry_s = val.partition("@")
        try:
            entry = float(entry_s) if entry_s.strip() not in ("", "None") else None
            entries[(venue, base)] = (float(size_s), entry)
        except ValueError:
            continue
    out: dict[tuple[str, str], dict[str, Any]] = {}
    markets = (v.snapshot or {}).get("markets") or []
    for m in markets:
        size = float(m.get("position") or 0)
        key = (str(m.get("venue") or "arcus"), str(m["market"]))
        if size and key not in out:
            out[key] = {"venue": key[0], "market": key[1], "size": size,
                        "mark": float(m["mark"]) if m.get("mark") else None, "entry": entries.get(key, (0, None))[1]}
    if not markets:
        for key, (size, entry) in entries.items():
            if size:
                out[key] = {"venue": key[0], "market": key[1], "size": size, "mark": None, "entry": entry}
    return list(out.values())


def _pos_line(p: dict[str, Any], venue: bool = False) -> str:
    side = "long" if p["size"] > 0 else "short"
    where = f" ({p['venue']})" if venue else ""
    val = f" {usd(abs(p['size'] * p['mark']), sign=False)}" if p.get("mark") else f" {abs(p['size']):.6g}"
    return f"{escape(p['market'])}{where} {side}{val}"


def status_text(views: list[ModeView]) -> str:
    if not views:
        return "No bot has run on this machine yet. Start one with /run &lt;session&gt;."
    parts = []
    for v in views:
        icon, state = state_of(v)
        snap = v.snapshot or {}
        head = f"{icon} <b>{v.mode.upper()}</b> · {escape(state)}"
        if v.running and snap.get("started_us"):
            head += f" · up {ago((snap.get('ts_us', 0) - snap['started_us']) / 1e6)}"
        lines = []
        if v.running and v.snapshot_age_s is not None and v.snapshot_age_s > 30:
            lines.append(f"⚠️ status not updated for {ago(v.snapshot_age_s)}")
        fills = sum(int(d["fills"]) for d in v.today.values())
        vol = sum(d["maker_volume"] for d in v.today.values())
        lines.append(f"Today <b>{usd(day_pnl(v))}</b> · {fills} fills · {money(vol)} volume")
        pos = positions_of(v)
        venues = len({p["venue"] for p in pos}) > 1
        n = len(v.open_orders)
        lines.append((", ".join(_pos_line(p, venues) for p in pos) or "Flat")
                     + f" · {n} open order{'' if n == 1 else 's'}")
        if v.resume_pending:
            lines.append("Resume requested, applies on the next tick")
        parts.append(head + "\n<blockquote>" + "\n".join(lines) + "</blockquote>")
    return "\n".join(parts)


def pnl_text(v: ModeView) -> str:
    snap = v.snapshot or {}
    head = f"<b>PnL — {v.mode.upper()}</b> <i>(since the bot started)</i>"
    cards, tot = [], 0.0
    for m in snap.get("markets", []):
        tot += float(m.get("net") or 0)
        cards.append(f"<blockquote><b>{escape(m['market'])}</b> <b>{usd(m.get('net'))}</b>\n"
                     f"spread {usd(m.get('spread_capture'))} · inventory {usd(m.get('inventory_mtm'))} · "
                     f"fees {usd(m.get('fees'), sign=False)}\n"
                     f"{m.get('fills', 0)} fills · {money(float(m.get('maker_volume') or 0))} maker volume</blockquote>")
    if not cards:
        return head + "\nNo PnL yet (the bot publishes it every 5 s while running)."
    return (head + "\n" + "".join(cards) + f"Since start <b>{usd(tot)}</b> · today <b>{usd(day_pnl(v))}</b>")


def positions_text(v: ModeView) -> str:
    pos = positions_of(v)
    if not pos:
        return f"<b>Positions — {v.mode.upper()}</b>\nFlat."
    cards = []
    for p in pos:
        lines = [f"<b>{escape(p['market'])}</b> {'long' if p['size'] > 0 else 'short'} {abs(p['size']):.6g}"
                 + (f" ≈ {usd(abs(p['size'] * p['mark']), sign=False)}" if p.get("mark") else "")
                 + (f" <i>({escape(p['venue'])})</i>" if p["venue"] != "arcus" else "")]
        if p.get("entry") and p.get("mark"):
            lines.append(f"entry {p['entry']:g} → {p['mark']:g} · <b>{usd(p['size'] * (p['mark'] - p['entry']))}</b>")
        cards.append("<blockquote>" + "\n".join(lines) + "</blockquote>")
    return f"<b>Positions — {v.mode.upper()}</b>\n" + "".join(cards)


def orders_text(v: ModeView, limit: int = 30) -> str:
    if not v.open_orders:
        return f"<b>Open orders — {v.mode.upper()}</b>\nNone."
    by_market: dict[str, list[dict[str, str]]] = {}
    for o in v.open_orders[:limit]:
        by_market.setdefault(o["market"], []).append(o)
    cards = []
    for market, rows in by_market.items():   # asks on top, highest price first, like a book
        rows.sort(key=lambda o: (o["side"].lower() != "sell", -float(o["price"] or 0)))
        cards.append(f"<blockquote><b>{escape(market)}</b>\n" + "\n".join(
            f"{'🔴' if o['side'].lower() == 'sell' else '🟢'} {o['side'].upper()} {float(o['size']):.6g} @ "
            f"{escape(o['price'])}" + (f" · {escape(o['tag'])}" if o["tag"] else "") for o in rows) + "</blockquote>")
    more = f"\n… and {len(v.open_orders) - limit} more" if len(v.open_orders) > limit else ""
    return f"<b>Open orders — {v.mode.upper()}</b> ({len(v.open_orders)})\n" + "".join(cards) + more


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


HELP = """<b>Pick and run</b>
/top3 🟢 breakeven · /volume 🔥 · /aggressive ⚡ · /maxvolume 🚀
/run — any market, setting and leverage
<code>/run BTC touch 0bp max paper</code>
/openpositions — what runs · go LIVE · close

<b>Watch</b>
/dashboard · /status · /balance · /positions · /orders · /pnl · /logs · /yesterdayreport

<b>Control</b>
/pauseneworders · /unpause · /stop · /resumeaftersl
/cancelall · /closeall (<code>taker</code> to cross now)

<b>Settings</b>
/settings · <code>/set name value</code> · /scannow · /alerts · /mute · /unmute
Add <code>paper</code> or <code>live</code> to pick a bot: <code>/status paper</code>"""

# Telegram's "/" list: the everyday commands (everything in HELP still works)
COMMANDS: list[tuple[str, str]] = [
    ("dashboard", "Live screen, every 10 s"),
    ("top3", "Breakeven top 3"), ("volume", "Most volume within your cost"),
    ("aggressive", "Aggressive Mid top 3"), ("maxvolume", "Most volume, any cost"),
    ("run", "Run any market, setting, leverage"), ("openpositions", "What runs · go LIVE · close"),
    ("status", "Running? today's PnL and volume"), ("balance", "Account balance and history"),
    ("positions", "What you hold"), ("orders", "Orders on the book"),
    ("pauseneworders", "Stop new orders"), ("unpause", "Quote again"),
    ("stop", "Shut the bot down (position kept)"), ("closeall", "Close every position"),
    ("cancelall", "Cancel every order"), ("resumeaftersl", "Trade again after a safety stop"),
    ("yesterdayreport", "Yesterday's report"), ("settings", "Settings"),
    ("set", "/set name value"), ("scannow", "Scan now"), ("menu", "Buttons"), ("help", "All commands"),
]


def menu_keyboard() -> Keyboard:
    return [
        [("📺 Live dashboard", "dashboard")],
        [("🟢 Top 3", "top3"), ("🔥 Volume", "volume"), ("⚡ Aggressive", "aggressive")],
        [("🚀 Max volume", "maxvolume"), ("🎯 Any setup", "run")],
        [("▶️ Running", "openpositions"), ("📊 Status", "status"), ("💰 Balance", "balance")],
        [("📦 Positions", "positions"), ("📋 Orders", "orders"), ("⚙️ Settings", "settings")],
        [("⏸ Pause", "pauseneworders"), ("▶️ Unpause", "unpause"), ("🛑 Stop", "stop")],
        [("❌ Cancel all", "cancelall"), ("🧯 Close all", "closeall"), ("🔔 Alerts", "alerts")],
    ]


def refresh_keyboard(cmd: str) -> Keyboard:
    return [[("🔄 Refresh", cmd), ("☰ Menu", "menu")]]


def dashboard_keyboard(live: bool = True) -> Keyboard:
    # only dashboard buttons: any other button would turn this message into its own reply
    return [[("⏹ Stop updating", "dashstop")]] if live else [[("▶️ Update live again", "dashresume")]]


def confirm_keyboard(pid: str) -> Keyboard:
    return [[("✅ Confirm", f"ok {pid}"), ("✖️ Cancel", f"no {pid}")]]


# ------------------------------------------------------------------ scout / pilot
def short(market: str) -> str:
    return market.removesuffix("-USD")


def kusd(x: float | None) -> str:
    """Compact dollars for volumes: $9.3k, $780."""
    if x is None:
        return "—"
    return f"${x / 1000:,.1f}k" if abs(x) >= 1000 else f"${x:,.0f}"


def cost_text(c: dict[str, Any]) -> str:
    from bot.scout.profiles import cost_1k

    x = cost_1k(c)
    return "" if x is None else "profit" if x == 0 else f"${x:.2f}/1k"


def _setting(c: dict[str, Any]) -> str:
    from bot.scout.pilot import setting_of

    return setting_of(c)


def _sizes(c: dict[str, Any]) -> str:
    """Order size and the largest position, and the smaller one outside US hours (RWA perps, 1.5x margin)."""
    if not c.get("order_usd"):
        return ""
    out = f"{money(c['order_usd'])} orders · max position {money(1.25 * c['cap_usd'])}"
    off = float(c.get("cap_off_usd") or c["cap_usd"])
    if off < float(c["cap_usd"]) - 0.01:
        out += f" ({money(1.25 * off)} at {float(c.get('leverage_off') or 0):g}x outside US hours)"
    return out


def card(c: dict[str, Any], i: int | None = None, *, cost: bool = False) -> str:
    """One scan row: title, backtest, sizes; a warning when it rests on under 3 days of data."""
    title = (f"<b>{f'{i} · ' if i else ''}{escape(short(c['market']))} · {escape(_setting(c))} · "
             f"{float(c['leverage']):g}x</b>" + (" <i>max</i>" if c.get("at_max") else ""))
    stats = (f"{kusd(c['volume_day'])}/day · {usd(c['pnl_day'])}/day · worst {usd(c['worst_day'])} · {c['days']}d"
             + (f" · 24h {usd(c['recent_pnl'])}" if c.get("recent_checked", True) and c.get("days") else ""))
    extra = [x for x in (cost_text(c) if cost else "", _sizes(c)) if x]
    lines = [title, stats] + ([" · ".join(extra)] if extra else [])
    if c.get("days", 0) < 3:
        lines.append(f"⚠️ {c.get('days', 0)} day{'' if c.get('days') == 1 else 's'} of data")
    return "<blockquote>" + "\n".join(lines) + "</blockquote>"


def candidate_lines(top: list[dict[str, Any]], *, cost: bool = False) -> str:
    return "".join(card(c, i, cost=cost) for i, c in enumerate(top, 1)) if top else "Nothing passes right now."


def profile_text(scan: dict[str, Any] | None, profile: str, budget: float, now: float, note: str = "") -> str:
    """One list's top 3 (bot/scout/profiles.py) from the last scan."""
    from bot.scout import profiles as P

    p = P.profile_of(profile)
    if not scan:
        return "No scan yet. On the server: <code>bot up</code>."
    top = P.top(scan, p, budget) if p.budget else list(scan.get("top") or [])
    cap = (scan.get("capital") or {}).get("usd")
    head = (f"{p.icon} <b>{p.title}</b> · scan {ago(now - scan['ts_us'] / 1e6)} ago"
            + (f" · {money(float(cap))}" if cap else "")
            + (f" · ≤${budget:.2f}/1k" if p.budget and not p.any_cost else ""))
    if top:
        body = candidate_lines(top, cost=p.budget)
    elif p.budget and not p.any_cost:
        near = P.nearest(scan, p, budget)
        body = f"Nothing within ${budget:.2f} per $1,000."
        if near:
            body += "\nClosest: " + " · ".join(f"{escape(short(c['market']))} {escape(_setting(c))} "
                                               f"{c['leverage']:g}x {cost_text(c)}" for c in near)
            body += f"\n<code>/set volume_cost {max((P.cost_1k(c) or 0) for c in near) + 0.005:.2f}</code>"
    else:
        body = "Nothing passes right now."
        near = [c for c in scan.get("ranked", []) if not c["go"] and c.get("days")][:3]
        if near:
            body += "\nClosest: " + "\n".join(f"· {escape(short(c['market']))} {escape(c['config'])}: "
                                              f"{escape(c['reasons'][0][:80])}" for c in near)
    return head + "\n" + body + (f"\n{note}" if note else "")


def profile_keyboard(profile: str, top: list[dict[str, Any]]) -> Keyboard:
    from bot.scout.profiles import LISTS

    rows: Keyboard = []
    if top:
        rows.append([(f"▶️ {i}", f"pick {profile} {i}") for i in range(1, len(top) + 1)])
    rows.append([(f"{p.icon} {p.title.split()[0]}", f"top3 {p.key}") for p in LISTS if p.key != profile])
    rows.append([("🎯 Any setup", "run"), ("🔄 Scan", f"rescan {profile}"), ("☰ Menu", "menu")])
    return rows


def pick_keyboard(top: list[dict[str, Any]], profile: str = "breakeven") -> Keyboard:
    row = [(f"▶️ {i}", f"pick {profile} {i}") for i in range(1, len(top) + 1)]
    return ([row] if row else []) + [[("▶️ Running", "openpositions"), ("☰ Menu", "menu")]]


# ------------------------------------------------------------------ run any setup: market -> setting -> leverage
def markets_keyboard(scan: dict[str, Any] | None) -> Keyboard:
    """Every scanned market, the most backtested volume first (its best setting at any leverage)."""
    best: dict[str, float] = {}
    for c in (scan or {}).get("all") or []:
        vol = float(c["volume_day"]) if c.get("days") else 0.0
        best[c["market"]] = max(best.get(c["market"], 0.0), vol)
    order = sorted(best, key=lambda m: (-best[m], m))
    buttons = [(f"{short(m)} {kusd(best[m]) if best[m] else '–'}", f"rm {m}") for m in order]
    return [buttons[i:i + 3] for i in range(0, len(buttons), 3)] + [[("☰ Menu", "menu")]]


def settings_keyboard(market: str, rows: list[dict[str, Any]]) -> Keyboard:
    """The menu's settings on one market, each at its highest-volume leverage."""
    from bot.scout.pilot import setting_id

    best: dict[str, dict[str, Any]] = {}
    for c in rows:
        b = best.get(_setting(c))
        if c.get("days") and (b is None or c["volume_day"] > b["volume_day"]):
            best[_setting(c)] = c
    order = sorted(best.values(), key=lambda c: -float(c["volume_day"]))
    out: Keyboard = [[(f"{_setting(c)} · {kusd(c['volume_day'])} · {usd(c['pnl_day'])}",
                       f"rs {market} {setting_id(_setting(c))}")] for c in order]
    return [*out, [("◀️ Markets", "run"), ("☰ Menu", "menu")]]


def lists_of(c: dict[str, Any], budget: float) -> list[Any]:
    """The lists this scan row belongs to (bot/scout/profiles.py)."""
    from bot.scout.profiles import LISTS, verdict

    return [p for p in LISTS if not verdict(c, p, budget)]


def ladder_text(market: str, setting: str, rows: list[dict[str, Any]], budget: float, star: float | None = None
                ) -> str:
    """One setting on one market at every leverage: volume, PnL, worst day, last 24 h, and the lists it is in."""
    lines = []
    for c in rows:
        lev = f"{float(c['leverage']):g}x"
        if c.get("too_small"):
            lines.append(f"{lev:>5}  needs {usd(c.get('min_capital_usd'), sign=False)} of capital")
            continue
        recent = f"{c['recent_pnl']:+.2f}" if c.get("recent_checked", True) and c.get("days") else "–"
        icons = "".join(p.icon for p in lists_of(c, budget))
        mark = "⭐" if star is not None and abs(float(c["leverage"]) - star) < 0.01 else ""
        lines.append(f"{lev:>5} {kusd(c['volume_day']):>7} {c['pnl_day']:>+6.2f} {c['worst_day']:>+6.2f} "
                     f"{recent:>6} {icons}{mark}")
    days = max((int(c.get("days") or 0) for c in rows), default=0)
    head = f"🎯 <b>{escape(short(market))} · {escape(setting)}</b> · {days}d of data"
    table = f"{'lev':>5} {'vol/d':>7} {'pnl/d':>6} {'worst':>6} {'24h':>6}\n" + "\n".join(lines)
    legend = "🟢 breakeven 🔥 volume ⚡ aggressive 🚀 max" + (" · ⭐ list pick" if star is not None else "")
    return f"{head}\n<pre>{escape(table)}</pre>\n<i>{legend}</i>"


def ladder_keyboard(market: str, sid: str, rows: list[dict[str, Any]], profile: str) -> Keyboard:
    buttons = [(f"{float(c['leverage']):g}x" + (" max" if c.get("at_max") else ""),
                f"rl {market} {sid} {float(c['leverage']):g} {profile}") for c in rows if not c.get("too_small")]
    return [buttons[i:i + 3] for i in range(0, len(buttons), 3)] + [[("◀️ Settings", f"rm {market}"),
                                                                        ("☰ Menu", "menu")]]


def run_text(c: dict[str, Any], budget: float, profile: str, running: list[str], live_ok: bool) -> str:
    """The run screen: sizes (in and outside US hours), the backtest, the lists it is in, what it replaces."""
    from bot.scout.profiles import profile_of

    ins = lists_of(c, budget)
    lines = [x for x in (_sizes(c),
                         f"Backtest {kusd(c['volume_day'])}/day · {usd(c['pnl_day'])}/day · worst "
                         f"{usd(c['worst_day'])} · {c['days']}d",
                         (f"Last 24 h {usd(c['recent_pnl'])}" if c.get("recent_checked", True) else "Last 24 h not "
                          "re-checked") + (f" · {cost_text(c)}" if cost_text(c) else "")) if x]
    p = profile_of(profile)
    notes = ["In " + ", ".join(f"{x.icon} {x.title}" for x in ins) if ins else "In no list"]
    notes.append(f"Runs as {p.icon} {p.title}" + ("" if p.listed else ": the scout never pauses it for its numbers"))
    if c.get("reasons"):
        notes.append("⚠️ " + escape("; ".join(c["reasons"][:2])[:200]))
    if running:
        notes.append(f"Replaces the running {' and '.join(running)} bot (closes its position first).")
    if not live_ok:
        notes.append("LIVE is off on this server (BOT_PILOT_LIVE=1 in .env).")
    title = (f"🎯 <b>{escape(short(c['market']))} · {escape(_setting(c))} · {float(c['leverage']):g}x</b>"
             + (" <i>max</i>" if c.get("at_max") else ""))
    return title + "\n<blockquote>" + "\n".join(lines) + "</blockquote>\n" + "\n".join(notes)


def run_keyboard(market: str, sid: str, lev: float, profile: str, live_ok: bool) -> Keyboard:
    base = f"rd {market} {sid} {lev:g} {profile}"
    row = [("📝 Paper", f"{base} paper")] + ([("🔴 LIVE", f"{base} live")] if live_ok else [])
    return [row, [("◀️ Leverage", f"rs {market} {sid}"), ("☰ Menu", "menu")]]


def pilot_text(pilot: Any) -> str:
    """/openpositions: what is deployed, how it does today against its backtest, the last check."""
    from bot.scout.profiles import profile_of

    st = pilot.state()
    a = st.get("active")
    if not a:
        return "Nothing deployed · /top3 · /run"
    running = pilot.control.is_running(a["mode"])
    view = pilot.control.view(a["mode"]) if running else None
    rev = st.get("last_review") or {}
    bt = a.get("backtest") or {}
    prof = profile_of(a.get("profile"))
    state = ("⏸ paused by the scout: " + escape(st["paused_by_scout"])) if st.get("paused_by_scout") else \
        ("running" if running else "⚠️ NOT running")
    since = dt.datetime.fromtimestamp(a["since"], dt.UTC).strftime("%m-%d %H:%M UTC")
    vol = sum(d["maker_volume"] for d in view.today.values()) if view is not None else None
    if not prof.listed:
        check = "your pick: not judged"
    elif rev.get("go"):
        check = f"✅ still in the list ({ago(time.time() - rev['ts'])} ago)"
    else:
        check = "⚠️ " + escape("; ".join(rev.get("reasons") or [])[:160]) if rev else "not checked yet"
    from bot.telegram.dashboard import quote_lines

    q = next((x.get("quotes") for x in ((view.snapshot or {}).get("sessions") or []) if x.get("quotes")), None) \
        if view is not None else None
    lines = [f"{state} · {prof.icon} {prof.title}{' · max lev' if a.get('lev') == 'max' else ''} · since {since}",
             f"Today {usd(day_pnl(view) if view is not None else None)} · {kusd(vol)} volume",
             *[escape(x) for x in quote_lines(q)],
             f"Backtest {kusd(bt.get('volume_day'))}/day ({bt.get('fills_day', 0) / 24:.1f} fills/hour) · "
             f"{usd(bt.get('pnl_day'))}/day", check]
    return (f"▶️ <b>{escape(short(a['market']))} · {escape(a['config'])}</b> · {a['mode'].upper()}\n<blockquote>"
            + "\n".join(lines) + "</blockquote>")


def pilot_keyboard(paper: bool = False) -> Keyboard:
    rows: Keyboard = [[("🔴 Go LIVE with this setup", "golive")]] if paper else []
    return [*rows, [("⏹ Close & stop", "pilotclose"), ("🔄", "openpositions"), ("☰ Menu", "menu")]]


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

    rows = ["⚙️ <b>Settings</b> · <code>/set name value</code> · <code>/set name default</code>"]
    for name, s in SETTINGS.items():
        cur = over.get(name, defaults.get(name))
        rows.append(f"<b>{name}</b> {escape(show(name, cur))}{' ✏️' if name in over else ''} — "
                    f"<i>{escape(s.help.split(':')[0].split(';')[0].split(' (')[0])}</i>")
    return "\n".join(rows) + "\n✏️ changed · LIVE on/off stays in .env (BOT_PILOT_LIVE)"
