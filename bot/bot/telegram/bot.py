"""The Telegram control bot: status, control and live alerts for the trading bot.

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

from bot.common import settings
from bot.common.logging import Log, redact_str
from bot.core.balances import BalanceLog
from bot.telegram import dashboard
from bot.telegram.api import Keyboard, TelegramAPI, TelegramError, split_html
from bot.telegram.control import MODES, Control, pause_where
from bot.telegram.views import (
    COMMANDS,
    HELP,
    _sizes,
    ago,
    balance_text,
    candidate_lines,
    confirm_keyboard,
    dashboard_keyboard,
    ladder_keyboard,
    ladder_text,
    markets_keyboard,
    menu_keyboard,
    orders_text,
    pick_keyboard,
    pilot_keyboard,
    pilot_text,
    pnl_text,
    positions_text,
    profile_keyboard,
    profile_text,
    refresh_keyboard,
    run_keyboard,
    run_text,
    sessions_text,
    settings_keyboard,
    settings_text,
    short,
    status_text,
)
from bot.telegram.watcher import Prefs, Watcher

log = Log("telegram_bot")
VENUES = ("arcus",)
# The commands' earlier names still work (not listed in Telegram's menu), so old habits and old buttons keep working.
ALIASES = {"scout": "top3", "pilot": "openpositions", "report": "yesterdayreport", "pause": "pauseneworders",
           "resume": "resumeaftersl", "flatten": "closeall"}
CONFIRM_TTL_S = 120
DASH_FILE = "telegram_dashboard.json"   # the live /dashboard message: {chat_id, message_id, since}
SCAN_WAIT_S = 30 * 60                   # no scan running or queued this long after a request: say so
SCAN_GIVE_UP_S = 6 * 3600               # stop waiting for a scan after this long (a slow one is still running)


MAX_LOSS_KEYS = ("sl=", "maxloss=", "max_loss=")


def _take_max_loss(args: list[str]) -> tuple[list[str], float | str | None]:
    """/run ... sl=30 (or sl=$30): (the other arguments, the dollar loss limit); a string is the error to show."""
    rest, out = [], None
    for a in args:
        key = next((k for k in MAX_LOSS_KEYS if a.lower().startswith(k)), None)
        if key is None:
            rest.append(a)
            continue
        try:
            v = float(a[len(key):].strip().lstrip("$").replace(",", ""))
        except ValueError:
            return args, f"sl= takes dollars, e.g. sl=30 (got {escape(a)})"
        if not 0.5 <= v <= 1_000_000:
            return args, "sl= must be between $0.50 and $1,000,000"
        out = v
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
        self.pilot = pilot                 # bot.scout.pilot.Pilot: picks what runs (optional)
        self.pilot_offset = -1
        self.username = ""
        self._tasks: set[asyncio.Task[Any]] = set()
        self._dash_account: dict[str, Any] | None = None   # the dashboard's own account read (when the bot has none)
        self.scan_waiters: list[dict[str, Any]] = []        # {chat, profile, since}: post that list after the scan

    # ------------------------------------------------------------------ plumbing
    def authorized(self, chat_id: int, user_id: int | None) -> bool:
        if self.allowed_user_ids:
            return user_id in self.allowed_user_ids and chat_id in (self.owner_chat_id, user_id)
        return chat_id == self.owner_chat_id

    async def _alert(self, text: str, critical: bool) -> None:
        await self.api.send(self.owner_chat_id, text, silent=not critical)

    async def reply(self, ctx: Ctx, text: str, keyboard: Keyboard | None = None) -> None:
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
        views = [self.control.view(m) for m in self.control.known_modes()]
        await self.api.send(self.owner_chat_id, "🤖 <b>Online</b>" + (" · read-only" if self.read_only else "")
                            + "\n" + status_text(views), keyboard=menu_keyboard(), silent=True)
        await asyncio.gather(self._poll_loop(), self._watch_loop(), self._dashboard_loop())

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
                        await self.api.send(self.owner_chat_id, f"⚠️ {escape(redact_str(str(e))[:300])}")

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
            text = escape(str(e.get("text", "")))
            top = e.get("top")
            if top is not None and e.get("kind") in ("offer", "paused", "suggest"):
                profile = str(e.get("profile") or "breakeven")
                text += ("\n" + candidate_lines(top, cost=profile != "breakeven")) if top else ""
                await self.api.send(self.owner_chat_id, text, keyboard=pick_keyboard(top, profile),
                                    silent=e.get("kind") == "offer")
            elif e.get("kind") == "deployed":
                await self.api.send(self.owner_chat_id, text, keyboard=[[("📺 Live dashboard", "dashboard")]],
                                    silent=True)
            else:
                await self.api.send(self.owner_chat_id, text, silent=e.get("kind") not in ("failed", "paused"))

    # ------------------------------------------------------------------ updates
    async def handle(self, u: dict[str, Any]) -> None:
        if "callback_query" in u:
            cq = u["callback_query"]
            msg = cq.get("message") or {}
            ctx = Ctx(int((msg.get("chat") or {}).get("id", 0)), (cq.get("from") or {}).get("id"),
                      _name(cq.get("from")), msg.get("message_id"))
            await self.api.answer(cq["id"])
            if not self.authorized(ctx.chat_id, ctx.user_id):
                return
            parts = str(cq.get("data") or "").split()
            if parts:
                await self.dispatch(ctx, parts[0], parts[1:])
            return
        msg = u.get("message") or {}
        text = str(msg.get("text") or "").strip()
        ctx = Ctx(int((msg.get("chat") or {}).get("id", 0)), (msg.get("from") or {}).get("id"), _name(msg.get("from")))
        if not text:
            return
        if text.split()[0].split("@")[0] == "/whoami":
            await self.api.send(ctx.chat_id, f"chat id <code>{ctx.chat_id}</code> · user id <code>{ctx.user_id}</code>")
            return
        if not self.authorized(ctx.chat_id, ctx.user_id):
            log.warning("unauthorized", data={"chat": ctx.chat_id, "user": ctx.user_id})
            return
        if text.isdigit():
            await self._code(ctx, text)
            return
        if not text.startswith("/"):
            await self.api.send(ctx.chat_id, "Send /menu or /help.")
            return
        head, *args = text.split()
        cmd = head[1:].split("@")[0].lower()
        if "@" in head and self.username and head.split("@")[1].lower() != self.username.lower():
            return  # a command for another bot in the same group
        await self.dispatch(ctx, cmd, args)

    # ------------------------------------------------------------------ commands
    async def dispatch(self, ctx: Ctx, cmd: str, args: list[str]) -> None:
        cmd = ALIASES.get(cmd, cmd)
        read = {"start": self.c_menu, "menu": self.c_menu, "help": self.c_help, "status": self.c_status,
                "top3": self.c_scout, "openpositions": self.c_pilot,
                "pnl": self.c_pnl, "positions": self.c_positions, "orders": self.c_orders,
                "sessions": self.c_sessions, "logs": self.c_logs, "yesterdayreport": self.c_report,
                "ping": self.c_ping, "alerts": self.c_alerts, "mute": self.c_mute, "unmute": self.c_unmute,
                "ok": self.c_ok, "no": self.c_no, "balance": self.c_balance, "settings": self.c_settings,
                "dashboard": self.c_dashboard, "dashstop": self.c_dashstop, "dashresume": self.c_dashresume,
                "volume": self.c_volume, "aggressive": self.c_aggressive, "maxvolume": self.c_maxvolume,
                "pick": self.c_pick, "rm": self.c_rm, "rs": self.c_rs, "rl": self.c_rl,
                "lev": self.c_old_button, "deploy": self.c_old_button}
        write = {"pauseneworders": self.c_pause, "unpause": self.c_unpause, "stop": self.c_stop,
                 "resumeaftersl": self.c_resume, "run": self.c_run, "doctor": self.c_doctor,
                 "cancelall": self.c_cancelall, "closeall": self.c_flatten, "rd": self.c_rd, "golive": self.c_golive,
                 "pilotclose": self.c_pilotclose, "set": self.c_set, "scannow": self.c_scannow,
                 "rescan": self.c_rescan}
        if cmd in read:
            await read[cmd](ctx, args)
        elif cmd in write:
            if self.read_only:
                await self.reply(ctx, "Read-only bot: controls are off.")
                return
            await write[cmd](ctx, args)
        else:
            await self.reply(ctx, f"Unknown /{escape(cmd)} · /help")

    # ------------------------------------------------------------------ scout / pilot: the lists
    async def c_scout(self, ctx: Ctx, args: list[str]) -> None:
        """/top3 [breakeven|volume|aggressive|max]: that list's top 3 from the last scan, with Run buttons."""
        from bot.scout.profiles import profile_of

        try:
            prof = profile_of(args[0] if args else None)
        except ValueError as e:
            await self.reply(ctx, escape(str(e)))
            return
        await self._list(ctx, prof.key)

    async def c_volume(self, ctx: Ctx, args: list[str]) -> None:
        await self._list(ctx, "volume")

    async def c_aggressive(self, ctx: Ctx, args: list[str]) -> None:
        await self._list(ctx, "aggressive")

    async def c_maxvolume(self, ctx: Ctx, args: list[str]) -> None:
        await self._list(ctx, "max")

    async def _list(self, ctx: Ctx, profile: str, *, force: bool = False) -> None:
        """The list from the last scan, and a scan when that one is stale (or asked for): its ETA under the list."""
        if self.pilot is None:
            await self.reply(ctx, "No pilot on this server.")
            return
        note = self._scan_note(ctx.chat_id, profile, force=force)
        await self.reply(ctx, profile_text(self.pilot.latest_scan(), profile, self.pilot.budget(), time.time(), note),
                         profile_keyboard(profile, self.pilot.top(profile)))

    async def c_pilot(self, ctx: Ctx, args: list[str]) -> None:
        if self.pilot is None:
            await self.reply(ctx, "No pilot on this server.")
            return
        a = self.pilot.active()
        paper = bool(a and a["mode"] == "paper" and self.control.is_running("paper"))
        await self.reply(ctx, pilot_text(self.pilot), pilot_keyboard(paper=paper))

    async def c_rescan(self, ctx: Ctx, args: list[str]) -> None:
        from bot.scout.profiles import profile_of

        try:
            prof = profile_of(args[0] if args else None)
        except ValueError as e:
            await self.reply(ctx, escape(str(e)))
            return
        await self._list(ctx, prof.key, force=True)

    def _scan_note(self, chat_id: int, profile: str, *, force: bool = False) -> str:
        """Ask for a scan when the last one is stale (or force), unless one is running or queued; one waiter per chat
        and list gets the fresh list when it is done. Returns the line to show, with the measured ETA."""
        from bot.scout.service import SCAN_NOW, eta_s, next_is_full, read_status, scan_workers

        if self.read_only:
            return ""
        now = time.time()
        st = read_status(Path(self.control.root))
        scan = self.pilot.latest_scan() if self.pilot is not None else None
        trigger = self._state_dir() / SCAN_NOW
        every, want = settings.scout_options(settings.load(self._state_dir()))
        age = now - scan["ts_us"] / 1e6 if scan else float("inf")
        if st.get("running"):
            started = float(st.get("started") or now)
            eta = eta_s(st, int(st.get("workers") or 1), next_is_full(scan, started))
            note = "🔎 Scan running" + (f" · ~{ago(max(60.0, eta - (now - started)))} left" if eta else "")
        elif force or trigger.exists() or age > 1.5 * 60 * float(every or 30):
            trigger.parent.mkdir(parents=True, exist_ok=True)
            trigger.touch()
            eta = eta_s(st, scan_workers(want, bool(self.control.running_modes())), next_is_full(scan, now))
            note = "🔎 Scan started" + (f" · ~{ago(eta)}" if eta else "")
        else:
            return ""
        if self.pilot is None:
            return note
        if not any((w["chat"], w["profile"]) == (chat_id, profile) for w in self.scan_waiters):
            self.scan_waiters.append({"chat": chat_id, "profile": profile, "since": now})
        return note + " · the list follows when it is done"

    async def scan_waiters_tick(self) -> None:
        """Post each waiting list once a scan that started after the request has finished."""
        from bot.scout.service import SCAN_NOW, read_status

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
                await self.api.send(w["chat"], f"⌛ No scan finished in {ago(waited)}. Is the scout running? "
                                    "<code>bot status</code>")
            elif waited < SCAN_GIVE_UP_S:
                keep.append(w)
        self.scan_waiters = keep

    def _pick_args(self, args: list[str]) -> tuple[str, int] | None:
        """[profile] k: button data from before the lists (plain "pick 1") means the breakeven list."""
        if args and args[0].isdigit():
            return "breakeven", int(args[0])
        if len(args) >= 2 and args[1].isdigit():
            return args[0], int(args[1])
        return None

    async def c_pick(self, ctx: Ctx, args: list[str]) -> None:
        """▶️ k on a list: that setup's whole leverage ladder, the list's pick starred."""
        from bot.scout.pilot import setting_id, setting_of

        parsed = self._pick_args(args)
        if self.pilot is None or parsed is None:
            return
        profile, k = parsed
        try:
            c = self.pilot.pick(k, profile, "rec")
        except ValueError as e:
            await self.reply(ctx, escape(str(e)))
            return
        await self._ladder(ctx, c["market"], setting_id(setting_of(c)), profile, star=float(c["leverage"]))

    async def c_old_button(self, ctx: Ctx, args: list[str]) -> None:
        await self.reply(ctx, "That button is from an older version. /top3 or /run.")

    # ------------------------------------------------------------------ run any setup: market -> setting -> leverage
    async def c_run(self, ctx: Ctx, args: list[str]) -> None:
        """/run: pick a market, a setting and a leverage (the whole ladder), then Paper or LIVE. /run QQQ [setting]
        [20x|max] [paper|live] [sl=30] jumps ahead (sl: the most the run may lose, in dollars); /run <session file>
        starts a session file as before."""
        from bot.scout.pilot import setting_id

        names = {x["name"]: x for x in self.control.sessions()}
        if args and args[0] in names:
            await self._run_session(ctx, args[0], any(a.lower() == "live" for a in args[1:]), names[args[0]])
            return
        if self.pilot is None:
            await self.reply(ctx, sessions_text(list(names.values()), self.control.runs()))
            return
        if not args:
            await self.reply(ctx, "🎯 <b>Run any setup</b> · market", markets_keyboard(self.pilot.latest_scan()))
            return
        args, max_loss = _take_max_loss(args)
        if isinstance(max_loss, str):
            await self.reply(ctx, max_loss)
            return
        parsed = self._parse_run(args)
        if isinstance(parsed, str):
            await self.reply(ctx, parsed)
            return
        market, setting, lev, mode = parsed
        if max_loss and (setting is None or lev is None or mode is None):
            await self.reply(ctx, "With sl= give the whole setup: <code>/run BTC touch 0bp 20x live sl=30</code>")
            return
        if setting is None:
            await self._settings(ctx, market)
        elif lev is None:
            await self._ladder(ctx, market, setting_id(setting), "manual")
        elif mode is None:
            await self._run_screen(ctx, market, setting_id(setting), lev, "manual")
        else:
            await self._deploy_flow(ctx, market, setting, lev, "manual", live=mode == "live", max_loss=max_loss)

    def _parse_run(self, args: list[str]) -> tuple[str, str | None, float | str | None, str | None] | str:
        """QQQ "touch 1bp" 20x live -> ("QQQ-USD", "touch 1bp", 20.0, "live"); a string is the error to show."""
        from bot.scout.scan import BY_NAME

        markets = {c["market"] for c in (self.pilot.latest_scan() or {}).get("all") or []} if self.pilot else set()
        m = args[0].upper()
        market = m if "-" in m else f"{m}-USD"
        if market not in markets:
            return f"Unknown market {escape(args[0])}. /run lists them."
        rest = [a.strip("\"'“”") for a in args[1:]]
        mode = rest.pop().lower() if rest and rest[-1].lower() in ("paper", "live") else None
        lev: float | str | None = None
        if rest and (rest[-1].lower() == "max" or rest[-1].lower().rstrip("x").replace(".", "", 1).isdigit()):
            t = rest.pop().lower()
            lev = "max" if t == "max" else float(t.rstrip("x"))
        if not rest:
            return (market, None, None, None) if lev is None and mode is None else \
                f"Which setting? /run {escape(short(market))} lists them."
        want = " ".join(" ".join(rest).replace(",", " ").split()).lower()
        setting = next((n for n in BY_NAME if " ".join(n.replace(",", " ").split()).lower() == want), None)
        if setting is None:
            return f"Unknown setting {escape(' '.join(rest))}. /run {escape(short(market))} lists them."
        return market, setting, lev, mode

    async def c_rm(self, ctx: Ctx, args: list[str]) -> None:
        if args and self.pilot is not None:
            await self._settings(ctx, args[0])

    async def _settings(self, ctx: Ctx, market: str) -> None:
        assert self.pilot is not None
        rows = self.pilot.rows(market)
        if not rows:
            await self.reply(ctx, f"{escape(market)} is not in the last scan.")
            return
        await self.reply(ctx, f"🎯 <b>{escape(short(market))}</b> · setting (best leverage)",
                         settings_keyboard(market, rows))

    async def c_rs(self, ctx: Ctx, args: list[str]) -> None:
        if len(args) >= 2 and self.pilot is not None:
            await self._ladder(ctx, args[0], args[1], args[2] if len(args) > 2 else "manual")

    async def _ladder(self, ctx: Ctx, market: str, sid: str, profile: str, star: float | None = None) -> None:
        from bot.scout.pilot import setting_by_id

        assert self.pilot is not None
        setting = setting_by_id(sid)
        if setting is None:
            await self.reply(ctx, "Unknown setting. /run")
            return
        rows = self.pilot.rows(market, setting)
        have = {round(float(r["leverage"]), 2) for r in rows}
        rows += [{"leverage": x, "not_backtested": True} for x in self.pilot.leverages(market) if round(x, 2) not in have]
        rows.sort(key=lambda r: -float(r["leverage"]))
        if not rows:
            await self.reply(ctx, f"{escape(short(market))}: no market data yet. /run")
            return
        await self.reply(ctx, ladder_text(market, setting, rows, self.pilot.budget(), star),
                         ladder_keyboard(market, sid, rows, profile))

    async def c_rl(self, ctx: Ctx, args: list[str]) -> None:
        if len(args) >= 3 and self.pilot is not None:
            with contextlib.suppress(ValueError):
                await self._run_screen(ctx, args[0], args[1], float(args[2]), args[3] if len(args) > 3 else "manual")

    def _find(self, market: str, sid_or_name: str, lev: float | str, profile: str) -> dict[str, Any]:
        """The scan row to run, judged by `profile` when it is in that list, else as the owner's own pick."""
        from bot.scout.pilot import setting_by_id
        from bot.scout.profiles import profile_of, verdict

        assert self.pilot is not None
        setting = setting_by_id(sid_or_name) or sid_or_name
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
        from bot.common.config import load_arcus_config
        from bot.scout.capital import account_snapshot

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

    async def _run_screen(self, ctx: Ctx, market: str, sid: str, lev: float | str, profile: str) -> None:
        from bot.scout.pilot import setting_id, setting_of

        assert self.pilot is not None
        await self._fresh_balance()
        try:
            c = self._find(market, sid, lev, profile)
        except ValueError as e:
            await self.reply(ctx, escape(str(e)))
            return
        from bot.scout.pilot import stale_note

        live_ok = os.environ.get("BOT_PILOT_LIVE") == "1"
        running = [m for m in ("paper", "live") if self.control.is_running(m)]
        stale = stale_note(self.pilot.latest_scan())
        await self.reply(ctx, run_text(c, self.pilot.budget(), c["profile"], running, live_ok)
                         + (f"\n{escape(stale)}" if stale else ""),
                         run_keyboard(market, setting_id(setting_of(c)), float(c["leverage"]), c["profile"], live_ok))

    async def c_rd(self, ctx: Ctx, args: list[str]) -> None:
        """📝 Paper / 🔴 LIVE on the run screen: rd <market> <setting id> <leverage> <profile> <paper|live>."""
        from bot.scout.pilot import setting_by_id

        if len(args) < 5 or self.pilot is None:
            return
        setting = setting_by_id(args[1])
        if setting is None:
            await self.c_old_button(ctx, args)
            return
        with contextlib.suppress(ValueError):
            await self._deploy_flow(ctx, args[0], setting, float(args[2]), args[3], live=args[4] == "live")

    async def c_golive(self, ctx: Ctx, args: list[str]) -> None:
        """🔴 Go LIVE on a running paper deployment: the same market, setting and leverage, with real money."""
        from bot.scout.pilot import setting_of

        a = self.pilot.active() if self.pilot is not None else None
        if not a or a["mode"] != "paper":
            await self.reply(ctx, "No paper run to take live. /openpositions")
            return
        lev = (a.get("backtest") or {}).get("leverage") or float(a["config"].split(" @ ")[1].rstrip("x"))
        await self._deploy_flow(ctx, a["market"], setting_of(a), float(lev), a.get("profile") or "manual", live=True,
                                max_loss=a.get("max_loss_usd"))

    async def _deploy_flow(self, ctx: Ctx, market: str, setting: str, lev: float | str, profile: str, *,
                           live: bool, max_loss: float | None = None) -> None:
        """Paper: a Confirm button. LIVE: BOT_PILOT_LIVE=1, a passing doctor, then a typed one-time code.
        max_loss: the owner's loss limit for the run (sl=)."""
        assert self.pilot is not None
        await self._fresh_balance()
        try:
            c = self._find(market, setting, lev, profile)
        except ValueError as e:
            await self.reply(ctx, escape(str(e)))
            return
        c = {**c, "max_loss_usd": max_loss}
        what = f"{escape(short(c['market']))} · {escape(setting)} · {float(c['leverage']):g}x"
        args_ = {"market": c["market"], "setting": setting, "lev": float(c["leverage"]), "profile": c["profile"],
                 "live": live, "max_loss": max_loss}
        mode = "live" if live else "paper"
        # alive, not just running: a bot that is closing has stopped its heartbeat but not exited yet
        alive = self.control.alive(mode)
        running = self.pilot.active() if alive else None
        paused = self.control.paused(mode)
        notes = ([f"🛑 Stops for good once this run loses {escape(f'${max_loss:,.2f}')} (the daily stop and kill "
                  "are raised to match; the position stop stays)"] if max_loss else []) + \
            ([f"↩️ Replaces the running {mode.upper()} bot ({escape(short(running['market']))} · "
              f"{escape(running['config'])}): it closes its orders and position first"] if running else
             [f"↩️ Starts once the {mode.upper()} bot that is stopping has exited"] if alive else []) + \
            ([f"▶️ Clears your pause on new orders ({escape(pause_where(paused))})"] if paused else [])
        if not live:
            await self._ask(ctx, "deploy", args_, f"📝 Paper · {what}?" + "".join(f"\n{n}" for n in notes))
            return
        if os.environ.get("BOT_PILOT_LIVE") != "1":
            await self.reply(ctx, "LIVE is off on this server: BOT_PILOT_LIVE=1 in .env, then restart.")
            return
        await self.reply(ctx, f"🩺 Checking the account for {what}…")

        async def go() -> None:
            try:   # checked from its own file: the running bot's session stays as it is until the owner confirms
                path = self.pilot.write_session(c, live=True, path=self._state_dir() / "pilot_check.yaml")
                ok, rep = await self.control.doctor(str(path), replacing=self.control.alive("live"))
            except Exception as e:
                await self.api.send(ctx.chat_id, f"⚠️ doctor failed: {escape(redact_str(str(e))[:300])}")
                return
            if not ok:
                await self.api.send(ctx.chat_id, f"❌ Not starting:\n<pre>{escape(rep[:3500])}</pre>")
                return
            await self._ask(Ctx(ctx.chat_id, ctx.user_id, ctx.user), "deploy", args_,
                            f"🔴 <b>LIVE</b> · {what}\n{escape(_sizes(c))}" + "".join(f"\n{n}" for n in notes),
                            code=True)
        self._spawn(go())

    async def c_pilotclose(self, ctx: Ctx, args: list[str]) -> None:
        a = self.pilot.active() if self.pilot is not None else None
        if not a:
            await self.reply(ctx, "Nothing is deployed.")
            return
        await self._ask(ctx, "pilotclose", {}, f"Close {escape(short(a['market']))} and stop? Maker exit, then taker "
                        "if it does not fill.")

    # ------------------------------------------------------------------ balance and settings
    def _state_dir(self) -> Path:
        return Path(self.control.root) / self.control.app.state_dir

    async def c_balance(self, ctx: Ctx, args: list[str]) -> None:
        """Read the account now, log it (state/balances.jsonl), and show it with its history."""
        from bot.common.config import load_arcus_config
        from bot.scout.capital import STATE, account_snapshot

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
        now = settings.show(name, over[name]) if name in over else "default"
        if text.lower() == "default":
            if name not in settings.SETTINGS:
                await self.reply(ctx, f"Unknown setting {escape(name)}. /settings lists them.")
                return
            await self._ask(ctx, "set", {"name": name, "value": None, "reset": True},
                            f"Put <b>{escape(name)}</b> back to its default (now {escape(now)})?")
            return
        try:
            value = settings.parse(name, text)
            settings.effective_sizing(self.control.app.sizing, {**over, name: value})   # the whole must be valid
        except ValueError as e:
            await self.reply(ctx, f"⚠️ {escape(str(e).splitlines()[-1])}")
            return
        s = settings.SETTINGS[name]
        await self._ask(ctx, "set", {"name": name, "value": value, "reset": False},
                        f"Set <b>{escape(name)}</b> to <b>{escape(settings.show(name, value))}</b> (now {escape(now)})?"
                        f"\n{escape(s.help)}. Applies: {escape(s.applies)}.")

    async def c_scannow(self, ctx: Ctx, args: list[str]) -> None:
        if self.pilot is None:
            await self.reply(ctx, self._scan_note(ctx.chat_id, "breakeven", force=True) or "🔎 Scan requested")
            return
        await self._list(ctx, "breakeven", force=True)

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
            from bot.common.config import load_arcus_config
            from bot.scout.capital import account_snapshot

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
        await self.reply(ctx, "☰ <b>Menu</b>", menu_keyboard())

    async def c_help(self, ctx: Ctx, args: list[str]) -> None:
        await self.reply(ctx, HELP, menu_keyboard())

    async def c_status(self, ctx: Ctx, args: list[str]) -> None:
        modes = [a.lower() for a in args if a.lower() in MODES] or self.control.known_modes()
        text = status_text([self.control.view(m) for m in modes])
        if self.pilot is not None:
            text += "\n\n" + pilot_text(self.pilot)
        await self.reply(ctx, text, refresh_keyboard("status"))

    async def _need_mode(self, ctx: Ctx, args: list[str], **kw: Any) -> tuple[str | None, list[str]]:
        mode, rest = self._mode(args, **kw)
        if mode is None:
            await self.reply(ctx, "No bot has run here yet. /run")
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
            await self.reply(ctx, "No decisions logged yet.")
            return
        lines = []
        for r in rows:
            t = dt.datetime.fromtimestamp(int(r.get("ts", 0)) / 1e6, dt.UTC).strftime("%m-%d %H:%M:%S")
            where = "/".join(x for x in (r.get("venue"), r.get("market")) if x)
            lines.append(f"{t} {r.get('event', '')} {where}: {str(r.get('reason', ''))[:140]}")
        await self.reply(ctx, "<b>Latest decisions</b>\n<pre>" + escape("\n".join(lines)) + "</pre>",
                         refresh_keyboard(f"logs {n}"))

    async def c_report(self, ctx: Ctx, args: list[str]) -> None:
        mode, rest = self._mode(args)
        date = rest[0] if rest else (dt.datetime.now(dt.UTC) - dt.timedelta(days=1)).strftime("%Y-%m-%d")
        rep = self.control.report(mode or "live", date) if mode else None
        await self.reply(ctx, f"<b>Report {escape(date)} ({mode})</b>\n<pre>{escape(rep[:3500])}</pre>" if rep else
                         f"No {mode or ''} report for {escape(date)} yet (written just after 00:00 UTC).")

    async def c_ping(self, ctx: Ctx, args: list[str]) -> None:
        run = self.control.running_modes()
        await self.reply(ctx, "pong · running: " + (", ".join(run) if run else "nothing"))

    async def c_alerts(self, ctx: Ctx, args: list[str]) -> None:
        p = self.prefs
        if len(args) >= 2 and args[0] == "fills" and args[1] in ("each", "summary", "off"):
            p.fills = args[1]
        elif len(args) >= 2 and args[0] == "digest":
            p.digest = args[1] == "on"
        elif len(args) >= 2 and args[0] == "pnl":
            p.pnl_alerts = args[1] == "on"
        p.save(self.prefs_path)
        muted = f"muted until {dt.datetime.fromtimestamp(p.mute_until, dt.UTC):%H:%M} UTC" if p.muted() else "on"
        text = (f"<b>Alerts</b> ({muted})\nCritical alerts (bot down, safe mode, loss limit) always come through.\n\n"
                f"Fills: <b>{p.fills}</b> · PnL warnings: <b>{'on' if p.pnl_alerts else 'off'}</b> · "
                f"daily digest: <b>{'on' if p.digest else 'off'}</b>")
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
        await self.reply(ctx, f"🔕 Muted {minutes} min (critical alerts still come). /unmute")

    async def c_unmute(self, ctx: Ctx, args: list[str]) -> None:
        self.prefs.mute_until = 0.0
        self.prefs.save(self.prefs_path)
        await self.reply(ctx, "🔔 Alerts on.")

    # ------------------------------------------------------------------ controls
    async def c_pause(self, ctx: Ctx, args: list[str]) -> None:
        mode, rest = await self._need_mode(ctx, args)
        if not mode:
            return
        market = rest[0].upper() if rest and rest[0].lower() != "all" else None
        self.control.set_pause(mode, market, f"Telegram ({ctx.user})")
        note = "" if self.control.is_running(mode) else " (applies when it starts)"
        await self.reply(ctx, f"⏸ <b>{mode.upper()}</b> · new orders paused on {market or 'all markets'}{note}. "
                         "Exit orders keep working.",
                         [[("▶️ Unpause", f"unpause {market or 'all'} {mode}"), ("📊 Status", f"status {mode}")]])

    async def c_unpause(self, ctx: Ctx, args: list[str]) -> None:
        mode, rest = await self._need_mode(ctx, args)
        if not mode:
            return
        market = rest[0].upper() if rest and rest[0].lower() != "all" else None
        left = self.control.clear_pause(mode, market)
        await self.reply(ctx, f"▶️ <b>{mode.upper()}</b> · quoting on {market or 'all markets'}"
                         + (f" · still paused: {', '.join(left)}" if left else ""), refresh_keyboard(f"status {mode}"))

    async def _ask(self, ctx: Ctx, action: str, args: dict[str, Any], desc: str, *, code: bool = False) -> None:
        pid = secrets.token_hex(4)
        p = Pending(pid, action, args, ctx.chat_id, desc)
        if code:
            p.code = f"{secrets.randbelow(900000) + 100000}"
            await self.api.send(ctx.chat_id, f"{desc}\nSend <code>{p.code}</code> to confirm (2 min).",
                                keyboard=[[("✖️ Cancel", f"no {pid}")]])
        else:
            await self.reply(ctx, f"❓ {desc}", confirm_keyboard(pid))
            p.message_id = ctx.message_id
        self.pending = {k: v for k, v in self.pending.items() if v.expires > time.time()}
        self.pending[pid] = p

    async def c_stop(self, ctx: Ctx, args: list[str]) -> None:
        mode, _ = self._mode(args, need_running=True)
        if not mode:
            await self.reply(ctx, "No bot is running.")
            return
        await self._ask(ctx, "stop", {"mode": mode}, f"Stop the <b>{mode.upper()}</b> bot? Quotes cancelled, "
                        "position kept.")

    async def c_resume(self, ctx: Ctx, args: list[str]) -> None:
        mode, rest = await self._need_mode(ctx, args)
        if not mode:
            return
        venue = rest[0] if rest and rest[0] in VENUES else None
        await self._ask(ctx, "resume", {"mode": mode, "venue": venue},
                        f"Clear the safety stops on <b>{mode.upper()}</b>? A daily stop re-arms from now: the rest of "
                        "the UTC day may lose one more daily stop. Check /logs first.")

    async def _run_session(self, ctx: Ctx, name: str, live: bool, sess: dict[str, Any]) -> None:
        """/run <session file> [live]: a session file from config/sessions (the pilot's own is pilot.yaml)."""
        mode = "live" if live else "paper"
        if self.control.is_running(mode):
            await self.reply(ctx, f"A {mode} bot is running. /stop {mode} first.")
            return
        if not live:
            await self._ask(ctx, "run", {"name": name, "live": False}, f"📝 Paper · <b>{escape(name)}</b>?")
            return
        if not sess.get("live_enabled"):
            await self.reply(ctx, f"<b>{escape(name)}</b>: <code>live_enabled: false</code> in "
                             f"config/sessions/{escape(name)}.yaml.")
            return
        await self.reply(ctx, f"🩺 Checking <b>{escape(name)}</b>…")
        self._spawn(self._doctor_then_ask(ctx, name))

    async def _doctor_then_ask(self, ctx: Ctx, name: str) -> None:
        try:
            ok, rep = await self.control.doctor(name)
        except Exception as e:
            await self.api.send(ctx.chat_id, f"⚠️ doctor failed: {escape(redact_str(str(e))[:300])}")
            return
        await self.api.send(ctx.chat_id, f"<pre>{escape(rep[:3500])}</pre>")
        if not ok:
            await self.api.send(ctx.chat_id, "❌ Not starting: fix the FAIL lines above.")
            return
        await self._ask(Ctx(ctx.chat_id, ctx.user_id, ctx.user), "run", {"name": name, "live": True},
                        f"🔴 <b>LIVE</b> · {escape(name)}", code=True)

    async def c_doctor(self, ctx: Ctx, args: list[str]) -> None:
        if not args:
            await self.reply(ctx, "Usage: /doctor &lt;session&gt;")
            return
        await self.reply(ctx, f"🩺 Running doctor for <b>{escape(args[0])}</b>…")

        async def go() -> None:
            try:
                ok, rep = await self.control.doctor(args[0])
                await self.api.send(ctx.chat_id, ("✅ READY" if ok else "❌ NOT READY") + f"\n<pre>{escape(rep[:3500])}</pre>")
            except Exception as e:
                await self.api.send(ctx.chat_id, f"⚠️ doctor failed: {escape(redact_str(str(e))[:300])}")
        self._spawn(go())

    def _venue_mode(self, args: list[str]) -> tuple[str | None, str, list[str]]:
        mode, rest = self._mode(args)
        venue = next((a for a in rest if a in VENUES), "arcus")
        return mode, venue, [a for a in rest if a not in VENUES]

    async def c_cancelall(self, ctx: Ctx, args: list[str]) -> None:
        mode, venue, _ = self._venue_mode(args)
        if mode in (None, "paper"):
            await self.reply(ctx, "Cancel-all is for a real account. Paper: /pauseneworders or /stop.")
            return
        await self._ask(ctx, "cancelall", {"mode": mode, "venue": venue},
                        f"Cancel every order on {venue} ({'MAINNET' if mode == 'live' else 'testnet'})? A running "
                        "bot re-quotes unless paused.")

    async def c_flatten(self, ctx: Ctx, args: list[str]) -> None:
        mode, venue, rest = self._venue_mode(args)
        if mode in (None, "paper"):
            await self.reply(ctx, "Close-all is for a real account. Paper: /pauseneworders works the position off.")
            return
        taker = any(a.lower() == "taker" for a in rest)
        await self._ask(ctx, "flatten", {"mode": mode, "venue": venue, "taker": taker},
                        f"🧯 Close every position on {venue} ({'MAINNET' if mode == 'live' else 'testnet'}) with "
                        f"reduce-only {'taker orders' if taker else 'maker orders at the touch'}? Pause first or it "
                        "may re-open.", code=True)

    # ------------------------------------------------------------------ confirmations
    async def c_ok(self, ctx: Ctx, args: list[str]) -> None:
        p = self.pending.get(args[0]) if args else None
        if p is None or p.expires < time.time() or p.chat_id != ctx.chat_id:
            await self.reply(ctx, "Expired. Run it again.")
            return
        if p.code is not None:
            await self.reply(ctx, "Type the code instead.")
            return
        self.pending.pop(p.pid, None)
        await self._execute(ctx, p)

    async def c_no(self, ctx: Ctx, args: list[str]) -> None:
        if args:
            self.pending.pop(args[0], None)
        await self.reply(ctx, "Cancelled.")

    async def _code(self, ctx: Ctx, text: str) -> None:
        now = time.time()
        for pid, p in list(self.pending.items()):
            if p.code == text and p.chat_id == ctx.chat_id and p.expires > now:
                self.pending.pop(pid, None)
                await self._execute(Ctx(ctx.chat_id, ctx.user_id, ctx.user), p)
                return
        await self.api.send(ctx.chat_id, "No action waits for that code.")

    async def _execute(self, ctx: Ctx, p: Pending) -> None:
        a = p.args
        log.info("operator_action", data={"action": p.action, "args": a, "by": ctx.user})
        if p.action == "stop":
            self.control.request_stop(a["mode"], f"Telegram ({ctx.user})")
            self.watcher.note_stop_requested(a["mode"])
            await self.reply(ctx, f"🛑 Stop sent to <b>{a['mode'].upper()}</b>.")
            self._spawn(self._ensure_stopped(ctx, a["mode"]))
        elif p.action == "deploy":
            try:
                c = {**self._find(a["market"], a["setting"], a["lev"], a["profile"]), "max_loss_usd": a.get("max_loss")}
            except ValueError as e:
                await self.reply(ctx, escape(str(e)))
                return
            await self.reply(ctx, f"🚀 Starting {escape(short(c['market']))} ({'LIVE' if a['live'] else 'paper'})…")

            async def deploy() -> None:
                try:
                    await self.pilot.deploy(c, live=bool(a["live"]), by=f"Telegram ({ctx.user})")
                except Exception as e:
                    await self.api.send(ctx.chat_id, f"⚠️ {escape(redact_str(str(e))[:400])}")
            self._spawn(deploy())
        elif p.action == "pilotclose":
            await self.reply(ctx, "⏹ Closing…")

            async def close() -> None:
                try:
                    await self.pilot.close(by=f"Telegram ({ctx.user})")
                except Exception as e:
                    await self.api.send(ctx.chat_id, f"⚠️ {escape(redact_str(str(e))[:400])}")
            self._spawn(close())
        elif p.action == "resume":
            self.control.request_resume(a["mode"], a.get("venue"))
            paused = self.control.paused(a["mode"])
            if paused:   # a resume clears the safety stops only; the owner's own pause is /unpause (2026-09-26)
                await self.reply(ctx, f"▶️ Safety stops cleared on <b>{a['mode'].upper()}</b>, but new orders are "
                                 f"still paused by you ({escape(pause_where(paused))}), so it will not quote. "
                                 "/unpause to quote again.",
                                 [[("▶️ Unpause", f"unpause all {a['mode']}"), ("📊 Dashboard", "dashboard")]])
            else:
                await self.reply(ctx, f"▶️ Resume sent to <b>{a['mode'].upper()}</b>: quoting restarts within a few "
                                 "seconds. /dashboard")
        elif p.action == "run":
            self.control.clear_pause("live" if a["live"] else "paper", None)   # a new run starts quoting (see deploy)
            rec = self.control.start_run(a["name"], live=bool(a["live"]))
            await self.reply(ctx, f"🚀 Starting <b>{escape(a['name'])}</b> ({'LIVE' if a['live'] else 'paper'})…")
            self._spawn(self._watch_start(ctx, rec, "live" if a["live"] else "paper"))
        elif p.action == "set":
            from bot.scout.capital import forget
            from bot.scout.service import SCAN_NOW

            name = a["name"]
            try:
                if a.get("reset"):
                    settings.reset(self._state_dir(), name)
                else:
                    settings.save(self._state_dir(), name, a["value"], self.control.app.sizing)
            except ValueError as e:
                await self.reply(ctx, f"⚠️ {escape(str(e))}")
                return
            if settings.SETTINGS[name].field:   # a sizing setting: rescan at the new numbers now
                forget(self._state_dir())
                (self._state_dir() / SCAN_NOW).touch()
            elif name in ("volume_cost", "crypto_lev"):   # re-rank now / backtest the new leverage now
                (self._state_dir() / SCAN_NOW).touch()
            over = settings.load(self._state_dir())
            shown = settings.show(name, over[name]) if name in over else "its default"
            await self.reply(ctx, f"✅ <b>{escape(name)}</b> = {escape(shown)} · applies "
                             f"{escape(settings.SETTINGS[name].applies)}", refresh_keyboard("settings"))
        elif p.action in ("cancelall", "flatten"):
            await self.reply(ctx, "⏳ Sending…")

            async def go() -> None:
                try:
                    if p.action == "cancelall":
                        res = await self.control.cancel_all(a["mode"], a["venue"])
                    else:
                        res = await self.control.flatten(a["mode"], a["venue"], bool(a["taker"]))
                    await self.api.send(ctx.chat_id, f"✅ {escape(res)}")
                except Exception as e:
                    await self.api.send(ctx.chat_id, f"⚠️ {p.action} failed: {escape(redact_str(str(e))[:300])}")
            self._spawn(go())

    async def _ensure_stopped(self, ctx: Ctx, mode: str, wait_s: float = 25.0) -> None:
        await asyncio.sleep(wait_s)
        if self.control.is_running(mode) and self.control.signal_stop(mode):
            await self.api.send(ctx.chat_id, f"{mode}: no stop after {wait_s:.0f} s, sent SIGINT (clean shutdown).")

    async def _watch_start(self, ctx: Ctx, rec: dict[str, Any], mode: str, wait_s: float = 90.0) -> None:
        t0 = time.time()
        while time.time() - t0 < wait_s:
            await asyncio.sleep(5)
            if self.control.is_running(mode):
                await self.api.send(ctx.chat_id, f"🟢 <b>{escape(rec['name'])}</b> running ({mode}).")
                return
            alive = next((r["alive"] for r in self.control.runs() if r["pid"] == rec["pid"]), False)
            if not alive:
                break
        tail = self.control.log_tail(rec["log"])
        await self.api.send(ctx.chat_id, f"❌ <b>{escape(rec['name'])}</b> did not start:\n"
                            f"<pre>{escape(tail[-3000:])}</pre>")


def _name(u: dict[str, Any] | None) -> str:
    if not u:
        return "?"
    return str(u.get("username") or u.get("first_name") or u.get("id"))
