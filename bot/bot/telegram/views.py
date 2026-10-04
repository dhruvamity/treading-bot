"""Message text and keyboards for the Telegram bot. Pure functions of ModeView / dicts, so they are easy to test and
easy to change. Every message follows the owner's templates (bot/common/tgfmt.py): one emoji and a bold title, a blank
line, then short monospace lines in groups and bold labels over sections. Output is Telegram HTML."""

from __future__ import annotations

import datetime as dt
import time
from html import escape
from typing import Any

from bot.common.tgfmt import b, card, code, codes, section
from bot.telegram.api import Keyboard
from bot.telegram.control import ModeView, pause_where


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


def mode_state(v: ModeView, healthy: str = "RUNNING") -> tuple[str, str, list[str]]:
    """(emoji, STATE, the plain lines that explain it) for a mode: the title word of /status and the dashboard."""
    if not v.running:
        if v.heartbeat_age_s is None:
            return "⚪", "NOT RUNNING", []
        if v.stopped:
            return "⏹", "STOPPED", [f"Stopped {ago(v.heartbeat_age_s)} ago"]
        return "🔴", "DOWN", [f"Heartbeat lost · {ago(v.heartbeat_age_s)}", "Guardian / dead-man switch protecting orders"]
    snap = v.snapshot or {}
    r = snap.get("risk") or {}
    if r.get("all_stopped"):
        why = str(r["all_stopped"])
        if why.startswith("this run is done"):
            return "✅", "RUN DONE", [why[:1].upper() + why[1:], "Start a new run: /run"]
        run = why.startswith("this run")
        return "🛑", "RUN STOP" if run else "KILL STOP", [why[:1].upper() + why[1:],
                                                          "Start a new run: /run" if run else "/resumeaftersl"]
    for venue, why in (r.get("safe_mode") or {}).items():
        return "🚨", "SAFE MODE", [f"{venue} · {why}", "/resumeaftersl once checked"]
    if r.get("venue_stopped_day"):
        return "🛑", "DAILY STOP", ["Trading stopped for today", "/resumeaftersl to trade again"]
    bases = {str(x.get("market")) for x in snap.get("sessions") or [] if x.get("market")}
    paused = {k: w for k, w in v.paused.items() if k == "*" or not bases or k in bases}   # the ones that hold it
    if paused:
        who = "the scout" if all(str(w).startswith("scout") for w in paused.values()) else "you"
        return "⏸", "PAUSED", [f"New orders paused by {who}: {pause_where(paused)}", "/unpause"]
    blocked = [m for m in snap.get("markets", []) if not m.get("quoting") and m.get("why")]
    if blocked:
        return "🟡", "NOT QUOTING", [f"{m['market']}: {m['why']}" for m in blocked]
    return "🟢", healthy, []


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


def position_line(p: dict[str, Any]) -> str:
    """"QQQ short $112.09" (or its size when there is no mark)."""
    side = "long" if p["size"] > 0 else "short"
    val = usd(abs(p["size"] * p["mark"]), sign=False) if p.get("mark") else f"{abs(p['size']):.6g}"
    return f"{p['market']} {side} {val}"


def _stop(v: ModeView, kind: str) -> float:
    return sum(float((s.get("stops") or {}).get(kind) or 0) for s in (v.snapshot or {}).get("sessions") or [])


def _primary(views: list[ModeView]) -> ModeView:
    return next((v for v in views if v.running and v.mode == "live"), None) or \
        next((v for v in views if v.running), None) or views[0]


def status_text(views: list[ModeView], running: list[str] | None = None) -> str:
    """/status: the main bot's health in the title (running live first), today, and the other bot's state."""
    if not views:
        return card("⚪", "NO BOT YET", codes("No bot has run on this machine yet", "/run to start one"))
    v = _primary(views)
    emoji, state, why = mode_state(v, "HEALTHY")
    name = v.mode.upper()
    title = f"{name} {state}" if state in ("DOWN", "STOPPED") else f"{name} · {state}"
    if not v.running:
        return card(emoji, title, *[codes(x) for x in why], *_others(views, v), running)
    snap = v.snapshot or {}
    n = len(v.open_orders)
    up = f" · up {ago((snap.get('ts_us', 0) - snap['started_us']) / 1e6)}" if snap.get("started_us") else ""
    health = codes(f"Heartbeat {ago(v.heartbeat_age_s)} ago{up}",
                   f"Quotes {'active' if state == 'HEALTHY' else 'off'} · {n} open order{'' if n == 1 else 's'}",
                   f"Status not updated for {ago(v.snapshot_age_s)}" if v.snapshot_age_s and v.snapshot_age_s > 30
                   else "", "Resume requested, applies on the next tick" if v.resume_pending else "", *why)
    fills = sum(int(d["fills"]) for d in v.today.values())
    vol = sum(d["maker_volume"] for d in v.today.values())
    pos = positions_of(v)
    today = section("Today", codes(f"PnL {usd(day_pnl(v))} · {fills} fills · {money(vol)} volume",
                                   " · ".join(position_line(p) for p in pos) or "Flat"))
    return card(emoji, title, health, today, running, *_others(views, v))


