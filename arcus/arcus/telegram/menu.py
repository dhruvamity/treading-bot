"""The "menu" layout of the Telegram bot: Home with six buttons and a Venue switch.

Off by default: `/set telegram_ui menu` turns it on, `/set telegram_ui default` gives the classic menu back (views.py).
Only the buttons change. Every button sends the command the classic menu sends (Lighter's with `l_` in front, the
funding arbitrage's with `arb_`), so the trading code, the confirm cards, the typed codes and the live lock are the
same in both layouts. Typed commands do not follow the venue switch: `/closeall` still closes Arcus and
`/l_closeall` closes Lighter.

    Home  = what the venue is doing, then   📊 Live      🚀 Start a run
                                            🛑 Control   💰 Money
                                            ⚙️ Settings  🔀 Venue: Arcus
    Start a run: the three lists and the run form       Control: the only screen with stop / cancel / close buttons
    Money: balance, account, PnL, positions, orders      Settings: settings, alerts, autopilot, scan now

Every screen under Home ends with ☰ Home (and ◀️ Back where its parent is not Home).
"""

from __future__ import annotations

from arcus.common.tgfmt import card, codes

Keyboard = list[list[tuple[str, str]]]

VENUES = ("arcus", "lighter", "arb")
NAME = {"arcus": "Arcus", "lighter": "Lighter", "arb": "Funding arb"}
ICON = {"arcus": "🤖", "lighter": "⚡", "arb": "⚖️"}
PREFIX = {"arcus": "", "lighter": "l_", "arb": "arb_"}
GROUPS = ("start", "control", "money", "settings")

# what each group offers, per venue: (button label, command). The command is what the classic menu sends.
ITEMS: dict[str, dict[str, list[tuple[str, str]]]] = {
    "arcus": {
        "start": [("🚀 Most volume", "top3"), ("💎 Cheapest", "cheapest"), ("🔥 Max volume", "maxvolume"),
                  ("🎛 Build a run", "run"), ("📌 What runs", "openpositions")],
        "control": [("⏸ Pause new orders", "pauseneworders"), ("▶️ Unpause", "unpause"),
                    ("🧹 Clear safety stop", "resumeaftersl"), ("⏹ Stop", "stop"),
                    ("❌ Cancel all", "cancelall"), ("🧯 Close all", "closeall")],
        "money": [("💰 Balance", "balance"), ("📒 Account", "account"), ("📈 PnL", "pnl"),
                  ("📌 Positions", "positions"), ("📋 Orders", "orders")],
        "settings": [("⚙️ Settings", "settings"), ("🔔 Alerts", "alerts"), ("🤖 Autopilot", "auto"),
                     ("🔎 Scan now", "scannow")],
    },
    "lighter": {
        "start": [("🚀 Most volume", "top3"), ("💎 Cheapest", "cheapest"), ("🔥 Max volume", "maxvolume"),
                  ("🎛 Build a run", "run"), ("📌 What runs", "openpositions")],
        "control": [("⏸ Pause new orders", "pauseneworders"), ("▶️ Unpause", "unpause"),
                    ("🧹 Clear safety stop", "resumeaftersl"), ("⏹ Stop", "stop"), ("🧯 Close all", "closeall")],
        "money": [("💰 Balance", "balance"), ("📒 Account", "account"), ("📌 Positions", "positions"),
                  ("📋 Orders", "orders")],
        "settings": [("⚙️ Settings", "settings"), ("🤖 Autopilot", "auto"), ("🔎 Scan now", "scannow")],
    },
    "arb": {
        "start": [("🔎 Scan", "scan"), ("📝 Start on paper", "start 120 120")],
        "control": [("⏸ Pause", "pause"), ("▶️ Resume", "resume"), ("⏹ Stop", "stop"), ("🧯 Close", "close"),
                    ("⚡ Close now", "closenow")],
        "money": [("📊 Status", "status"), ("📡 Feeds", "feeds")],
        "settings": [("⚙️ Settings", "settings")],
    },
}
LIVE = {"arcus": "dashboard", "lighter": "dashboard", "arb": "status"}     # the 📊 Live button
HINT = {
    "start": "Pick a list, or build your own run. Paper first; LIVE asks for a typed code",
    "control": "Stop, cancel and close ask you to confirm first",
    "money": "What you have and what it did",
    "settings": "Change a setting, the alerts or the autopilot",
}
TITLE = {"start": "Start a run", "control": "Control", "money": "Money", "settings": "Settings"}
# where ◀️ Back goes from a screen the Home buttons lead to (a typed or tapped Arcus command)
BACK = {"top3": "m_start", "cheapest": "m_start", "maxvolume": "m_start", "run": "m_start", "openpositions": "m_start",
        "balance": "m_money", "account": "m_money", "pnl": "m_money", "positions": "m_money", "orders": "m_money",
        "settings": "m_settings", "alerts": "m_settings", "auto": "m_settings"}


def command(venue: str, name: str) -> str:
    """The callback data for `name` on `venue`: `top3`, `l_top3`, `arb_scan`."""
    return PREFIX[venue] + name


def next_venue(venue: str) -> str:
    return VENUES[(VENUES.index(venue) + 1) % len(VENUES)] if venue in VENUES else VENUES[0]


def home_keyboard(venue: str) -> Keyboard:
    return [[("📊 Live", command(venue, LIVE[venue])), ("🚀 Start a run", "m_start")],
            [("🛑 Control", "m_control"), ("💰 Money", "m_money")],
            [("⚙️ Settings", "m_settings"), (f"🔀 Venue: {NAME[venue]}", "venue")]]


def screen_text(venue: str, group: str, *, read_only: bool = False) -> str:
    return card(ICON[venue], f"{NAME[venue]} · {TITLE[group]}", codes(HINT[group], "Read-only: controls are off"
                                                                       if read_only and group == "control" else ""))


def screen_keyboard(venue: str, group: str, *, live_allowed: bool = True) -> Keyboard:
    items = list(ITEMS[venue][group])
    if venue == "arb" and group == "start" and live_allowed:
        items.append(("🔴 Start LIVE", "start live"))
    if group == "settings":
        items.append(("🧭 Classic menu", "set telegram_ui classic"))
    buttons = [(label, command(venue, cmd)) for label, cmd in items]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    return [*rows, [("☰ Home", "home")]]


def with_nav(keyboard: Keyboard, back: str = "") -> Keyboard:
    """A screen's own buttons plus ◀️ Back / ☰ Home. Left alone: confirm cards (a tap there must only confirm or cancel)
    and the live dashboard (any other button would turn that message into its own reply). The classic ☰ Menu button
    is replaced, since Home is its place here."""
    data = [d for row in keyboard for _label, d in row]
    if any(d.startswith(("ok ", "no ", "dash")) for d in data) or "home" in data or "venue" in data:
        return keyboard
    rows = [[btn for btn in row if btn[1] != "menu"] for row in keyboard]
    nav = [("◀️ Back", back), ("☰ Home", "home")] if back else [("☰ Home", "home")]
    return [*(r for r in rows if r), nav]
