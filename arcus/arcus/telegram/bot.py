"""The Telegram control bot: status, control and live alerts for the trading bot. ONE bot for all of it: Arcus
(the commands below), Lighter (/l_<command>) and the funding arbitrage (/arb_<command>): arcus/telegram/others.py.

Security:
- Only the owner's chat (TELEGRAM_CHAT_ID) is served; with TELEGRAM_ALLOWED_USER_IDS set, only those users, in the
  owner chat or in their own private chat. Everyone else is ignored (only /whoami answers, with their own ids).
- Nothing that can lose money happens on one tap. Stop, resume, cancel-all and a paper start need a Confirm button;
  flatten and a LIVE start need a one-time code typed back within 2 minutes, and a live start also needs the
  session's live_enabled: true and a passing `doctor`.
- The bot never holds trading state or keys of its own: it reads the runner's database and heartbeat, writes flags
  the runner applies on its next tick, and uses the CLI's own code for venue actions.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import json
import os
import secrets
import time
from dataclasses import dataclass, field
from html import escape
from pathlib import Path
from typing import Any

from arcus.common import settings
from arcus.common.logging import Log, redact_str
from arcus.common.tgfmt import b, card, codes, plain_card, section
from arcus.common.tgfmt import code as mono
from arcus.core.balances import BalanceLog
from arcus.telegram import dashboard, menu, others
from arcus.telegram.api import Keyboard, TelegramAPI, TelegramError, split_html
from arcus.telegram.control import MODES, Control, pause_where
from arcus.telegram.views import (
    COMMANDS,
    HELP,
    _risk,
    account_text,
    ago,
    auto_keyboard,
    balance_text,
    candidate_lines,
    cannot_start,
    confirm_keyboard,
    dashboard_keyboard,
    doctor_checks,
    form_keyboard,
    form_text,
    markets_keyboard,
    menu_keyboard,
    orders_text,
    pick_keyboard,
    pilot_keyboard,
    pnl_text,
    positions_text,
    profile_keyboard,
    profile_text,
    refresh_keyboard,
    running_block,
    sessions_text,
    set_value,
    settings_text,
    short,
    size_lines,
    status_text,
    what,
)
from arcus.telegram.watcher import Prefs, Watcher

log = Log("telegram_bot")
VENUES = ("arcus",)
# The commands' earlier names still work (not listed in Telegram's menu), so old habits and old buttons keep working.
TAKEOVER = {"stop": "/stop", "deploy": "/run", "run": "/run", "pilotclose": "/pilotclose", "flatten": "/closeall",
            "cancelall": "/cancelall"}   # the owner's own actions, which turn the autopilot off
ALIASES = {"scout": "top3", "pilot": "openpositions", "report": "yesterdayreport", "pause": "pauseneworders",
           "resume": "resumeaftersl", "flatten": "closeall", "aggressive": "top3", "breakeven": "cheapest",
           "form": "run"}
CONFIRM_TTL_S = 120
DASH_FILE = "telegram_dashboard.json"   # the live /dashboard message: {chat_id, message_id, since}
SCAN_WAIT_S = 30 * 60                   # no scan running or queued this long after a request: say so
SCAN_GIVE_UP_S = 6 * 3600               # stop waiting for a scan after this long (a slow one is still running)


# the run's own limits (Tread's Stop Loss, Take Profit and Volume): /run ... sl=10 tp=5 vol=100k
LIMIT_KEYS = {"sl": "max_loss_usd", "maxloss": "max_loss_usd", "max_loss": "max_loss_usd", "tp": "take_profit_usd",
              "vol": "volume_target_usd", "volume": "volume_target_usd"}


def _amount(text: str) -> float:
    """"30", "$30", "100k", "1.5m", "250,000" -> dollars."""
    t = text.strip().lower().lstrip("$").replace(",", "")
    mult = {"k": 1e3, "m": 1e6}.get(t[-1:], 1.0)
    return float(t[:-1] if mult != 1.0 else t) * mult


def _take_limits(args: list[str]) -> tuple[list[str], dict[str, float] | str]:
    """/run ... sl=30 tp=5 vol=100k: (the other arguments, {session field: dollars}); a string is the error to show."""
    rest, out = [], {}
    for a in args:
        key, eq, val = a.partition("=")
        field_ = LIMIT_KEYS.get(key.lower()) if eq else None
        if field_ is None:
            rest.append(a)
            continue
        try:
            v = _amount(val)
        except ValueError:
            return args, f"{key}= takes dollars, e.g. {key}=30 (got {a})"
        lo, hi = (1000.0, 1e9) if field_ == "volume_target_usd" else (0.5, 1_000_000.0)
        if not lo <= v <= hi:
            return args, f"{key}= must be between ${lo:,.2f} and ${hi:,.0f}"
        out[field_] = v
    return rest, out


@dataclass
class Pending:
    pid: str
    action: str
    args: dict[str, Any]
    chat_id: int
    desc: str
    code: str | None = None
    expires: float = field(default_factory=lambda: time.time() + CONFIRM_TTL_S)
    message_id: int | None = None


@dataclass
class Ctx:
    chat_id: int
    user_id: int | None
    user: str
    message_id: int | None = None  # set when the command came from a button (edit that message in place)
    back: str = ""                 # menu layout: where ◀️ Back goes from the screen this command shows


class TelegramBot:
    def __init__(self, api: TelegramAPI, control: Control, *, owner_chat_id: int, allowed_user_ids: set[int],
                 prefs_path: Path, read_only: bool = False, daily_loss_pct: float = 3.0,
                 watch_every_s: float = 10.0, pilot: Any = None) -> None:
        self.api = api
        self.control = control
        self.owner_chat_id = owner_chat_id
        self.allowed_user_ids = allowed_user_ids
        self.read_only = read_only
        self.prefs_path = prefs_path
        self.prefs = Prefs.load(prefs_path)
        self.watcher = Watcher(control, self._alert, self.prefs, daily_loss_pct=daily_loss_pct)
        self.watch_every_s = watch_every_s
        self.pending: dict[str, Pending] = {}
        self.pilot = pilot                 # arcus.scout.pilot.Pilot: picks what runs (optional)
        self.pilot_offset = -1
        self.username = ""
        self._tasks: set[asyncio.Task[Any]] = set()
        self._dash_account: dict[str, Any] | None = None   # the dashboard's own account read (when the bot has none)
        self.scan_waiters: list[dict[str, Any]] = []        # {chat, profile, since}: post that list after the scan
        # the other two parts of the bot, in this same Telegram bot (arcus/telegram/others.py)
        self.lighter = others.Lighter(str(getattr(api, "token", "")), owner_chat_id, allowed_user_ids,
                                      home_if=lambda: self.ui() == "menu")
        self.arb = others.Arb()
        self.venues: dict[int, str] = {}     # menu layout: which part the Home buttons act on, per chat (default Arcus)

    # ------------------------------------------------------------------ plumbing
    def authorized(self, chat_id: int, user_id: int | None) -> bool:
        if self.allowed_user_ids:
            return user_id in self.allowed_user_ids and chat_id in (self.owner_chat_id, user_id)
        return chat_id == self.owner_chat_id

    def ui(self) -> str:
        """"menu" when the owner chose the Home layout (/set telegram_ui menu), else "classic"."""
        try:
            return "menu" if settings.load(self._state_dir()).get("telegram_ui") == "menu" else "classic"
        except Exception:      # an unreadable settings file must not take the bot's menu with it
            return "classic"

    async def _alert(self, text: str, critical: bool) -> None:
        await self.api.send(self.owner_chat_id, text, silent=not critical)

    async def reply(self, ctx: Ctx, text: str, keyboard: Keyboard | None = None) -> None:
        if keyboard is not None and self.ui() == "menu":
            keyboard = menu.with_nav(keyboard, ctx.back)
        if ctx.message_id is not None and len(split_html(text)) == 1:
            try:
                await self.api.edit(ctx.chat_id, ctx.message_id, text, keyboard=keyboard)
                return
            except TelegramError:
                pass  # too old to edit, or text too long for one message: send a new one
        await self.api.send(ctx.chat_id, text, keyboard=keyboard)

    def _spawn(self, coro: Any) -> None:
        t = asyncio.ensure_future(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    def _mode(self, args: list[str], *, need_running: bool = False) -> tuple[str | None, list[str]]:
        """Pull an explicit mode out of the arguments, else the running bot's mode (live first)."""
        rest = [a for a in args if a.lower() not in MODES]
        explicit = [a.lower() for a in args if a.lower() in MODES]
        if explicit:
            return explicit[0], rest
        run = self.control.running_modes()
        if run:
            return run[0], rest
        return (None if need_running else self.control.default_mode()), rest

    # ------------------------------------------------------------------ main loops
    async def run(self) -> None:
        me = await self.api.call("getMe")
        self.username = str((me or {}).get("username") or "")
        await self.api.set_commands(COMMANDS)
        d = dashboard.collect(self.control)
        text = dashboard.control_text(d, "Bot Online", ["Read-only: controls are off"] if self.read_only else None)
        await self.api.send(self.owner_chat_id, text,
                            keyboard=menu.home_keyboard(self._venue(self.owner_chat_id)) if self.ui() == "menu"
                            else menu_keyboard(), silent=True)
        loops = [self._poll_loop(), self._watch_loop(), self._dashboard_loop()]
        panel = self.lighter.load()
        if panel is not None:
            loops.append(panel.run())
        await asyncio.gather(*loops)

    async def _poll_loop(self) -> None:
        offset: int | None = None
        while True:
            try:
                updates = await self.api.get_updates(offset, timeout=50)
            except TelegramError as e:
                log.warning("poll_failed", reason=str(e)[:200])
                await asyncio.sleep(5)
                continue
            for u in updates:
                offset = int(u["update_id"]) + 1
                try:
                    await self.handle(u)
                except Exception as e:  # one bad update must not stop the bot
                    log.error("update_failed", reason=type(e).__name__, data={"err": str(e)[:300]}, exc_info=True)
                    with contextlib.suppress(TelegramError):
                        await self.api.send(self.owner_chat_id, card("⚠️", "ERROR", codes(redact_str(str(e))[:300])))

    async def _watch_loop(self) -> None:
        while True:
            try:
                await self.watcher.tick()
                await self.pilot_events()
                await self.scan_waiters_tick()
            except Exception as e:
                log.error("watch_failed", reason=type(e).__name__, data={"err": str(e)[:300]}, exc_info=True)
            await asyncio.sleep(self.watch_every_s)

    async def pilot_events(self) -> None:
        """Post what the pilot did since the last look (offers come with Run buttons)."""
        if self.pilot is None:
            return
        events, self.pilot_offset = self.pilot.events_since(self.pilot_offset)
        for e in events:
            text = plain_card(str(e.get("text", "")))   # the pilot writes plain text: title, then lines
            top = e.get("top")
            if top is not None and e.get("kind") in ("offer", "paused", "suggest"):
                profile = str(e.get("profile") or "volume")
                text += ("\n\n" + candidate_lines(top, cost=True)) if top else ""
                await self.api.send(self.owner_chat_id, text, keyboard=pick_keyboard(top, profile),
                                    silent=e.get("kind") == "offer")
            elif e.get("kind") == "deployed":
                await self.api.send(self.owner_chat_id, text, keyboard=[[("📊 Dashboard", "dashboard")]],
                                    silent=True)
            else:
                await self.api.send(self.owner_chat_id, text,
                                    silent=e.get("kind") not in ("failed", "paused", "auto_alert", "alert"))

    # ------------------------------------------------------------------ updates
    async def handle(self, u: dict[str, Any]) -> None:
        if "callback_query" in u:
            cq = u["callback_query"]
            msg = cq.get("message") or {}
            ctx = Ctx(int((msg.get("chat") or {}).get("id", 0)), (cq.get("from") or {}).get("id"),
                      _name(cq.get("from")), msg.get("message_id"))
            data = str(cq.get("data") or "")
            if data.startswith(others.LIGHTER + " "):     # a button of the Lighter panel: it answers with its note
                note = ""
                if self.authorized(ctx.chat_id, ctx.user_id):
                    note = await self._lighter_button(ctx, data[len(others.LIGHTER) + 1:])
                await self.api.answer(cq["id"], note)
                return
            await self.api.answer(cq["id"])
            if not self.authorized(ctx.chat_id, ctx.user_id):
                return
            parts = data.split()
            if parts:
                await self.dispatch(ctx, parts[0], parts[1:])
            return
        msg = u.get("message") or {}
        text = str(msg.get("text") or "").strip()
        ctx = Ctx(int((msg.get("chat") or {}).get("id", 0)), (msg.get("from") or {}).get("id"), _name(msg.get("from")))
        if not text:
            return
        if text.split()[0].split("@")[0] == "/whoami":
            await self.api.send(ctx.chat_id, card("🆔", "WHO AM I", codes(f"Chat id {ctx.chat_id} · user id {ctx.user_id}")))
            return
        if not self.authorized(ctx.chat_id, ctx.user_id):
            log.warning("unauthorized", data={"chat": ctx.chat_id, "user": ctx.user_id})
            return
        if text.isdigit():
            await self._code(ctx, text)
            return
        if not text.startswith("/"):
            if self.ui() == "menu":      # not a dead end: Home, with its buttons
                home, kb = await self._home_card(ctx.chat_id)
                await self.api.send(ctx.chat_id, home, keyboard=kb)
            else:
                await self.api.send(ctx.chat_id, card("❔", "Send a Command", codes("/menu or /help")))
            return
        head, *args = text.split()
        cmd = head[1:].split("@")[0].lower()
        if "@" in head and self.username and head.split("@")[1].lower() != self.username.lower():
            return  # a command for another bot in the same group
        await self.dispatch(ctx, cmd, args)

    # ------------------------------------------------------------------ commands
    def _tables(self) -> tuple[dict[str, Any], dict[str, Any]]:
        """(read commands, write commands): name -> handler. Write commands are refused when the bot is read-only."""
        read = {"start": self.c_menu, "menu": self.c_menu, "help": self.c_help, "status": self.c_status,
                "top3": self.c_scout, "openpositions": self.c_pilot,
                "pnl": self.c_pnl, "positions": self.c_positions, "orders": self.c_orders,
                "sessions": self.c_sessions, "logs": self.c_logs, "yesterdayreport": self.c_report,
                "ping": self.c_ping, "alerts": self.c_alerts, "mute": self.c_mute, "unmute": self.c_unmute,
                "ok": self.c_ok, "no": self.c_no, "balance": self.c_balance, "account": self.c_account,
                "settings": self.c_settings,
                "dashboard": self.c_dashboard, "dashstop": self.c_dashstop, "dashresume": self.c_dashresume,
                "volume": self.c_volume, "cheapest": self.c_cheapest, "maxvolume": self.c_maxvolume,
                "pick": self.c_pick, "rm": self.c_rm, "f": self.c_f, "auto": self.c_auto,
                "rs": self.c_old_button, "rl": self.c_old_button, "lev": self.c_old_button,
                "deploy": self.c_old_button,
                "home": self.c_home, "venue": self.c_venue,
                **{f"m_{g}": self._screen(g) for g in menu.GROUPS}}
        write = {"pauseneworders": self.c_pause, "unpause": self.c_unpause, "stop": self.c_stop,
                 "resumeaftersl": self.c_resume, "run": self.c_run, "doctor": self.c_doctor,
                 "cancelall": self.c_cancelall, "closeall": self.c_flatten, "fd": self.c_fd,
                 "rd": self.c_old_button, "golive": self.c_golive,
                 "pilotclose": self.c_pilotclose, "set": self.c_set, "scannow": self.c_scannow,
                 "rescan": self.c_rescan}
        return read, write

    async def dispatch(self, ctx: Ctx, cmd: str, args: list[str]) -> None:
        cmd, args = others.arcus_spelling(cmd, args)      # /arcus_status, /a_status: Arcus, spelled like the others
        part = others.part_of(cmd)
        if part is not None:      # /l_status, /l status, /arb_hold 72 ...: Lighter or the funding arbitrage
            name, rest = (part[1], args) if part[1] else ((args[0].lower(), args[1:]) if args else ("", []))
            await (self.c_lighter if part[0] == others.LIGHTER else self.c_arb)(ctx, name, rest)
            return
        cmd = ALIASES.get(cmd, cmd)
        if self.ui() == "menu":
            ctx.back = menu.BACK.get(cmd, "")
        read, write = self._tables()
        if cmd in read:
            await read[cmd](ctx, args)
        elif cmd in write:
            if self.read_only:
                await self.reply(ctx, card("🔒", "READ-ONLY", codes("Controls are off on this bot")))
                return
            await write[cmd](ctx, args)
        else:
            await self.reply(ctx, card("❔", "UNKNOWN COMMAND", codes(f"/{cmd}"), codes("/help")))

    # ------------------------------------------------------------------ Lighter and the funding arbitrage
    async def c_lighter(self, ctx: Ctx, name: str, rest: list[str]) -> None:
        """/l_<name> ...: the same command on Lighter (/l or /l_menu lists them). The panel sends its own replies."""
        panel = self.lighter.load()
        if panel is None:
            await self.reply(ctx, self.lighter.missing())
            return
        text = "/" + " ".join([name or "menu", *rest])
        await panel.text(ctx.chat_id, ctx.user_id or 0, text, read_only=self.read_only)

    async def _lighter_button(self, ctx: Ctx, data: str) -> str:
        panel = self.lighter.load()
        if panel is None:
            await self.api.send(ctx.chat_id, self.lighter.missing())
            return ""
        return str(await panel.button(ctx.chat_id, ctx.message_id or 0, data, read_only=self.read_only))

    async def c_arb(self, ctx: Ctx, name: str, rest: list[str]) -> None:
        """/arb_<name> ...: the funding arbitrage (/arb lists the commands). Reads answer at once; what changes a LIVE
        bot or its position asks first, and a LIVE start needs the code typed back."""
        loaded = self.arb.load()
        if loaded is None:
            await self.reply(ctx, self.arb.missing())
            return
        tg, ops = loaded
        explicit = [x.lower() for x in rest if x.lower() in ops.MODES]
        rest = [x for x in rest if x.lower() not in ops.MODES]
        mode = explicit[0] if explicit else ops.current_mode()
        if name in ("", "menu", "help"):
            await self.reply(ctx, others.arb_menu(mode, {m: ops.running(m) for m in ops.MODES}, self.arb.live_allowed()),
                             others.arb_keyboard())
            return
        if name in ("start", "stop"):
            if self.read_only:
                await self.reply(ctx, card("🔒", "READ-ONLY", codes("Controls are off on this bot")))
                return
            await self._arb_service(ctx, name, mode if explicit or name == "stop" else "paper", rest)
            return
        argv = tg.to_argv("/" + " ".join([name, *rest]), mode == "live")
        if argv is None:
            await self.reply(ctx, card("❔", "UNKNOWN FUNDING ARB COMMAND", codes(f"/arb_{name}"), codes("/arb lists them")))
            return
        if tg.changes_something(argv):
            if self.read_only:
                await self.reply(ctx, card("🔒", "READ-ONLY", codes("Controls are off on this bot")))
                return
            if mode == "live":      # real money: one tap never does it
                await self._ask(ctx, "arb", {"argv": argv, "title": name}, card(
                    "⚖️", f"FUNDING ARB · {name.upper()} (LIVE)", codes("arb " + " ".join(argv))))
                return
        self._spawn(self._arb_run(ctx.chat_id, name, argv))

    async def _arb_run(self, chat_id: int, title: str, argv: list[str]) -> None:
        """Run one arbitrage command and post what it printed (a scan reads both venues: it takes a while)."""
        from arbitrage import telegram as tg

        try:
            out = await tg.run_command(argv)
        except Exception as e:
            out = f"{type(e).__name__}: {redact_str(str(e))[:300]}"
        await self.api.send(chat_id, others.arb_card(title, out), keyboard=others.arb_keyboard())

    async def _arb_service(self, ctx: Ctx, name: str, mode: str, rest: list[str]) -> None:
        """/arb_start 120 120 (paper) · /arb_start live · /arb_stop [live]: the executor in the background."""
        from arbitrage import ops

        if name == "stop":
            if not ops.running(mode):
                await self.reply(ctx, card("ℹ️", f"FUNDING ARB · {mode.upper()} IS NOT RUNNING"))
                return
            await self._ask(ctx, "arb_stop", {"mode": mode}, card(
                "⏹", f"FUNDING ARB · STOP {mode.upper()}",
                codes("The position, if any, is kept", "The venues' own stop orders stay", "/arb_close closes it")))
            return
        if ops.running(mode):
            await self.reply(ctx, card("ℹ️", f"FUNDING ARB · {mode.upper()} ALREADY RUNS", codes("/arb_status")))
            return
        if mode == "live":
            if not self.arb.live_allowed():
                await self.reply(ctx, card("🔒", "FUNDING ARB · LIVE IS OFF", codes(
                    "Put ARB_LIVE=1 in arcus/.env, then tbot down and tbot up", "Nothing was started")))
                return
            await self._ask(ctx, "arb_start", {"mode": "live"}, card(
                "🔴", "FUNDING ARB · START LIVE",
                codes("REAL orders on Arcus and on Lighter", "Size: the smaller venue's free margin",
                      "/arb_settings shows the limits (max_notional_usd caps a first run)")), code=True)
            return
        try:
            money = {"arcus": float(rest[0]), "lighter": float(rest[1])}
        except (IndexError, ValueError):
            await self.reply(ctx, card("❔", "FUNDING ARB · PAPER NEEDS PRETEND MONEY", codes(
                "/arb_start 120 120", "dollars on Arcus, then on Lighter", "/arb_start live for real orders")))
            return
        await self._ask(ctx, "arb_start", {"mode": "paper", "money": money}, card(
            "📝", "FUNDING ARB · START PAPER",
            codes(f"Pretend ${money['arcus']:,.0f} on Arcus, ${money['lighter']:,.0f} on Lighter",
                  "Real prices and funding, no real orders")))

    # ------------------------------------------------------------------ scout / pilot: the lists
    async def c_scout(self, ctx: Ctx, args: list[str]) -> None:
        """/top3 [volume|cheapest|max]: that list's top 3 from the last scan, with Run buttons."""
        from arcus.scout.profiles import profile_of

        try:
            prof = profile_of(args[0] if args else None)
        except ValueError as e:
            await self.reply(ctx, card("❌", "UNKNOWN LIST", codes(str(e))))
            return
        await self._list(ctx, prof.key)

    async def c_volume(self, ctx: Ctx, args: list[str]) -> None:
        await self._list(ctx, "volume")

    async def c_cheapest(self, ctx: Ctx, args: list[str]) -> None:
        await self._list(ctx, "cheapest")

    async def c_maxvolume(self, ctx: Ctx, args: list[str]) -> None:
        await self._list(ctx, "max")

    async def _list(self, ctx: Ctx, profile: str, *, force: bool = False) -> None:
        """The list from the last scan, and a scan when that one is stale (or asked for): its ETA under the list."""
        if self.pilot is None:
            await self.reply(ctx, card("ℹ️", "NO PILOT", codes("No pilot on this server")))
            return
        note = self._scan_note(ctx.chat_id, profile, force=force)
        await self.reply(ctx, profile_text(self.pilot.latest_scan(), profile, self.pilot.budget(), time.time(), note),
                         profile_keyboard(profile, self.pilot.top(profile)))

    async def c_pilot(self, ctx: Ctx, args: list[str]) -> None:
        """/openpositions: the owner's positions template, and what is deployed under it."""
        if self.pilot is None:
            await self.c_positions(ctx, args)
            return
        a = self.pilot.active()
        paper = bool(a and a["mode"] == "paper" and self.control.is_running("paper"))
        mode = a["mode"] if a else (self.control.default_mode() or "live")
        await self.reply(ctx, positions_text(self.control.view(mode), running=running_block(self.pilot)),
                         pilot_keyboard(paper=paper))

    async def c_rescan(self, ctx: Ctx, args: list[str]) -> None:
        from arcus.scout.profiles import profile_of

        try:
            prof = profile_of(args[0] if args else None)
        except ValueError as e:
            await self.reply(ctx, card("❌", "UNKNOWN LIST", codes(str(e))))
            return
        await self._list(ctx, prof.key, force=True)

    def _scan_note(self, chat_id: int, profile: str, *, force: bool = False) -> str:
        """Ask for a scan when the last one is stale (or force), unless one is running or queued; one waiter per chat
        and list gets the fresh list when it is done. Returns the line to show, with the measured ETA."""
        from arcus.scout.service import SCAN_NOW, eta_s, next_is_full, read_status, scan_workers

        if self.read_only:
            return ""
        now = time.time()
        st = read_status(Path(self.control.root))
        scan = self.pilot.latest_scan() if self.pilot is not None else None
        trigger = self._state_dir() / SCAN_NOW
        every, want = settings.scout_options(settings.load(self._state_dir()))
        age = now - scan["ts_us"] / 1e6 if scan else float("inf")
        from arcus.common import role as roles

        with contextlib.suppress(ValueError):
            if not roles.ranks(roles.role()):   # a trader: nothing scans here, the lists are fetched (arcus/handoff.py)
                if not (force or age > 1.5 * 60 * float(every or 30)):
                    return ""
                trigger.parent.mkdir(parents=True, exist_ok=True)
                trigger.touch()                 # the follow service takes its next turn now
                return ("This machine does not scan: its lists come from the other machine · fetching now"
                        if os.environ.get("BOT_SYNC_FROM") else
                        "This machine does not scan and no other machine is set to send lists (tbot sync status) · "
                        "/run works without them")
        if st.get("running"):
            started = float(st.get("started") or now)
            eta = eta_s(st, int(st.get("workers") or 1), next_is_full(scan, started))
            note = "Scan running" + (f" · ~{ago(max(60.0, eta - (now - started)))} left" if eta else "")
        elif force or trigger.exists() or age > 1.5 * 60 * float(every or 30):
            trigger.parent.mkdir(parents=True, exist_ok=True)
            trigger.touch()
            eta = eta_s(st, scan_workers(want, bool(self.control.running_modes())), next_is_full(scan, now))
            note = "Scan started" + (f" · ~{ago(eta)}" if eta else "")
        else:
            return ""
        if self.pilot is None:
            return note
        if not any((w["chat"], w["profile"]) == (chat_id, profile) for w in self.scan_waiters):
            self.scan_waiters.append({"chat": chat_id, "profile": profile, "since": now})
        return note + " · the list follows when it is done"

    async def scan_waiters_tick(self) -> None:
        """Post each waiting list once a scan that started after the request has finished."""
        from arcus.scout.service import SCAN_NOW, read_status

        if not self.scan_waiters or self.pilot is None:
            return
        scan = self.pilot.latest_scan() or {}
        done = scan.get("ts_us", 0) / 1e6
        busy = read_status(Path(self.control.root)).get("running") or (self._state_dir() / SCAN_NOW).exists()
        keep = []
        for w in self.scan_waiters:
            waited = time.time() - w["since"]
            if done > w["since"]:
                await self.api.send(w["chat"], profile_text(scan, w["profile"], self.pilot.budget(), time.time()),
                                    keyboard=profile_keyboard(w["profile"], self.pilot.top(w["profile"])))
            elif not busy and waited > SCAN_WAIT_S:
                await self.api.send(w["chat"], card("⌛", "NO SCAN FINISHED",
                                                    codes(f"Waited {ago(waited)} · is the scout running?", "tbot status")))
            elif waited < SCAN_GIVE_UP_S:
                keep.append(w)
        self.scan_waiters = keep

    def _pick_args(self, args: list[str]) -> tuple[str, int] | None:
        """[profile] k: button data from before the lists (plain "pick 1") means the default list."""
        if args and args[0].isdigit():
            return "volume", int(args[0])
        if len(args) >= 2 and args[1].isdigit():
            return args[0], int(args[1])
        return None

    async def c_pick(self, ctx: Ctx, args: list[str]) -> None:
        """▶️ k on a list: the run form with that setup, at the leverage the list backtested."""
        from arcus.scout.pilot import setup_of

        parsed = self._pick_args(args)
        if self.pilot is None or parsed is None:
            return
        profile, k = parsed
        try:
            c = self.pilot.pick(k, profile, "rec")
        except ValueError as e:
            await self.reply(ctx, card("❌", "CANNOT PICK", codes(str(e))))
            return
        await self._form(ctx, c["market"], setup_of(c), float(c["leverage"]), profile=profile)

    async def c_old_button(self, ctx: Ctx, args: list[str]) -> None:
        await self.reply(ctx, card("ℹ️", "OLD BUTTON", codes("From an older version · /top3 or /run")))

    # ------------------------------------------------------------------ run any setup: the run form
    async def c_run(self, ctx: Ctx, args: list[str]) -> None:
        """/run: the run form (Tread.fi's order form as buttons) for a market. /run BTC mid 0 long 40x live sl=10 tp=5
        vol=100k: any part may be left out (the form opens with the rest); with paper or live and a setup it goes
        straight to the confirmation. Old names still work (/run BTC touch 0bp 40x live). /run <session file> starts
        a session file as before."""
        names = {x["name"]: x for x in self.control.sessions()}
        if args and args[0] in names:
            await self._run_session(ctx, args[0], any(a.lower() == "live" for a in args[1:]), names[args[0]])
            return
        if self.pilot is None:
            await self.reply(ctx, sessions_text(list(names.values()), self.control.runs()))
            return
        if not args:
            await self.reply(ctx, card("🎛", "Run Form", codes("Pick a market")),
                             markets_keyboard(self.pilot.latest_scan()))
            return
        args, limits = _take_limits(args)
        if isinstance(limits, str):
            await self.reply(ctx, card("❌", "BAD LIMIT", codes(limits)))
            return
        parsed = self._parse_run(args)
        if isinstance(parsed, str):
            await self.reply(ctx, card("❌", "CANNOT RUN", codes(*parsed.split("\n"))))
            return
        market, setup, lev, mode = parsed
        if mode is not None and setup is not None:
            await self._deploy_flow(ctx, market, setup.name, lev or "max", "manual", live=mode == "live",
                                    limits=limits)
            return
        await self._form(ctx, market, setup, lev, limits)

    def _parse_run(self, args: list[str]) -> tuple[str, Any, float | str | None, str | None] | str:
        """["BTC", "mid", "+1", "long", "40x", "live"] -> ("BTC-USD", Setup(mid, 1, long), 40.0, "live"), in any
        order after the market; a string is the error to show (plain lines)."""
        from arcus.strategies import setup as su

        markets = {c["market"] for c in (self.pilot.latest_scan() or {}).get("all") or []} if self.pilot else set()
        m = args[0].upper()
        market = m if "-" in m else f"{m}-USD"
        if market not in markets:
            return f"Unknown market {args[0]}\n/run lists them"
        mode: str | None = None
        lev: float | str | None = None
        words = []
        for a in (x.strip("\"'“”") for x in args[1:]):
            t = a.lower()
            if t in ("paper", "live"):
                mode = t
            elif t == "max" or (t.endswith("x") and t[:-1].replace(".", "", 1).isdigit()):
                lev = "max" if t == "max" else float(t[:-1])
            else:
                words.append(a)
        if not words:
            return (market, None, lev, None) if mode is None else \
                f"Which setup? e.g. /run {short(market)} mid 0 {mode}"
        try:
            return market, su.parse(" ".join(words)), lev, mode
        except ValueError as e:
            return f"{e}\n/run {short(market)} opens the form"

    def _default_setup(self, market: str) -> tuple[Any, float | str]:
        """What the form opens with: the market's pick in the Most Volume list, else its most-volume backtest, else
        Mid 0 Neutral; at the market's maximum leverage."""
        from arcus.scout import profiles
        from arcus.scout.pilot import setup_of
        from arcus.strategies import setup as su

        assert self.pilot is not None
        scan = self.pilot.latest_scan()
        rows = [c for c in profiles.top(scan, profiles.DEFAULT, self.pilot.budget(), n=100) if c["market"] == market]
        rows = rows or sorted((c for c in self.pilot.rows(market) if c.get("days")),
                              key=lambda c: -float(c["volume_day"]))
        for c in rows:
            with contextlib.suppress(ValueError):
                return setup_of(c), "max"
        return su.Setup(), "max"

    async def _form(self, ctx: Ctx, market: str, setup: Any = None, lev: float | str | None = None,
                    limits: dict[str, float] | None = None, profile: str = "manual") -> None:
        """The run form for one market (edited in place when it came from one of its buttons). profile: the list a
        pick came from; the setup is judged by it while it stays in it (Pilot.review), else it is the owner's own."""
        from arcus.scout.pilot import stale_note

        assert self.pilot is not None
        if setup is None:
            setup, lev0 = self._default_setup(market)
            lev = lev or lev0
        await self._fresh_balance()
        try:
            c = self._find(market, setup.name, lev or "max", profile)
        except ValueError as e:
            await self.reply(ctx, card("❌", "CANNOT RUN", codes(str(e))))
            return
        from arcus.scout.scan import BY_NAME

        lim = limits or {}
        c = {**c, **lim, "in_menu": setup.name in BY_NAME}
        live_ok = os.environ.get("BOT_PILOT_LIVE") == "1"
        running = [m for m in ("paper", "live") if self.control.is_running(m)]
        await self.reply(ctx, form_text(c, self.pilot.budget(), running, live_ok,
                                        stale_note(self.pilot.latest_scan())),
                         form_keyboard(market, setup, float(c["leverage"]), self._leverages(market),
                                       lim.get("max_loss_usd") or 0.0, lim.get("volume_target_usd") or 0.0,
                                       lim.get("take_profit_usd") or 0.0, live_ok, profile))

    def _leverages(self, market: str) -> list[float]:
        """The form's leverage buttons: the market's maximum and the ladder below it, and any the scan backtested."""
        assert self.pilot is not None
        return sorted({*self.pilot.leverages(market), *(float(r["leverage"]) for r in self.pilot.rows(market))},
                      reverse=True)[:6]

    @staticmethod
    def _form_args(args: list[str]) -> tuple[str, Any, float, dict[str, float], str] | None:
        """Button data of the form: market, setup id, leverage, run stop $, volume target $k, take profit $, list."""
        from arcus.scout.profiles import PROFILES
        from arcus.strategies import setup as su

        if len(args) < 7 or args[6] not in PROFILES:
            return None
        s = su.from_sid(args[1])
        try:
            lev, sl, vol, tp = float(args[2]), float(args[3]), float(args[4]) * 1000, float(args[5])
        except ValueError:
            return None
        if s is None:
            return None
        lim = {k: v for k, v in (("max_loss_usd", sl), ("volume_target_usd", vol), ("take_profit_usd", tp)) if v > 0}
        return args[0], s, lev, lim, args[6]

    async def c_rm(self, ctx: Ctx, args: list[str]) -> None:
        """A market button under /run: its run form."""
        if args and self.pilot is not None:
            await self._form(ctx, args[0])

    async def c_f(self, ctx: Ctx, args: list[str]) -> None:
        """A field of the run form changed: the form again, with the new setup's backtest."""
        got = self._form_args(args)
        if got is None or self.pilot is None:
            await self.c_old_button(ctx, args)
            return
        market, s, lev, lim, profile = got
        await self._form(ctx, market, s, lev, lim, profile)

    async def c_fd(self, ctx: Ctx, args: list[str]) -> None:
        """📝 Paper / 🔴 LIVE on the run form."""
        got = self._form_args(args[:7])
        if got is None or self.pilot is None or len(args) < 8:
            await self.c_old_button(ctx, args)
            return
        market, s, lev, lim, profile = got
        await self._deploy_flow(ctx, market, s.name, lev, profile, live=args[7] == "live", limits=lim)

    def _find(self, market: str, setting: str, lev: float | str, profile: str) -> dict[str, Any]:
        """The scan row to run, judged by `profile` when it is in that list, else as the owner's own pick."""
        from arcus.scout.profiles import profile_of, verdict

        assert self.pilot is not None
        c: dict[str, Any] = self.pilot.find(market, setting, lev)
        p = profile_of(profile)
        if p.listed and not verdict(c, p, self.pilot.budget()):
            c["profile"] = p.key
            if c.get("at_max"):
                c["lev"] = "max"
        return c

    async def _fresh_balance(self) -> None:
        """Read the account now and log it, so a run is sized for today's balance (Pilot.capital_now), not the one the
        scan sized for this morning. Best effort: the last logged balance stands if the read fails."""
        from arcus.common.config import load_arcus_config
        from arcus.scout.capital import account_snapshot

        idx = self.pilot.account_index if self.pilot is not None else 0
        try:
            url = load_arcus_config(Path(self.control.root) / "config" / "venues" / "arcus.yaml").rest.mainnet
            snap = await account_snapshot(url, idx)
        except Exception as e:
            log.warning("balance_read_failed", reason=type(e).__name__)
            return
        if snap and snap["equity"] > 0:
            BalanceLog(self._state_dir() / "balances.jsonl").record(
                source="telegram", account_index=idx, equity=snap["equity"], free=snap["free"],
                net_deposits=snap["net_deposits"])

    async def c_golive(self, ctx: Ctx, args: list[str]) -> None:
        """🔴 Go LIVE on a running paper deployment: the same market, setting and leverage, with real money."""
        from arcus.scout.pilot import setting_of

        a = self.pilot.active() if self.pilot is not None else None
        if not a or a["mode"] != "paper":
            await self.reply(ctx, card("ℹ️", "NO PAPER RUN", codes("Nothing to take live · /openpositions")))
            return
        lev = (a.get("backtest") or {}).get("leverage") or float(a["config"].split(" @ ")[1].rstrip("x"))
        limits = {k: float(a[k]) for k in ("max_loss_usd", "take_profit_usd", "volume_target_usd") if a.get(k)}
        await self._deploy_flow(ctx, a["market"], setting_of(a), float(lev), a.get("profile") or "manual", live=True,
                                limits=limits)

    async def _deploy_flow(self, ctx: Ctx, market: str, setting: str, lev: float | str, profile: str, *,
                           live: bool, limits: dict[str, float] | None = None) -> None:
        """Paper: a Confirm button. LIVE: BOT_PILOT_LIVE=1, a passing doctor, then a typed one-time code.
        limits: the run's own limits, {max_loss_usd, take_profit_usd, volume_target_usd} (sl=, tp=, vol=)."""
        from arcus.scout.pilot import limit_lines, setting_of

        assert self.pilot is not None
        await self._fresh_balance()
        try:
            c = self._find(market, setting, lev, profile)
        except ValueError as e:
            await self.reply(ctx, card("❌", "CANNOT RUN", codes(str(e))))
            return
        limits = dict(limits or {})
        c = {**c, **limits}
        w = what(c)
        args_ = {"market": c["market"], "setting": setting_of(c), "lev": float(c["leverage"]),
                 "profile": c["profile"], "live": live, "limits": limits}
        mode = "live" if live else "paper"
        # alive, not just running: a bot that is closing has stopped its heartbeat but not exited yet
        alive = self.control.alive(mode)
        running = self.pilot.active() if alive else None
        paused = self.control.paused(mode)
        notes = codes(*limit_lines(c, ":"),
                      f"Replaces the running {mode.upper()} bot ({short(running['market'])} · {running['config']}): "
                      "it closes its orders and position first" if running else
                      f"Starts once the {mode.upper()} bot that is stopping has exited" if alive else "",
                      f"Clears your pause on new orders ({pause_where(paused)})" if paused else "")
        if not live:
            await self._ask(ctx, "deploy", args_, card("📝", "PAPER RUN", codes(w, *size_lines(c)), notes))
            return
        if os.environ.get("BOT_PILOT_LIVE") != "1":
            await self.reply(ctx, card("🔒", "LIVE IS OFF", codes("BOT_PILOT_LIVE=1 in .env, then restart")))
            return
        await self.reply(ctx, card("🩺", "Checking Account", codes(w)))

        async def go() -> None:
            try:   # checked from its own file: the running bot's session stays as it is until the owner confirms
                path = self.pilot.write_session(c, live=True, path=self._state_dir() / "pilot_check.yaml")
                ok, rep = await self.control.doctor(str(path), replacing=self.control.alive("live"))
            except Exception as e:
                await self.api.send(ctx.chat_id, card("⚠️", "CHECK FAILED", codes(redact_str(str(e))[:300])))
                return
            if not ok:
                await self.api.send(ctx.chat_id, cannot_start(w, rep))
                return
            await self._ask(Ctx(ctx.chat_id, ctx.user_id, ctx.user), "deploy", args_,
                            card("🔴", "LIVE RUN", codes(w, *size_lines(c)), notes, _warnings(rep)), code=True)
        self._spawn(go())

    # ------------------------------------------------------------------ autopilot
    def _auto_card(self, title: str = "AUTOPILOT", emoji: str = "🤖") -> tuple[str, Keyboard]:
        from arcus.scout import autopilot as ap
        from arcus.scout import playbook as pbk

        st = ap.load(self._state_dir())
        s = ap.settings_of(st)
        pb = pbk.load(self.pilot.root / "data" / "scout") if self.pilot is not None else {}
        plan = ap.plan_lines(pb, ap.ceiling(s, pb)) if pb.get("markets") else ["No playbook yet: the scout builds it after "
                                                                          "its next scan"]
        return card(emoji, title, codes(*ap.status_lines(st, pb)),
                    section("Next 24 h at a usual market", codes(*plan)),
                    codes("Picks the most volume within the ceiling, per session and market state",
                          "Flat around CPI · NFP · FOMC and a stock's earnings")), auto_keyboard(s.on)

    async def c_auto(self, ctx: Ctx, args: list[str]) -> None:
        """/auto: what the autopilot does now and would do next. /auto on [paper|live] [budget=5] [cost=1.5],
        /auto off, /auto budget 5, /auto cost 1.5|auto."""
        from arcus.scout import autopilot as ap

        sub = args[0].lower() if args else ""
        if sub in ("", "status", "plan"):
            text, kb = self._auto_card()
            await self.reply(ctx, text, kb)
            return
        if self.read_only:
            await self.reply(ctx, card("🔒", "READ-ONLY", codes("Controls are off on this bot")))
            return
        s = ap.settings_of(ap.load(self._state_dir()))
        try:
            if sub == "on":
                live = any(a.lower() == "live" for a in args[1:])
                kw = {k.lower(): v for k, _, v in (a.partition("=") for a in args[1:] if "=" in a)}
                budget = _amount(kw["budget"]) if "budget" in kw else s.budget_day
                cost = kw.get("cost")
                args_ = {"live": live, "budget": budget, "cost": None if cost in (None, "auto") else float(cost)}
                ap.validate(ap.Settings(budget_day=budget, max_cost_bp=args_["cost"] or s.max_cost_bp))
                what = codes(f"{'LIVE' if live else 'PAPER'} · budget ${budget:.2f}/day (the pot holds "
                             f"{ap.POT_DAYS:g} days)",
                             f"Cost ceiling {args_['cost']:.2f} bp" if args_["cost"] else
                             "Cost ceiling tuned daily for this budget",
                             "Starts, switches and stops runs by itself; each run's stop is what is left of the pot",
                             "Your /run, /stop or /closeall turns it off")
                if not live:
                    await self._ask(ctx, "auto_on", args_, card("🤖", "AUTOPILOT ON · PAPER", what))
                    return
                if os.environ.get("BOT_PILOT_LIVE") != "1":
                    await self.reply(ctx, card("🔒", "LIVE IS OFF", codes("BOT_PILOT_LIVE=1 in .env, then restart")))
                    return
                await self._ask(ctx, "auto_on", args_, card("🔴", "AUTOPILOT ON · LIVE", what,
                                                            codes("A doctor check runs before every start")),
                                code=True)
            elif sub == "off":
                await self._ask(ctx, "auto_off", {}, card("⏹", "AUTOPILOT OFF",
                                                          codes("Closes its run (maker, then taker) and stops")))
            elif sub == "budget" and len(args) > 1:
                ap.configure(self._state_dir(), budget_day=_amount(args[1]))
                text, kb = self._auto_card("BUDGET SAVED", "✅")
                await self.reply(ctx, text, kb)
            elif sub == "cost" and len(args) > 1:
                if args[1].lower() == "auto":
                    ap.configure(self._state_dir(), auto_cost=True)
                else:
                    ap.configure(self._state_dir(), max_cost_bp=float(args[1].lower().removesuffix("bp")),
                                 auto_cost=False)
                text, kb = self._auto_card("COST CEILING SAVED", "✅")
                await self.reply(ctx, text, kb)
            else:
                await self.reply(ctx, card("❔", "USAGE", codes("/auto", "/auto on paper|live [budget=5] [cost=1.5]",
                                                               "/auto off", "/auto budget 5", "/auto cost 1.5|auto")))
        except (ValueError, KeyError) as e:
            await self.reply(ctx, card("❌", "NOT SAVED", codes(str(e))))

    async def c_pilotclose(self, ctx: Ctx, args: list[str]) -> None:
        a = self.pilot.active() if self.pilot is not None else None
        if not a:
            await self.reply(ctx, card("ℹ️", "NOTHING DEPLOYED", codes("/top3 · /run")))
            return
        await self._ask(ctx, "pilotclose", {}, card("⏹", "CLOSE AND STOP", codes(
            f"{short(a['market'])} · {a['config'].replace(' @ ', ' · ')} · {a['mode'].upper()}",
            "Maker exit, then taker if it does not fill")))

    # ------------------------------------------------------------------ balance and settings
    def _state_dir(self) -> Path:
        return Path(self.control.root) / self.control.app.state_dir

    async def c_balance(self, ctx: Ctx, args: list[str]) -> None:
        """Read the account now, log it (state/balances.jsonl), and show it with its history."""
        from arcus.common.config import load_arcus_config
        from arcus.scout.capital import STATE, account_snapshot

        blog = BalanceLog(self._state_dir() / "balances.jsonl")
        idx = self.pilot.account_index if self.pilot is not None else 0
        snap = None
        try:
            url = load_arcus_config(Path(self.control.root) / "config" / "venues" / "arcus.yaml").rest.mainnet
            snap = await account_snapshot(url, idx)
        except Exception as e:  # show the history even when the account cannot be read right now
            log.warning("balance_read_failed", reason=type(e).__name__)
        if snap and snap["equity"] > 0:
            blog.record(source="telegram", account_index=idx, equity=snap["equity"], free=snap["free"],
                        net_deposits=snap["net_deposits"])
        try:
            held = json.loads((self._state_dir() / STATE).read_text())
        except (OSError, ValueError):
            held = None
        view = self.control.view("live") if self.control.is_running("live") else None
        live = next((x.get("size_capital") for x in ((view.snapshot or {}).get("sessions") or [])), None) \
            if view else None
        await self.reply(ctx, balance_text(snap, blog.summary(), held, live, idx), refresh_keyboard("balance"))

    async def c_account(self, ctx: Ctx, args: list[str]) -> None:
        """All-time perps volume, fees paid and earned, the fee tier and the realized result, as Arcus keeps them."""
        from arcus.common.config import load_arcus_config
        from arcus.core import account_stats

        raw, err = None, ""
        try:
            url = load_arcus_config(Path(self.control.root) / "config" / "venues" / "arcus.yaml").rest.mainnet
            raw = await account_stats.read(url)
        except Exception as e:
            log.warning("account_read_failed", reason=type(e).__name__)
            err = _reason(e)[:120]
        await self.reply(ctx, account_text(raw, err), refresh_keyboard("account"))

    async def c_settings(self, ctx: Ctx, args: list[str]) -> None:
        over = settings.load(self._state_dir())
        await self.reply(ctx, settings_text(over, settings.defaults(self.control.app.sizing, 30, "auto")),
                         refresh_keyboard("settings"))

    async def c_set(self, ctx: Ctx, args: list[str]) -> None:
        if len(args) < 2:
            await self.c_settings(ctx, args)
            return
        name, text = args[0].lower(), " ".join(args[1:])
        over = settings.load(self._state_dir())
        base = settings.defaults(self.control.app.sizing, 30, "auto")
        now = settings.show(name, over[name]) if name in over else \
            f"{settings.show(name, base[name])} (default)" if name in base else "default"
        if text.lower() == "default":
            if name not in settings.SETTINGS:
                await self.reply(ctx, card("❌", "UNKNOWN SETTING", codes(name, "/settings lists them")))
                return
            await self._ask(ctx, "set", {"name": name, "value": None, "reset": True},
                            card("⚙️", name.replace("_", " ").upper(), codes(f"Back to its default · now {now}")))
            return
        try:
            value = settings.parse(name, text)
            settings.effective_sizing(self.control.app.sizing, {**over, name: value})   # the whole must be valid
        except ValueError as e:
            await self.reply(ctx, card("❌", "CANNOT SET", codes(_reason(e))))
            return
        s = settings.SETTINGS[name]
        await self._ask(ctx, "set", {"name": name, "value": value, "reset": False},
                        card("⚙️", name.replace("_", " ").upper(), codes(f"Now {now}",
                                                                           f"New {settings.show(name, value)}"),
                             codes(s.help), codes(f"Applies {s.applies}")))

    async def c_scannow(self, ctx: Ctx, args: list[str]) -> None:
        if self.pilot is None:
            await self.reply(ctx, card("🔎", "SCAN REQUESTED",
                                       codes(self._scan_note(ctx.chat_id, "volume", force=True))))
            return
        await self._list(ctx, "volume", force=True)

    # ------------------------------------------------------------------ live dashboard
    def _dash_load(self) -> dict[str, Any] | None:
        try:
            d: dict[str, Any] = json.loads((self._state_dir() / DASH_FILE).read_text())
            return d if d.get("chat_id") and d.get("message_id") else None
        except (OSError, ValueError):
            return None

    def _dash_save(self, d: dict[str, Any] | None) -> None:
        p = self._state_dir() / DASH_FILE
        if d is None:
            with contextlib.suppress(OSError):
                p.unlink()
            return
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(d))

    async def _dash_text(self, frame: str = "live") -> str:
        """The dashboard now. When the running bot publishes no fresh account reading (no live bot, or one started
        before it did), read the account here, at most once a minute."""
        idx = self.pilot.account_index if self.pilot is not None else 0
        now = time.time()
        d = dashboard.collect(self.control, now=now, account=idx, extra=self._dash_account)
        stale = d.account_age_s is None or d.account_age_s > dashboard.FRESH_S
        own_age = now - self._dash_account["ts"] if self._dash_account else None
        if d.mode != "paper" and stale and (own_age is None or own_age > dashboard.FRESH_S):
            from arcus.common.config import load_arcus_config
            from arcus.scout.capital import account_snapshot

            try:
                url = load_arcus_config(Path(self.control.root) / "config" / "venues" / "arcus.yaml").rest.mainnet
                snap = await account_snapshot(url, idx)
            except Exception as e:   # the dashboard shows the last reading instead
                log.warning("dashboard_account_failed", reason=type(e).__name__)
                snap = None
            if snap and snap["equity"] > 0:
                self._dash_account = {**snap, "ts": now, "from": "the dashboard"}
                d = dashboard.collect(self.control, now=now, account=idx, extra=self._dash_account)
        return dashboard.render(d, html=True, frame=frame)

    async def _dash_retire(self, st: dict[str, Any], note: str = "") -> None:
        """Last frame on a dashboard message that stops updating, with a button to start it again; unpin it."""
        try:
            text = await self._dash_text("stopped")
            await self.api.edit(st["chat_id"], st["message_id"], text + (f"\n{note}" if note else ""),
                                keyboard=dashboard_keyboard(live=False))
        except TelegramError:
            pass   # deleted or too old: nothing to retire
        with contextlib.suppress(TelegramError):
            await self.api.call("unpinChatMessage", chat_id=st["chat_id"], message_id=st["message_id"])

    async def _dash_start(self, chat_id: int, message_id: int) -> None:
        old = self._dash_load()
        self._dash_save({"chat_id": chat_id, "message_id": message_id, "since": time.time()})
        if old and (old["chat_id"], old["message_id"]) != (chat_id, message_id):
            await self._dash_retire(old, "<i>A newer dashboard is below.</i>")
        with contextlib.suppress(TelegramError):   # pinned: stays at the top of the chat while alerts arrive below
            await self.api.call("pinChatMessage", chat_id=chat_id, message_id=message_id, disable_notification=True)

    async def c_dashboard(self, ctx: Ctx, args: list[str]) -> None:
        """A new message that updates itself every 10 s (and replaces any older live dashboard)."""
        msg = await self.api.send(ctx.chat_id, await self._dash_text(), keyboard=dashboard_keyboard(), silent=True)
        if msg and msg.get("message_id"):
            await self._dash_start(ctx.chat_id, int(msg["message_id"]))

    async def c_dashresume(self, ctx: Ctx, args: list[str]) -> None:
        """▶️ on a stopped dashboard: that same message goes live again."""
        if ctx.message_id is None:
            await self.c_dashboard(ctx, args)
            return
        await self.api.edit(ctx.chat_id, ctx.message_id, await self._dash_text(), keyboard=dashboard_keyboard())
        await self._dash_start(ctx.chat_id, ctx.message_id)

    async def c_dashstop(self, ctx: Ctx, args: list[str]) -> None:
        st = self._dash_load()
        if st is None and ctx.message_id is not None:
            st = {"chat_id": ctx.chat_id, "message_id": ctx.message_id}
        self._dash_save(None)
        if st is not None:
            await self._dash_retire(st)

    async def _dashboard_loop(self) -> None:
        """Edit the live dashboard every 10 s, on the 10 s marks."""
        while True:
            await asyncio.sleep(dashboard.REFRESH_S - time.time() % dashboard.REFRESH_S)
            try:
                await self._dash_tick()
            except Exception as e:   # a bad frame must not end the loop
                log.error("dashboard_failed", reason=type(e).__name__, data={"err": str(e)[:300]}, exc_info=True)

    async def _dash_tick(self) -> None:
        """One refresh of the live dashboard. A message that was deleted ends it."""
        st = self._dash_load()
        if st is None:
            return
        try:
            await self.api.edit(st["chat_id"], st["message_id"], await self._dash_text(), keyboard=dashboard_keyboard())
        except TelegramError as e:
            if "not found" not in str(e) and "can't be edited" not in str(e):
                log.warning("dashboard_edit_failed", reason=str(e)[:200])
                return
            log.info("dashboard_gone", reason=str(e)[:120])
            if self._dash_load() == st:
                self._dash_save(None)

    async def c_menu(self, ctx: Ctx, args: list[str]) -> None:
        """/start and /menu (the owner's "Bot Control" template) with every button; in the menu layout, Home."""
        if self.ui() == "menu":
            await self.c_home(ctx, args)
            return
        idx = self.pilot.account_index if self.pilot is not None else 0
        await self.reply(ctx, dashboard.control_text(dashboard.collect(self.control, account=idx)), menu_keyboard())

    async def c_help(self, ctx: Ctx, args: list[str]) -> None:
        await self.reply(ctx, HELP, [[("☰ Home", "home")]] if self.ui() == "menu" else menu_keyboard())

    # ------------------------------------------------------------------ the menu layout (arcus/telegram/menu.py)
    def _venue(self, chat: int) -> str:
        return self.venues.get(chat, "arcus")

    async def _home_card(self, chat: int) -> tuple[str, Keyboard]:
        """The Home card of the venue the buttons act on, and its six buttons."""
        v = self._venue(chat)
        if v == "lighter":
            text = self._lighter_home()
        elif v == "arb":
            text = self._arb_home()
        else:
            idx = self.pilot.account_index if self.pilot is not None else 0
            text = dashboard.control_text(dashboard.collect(self.control, account=idx), "Arcus · Home",
                                          ["Read-only: controls are off"] if self.read_only else None)
        return text, menu.home_keyboard(v)

    def _lighter_home(self) -> str:
        panel = self.lighter.load()
        if panel is None:
            return self.lighter.missing()
        try:
            from lighter_bot.telegram.embed import relabel

            return str(relabel(str(panel.bot.status_card())))
        except Exception as e:
            log.warning("lighter_home_failed", reason=type(e).__name__)
            return card("⚡", "LIGHTER · Home", codes("Its status could not be read", "/l_status"))

    def _arb_home(self) -> str:
        loaded = self.arb.load()
        if loaded is None:
            return self.arb.missing()
        _tg, ops = loaded
        return others.arb_home(ops.current_mode(), {m: ops.running(m) for m in ops.MODES}, self._arb_live_allowed())

    def _arb_live_allowed(self) -> bool:
        try:
            return self.arb.live_allowed()
        except Exception:
            return False

    async def c_home(self, ctx: Ctx, args: list[str]) -> None:
        text, kb = await self._home_card(ctx.chat_id)
        await self.reply(ctx, text, kb)

    async def c_venue(self, ctx: Ctx, args: list[str]) -> None:
        """🔀 Venue: Arcus → Lighter → Funding arb → Arcus. Only the Home buttons follow it; typed commands do not."""
        self.venues[ctx.chat_id] = menu.next_venue(self._venue(ctx.chat_id))
        await self.c_home(ctx, args)

    def _screen(self, group: str) -> Any:
        async def show(ctx: Ctx, args: list[str]) -> None:
            v = self._venue(ctx.chat_id)
            await self.reply(ctx, menu.screen_text(v, group, read_only=self.read_only),
                             menu.screen_keyboard(v, group, live_allowed=self._arb_live_allowed()))
        return show

    async def c_status(self, ctx: Ctx, args: list[str]) -> None:
        modes = [a.lower() for a in args if a.lower() in MODES] or self.control.known_modes()
        text = status_text([self.control.view(m) for m in modes],
                           running=running_block(self.pilot) if self.pilot is not None else None)
        await self.reply(ctx, text, refresh_keyboard("status"))

    async def _need_mode(self, ctx: Ctx, args: list[str], **kw: Any) -> tuple[str | None, list[str]]:
        mode, rest = self._mode(args, **kw)
        if mode is None:
            await self.reply(ctx, card("ℹ️", "NO BOT YET", codes("No bot has run here yet · /run")))
        return mode, rest

    async def c_pnl(self, ctx: Ctx, args: list[str]) -> None:
        mode, _ = await self._need_mode(ctx, args)
        if mode:
            await self.reply(ctx, pnl_text(self.control.view(mode)), refresh_keyboard(f"pnl {mode}"))

    async def c_positions(self, ctx: Ctx, args: list[str]) -> None:
        mode, _ = await self._need_mode(ctx, args)
        if mode:
            await self.reply(ctx, positions_text(self.control.view(mode)), refresh_keyboard(f"positions {mode}"))

    async def c_orders(self, ctx: Ctx, args: list[str]) -> None:
        mode, _ = await self._need_mode(ctx, args)
        if mode:
            await self.reply(ctx, orders_text(self.control.view(mode)), refresh_keyboard(f"orders {mode}"))

    async def c_sessions(self, ctx: Ctx, args: list[str]) -> None:
        await self.reply(ctx, sessions_text(self.control.sessions(), self.control.runs()), refresh_keyboard("sessions"))

    async def c_logs(self, ctx: Ctx, args: list[str]) -> None:
        n = int(args[0]) if args and args[0].isdigit() else 15
        rows = self.control.recent_decisions(min(n, 50))
        if not rows:
            await self.reply(ctx, card("🧾", "NO DECISIONS YET"))
            return
        lines = []
        for r in rows:
            t = dt.datetime.fromtimestamp(int(r.get("ts", 0)) / 1e6, dt.UTC).strftime("%m-%d %H:%M:%S")
            where = "/".join(x for x in (r.get("venue"), r.get("market")) if x)
            lines.append(f"{t} {r.get('event', '')} {where}: {str(r.get('reason', ''))[:140]}")
        await self.reply(ctx, card("🧾", "LATEST DECISIONS", f"<pre>{escape(chr(10).join(lines))}</pre>"),
                         refresh_keyboard(f"logs {n}"))

    async def c_report(self, ctx: Ctx, args: list[str]) -> None:
        mode, rest = self._mode(args)
        date = rest[0] if rest else (dt.datetime.now(dt.UTC) - dt.timedelta(days=1)).strftime("%Y-%m-%d")
        rep = self.control.report(mode or "live", date) if mode else None
        await self.reply(ctx, card("🗓", f"REPORT {date} · {(mode or 'live').upper()}", f"<pre>{escape(rep[:3500])}</pre>")
                         if rep else card("🗓", "NO REPORT YET",
                                          codes(f"{date} · {(mode or 'live').upper()}", "Written just after 00:00 UTC")))

    async def c_ping(self, ctx: Ctx, args: list[str]) -> None:
        run = self.control.running_modes()
        await self.reply(ctx, card("🏓", "PONG", codes("Running: " + (", ".join(run) if run else "nothing"))))

    async def c_alerts(self, ctx: Ctx, args: list[str]) -> None:
        p = self.prefs
        if len(args) >= 2 and args[0] == "fills" and args[1] in ("each", "summary", "off"):
            p.fills = args[1]
        elif len(args) >= 2 and args[0] == "digest":
            p.digest = args[1] == "on"
        elif len(args) >= 2 and args[0] == "pnl":
            p.pnl_alerts = args[1] == "on"
        p.save(self.prefs_path)
        muted = f"Muted until {dt.datetime.fromtimestamp(p.mute_until, dt.UTC):%H:%M} UTC" if p.muted() else "On"
        text = card("🔔", "Alerts", codes(muted, "Critical alerts (tbot down, safe mode, loss limit) always come"),
                    codes(f"Fills {p.fills} · PnL warnings {'on' if p.pnl_alerts else 'off'} · daily digest "
                          f"{'on' if p.digest else 'off'}"))
        kb: Keyboard = [
            [("Fills: each", "alerts fills each"), ("hourly", "alerts fills summary"), ("off", "alerts fills off")],
            [("PnL warnings " + ("off" if p.pnl_alerts else "on"), f"alerts pnl {'off' if p.pnl_alerts else 'on'}"),
             ("Digest " + ("off" if p.digest else "on"), f"alerts digest {'off' if p.digest else 'on'}")],
            [("🔕 Mute 1h", "mute 60"), ("🔔 Unmute", "unmute"), ("☰ Menu", "menu")],
        ]
        await self.reply(ctx, text, kb)

    async def c_mute(self, ctx: Ctx, args: list[str]) -> None:
        minutes = int(args[0]) if args and args[0].isdigit() else 60
        self.prefs.mute_until = time.time() + minutes * 60
        self.prefs.save(self.prefs_path)
        await self.reply(ctx, card("🔕", f"MUTED {minutes} MIN", codes("Critical alerts still come · /unmute")))

    async def c_unmute(self, ctx: Ctx, args: list[str]) -> None:
        self.prefs.mute_until = 0.0
        self.prefs.save(self.prefs_path)
        await self.reply(ctx, card("🔔", "ALERTS ON"))

    # ------------------------------------------------------------------ controls
    async def c_pause(self, ctx: Ctx, args: list[str]) -> None:
        mode, rest = await self._need_mode(ctx, args)
        if not mode:
            return
        market = rest[0].upper() if rest and rest[0].lower() != "all" else None
        self.control.set_pause(mode, market, f"Telegram ({ctx.user})")
        await self.reply(ctx, card("⏸", "NEW ORDERS PAUSED", codes((market or "All markets")
                                                                   + (" · paper" if mode == "paper" else "")),
                                   codes("Existing exit orders remain active.",
                                         "" if self.control.is_running(mode) else "Applies when the bot starts.")),
                         [[("▶️ Unpause", f"unpause {market or 'all'} {mode}"), ("🩺 Status", f"status {mode}")]])

    async def c_unpause(self, ctx: Ctx, args: list[str]) -> None:
        mode, rest = await self._need_mode(ctx, args)
        if not mode:
            return
        market = rest[0].upper() if rest and rest[0].lower() != "all" else None
        left = self.control.clear_pause(mode, market)
        risk = _risk(self.control.view(mode))   # a safety stop is cleared by /resumeaftersl, not by an unpause
        await self.reply(ctx, card("▶️", "NEW ORDERS RESUMED", codes((market or "All markets")
                                                                     + (" · paper" if mode == "paper" else "")),
                                   codes(f"Still paused: {pause_where(left)}" if left else "",
                                         f"A safety stop still holds: {risk[0][:80]}" if risk else "",
                                         "/resumeaftersl" if risk else "")),
                         refresh_keyboard(f"status {mode}"))

    async def _ask(self, ctx: Ctx, action: str, args: dict[str, Any], desc: str, *, code: bool = False) -> None:
        pid = secrets.token_hex(4)
        p = Pending(pid, action, args, ctx.chat_id, desc)
        if code:   # the owner's template: "Confirm within 2 min", then the code to type back
            p.code = f"{secrets.randbelow(900000) + 100000}"
            await self.api.send(ctx.chat_id, f"{desc}\n\n{b('Confirm within 2 min')}\n\n{mono(p.code)}",
                                keyboard=[[("✖️ Cancel", f"no {pid}")]])
        else:
            await self.reply(ctx, desc, confirm_keyboard(pid))
            p.message_id = ctx.message_id
        self.pending = {k: v for k, v in self.pending.items() if v.expires > time.time()}
        self.pending[pid] = p

    async def c_stop(self, ctx: Ctx, args: list[str]) -> None:
        mode, _ = self._mode(args, need_running=True)
        if not mode:
            await self.reply(ctx, card("ℹ️", "NO BOT RUNNING"))
            return
        await self._ask(ctx, "stop", {"mode": mode}, card("⏹", f"STOP {mode.upper()} BOT",
                                                          codes("Quotes cancelled · positions kept")))

    async def c_resume(self, ctx: Ctx, args: list[str]) -> None:
        mode, rest = await self._need_mode(ctx, args)
        if not mode:
            return
        venue = rest[0] if rest and rest[0] in VENUES else None
        await self._ask(ctx, "resume", {"mode": mode, "venue": venue},
                        card("▶️", "RESUME AFTER A STOP", codes(f"{mode.upper()} · clears safe mode, the daily stop "
                                                                 "and the kill", "A daily stop re-arms from now"),
                             codes("Check /logs first")))

    async def _run_session(self, ctx: Ctx, name: str, live: bool, sess: dict[str, Any]) -> None:
        """/run <session file> [live]: a session file from config/sessions (the pilot's own is pilot.yaml)."""
        mode = "live" if live else "paper"
        if self.control.is_running(mode):
            await self.reply(ctx, card("❌", "Cannot Start", codes(name),
                                       section("Reason", codes(f"Another {mode.upper()} bot is already running.")),
                                       section("Action", codes(f"/stop {mode}"))))
            return
        if not live:
            await self._ask(ctx, "run", {"name": name, "live": False}, card("📝", "PAPER RUN", codes(name)))
            return
        if not sess.get("live_enabled"):
            await self.reply(ctx, card("❌", "Cannot Start", codes(name),
                                       section("Reason", codes(f"live_enabled: false in config/sessions/{name}.yaml")),
                                       section("Action", codes("Set live_enabled: true in that file"))))
            return
        await self.reply(ctx, card("🩺", "Checking Account", codes(name)))
        self._spawn(self._doctor_then_ask(ctx, name))

    async def _doctor_then_ask(self, ctx: Ctx, name: str) -> None:
        try:
            ok, rep = await self.control.doctor(name)
        except Exception as e:
            await self.api.send(ctx.chat_id, card("⚠️", "CHECK FAILED", codes(redact_str(str(e))[:300])))
            return
        if not ok:
            await self.api.send(ctx.chat_id, cannot_start(name, rep))
            return
        await self._ask(Ctx(ctx.chat_id, ctx.user_id, ctx.user), "run", {"name": name, "live": True},
                        card("🔴", "LIVE RUN", codes(name), _warnings(rep)), code=True)

    async def c_doctor(self, ctx: Ctx, args: list[str]) -> None:
        if not args:
            await self.reply(ctx, card("❔", "USAGE", codes("/doctor <session>")))
            return
        await self.reply(ctx, card("🩺", "Running Doctor", codes(args[0])))

        async def go() -> None:
            try:
                ok, rep = await self.control.doctor(args[0])
                await self.api.send(ctx.chat_id, card("✅" if ok else "❌", "READY" if ok else "NOT READY",
                                                      f"<pre>{escape(rep[:3500])}</pre>"))
            except Exception as e:
                await self.api.send(ctx.chat_id, card("⚠️", "CHECK FAILED", codes(redact_str(str(e))[:300])))
        self._spawn(go())

    def _venue_mode(self, args: list[str]) -> tuple[str | None, str, list[str]]:
        mode, rest = self._mode(args)
        venue = next((a for a in rest if a in VENUES), "arcus")
        return mode, venue, [a for a in rest if a not in VENUES]

    async def c_cancelall(self, ctx: Ctx, args: list[str]) -> None:
        mode, venue, _ = self._venue_mode(args)
        if mode in (None, "paper"):
            await self.reply(ctx, card("ℹ️", "PAPER BOT", codes("Cancel-all is for a real account",
                                                                "Paper: /pauseneworders or /stop")))
            return
        await self._ask(ctx, "cancelall", {"mode": mode, "venue": venue},
                        card("❌", "CANCEL ALL ORDERS", codes(f"{venue.title()} {'MAINNET' if mode == 'live' else 'testnet'}",
                                                             "A running bot re-quotes unless paused.")))

    async def c_flatten(self, ctx: Ctx, args: list[str]) -> None:
        mode, venue, rest = self._venue_mode(args)
        if mode in (None, "paper"):
            await self.reply(ctx, card("ℹ️", "PAPER BOT", codes("Close-all is for a real account",
                                                                "Paper: /pauseneworders works the position off")))
            return
        taker = any(a.lower() == "taker" for a in rest)
        await self._ask(ctx, "flatten", {"mode": mode, "venue": venue, "taker": taker},
                        card("🧯", "CLOSE ALL POSITIONS", codes("Reduce-only orders will be used."),
                             codes(f"{venue.title()} {'MAINNET' if mode == 'live' else 'testnet'} · "
                                   f"{'taker (IOC) now' if taker else 'maker at the touch'}",
                                   "Pause first or it may re-open")), code=True)

    # ------------------------------------------------------------------ confirmations
    async def c_ok(self, ctx: Ctx, args: list[str]) -> None:
        p = self.pending.get(args[0]) if args else None
        if p is None or p.expires < time.time() or p.chat_id != ctx.chat_id:
            await self.reply(ctx, card("⌛", "EXPIRED", codes("Run it again")))
            return
        if p.code is not None:
            await self.reply(ctx, card("🔢", "TYPE THE CODE", codes("This one needs the code, not a button")))
            return
        self.pending.pop(p.pid, None)
        await self._execute(ctx, p)

    async def c_no(self, ctx: Ctx, args: list[str]) -> None:
        if args:
            self.pending.pop(args[0], None)
        await self.reply(ctx, card("✖️", "CANCELLED"))

    async def _code(self, ctx: Ctx, text: str) -> None:
        now = time.time()
        for pid, p in list(self.pending.items()):
            if p.code == text and p.chat_id == ctx.chat_id and p.expires > now:
                self.pending.pop(pid, None)
                await self._execute(Ctx(ctx.chat_id, ctx.user_id, ctx.user), p)
                return
        if self.lighter.waits_for_code(ctx.chat_id) and not self.read_only:   # a Lighter LIVE start's code
            await self.lighter.panel.text(ctx.chat_id, ctx.user_id or 0, text)
            return
        await self.api.send(ctx.chat_id, card("❔", "UNKNOWN CODE", codes("No action waits for that code")))

    async def _execute(self, ctx: Ctx, p: Pending) -> None:
        from arcus.scout import autopilot as ap

        a = p.args
        log.info("operator_action", data={"action": p.action, "args": a, "by": ctx.user})
        took = TAKEOVER.get(p.action)
        if took and ap.turn_off(self._state_dir(), f"your {took} ({ctx.user})"):
            await self.reply(ctx, card("🤖", "AUTOPILOT OFF", codes(f"You took over with {took}", "/auto to turn it "
                                                                                                 "on again")))
        if p.action in ("arb", "arb_start", "arb_stop"):
            await self._execute_arb(ctx, p)
            return
        if p.action == "auto_on":
            ap.configure(self._state_dir(), on=True, mode="live" if a["live"] else "paper", budget_day=a["budget"],
                         by=ctx.user, **({"max_cost_bp": a["cost"], "auto_cost": False} if a.get("cost") else {}))
            text, kb = self._auto_card("AUTOPILOT ON", "🟢")
            await self.reply(ctx, text, kb)
            return
        if p.action == "auto_off":
            ap.turn_off(self._state_dir(), f"/auto off ({ctx.user})")
            act = self.pilot.active() if self.pilot is not None else None
            if act and act.get("by") == "autopilot" and self.control.alive(act["mode"]):
                async def close() -> None:
                    try:
                        await self.pilot.close(by=f"Telegram ({ctx.user})")
                    except Exception as e:
                        await self.api.send(ctx.chat_id, card("⚠️", "CLOSE FAILED", codes(redact_str(str(e))[:400])))
                self._spawn(close())
            text, kb = self._auto_card("AUTOPILOT OFF", "⏹")
            await self.reply(ctx, text, kb)
            return
        if p.action == "stop":
            self.control.request_stop(a["mode"], f"Telegram ({ctx.user})")
            self.watcher.note_stop_requested(a["mode"])
            await self.reply(ctx, card("⏳", f"Stopping {a['mode'].upper()}…", codes("Quotes cancelled · positions kept")))
            self._spawn(self._ensure_stopped(ctx, a["mode"]))
        elif p.action == "deploy":
            try:
                c = {**self._find(a["market"], a["setting"], a["lev"], a["profile"]), **(a.get("limits") or {})}
            except ValueError as e:
                await self.reply(ctx, card("❌", "CANNOT RUN", codes(str(e))))
                return
            await self.reply(ctx, card("🚀", f"Starting {'LIVE' if a['live'] else 'PAPER'}", codes(what(c))))

            async def deploy() -> None:
                try:
                    await self.pilot.deploy(c, live=bool(a["live"]), by=f"Telegram ({ctx.user})")
                except Exception as e:
                    await self.api.send(ctx.chat_id, card("⚠️", "START FAILED", codes(redact_str(str(e))[:400])))
            self._spawn(deploy())
        elif p.action == "pilotclose":
            act = self.pilot.active() if self.pilot is not None else None
            await self.reply(ctx, card("⏳", "Closing…", codes(f"{short(act['market'])} · {act['config']}" if act else "",
                                                               "Maker exit, then taker if it does not fill")))

            async def close() -> None:
                try:
                    await self.pilot.close(by=f"Telegram ({ctx.user})")
                except Exception as e:
                    await self.api.send(ctx.chat_id, card("⚠️", "CLOSE FAILED", codes(redact_str(str(e))[:400])))
            self._spawn(close())
        elif p.action == "resume":
            self.control.request_resume(a["mode"], a.get("venue"))
            paused = self.control.paused(a["mode"])
            if paused:   # a resume clears the safety stops only; the owner's own pause is /unpause (2026-09-26)
                await self.reply(ctx, card("⏸", "STILL PAUSED", codes("Safety stops cleared",
                                                                     f"New orders paused by you: {pause_where(paused)}"),
                                           codes("/unpause to quote again")),
                                 [[("▶️ Unpause", f"unpause all {a['mode']}"), ("📊 Dashboard", "dashboard")]])
            else:
                act = self.pilot.active() if self.pilot is not None else None
                await self.reply(ctx, card("▶️", "ORDERS RESUMED", codes(
                    " · ".join(x for x in (a["mode"].upper(), short(act["market"]) if act else "", "quoting restarting")
                               if x))))
        elif p.action == "run":
            self.control.clear_pause("live" if a["live"] else "paper", None)   # a new run starts quoting (see deploy)
            rec = self.control.start_run(a["name"], live=bool(a["live"]))
            await self.reply(ctx, card("🚀", f"Starting {'LIVE' if a['live'] else 'PAPER'}", codes(a["name"])))
            self._spawn(self._watch_start(ctx, rec, "live" if a["live"] else "paper"))
        elif p.action == "set":
            from arcus.scout.capital import forget
            from arcus.scout.service import SCAN_NOW

            name = a["name"]
            try:
                if a.get("reset"):
                    settings.reset(self._state_dir(), name)
                else:
                    settings.save(self._state_dir(), name, a["value"], self.control.app.sizing)
            except ValueError as e:
                await self.reply(ctx, card("❌", "CANNOT SET", codes(str(e))))
                return
            if settings.SETTINGS[name].field:   # a sizing setting: rescan at the new numbers now
                forget(self._state_dir())
                (self._state_dir() / SCAN_NOW).touch()
            elif name in ("volume_cost", "crypto_lev"):   # re-rank now / backtest the new leverage now
                (self._state_dir() / SCAN_NOW).touch()
            over = settings.load(self._state_dir())
            value = over[name] if name in over else settings.defaults(self.control.app.sizing, 30, "auto").get(name)
            await self.reply(ctx, card("⚙️", name.replace("_", " ").upper(),   # the owner's /set template
                                       codes(set_value(name, value) + ("" if name in over else " (default)"),
                                             f"Applies {settings.SETTINGS[name].applies}"),
                                       f"✅ {b('Saved')}"), refresh_keyboard("settings"))
        elif p.action in ("cancelall", "flatten"):
            await self.reply(ctx, card("⏳", "Cancelling orders…" if p.action == "cancelall" else "Closing positions…"))

            async def go() -> None:
                try:
                    if p.action == "cancelall":
                        res = await self.control.cancel_all(a["mode"], a["venue"])
                        left = res.get("open")
                        await self.api.send(ctx.chat_id, card("✅", "ORDERS CANCELLED", codes(
                            f"Active orders: {left if left is not None else 'unknown'}")))
                        return
                    res = await self.control.flatten(a["mode"], a["venue"], bool(a["taker"]))
                    closed = int(res["positions"]) - int(res["open"])
                    if not res["open"]:
                        await self.api.send(ctx.chat_id, card("✅", "POSITIONS CLOSED",
                                                              codes(f"Closed: {closed} · Open: 0")))
                    else:   # maker orders at the touch take a while to fill: say what is still open
                        await self.api.send(ctx.chat_id, card("🧯", "CLOSING POSITIONS", codes(
                            f"Closed: {closed} · Open: {res['open']}",
                            f"{res['orders']} reduce-only {'IOC' if res['taker'] else 'maker'} orders sent"),
                            codes("/openpositions to check")))
                except Exception as e:
                    await self.api.send(ctx.chat_id, card("⚠️", "CANCEL FAILED" if p.action == "cancelall" else
                                                          "CLOSE FAILED", codes(redact_str(str(e))[:300])))
            self._spawn(go())

    async def _execute_arb(self, ctx: Ctx, p: Pending) -> None:
        """A confirmed funding-arbitrage action. It never turns the Arcus autopilot off: it is another part."""
        from arbitrage import ops

        a = p.args
        log.info("operator_action", data={"action": p.action, "args": a, "by": ctx.user})
        if p.action == "arb":
            await self.reply(ctx, card("⏳", f"FUNDING ARB · {str(a['title']).upper()}", codes("arb " + " ".join(a["argv"]))))
            self._spawn(self._arb_run(ctx.chat_id, str(a["title"]), list(a["argv"])))
            return
        mode = str(a["mode"])
        try:
            ok, msg = await asyncio.to_thread(ops.start, mode, a.get("money")) if p.action == "arb_start" else \
                await asyncio.to_thread(ops.stop, mode)
        except Exception as e:
            ok, msg = False, f"{type(e).__name__}: {redact_str(str(e))[:300]}"
        head = ("STARTED" if ok else "NOT STARTED") if p.action == "arb_start" else ("STOPPED" if ok else "NOT STOPPED")
        await self.reply(ctx, card("🟢" if ok and p.action == "arb_start" else "⏹" if ok else "❌",
                                   f"FUNDING ARB · {mode.upper()} {head}", codes(*msg.splitlines()),
                                   codes("/arb_status")), others.arb_keyboard())

    async def _ensure_stopped(self, ctx: Ctx, mode: str, wait_s: float = 25.0) -> None:
        await asyncio.sleep(wait_s)
        if self.control.is_running(mode) and self.control.signal_stop(mode):
            await self.api.send(ctx.chat_id, card("⚠️", f"NO STOP AFTER {wait_s:.0f}S",
                                                  codes(f"{mode.upper()} · sent SIGINT (clean shutdown)")))

    async def _watch_start(self, ctx: Ctx, rec: dict[str, Any], mode: str, wait_s: float = 90.0) -> None:
        t0 = time.time()
        while time.time() - t0 < wait_s:
            await asyncio.sleep(5)
            if self.control.is_running(mode):
                await self.api.send(ctx.chat_id, card("🟢", f"{mode.upper()} STARTED", codes(rec["name"])))
                return
            alive = next((r["alive"] for r in self.control.runs() if r["pid"] == rec["pid"]), False)
            if not alive:
                break
        tail = self.control.log_tail(rec["log"])
        await self.api.send(ctx.chat_id, card("❌", "DID NOT START", codes(rec["name"]),
                                              f"<pre>{escape(tail[-3000:])}</pre>"))


def _reason(e: Exception) -> str:
    """The reason in an error: for a pydantic validation error its "Value error, ..." text, not the help link on its
    last line (a refused /set showed only "For further information visit ...")."""
    lines = [ln.strip() for ln in str(e).splitlines() if ln.strip() and not ln.strip().startswith("For further")]
    for ln in lines:
        if "error, " in ln:
            return ln.split("error, ", 1)[1].split(" [type=")[0]
    return lines[-1] if lines else type(e).__name__


def _warnings(rep: str) -> list[str]:
    """The doctor's warnings as a section of a confirmation (the failing checks never reach one)."""
    return section("Warnings", codes(*(f"{area}: {msg}" for area, msg, _fix in doctor_checks(rep, "WARN"))))


def _name(u: dict[str, Any] | None) -> str:
    if not u:
        return "?"
    return str(u.get("username") or u.get("first_name") or u.get("id"))