def _others(views: list[ModeView], main: ModeView) -> list[list[str]]:
    out = []
    for o in views:
        if o is main:
            continue
        _e, st, why = mode_state(o, "RUNNING")
        out.append(section(o.mode.title(), codes(st.title() + (f" · {why[0]}" if why else ""))))
    return out


def pnl_text(v: ModeView) -> str:
    snap = v.snapshot or {}
    title = f"{v.mode.upper()} PNL"
    blocks, tot = [], 0.0
    for m in snap.get("markets", []):
        tot += float(m.get("net") or 0)
        blocks.append(section(str(m["market"]), codes(
            f"Net {usd(m.get('net'))} · spread {usd(m.get('spread_capture'))} · inventory {usd(m.get('inventory_mtm'))}",
            f"Fees {usd(m.get('fees'), sign=False)} · {m.get('fills', 0)} fills · "
            f"{money(float(m.get('maker_volume') or 0))} maker")))
    if not blocks:
        return card("💵", title, codes("No PnL yet", "The bot publishes it every 5 s while running"))
    return card("💵", title, codes(f"Since start {usd(tot)} · today {usd(day_pnl(v))}"), *blocks)


def positions_text(v: ModeView, running: list[str] | None = None) -> str:
    """/openpositions and /positions (the owner's template): each position, its stop, the open orders; with
    `running`, the deployed setup under them."""
    pos = positions_of(v)
    n = len(v.open_orders)
    title = "OPEN POSITIONS" + (" · PAPER" if v.mode == "paper" else "")
    if not pos:
        return card("📌", title, codes("None"), codes(f"0 positions · {n} order{'' if n == 1 else 's'}"), running)
    stop = _stop(v, "position") if v.running else 0.0
    blocks = []
    for p in pos:
        worth = [f"Notional {money(abs(p['size'] * p['mark']))}" if p.get("mark") else "",
                 f"PnL {usd(p['size'] * (p['mark'] - p['entry']))}" if p.get("entry") and p.get("mark") else ""]
        blocks.append(codes(f"{p['market']}  {'LONG' if p['size'] > 0 else 'SHORT'}  {abs(p['size']):.6g}",
                            " · ".join(x for x in worth if x), f"Stop -${stop:,.2f}" if stop else ""))
    return card("📌", title, *blocks, f"{b('Orders:')} {n}", running)


def orders_text(v: ModeView, limit: int = 30) -> str:
    title = "OPEN ORDERS" + (" · PAPER" if v.mode == "paper" else "")
    if not v.open_orders:
        return card("📋", title, codes("None"))
    by_market: dict[str, list[dict[str, str]]] = {}
    for o in v.open_orders[:limit]:
        by_market.setdefault(o["market"], []).append(o)
    blocks = []
    for market, rows in by_market.items():   # asks on top, highest price first, like a book
        rows.sort(key=lambda o: (o["side"].lower() != "sell", -float(o["price"] or 0)))
        blocks.append(section(market, codes(*(f"{o['side'].upper():<4} {float(o['size']):.6g} @ {o['price']}"
                                               + (f" · {o['tag']}" if o["tag"] else "") for o in rows))))
    more = codes(f"… and {len(v.open_orders) - limit} more") if len(v.open_orders) > limit else None
    return card("📋", f"{title} ({len(v.open_orders)})", *blocks, more)


def sessions_text(sessions: list[dict[str, Any]], runs: list[dict[str, Any]]) -> str:
    rows = []
    for s in sessions:
        if "error" in s:
            rows.append(f"{s['name']}: {s['error']}")
            continue
        live = "live OK" if s.get("live_enabled") else "paper only"
        if s.get("kind") == "mm":
            rows.append(f"{s['name']} · {s['market']} {s['mode']} · ${s.get('order_usd')} orders · "
                        f"${s.get('cap_usd')} cap · sub {s.get('account')} · {live}")
        else:
            rows.append(f"{s['name']} · {s['market']} {s['mode']} (delta-neutral) · {live}")
    alive = [r for r in runs if r.get("alive")]
    return card("🗂", "SESSIONS", codes(*rows) or codes("None in config/sessions"),
                section("Started from Telegram", codes(*(f"{r['name']} ({'LIVE' if r['live'] else 'paper'}) · "
                                                         f"pid {r['pid']}" for r in alive))),
                codes("/run <name> · /run <name> live"))


def fill_line(f: dict[str, Any]) -> str:
    t = dt.datetime.fromtimestamp(f["ts_us"] / 1e6, dt.UTC).strftime("%H:%M:%S")
    return (f"{t} {f['market']} {f['side'].upper()} {f['size']:.6g} @ {f['price']:.6g} · "
            f"{usd(f['price'] * f['size'], sign=False)}{'' if f['maker'] else ' · taker'}")


def doctor_checks(rep: str, level: str) -> list[tuple[str, str, str]]:
    """(area, message, fix) of each check at `level` ("FAIL", "WARN") in a `bot doctor` report."""
    out: list[list[str]] = []
    cur: list[str] | None = None
    for ln in rep.splitlines():
        if ln.startswith("["):
            tag, _, rest = ln[1:].partition("] ")
            cur = None
            if tag.strip() == level:
                cur = [rest[:18].strip(), rest[18:].strip(), ""]
                out.append(cur)
        elif cur is not None and ln.strip().startswith("fix:"):
            cur[2] = ln.strip()[4:].strip()
    return [(a, m, f) for a, m, f in out]


def cannot_start(what: str, rep: str) -> str:
    """The owner's "Cannot Start" template from a failing `bot doctor` report: each failing check and its fix."""
    fails = doctor_checks(rep, "FAIL")
    return card("❌", "Cannot Start", codes(what),
                section("Reason", codes(*(f"{m[:1].upper()}{m[1:]}." if m else a for a, m, _f in fails))
                        or codes("The account check failed")),
                section("Action", codes(*(f for _a, _m, f in fails if f))))


HELP = f"""❔ {b("Commands")}

{b("Pick and run")}
/top3 (most volume) · /cheapest · /maxvolume
/run · the run form: market, Mid, Grid or Smart, spread, bias, leverage
{code("/run BTC mid 0 neutral 40x paper")}
{code("/run BTC mid 0 40x live sl=10 vol=100k")}
{code("/run SPY grid +1 long max live tp=5")}
{code("/run SPY smart 0 50x paper")} Smart: Mid that skips likely losing fills
sl= run stop $ · tp= take profit $ · vol= volume target
/openpositions · what runs · go LIVE · close

{b("Watch")}
/dashboard · /status · /balance · /positions · /orders · /pnl · /logs · /yesterdayreport
/account · all-time volume, fees paid and earned, fee tier

{b("Control")}
/pauseneworders · /unpause · /stop · /resumeaftersl
/cancelall · /closeall ({code("taker")} to cross now)

{b("Autopilot")}
/auto · what it runs now and next, on or off
{code("/auto on live budget=5")} · {code("/auto off")}
{code("/auto budget 5")} $ a day · {code("/auto cost 1.5")} bp ceiling ({code("auto")}: tuned daily for the budget)

{b("Settings")}
/settings · {code("/set name value")} · /scannow · /alerts · /mute · /unmute
{code("/status paper")} picks a bot

{b("Lighter")} (the same bot, the other venue)
/l · its commands: the ones above with {code("l_")} in front
{code("/l_status")} · {code("/l_top3")} · {code("/l_run SPY smart +1")} · {code("/l_closeall")}

{b("Funding arbitrage")} (Arcus against Lighter)
/arb · its commands
{code("/arb_status")} · {code("/arb_scan")} · {code("/arb_hold 72")} · {code("/arb_close")}
{code("/arb_start 120 120")} paper · {code("/arb_start live")} · {code("/arb_stop")}"""

# Telegram's "/" list: the everyday commands (everything in HELP still works)
COMMANDS: list[tuple[str, str]] = [
    ("dashboard", "Live screen, every 10 s"),
    ("top3", "Most volume within your cost"), ("cheapest", "Cheapest per $1,000 traded"),
    ("maxvolume", "Most volume, any cost"),
    ("run", "Run form: Mid/Grid/Smart, spread, bias, leverage"), ("auto", "Autopilot: trades by itself in a budget"),
    ("openpositions", "Positions · what runs · go LIVE · close"),
    ("status", "Running? today's PnL and volume"), ("balance", "Account balance and history"),
    ("account", "All-time volume, fees paid and earned, fee tier"),
    ("positions", "What you hold"), ("orders", "Orders on the book"),
    ("pauseneworders", "Stop new orders"), ("unpause", "Quote again"),
    ("stop", "Shut the bot down (position kept)"), ("closeall", "Close every position"),
    ("cancelall", "Cancel every order"), ("resumeaftersl", "Trade again after a safety stop"),
    ("yesterdayreport", "Yesterday's report"), ("settings", "Settings"),
    ("set", "/set name value"), ("scannow", "Scan now"),
    ("l", "Lighter: the same commands as /l_status, /l_run ..."),
    ("arb", "Funding arbitrage: /arb_status, /arb_scan ..."),
    ("menu", "Buttons"), ("help", "All commands"),
]


def menu_keyboard() -> Keyboard:
    return [
        [("📊 Dashboard", "dashboard"), ("▶️ Resume", "unpause")],
        [("⏸ Pause Orders", "pauseneworders"), ("⏹ Stop", "stop")],
        [("🚀 Most Volume", "top3"), ("💎 Cheapest", "cheapest"), ("🔥 Max Volume", "maxvolume")],
        [("🎛 Run form", "run"), ("🤖 Autopilot", "auto")],
        [("📌 Positions", "openpositions"), ("📋 Orders", "orders")],
        [("💰 Balance", "balance"), ("📒 Account", "account")],
        [("🩺 Status", "status"), ("⚙️ Settings", "settings"), ("🔔 Alerts", "alerts")],
        [("❌ Cancel all", "cancelall"), ("🧯 Close all", "closeall")],
        [("⚡ Lighter", "l_menu"), ("⚖️ Funding arb", "arb_menu")],     # the other two parts of this bot
    ]


def auto_keyboard(on: bool) -> Keyboard:
    if on:
        return [[("⏹ Turn off", "auto off"), ("🔄 Refresh", "auto")]]
    return [[("📝 On (paper)", "auto on paper"), ("🔴 On (LIVE)", "auto on live")], [("🔄 Refresh", "auto")]]


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


def _k2(x: float) -> str:
    """The lists' volumes: $9.35k, $780."""
    return f"${x / 1000:,.2f}k" if abs(x) >= 1000 else f"${x:,.0f}"


def cost_text(c: dict[str, Any]) -> str:
    """"$0.02/k" (dollars lost per $1,000 traded), "profit", or "" when unknown."""
    from bot.scout.profiles import cost_1k

    x = cost_1k(c)
    return "" if x is None else "profit" if x == 0 else f"${x:.2f}/k"


def _cost(ct: str) -> str:
    """" · Cost $0.02/k", " · Profitable", or nothing."""
    return " · Profitable" if ct == "profit" else f" · Cost {ct}" if ct else ""


def _setting(c: dict[str, Any]) -> str:
    """"Mid 0 · Neutral" (the setup as the owner reads it)."""
    from bot.scout.pilot import setting_of, setup_of

    try:
        return str(setup_of(c).label)
    except ValueError:
        return setting_of(c)


def size_lines(c: dict[str, Any]) -> list[str]:
    """Order size and the largest position, and the smaller one outside US hours (RWA perps, 1.5x margin)."""
    if not c.get("order_usd"):
        return []
    out = [f"Order {money(c['order_usd'])} · Max position {money(1.25 * c['cap_usd'])}"]
    off = float(c.get("cap_off_usd") or c["cap_usd"])
    if off < float(c["cap_usd"]) - 0.01:
        out.append(f"Outside US hours: max position {money(1.25 * off)} at {float(c.get('leverage_off') or 0):g}x")
    return out


def what(c: dict[str, Any]) -> str:
    """"BTC · Mid 0 · Neutral · 40x"."""
    from bot.scout.pilot import what_line

    return what_line(c)


def setup_block(c: dict[str, Any], i: int | None = None, *, cost: bool = False) -> list[str]:
    """One scan row (the owner's list template): the market, then setting · leverage · volume, PnL (and cost), the
    worst day and the last 24 h."""
    days = int(c.get("days") or 0)
    recent = f" · 24h {usd(c['recent_pnl'])}" if c.get("recent_checked", True) and days else ""
    ct = cost_text(c) if cost else ""
    return [b(f"{f'{i}. ' if i else ''}{short(c['market'])}"),
            *codes(f"{_setting(c)} · {float(c['leverage']):g}x · {_k2(float(c['volume_day']))}/day",
                   f"PnL {usd(c['pnl_day'])}" + _cost(ct),
                   f"Worst {usd(c['worst_day'])}{recent} · {days}d" + (" of data only" if days < 3 else ""))]


def candidate_lines(top: list[dict[str, Any]], *, cost: bool = False) -> str:
    if not top:
        return code("Nothing passes right now.")
    return "\n\n".join("\n".join(setup_block(c, i, cost=cost)) for i, c in enumerate(top, 1))


RUN_HINT = "/run <symbol> <mid|grid|smart> <spread> <bias> <leverage> <paper|live>"


def profile_text(scan: dict[str, Any] | None, profile: str, budget: float, now: float, note: str = "") -> str:
    """One list's top 3 (bot/scout/profiles.py) from the last scan, in the owner's list template."""
    from bot.scout import profiles as P
    from bot.scout.pilot import stale_note

    p = P.profile_of(profile)
    if not scan:
        return card("🔎", "NO SCAN YET", codes("On the server: bot up"))
    top = P.top(scan, p, budget)
    cap = (scan.get("capital") or {}).get("usd")
    head = " · ".join(x for x in (f"Capital {money(float(cap))}" if cap else "",
                                  f"Budget ${budget:.2f} / $1k" if p.budget and not p.any_cost else "",
                                  f"Scan {ago(now - scan['ts_us'] / 1e6)} ago") if x)
    pend = scan.get("pending") or []
    info = codes(head, stale_note(scan, now),
                 (f"Not backtested at this capital yet: {len(pend)} market{'s' if len(pend) > 1 else ''} ("
                  + ", ".join(short(m) for m in pend[:6]) + ("…" if len(pend) > 6 else "") + ")") if pend else "",
                 note)
    if top:
        return card(p.icon, f"{p.title} · Top 3", info, candidate_lines(top, cost=True), codes(RUN_HINT))
    if not p.any_cost:
        near = P.nearest(scan, p, budget)
        closest = section("Closest", codes(*(f"{short(c['market'])} · {_setting(c)} · {c['leverage']:g}x · "
                                             f"{cost_text(c)}" for c in near)))
        fix = codes(f"/set volume_cost {max((P.cost_1k(c) or 0) for c in near) + 0.005:.2f}") if near else None
        return card(p.icon, f"{p.title} · Top 3", info, codes(f"Nothing within ${budget:.2f} per $1,000"), closest, fix)
    return card(p.icon, f"{p.title} · Top 3", info, codes("Nothing passes the safety checks right now."))


def profile_keyboard(profile: str, top: list[dict[str, Any]]) -> Keyboard:
    from bot.scout.profiles import LISTS

    rows: Keyboard = []
    if top:
        rows.append([(f"▶️ {i}", f"pick {profile} {i}") for i in range(1, len(top) + 1)])
    rows.append([(f"{p.icon} {p.title}", f"top3 {p.key}") for p in LISTS if p.key != profile])
    rows.append([("🎯 Any setup", "run"), ("🔄 Scan", f"rescan {profile}"), ("☰ Menu", "menu")])
    return rows


def pick_keyboard(top: list[dict[str, Any]], profile: str = "volume") -> Keyboard:
    row = [(f"▶️ {i}", f"pick {profile} {i}") for i in range(1, len(top) + 1)]
    return ([row] if row else []) + [[("📌 Positions", "openpositions"), ("☰ Menu", "menu")]]


# ------------------------------------------------------------------ run any setup: the run form (Tread.fi's order form)
def markets_keyboard(scan: dict[str, Any] | None) -> Keyboard:
    """Every scanned market, the most backtested volume first (its best setup)."""
    best: dict[str, float] = {}
    for c in (scan or {}).get("all") or []:
        vol = float(c["volume_day"]) if c.get("days") else 0.0
        best[c["market"]] = max(best.get(c["market"], 0.0), vol)
    order = sorted(best, key=lambda m: (-best[m], m))
    buttons = [(f"{short(m)} {kusd(best[m]) if best[m] else '–'}", f"rm {m}") for m in order]
    return [buttons[i:i + 3] for i in range(0, len(buttons), 3)] + [[("☰ Menu", "menu")]]


def lists_of(c: dict[str, Any], budget: float) -> list[Any]:
    """The lists this scan row belongs to (bot/scout/profiles.py)."""
    from bot.scout.profiles import LISTS, verdict

    return [p for p in LISTS if not verdict(c, p, budget)]


FORM_SL = (0.0, 5.0, 10.0, 30.0)                   # run stop buttons, dollars (0: none, the daily stop applies)
FORM_VOL = (0.0, 50_000.0, 100_000.0, 250_000.0)   # volume target buttons (0: none)


def how_it_quotes(s: Any, c: dict[str, Any] | None = None) -> list[str]:
    """One or two plain lines on what a setup does (bot/strategies/setup.py)."""
    from bot.strategies import setup as su

    x = s.spread
    if s.mode in ("mid", "smart"):
        line = ("Quotes the best bid and ask, following the mid" if x == 0 else
                f"Quotes {su.spread_text(x).lstrip('+')} bp either side of the mid, following it" if x > 0 else
                "Quotes inside the mid, 1 tick from the other side")
    else:
        line = ("Sells no lower than the last buy, buys no higher than the last sell" if x == 0 else
                f"Sells {x:g} bp above the last buy, buys {x:g} bp below the last sell")
        line += f" · reset at {su.GRID_RESET_PCT:g}%"
    out = [line]
    if s.mode == "smart":
        out.append("Smart: leaves a side out while the book leans against it or the price just moved against it")
    if s.bias != "neutral":
        held = f" (~{money(su.BIAS_FRAC * float(c['cap_usd']))})" if c and c.get("cap_usd") else ""
        out.append(f"{s.bias.capitalize()} bias: holds about half the position cap {s.bias}{held}")
    return out


def form_text(c: dict[str, Any], budget: float, running: list[str], live_ok: bool, stale: str = "") -> str:
    """The run form: the setup (mode, spread, bias, leverage), its sizes, how it quotes, its backtest and the run's
    limits. The buttons under it change one field at a time (form_keyboard)."""
    from bot.scout.pilot import limit_lines, setup_of, what_line

    s = setup_of(c)
    head = codes(what_line(c), *size_lines(c))
    how = section("How it quotes", codes(*how_it_quotes(s, c)))
    if c.get("volume_day") is None:
        why = "Not in the scout's menu" if not c.get("in_menu", True) else \
            f"No backtest at {float(c['leverage']):g}x (the scout tests each market's maximum)"
        bt = section("Backtest", codes(why, "Runs anyway, sized from the account now"))
    else:
        at = "" if c.get("backtested", True) or not c.get("backtest_capital_usd") else \
            f" at {money(float(c['backtest_capital_usd']))}"
        ct = cost_text(c)
        ins = lists_of(c, budget)
        recent = f" · 24h {usd(c['recent_pnl'])}" if c.get("recent_checked", True) else ""
        bt = section(f"Backtest · {c['days']}d{at}", codes(
            f"{kusd(c['volume_day'])}/day" + _cost(ct),
            f"PnL {usd(c['pnl_day'])}/day · worst {usd(c['worst_day'])}{recent}",
            ("In " + ", ".join(p.title for p in ins)) if ins else
            "In no list" + (f": {c['reasons'][0][:70]}" if c.get("reasons") else "")))
    lim = limit_lines(c) or ["No run stop: the daily stop applies"]
    from bot.scout.profiles import profile_of

    p = profile_of(c.get("profile") or "manual")
    notes = codes("Runs as your pick: never paused for its numbers" if not p.listed else
                  f"From {p.title}: paused if it drops out of the list",
                  *(f"Replaces the running {m.upper()} bot (closes its position first)" for m in running),
                  "" if live_ok else "LIVE is off on this server (BOT_PILOT_LIVE=1 in .env)", stale)
    return card("🎛", f"{short(c['market'])} · RUN SETUP", head, how, bt, section("Limits", codes(*lim)), notes)


def form_data(market: str, s: Any, lev: float, sl: float, vol: float, tp: float, profile: str = "manual") -> str:
    """"BTC-USD m0n 40 10 100 0 manual": market, setup id, leverage, run stop $, volume target $k, take profit $, and
    the list it was picked from (a list's pick is judged by that list while it stays in it)."""
    return f"{market} {s.sid} {lev:g} {sl:g} {vol / 1000:g} {tp:g} {profile}"


def form_keyboard(market: str, s: Any, lev: float, levs: list[float], sl: float, vol: float, tp: float,
                  live_ok: bool, profile: str = "manual") -> Keyboard:
    """Tread's form as buttons: mode, spread, bias, leverage, run stop, volume target; then Paper or LIVE."""
    from bot.strategies import setup as su

    def f(**kw: Any) -> str:
        st = {"s": s, "lev": lev, "sl": sl, "vol": vol, "tp": tp, **kw}
        return "f " + form_data(market, st["s"], st["lev"], st["sl"], st["vol"], st["tp"], profile)

    def on(x: bool, t: str) -> str:
        return f"• {t}" if x else t

    spreads = list({"mid": su.MID_SPREADS, "grid": su.GRID_SPREADS}.get(s.mode, su.SMART_SPREADS))
    if s.spread not in spreads:
        spreads = sorted([*spreads, s.spread])
    lev_row = sorted({*levs, lev}, reverse=True)
    go = form_data(market, s, lev, sl, vol, tp, profile)
    return [
        [(on(s.mode == m, m.capitalize()), f(s=s.with_(mode=m, spread=max(s.spread, 0.0) if m == "grid" else s.spread)))
         for m in su.MODES],
        [(on(x == s.spread, f"{su.spread_text(x)} bp"), f(s=s.with_(spread=x))) for x in spreads],
        [(on(b_ == s.bias, b_.capitalize()), f(s=s.with_(bias=b_))) for b_ in ("short", "neutral", "long")],
        [(on(abs(x - lev) < 0.01, f"{x:g}x"), f(lev=x)) for x in lev_row],
        [(on(abs(x - sl) < 0.01, f"SL ${x:g}" if x else "No SL"), f(sl=x)) for x in FORM_SL],
        [(on(abs(x - vol) < 1, f"Vol {kusd(x)}" if x else "No target"), f(vol=x)) for x in FORM_VOL],
        [("📝 Paper", f"fd {go} paper")] + ([("🔴 LIVE", f"fd {go} live")] if live_ok else []),
        [("◀️ Markets", "run"), ("☰ Menu", "menu")],
    ]


def running_block(pilot: Any) -> list[str]:
    """What is deployed: since when, today against its backtest, how it quotes, the scout's last check."""
    from bot.scout.pilot import limit_lines
    from bot.scout.profiles import profile_of
    from bot.telegram.dashboard import quote_summary

    st = pilot.state()
    a = st.get("active")
    if not a:
        return section("Running", codes("Nothing deployed · /top3 · /run"))
    running = pilot.control.is_running(a["mode"])
    view = pilot.control.view(a["mode"]) if running else None
    rev = st.get("last_review") or {}
    bt = a.get("backtest") or {}
    prof = profile_of(a.get("profile"))
    state = f"Paused by the scout: {st['paused_by_scout']}" if st.get("paused_by_scout") else \
        ("Running" if running else "NOT running")
    since = dt.datetime.fromtimestamp(a["since"], dt.UTC).strftime("%m-%d %H:%M UTC")
    vol = sum(d["maker_volume"] for d in view.today.values()) if view is not None else None
    if not prof.listed:
        check = "Your pick: not judged by the scout"
    elif rev.get("go"):
        check = f"Still in the list (checked {ago(time.time() - rev['ts'])} ago)"
    else:
        check = "; ".join(rev.get("reasons") or [])[:160] if rev else "Not checked yet"
    q = next((x.get("quotes") for x in ((view.snapshot or {}).get("sessions") or []) if x.get("quotes")), None) \
        if view is not None else None
    return section("Running", codes(
        f"{what({'market': a['market'], 'config': a['config'], 'leverage': a['config'].split(' @ ')[-1].rstrip('x')})}"
        f" · {a['mode'].upper()}",
        f"{state} · {prof.title}{' · max lev' if a.get('lev') == 'max' else ''} · since {since}",
        *limit_lines(a),
        f"Today {usd(day_pnl(view) if view is not None else None)} · {kusd(vol)} volume",
        *quote_summary(q),
        f"Backtest {kusd(bt.get('volume_day'))}/day · {usd(bt.get('pnl_day'))}/day", check))


def pilot_keyboard(paper: bool = False) -> Keyboard:
    rows: Keyboard = [[("🔴 Go LIVE with this setup", "golive")]] if paper else []
    return [*rows, [("⏹ Close & stop", "pilotclose"), ("🔄 Refresh", "openpositions"), ("☰ Menu", "menu")]]


# ------------------------------------------------------------------ balance and settings
def balance_text(snap: dict[str, float] | None, summary: dict[str, Any], held: dict[str, Any] | None,
                 live_capital: str | None, account: int) -> str:
    """/balance: the account now (or the last reading), deposits vs trading PnL, the change over 1/7/30 days, and the
    capital the scout and the live bot size for."""
    last = summary.get("latest") or {}
    if snap and snap.get("equity"):
        where = f"Subaccount {account} · read now"
        eq, free, nd = snap["equity"], snap.get("free"), snap.get("net_deposits")
    elif last:
        when = dt.datetime.fromtimestamp(last["ts"], dt.UTC).strftime("%m-%d %H:%M")
        where = f"Subaccount {account} · unreadable now, last reading {when} UTC"
        eq, free, nd = last["equity"], last.get("free"), last.get("net_deposits")
    else:
        return card("💰", "BALANCE", codes("No reading yet"),
                    codes("Is ARCUS_ADDRESS in .env, and has the account been funded?"))
    changes = []
    for key, name in (("1d", "24h"), ("7d", "7d"), ("30d", "30d")):
        c = summary.get(key)
        if c:
            t = f"{name} {usd(c['equity_change'])}"
            if c.get("pnl_change") is not None and abs(c["pnl_change"] - c["equity_change"]) > 0.005:
                t += f" (trading {usd(c['pnl_change'])})"
            changes.append(t)
    since = dt.datetime.fromtimestamp((held or {}).get("ts", 0), dt.UTC).strftime("%m-%d %H:%M")
    sizing = codes(f"Scans size for {usd(held['usd'], sign=False)} ({held.get('source')}, since {since} UTC)"
                   if held else "",
                   f"The live bot sizes for {usd(float(live_capital), sign=False)}" if live_capital else "")
    return card("💰", "BALANCE", codes(where),
                codes(f"Equity {usd(eq, sign=False)}" + (f" · free {usd(free, sign=False)}" if free else ""),
                      f"Deposited {usd(nd, sign=False)} · trading PnL {usd(eq - nd)}" if nd is not None else ""),
                section("Change", codes(" · ".join(changes))), section("Sizing", sizing),
                codes(f"{summary.get('rows_30d', 0)} readings in the last 30 days"))


def account_text(raw: dict[str, Any] | None, error: str = "") -> str:
    """/account: the account's figures as Arcus keeps them (bot/core/account_stats.py)."""
    from bot.core import account_stats

    if raw is None:
        return card("📒", "ACCOUNT", codes("Arcus did not answer" + (f": {error}" if error else "")),
                    codes("Is ARCUS_ADDRESS in .env? Try again in a minute"))
    head_line, blocks = account_stats.lines(raw)
    return card("📒", "ACCOUNT", codes(head_line), *(section(label, codes(*ls)) for label, ls in blocks))


SET_LABELS = {"volume_cost": "Budget", "capital": "Capital", "trade_share": "Share of the balance",
              "max_capital": "Most capital", "position_stop": "Position stop", "daily_stop": "Daily stop",
              "kill": "Kill", "scan_every": "Scan every", "scan_budget": "Scan budget", "scan_workers": "Scan cores",
              "crypto_lev": "BTC/ETH leverage"}


def set_value(name: str, value: Any) -> str:
    """"Budget: $0.15 / $1,000" (the owner's /set template)."""
    from bot.common.settings import show

    return f"{SET_LABELS.get(name, name.replace('_', ' ').capitalize())}: {show(name, value).replace(' per ', ' / ')}"


def settings_text(over: dict[str, Any], defaults: dict[str, Any]) -> str:
    from bot.common.settings import SETTINGS, show

    rows = [f"{b(name)} {code(show(name, over.get(name, defaults.get(name))))}{' (changed)' if name in over else ''}"
            f" · {escape(s.help.split(':')[0].split(';')[0].split(' (')[0])}" for name, s in SETTINGS.items()]
    return card("⚙️", "Settings", rows, codes("/set name value · /set name default",
                                             "LIVE on/off stays in .env (BOT_PILOT_LIVE)"))
