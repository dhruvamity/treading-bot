"""The Lighter bot's Telegram control (its own bot: LBOT_TELEGRAM_TOKEN, not the Arcus bot's).

Everyday: /top3 (Most Volume), /cheapest, /maxvolume, /run (the run form), /status, /dashboard, /balance, /account.
Control: /pause, /unpause, /stop, /closeall, /resumeaftersl. Settings: /settings, /set. /scannow, /whoami, /help.
It reads the runs' status files and writes commands the runs apply within a second; it holds no trading state.
LIVE needs LBOT_LIVE=1, a passing doctor and a one-time code typed back within 2 minutes.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import secrets
import time
from dataclasses import dataclass, field, replace
from typing import Any

from lbot import ops, settings
from lbot.config import Config
from lbot.log import Log
from lbot.scout import pilot
from lbot.scout.scan import LIST_NAMES
from lbot.telegram.api import Api, b, buttons, card, code, lines
from lbot.trade.engine import RunSpec, send_control
from lbot.trade.strategy import Setup, checked, parse, spread_text

log = Log("telegram")
LIST_CMDS = {"top3": "most", "mostvolume": "most", "cheapest": "cheapest", "maxvolume": "max"}
FORM_SPREADS = (0.0, 0.25, 0.5, 1.0, 2.0, 3.0)
FORM_LEVS = ("max", "20", "10", "5")
FORM_SL = ("none", "5", "10", "25")
CODE_TTL_S = 120


@dataclass
class Form:
    market: str
    mode: str = "mid"
    spread: float = 0.5
    bias: str = "neutral"
    lev: str = "max"
    sl: str = "none"
    source: str = "you"
    vol: float | None = None
    tp: float | None = None


@dataclass
class Pending:
    spec: RunSpec
    code: str
    until: float


@dataclass
class Bot:
    cfg: Config
    api: Api
    forms: dict[int, Form] = field(default_factory=dict)
    pending: dict[int, Pending] = field(default_factory=dict)
    confirms: dict[str, tuple[str, str]] = field(default_factory=dict)   # token -> (kind, arg)
    dash: dict[int, int] = field(default_factory=dict)                   # chat -> dashboard message id
    events_at: int = 0
    last_state: dict[str, str] = field(default_factory=dict)

    # ---------------------------------------------------------------- access
    def allowed(self, user: int, chat: int) -> bool:
        if self.cfg.telegram_users:
            return user in self.cfg.telegram_users
        return str(chat) == str(self.cfg.telegram_chat)

    # ---------------------------------------------------------------- views
    def markets(self) -> dict[str, Any]:
        from lbot.scout.record import load_markets
        return load_markets(self.cfg.data_dir)

    def scan(self) -> dict[str, Any] | None:
        return pilot.latest(self.cfg)

    def list_card(self, lst: str) -> tuple[str, dict[str, Any] | None]:
        sc = self.scan()
        emoji = {"most": "🚀", "cheapest": "💎", "max": "🔥"}[lst]
        if sc is None:
            return card(emoji, LIST_NAMES[lst], lines("No scan yet: is the scout running? (lbot up)")), None
        age = (time.time() - sc["t"]) / 60
        rows = sc["lists"].get(lst) or []
        head = lines(f"Capital ${sc['capital']:,.0f} · stops {'/'.join(f'{x:g}' for x in sc['stops'])}% · "
                     f"budget ${sc['volume_cost']:.2f}/1k", f"Scan {age:.0f} min ago (backtest, not a promise)")
        blocks: list[str | list[str]] = [head]
        btns = []
        for i, r in enumerate(rows, 1):
            blocks.append([b(f"{i}. {r['market']} {r['setup']} @ {r['leverage']:g}x"),
                           *lines(f"${r['volume_d']:,.0f} a day · PnL ${r['pnl_d']:+.2f} · worst ${r['worst']:+.2f}",
                                  f"cost ${r['cost_1k']:.3f} per $1,000 · {r['fills_d']:,.0f} fills · "
                                  f"{r['days']} day(s) of data")])
            btns.append((f"▶️ {i}", f"pick:{lst}:{i}"))
        if not rows:
            blocks.append(lines("Nothing passes every check right now."))
        return card(emoji, LIST_NAMES[lst], *blocks), (buttons([btns, [("🔄 Scan now", "scan")]]) if btns else
                                                        buttons([[("🔄 Scan now", "scan")]]))

    def status_card(self) -> str:
        st = ops.status(self.cfg)
        blocks: list[str | list[str]] = []
        for mode in ("live", "paper"):
            s = st.get(mode)
            if not s:
                continue
            alive = st["services"].get(f"run-{mode}")
            eq = s.get("equity")
            rp = s.get("run_pnl")
            dp = s.get("day_pnl")
            lim = s.get("limits") or {}
            blocks.append([b(f"{'🔴 LIVE' if mode == 'live' else '📝 PAPER'} {s['market']} {s['setup']} @ "
                             f"{s['leverage']:g}x"),
                           *lines(f"{'Running' if alive else 'Stopped'} · {s['state']}"
                                  + (f" · {s['why']}" if s.get('why') else " · quoting"),
                                  f"Equity ${eq:,.2f}" if eq is not None else "Equity ?",
                                  f"Today ${s['day_volume']:,.0f} · {s['day_fills']} fills · PnL "
                                  f"{dp:+.2f}" if dp is not None else f"Today ${s['day_volume']:,.0f}",
                                  f"Run ${s['run_volume']:,.0f} · PnL {rp:+.2f}" if rp is not None else "",
                                  f"Position {s['pos']:+g} (${s['pos_usd']:,.0f})",
                                  f"Quoting {s['quoting_pct']:.0f}% · {s['requests_last_min']} requests/min",
                                  " · ".join(f"{k} {v}" for k, v in (("sl", lim.get("sl")), ("tp", lim.get("tp")),
                                                                     ("vol", lim.get("vol"))) if v) or "",
                                  f"RUN DONE: {s['done']}" if s.get("done") else "")])
        if not blocks:
            blocks.append(lines("No run. /top3 for the lists, /run to pick your own."))
        sc = st.get("scan")
        if sc:
            blocks.append(lines(f"Last scan {sc['age_min']:.0f} min ago"))
        svc = st["services"]
        blocks.append(lines("Scout " + ("up" if svc.get("scout") else "down")))
        return card("📊", "Status", *blocks)

    def form_card(self, f: Form) -> tuple[str, dict[str, Any]]:
        ms = self.markets()
        m = ms.get(f.market)
        max_lev = m.max_leverage if m else 50
        lev = max_lev if f.lev == "max" else min(float(f.lev), max_lev)
        s = Setup(f.mode, f.spread, f.bias)
        sc = self.scan()
        row = None
        if sc:
            row = next((r for r in sc["table"] if r["market"] == f.market and r["setup"] == s.name), None)
        bt = (lines(f"Backtest at {row['leverage']:g}x: ${row['volume_d']:,.0f} a day, PnL ${row['pnl_d']:+.2f}, "
                    f"cost ${row['cost_1k']:.3f}/1k ({row['days']} day(s))",
                    "Checks: " + ("pass" if not row["why"] else "; ".join(row["why"])))
              if row else lines("Not backtested at these settings: runs as your pick"))
        text = card("🎛", f"Run {f.market} {s.name} @ {lev:g}x",
                    lines(f"Run stop: {'none' if f.sl == 'none' else '$' + f.sl}"
                          + (f" · volume target ${f.vol:,.0f}" if f.vol else "")
                          + (f" · take profit ${f.tp:,.0f}" if f.tp else "")), bt)

        def mark(sel: bool, t: str) -> str:
            return f"• {t}" if sel else t

        kb = buttons([
            [(mark(f.mode == x, x.capitalize()), f"f:mode:{x}") for x in ("mid", "smart", "grid", "touch")],
            [(mark(f.spread == x, spread_text(x)), f"f:spread:{x:g}") for x in FORM_SPREADS],
            [(mark(f.bias == x, x.capitalize()), f"f:bias:{x}") for x in ("short", "neutral", "long")],
            [(mark(f.lev == x, "max" if x == "max" else f"{x}x"), f"f:lev:{x}") for x in FORM_LEVS],
            [(mark(f.sl == x, "no SL" if x == "none" else f"SL ${x}"), f"f:sl:{x}") for x in FORM_SL],
            [("📝 Paper", "f:go:paper"), ("🔴 LIVE", "f:go:live")] if self.cfg.live_allowed else
            [("📝 Paper", "f:go:paper")],
        ])
        return text, kb

    def spec_of(self, f: Form, mode: str) -> RunSpec:
        m = self.markets().get(f.market)
        max_lev = m.max_leverage if m else 50.0
        cap = settings.lev_cap(self.cfg)
        lev = max_lev if f.lev == "max" else float(f.lev)
        lev = min(lev, max_lev, cap or max_lev)
        s = checked(Setup(f.mode, f.spread, f.bias))
        paper_cap = None
        if mode == "paper":
            sc = self.scan()
            paper_cap = float(sc["capital"]) if sc else self.cfg.sizing.paper_capital_usd
        return RunSpec(market=f.market, setup=s.name, leverage=lev, mode=mode, capital=paper_cap,
                       stops=settings.stops(self.cfg), sl=None if f.sl == "none" else float(f.sl), tp=f.tp,
                       vol=f.vol, source=f.source)

    def _takeover(self, why: str = "you started your own run") -> None:
        from lbot.scout import autopilot
        if autopilot.Auto.load(self.cfg).on:
            autopilot.turn_off(self.cfg, why)

    # ---------------------------------------------------------------- starting runs
    async def go(self, chat: int, spec: RunSpec) -> None:
        if spec.mode == "live":
            if not self.cfg.live_allowed:
                await self.api.send(chat, card("⛔", "LIVE is off", lines("Set LBOT_LIVE=1 in lighter/.env on the "
                                                                          "bot's machine, then restart lbot.")))
                return
            from lbot import doctor
            ok, dl = await doctor.check(self.cfg, spec.market, spec.leverage, spec.stops)
            if not ok:
                await self.api.send(chat, card("⛔", "Not ready for LIVE",
                                               lines(*[f"{lv} {n}: {t}" for lv, n, t in dl if lv != "PASS"])))
                return
            c = f"{secrets.randbelow(10**6):06d}"
            self.pending[chat] = Pending(spec, c, time.time() + CODE_TTL_S)
            await self.api.send(chat, card("🔴", "Confirm LIVE", lines(
                f"{spec.market} {spec.setup} @ {spec.leverage:g}x with real money",
                "Doctor: READY", f"Type {c} within 2 minutes to start. Anything else cancels.")))
            return
        self._takeover()
        await asyncio.to_thread(pilot.start, self.cfg, spec)
        await self.api.send(chat, card("📝", "Paper run started", lines(
            f"{spec.market} {spec.setup} @ {spec.leverage:g}x", "/status or /dashboard to watch it")))

    # ---------------------------------------------------------------- commands
    async def on_text(self, chat: int, user: int, text: str) -> None:
        t = text.strip()
        p = self.pending.pop(chat, None)
        if p is not None and not t.startswith("/"):
            if t == p.code and time.time() < p.until and p.spec.market == "AUTO":
                from lbot.scout import autopilot
                autopilot.turn_on(self.cfg, "live")
                await self.api.send(chat, card("🔴", "Autopilot ON (LIVE)", lines("/auto shows what it does")))
            elif t == p.code and time.time() < p.until:
                self._takeover()
                await asyncio.to_thread(pilot.start, self.cfg, p.spec, confirmed=True)
                await self.api.send(chat, card("🔴", "LIVE run started", lines(
                    f"{p.spec.market} {p.spec.setup} @ {p.spec.leverage:g}x", "/status · /dashboard · /closeall")))
            else:
                await self.api.send(chat, card("❎", "Not started", lines("The code did not match or expired.")))
            return
        if not t.startswith("/"):
            return
        cmd, _, arg = t[1:].partition(" ")
        cmd = cmd.split("@")[0].lower()
        arg = arg.strip()
        if cmd in ("start", "help", "menu"):
            await self.api.send(chat, card("🤖", "Lighter bot", lines(
                "/top3 · /cheapest · /maxvolume: the lists", "/run: pick a market and a setup",
                "/status · /dashboard · /balance · /account", "/pause · /unpause · /stop · /closeall",
                "/resumeaftersl · /settings · /set · /scannow · /auto")),
                buttons([[("🚀 Top 3", "list:most"), ("💎 Cheapest", "list:cheapest"), ("🔥 Max", "list:max")],
                         [("🎛 Run", "runform"), ("📊 Status", "status"), ("📺 Dashboard", "dash")]]))
        elif cmd in LIST_CMDS:
            text_, kb = self.list_card(LIST_CMDS[cmd])
            await self.api.send(chat, text_, kb)
        elif cmd in ("status", "openpositions", "positions", "orders"):
            await self.api.send(chat, self.status_card())
        elif cmd == "dashboard":
            await self.start_dash(chat)
        elif cmd == "run":
            await self.cmd_run(chat, arg)
        elif cmd in ("pause", "pauseneworders", "unpause", "stop", "closeall", "resumeaftersl"):
            action = {"pauseneworders": "pause", "closeall": "close", "resumeaftersl": "resume"}.get(cmd, cmd)
            tok = secrets.token_hex(3)
            self.confirms[tok] = ("ctl", action)
            await self.api.send(chat, card("⚠️", f"Confirm: {action}", lines(
                {"pause": "No new orders; orders that close the position keep working",
                 "unpause": "Quote again", "stop": "Stop the bot: quotes cancelled, the position kept",
                 "close": "Close the position (maker, then taker) and stop",
                 "resume": "Trade again after the kill or a daily stop"}[action])),
                buttons([[("✅ Confirm", f"ok:{tok}"), ("✖️ Cancel", "nop")]]))
        elif cmd in ("balance", "account"):
            from lbot.account import report
            await self.api.send(chat, card("📒", "Account", lines(*(await report(self.cfg)).splitlines())))
        elif cmd == "settings":
            eff = settings.effective(self.cfg)
            await self.api.send(chat, card("⚙️", "Settings", [f"{b(k)}: {code(settings.show(k, v))}\n"
                                                             f"{settings.SETTINGS[k].help}" for k, v in eff.items()],
                                           lines("Change one: /set daily_stop 6 (then Confirm); "
                                                 "/set daily_stop default undoes it")))
        elif cmd == "set":
            await self.cmd_set(chat, arg)
        elif cmd == "auto":
            await self.cmd_auto(chat, arg)
        elif cmd == "scannow":
            (self.cfg.state_dir / "scan_now").write_text(str(time.time()))
            await self.api.send(chat, card("🔄", "Scan asked", lines("The scout scans within a few seconds; "
                                                                    "/top3 afterwards")))
        elif cmd == "whoami":
            await self.api.send(chat, card("🪪", "You", lines(f"user id {user}", f"chat id {chat}")))
        else:
            await self.api.send(chat, card("❓", "Unknown command", lines("/help lists them")))

    async def cmd_run(self, chat: int, arg: str) -> None:
        if not arg:
            ms = self.markets()
            top = sorted(ms.values(), key=lambda m: -m.day_volume_usd)[:12]
            rows = [[(m.symbol, f"rm:{m.symbol}") for m in top[i:i + 4]] for i in range(0, len(top), 4)]
            await self.api.send(chat, card("🎛", "Run: which market?", lines("Or type /run SYMBOL")), buttons(rows))
            return
        words = arg.split()
        mk = words[0].upper()
        if mk not in self.markets():
            await self.api.send(chat, card("❓", f"{mk} is not a Lighter perp", lines("/run lists the markets")))
            return
        f = Form(mk)
        rest = []
        mode_word = None
        for w in words[1:]:
            lw = w.lower()
            kv = re.fullmatch(r"(sl|tp|vol)=([\d.,]+[km]?)", lw)
            if kv:
                v = kv.group(2).replace(",", "")
                num = float(v.rstrip("km")) * (1000 if v.endswith("k") else 1_000_000 if v.endswith("m") else 1)
                if kv.group(1) == "sl":
                    f.sl = f"{num:g}"
                elif kv.group(1) == "tp":
                    f.tp = num
                else:
                    f.vol = num
            elif lw in ("paper", "live"):
                mode_word = lw
            elif re.fullmatch(r"\d+(\.\d+)?x|max", lw):
                f.lev = "max" if lw == "max" else lw.removesuffix("x")
            else:
                rest.append(w)
        if rest:
            try:
                s = parse(" ".join(rest))
                f.mode, f.spread, f.bias = s.mode, s.spread, s.bias
            except ValueError as e:
                await self.api.send(chat, card("❓", "Which setup?", lines(str(e))))
                return
        if rest and mode_word:
            await self.go(chat, self.spec_of(f, mode_word))
            return
        self.forms[chat] = f
        text_, kb = self.form_card(f)
        await self.api.send(chat, text_, kb)

    def auto_card(self) -> tuple[str, dict[str, Any]]:
        from lbot.scout import autopilot
        a = autopilot.Auto.load(self.cfg)
        ceiling = "the list budget" if a.ceiling is None else f"${a.ceiling:.3f} per $1,000"
        blocks = [lines(f"{'ON (' + a.mode.upper() + ')' if a.on else 'Off'} · budget ${a.budget:g} a day · pot "
                        f"${a.pot:.2f}", f"Ceiling {ceiling}",
                        f"Running {a.run['market']} {a.run['setup']} (run stop ${a.run.get('sl', 0):.2f})"
                        if a.run else "", a.last)]
        recent = [x["text"] for x in a.log[-5:]]
        if recent:
            blocks.append([b("Recent"), *lines(*recent)])
        kb = [[("📝 On (paper)", "auto:on:paper")] + ([("🔴 On (LIVE)", "auto:on:live")] if self.cfg.live_allowed else [])
              + [("⏹ Off", "auto:off")]]
        return card("🧭", "Autopilot", *blocks), buttons(kb)

    async def cmd_auto(self, chat: int, arg: str) -> None:
        from lbot.scout import autopilot
        w = arg.lower().split()
        if len(w) >= 2 and w[0] in ("budget", "cost"):
            a = autopilot.Auto.load(self.cfg)
            try:
                if w[0] == "budget":
                    a.budget = float(w[1].removeprefix("$"))
                else:
                    a.ceiling = None if w[1] == "auto" else float(w[1].removeprefix("$"))
            except ValueError:
                await self.api.send(chat, card("❓", "Not a number", lines("/auto budget 5 · /auto cost 0.05")))
                return
            a.save(self.cfg)
        elif w and w[0] == "off":
            autopilot.turn_off(self.cfg, "you turned it off")
        text_, kb = self.auto_card()
        await self.api.send(chat, text_, kb)

    async def cmd_set(self, chat: int, arg: str) -> None:
        name, _, val = arg.partition(" ")
        if not name or not val:
            await self.api.send(chat, card("⚙️", "How to set", lines("/set NAME VALUE, e.g. /set daily_stop 6",
                                                                   "/settings lists them")))
            return
        try:
            v = "default" if val.strip() in ("default", "reset") else settings.parse(name, val)
        except ValueError as e:
            await self.api.send(chat, card("❓", "Not a valid value", lines(str(e))))
            return
        tok = secrets.token_hex(3)
        self.confirms[tok] = ("set", json.dumps([name, v]))
        await self.api.send(chat, card("⚙️", f"Set {name}", lines(f"{name} = {v if v == 'default' else settings.show(name, v)}")),
                            buttons([[("✅ Confirm", f"ok:{tok}"), ("✖️ Cancel", "nop")]]))

    # ---------------------------------------------------------------- buttons
    async def on_button(self, chat: int, msg_id: int, data: str) -> str:
        if data == "nop":
            return "cancelled"
        if data.startswith("list:"):
            text_, kb = self.list_card(data.split(":")[1])
            await self.api.send(chat, text_, kb)
        elif data == "scan":
            (self.cfg.state_dir / "scan_now").write_text(str(time.time()))
            return "scan asked"
        elif data == "status":
            await self.api.send(chat, self.status_card())
        elif data == "dash":
            await self.start_dash(chat)
        elif data == "dash:stop":
            self.dash.pop(chat, None)
            return "dashboard stopped"
        elif data == "runform":
            await self.cmd_run(chat, "")
        elif data.startswith("rm:"):
            await self.cmd_run(chat, data[3:])
        elif data.startswith("pick:"):
            _, lst, n = data.split(":")
            try:
                r = pilot.pick(self.cfg, lst, int(n))
            except ValueError as e:
                return str(e)
            s = parse(r["setup"])
            f = Form(r["market"], s.mode, s.spread, s.bias, lev=f"{r['leverage']:g}", source=lst)
            self.forms[chat] = f
            text_, kb = self.form_card(f)
            await self.api.send(chat, text_, kb)
        elif data.startswith("f:"):
            f = self.forms.get(chat)
            if f is None:
                return "the form expired: /run again"
            _, field_, val = data.split(":", 2)
            if field_ == "go":
                try:
                    spec = self.spec_of(f, val)
                except ValueError as e:
                    return str(e)
                await self.go(chat, spec)
                return "starting" if val == "paper" else "checking"
            if field_ == "spread":
                f.spread = float(val)
            elif field_ == "lev":
                f.lev = val
            else:
                setattr(f, field_, val)
            if field_ in ("mode", "spread", "bias"):
                f.source = "you"          # a changed setup is the owner's pick, not the list's
            try:
                checked(Setup(f.mode, f.spread, f.bias))
            except ValueError as e:
                return str(e)
            text_, kb = self.form_card(f)
            await self.api.edit(chat, msg_id, text_, kb)
        elif data.startswith("auto:"):
            from lbot.scout import autopilot
            parts = data.split(":")
            if parts[1] == "off":
                autopilot.turn_off(self.cfg, "you turned it off")
                return "autopilot off"
            if parts[2] == "live":
                if not self.cfg.live_allowed:
                    return "LBOT_LIVE=1 is not set"
                c = f"{secrets.randbelow(10**6):06d}"
                self.pending[chat] = Pending(RunSpec("AUTO", "auto", 0, mode="live", source="auto"), c,
                                             time.time() + CODE_TTL_S)
                await self.api.send(chat, card("🔴", "Confirm the LIVE autopilot", lines(
                    "It will start and stop real-money runs by itself within its budget.",
                    f"Type {c} within 2 minutes.")))
                return "type the code"
            autopilot.turn_on(self.cfg, "paper")
            text_, kb = self.auto_card()
            await self.api.edit(chat, msg_id, text_, kb)
            return "autopilot on (paper)"
        elif data.startswith("ok:"):
            item = self.confirms.pop(data[3:], None)
            if item is None:
                return "expired"
            kind, arg = item
            if kind == "ctl":
                if arg in ("stop", "close"):
                    self._takeover(f"you sent {arg}")
                modes = [m for m in ("live", "paper") if ops.running(self.cfg, f"run-{m}")]
                for m in modes:
                    send_control(self.cfg.state_dir, m, arg)
                return f"{arg} sent" if modes else "no run is going"
            if kind == "set":
                name, v = json.loads(arg)
                if v == "default":
                    settings.reset(self.cfg, name)
                else:
                    try:
                        settings.save(self.cfg, name, v)
                    except ValueError as e:
                        return str(e)
                eff = settings.effective(self.cfg)
                await self.api.send(chat, card("✅", "Saved", lines(f"{name} = {settings.show(name, eff[name])}",
                                                                   "The next scan and the next run use it")))
        return ""

    # ---------------------------------------------------------------- dashboard and alerts
    async def start_dash(self, chat: int) -> None:
        r = await self.api.send(chat, self.status_card(), buttons([[("⏹ Stop updating", "dash:stop")]]))
        if r:
            self.dash[chat] = int(r["message_id"])

    async def refresh(self) -> None:
        while True:
            await asyncio.sleep(10)
            for chat, mid in list(self.dash.items()):
                await self.api.edit(chat, mid, self.status_card(), buttons([[("⏹ Stop updating", "dash:stop")]]))

    async def alerts(self) -> None:
        chat = self.cfg.telegram_chat
        p = self.cfg.state_dir / "pilot_events.jsonl"
        with contextlib.suppress(OSError):
            self.events_at = p.stat().st_size
        while True:
            await asyncio.sleep(5)
            if not chat:
                continue
            with contextlib.suppress(OSError):
                if p.stat().st_size > self.events_at:
                    new, self.events_at = await asyncio.to_thread(read_from, p, self.events_at)
                    for ln in new.splitlines():
                        with contextlib.suppress(ValueError):
                            ev = json.loads(ln)
                            await self.api.send(chat, card("🧭", ev.get("kind", "pilot").capitalize(),
                                                           lines(ev.get("text", ""))))
            for mode in ("live", "paper"):
                st = ops.read_json(self.cfg.state_dir / f"status-{mode}.json")
                if not st:
                    continue
                key = f"{st.get('state')}|{st.get('done')}"
                old = self.last_state.get(mode)
                self.last_state[mode] = key
                if old is not None and old != key and st.get("state") != "normal":
                    await self.api.send(chat, card("🛑" if st.get("state") in ("killed", "done") else "⚠️",
                                                   f"{mode.upper()} {st['market']}: {st.get('state')}",
                                                   lines(st.get("why") or st.get("done") or "")))
                alive = ops.running(self.cfg, f"run-{mode}")
                stale = time.time() - float(st.get("t", 0)) > 60
                if alive and stale and self.last_state.get(mode + ":stale") != "1":
                    self.last_state[mode + ":stale"] = "1"
                    await self.api.send(chat, card("🚨", f"{mode.upper()} bot silent", lines(
                        "No status for over a minute. Lighter cancels its orders within 5 minutes if it is down "
                        "(the dead man's switch).")))
                elif not stale:
                    self.last_state[mode + ":stale"] = "0"

    # ---------------------------------------------------------------- the loop
    async def poll(self) -> None:
        offset = 0
        while True:
            for u in await self.api.updates(offset):
                offset = max(offset, int(u["update_id"]) + 1)
                try:
                    if "message" in u:
                        m = u["message"]
                        chat, user = int(m["chat"]["id"]), int(m["from"]["id"])
                        if not self.allowed(user, chat):
                            if (m.get("text") or "").startswith("/whoami"):
                                await self.api.send(chat, card("🪪", "You", lines(f"user id {user}", f"chat id {chat}")))
                            continue
                        await self.on_text(chat, user, m.get("text") or "")
                    elif "callback_query" in u:
                        q = u["callback_query"]
                        chat, user = int(q["message"]["chat"]["id"]), int(q["from"]["id"])
                        if not self.allowed(user, chat):
                            await self.api.answer(q["id"], "not allowed")
                            continue
                        note = await self.on_button(chat, int(q["message"]["message_id"]), q.get("data", ""))
                        await self.api.answer(q["id"], note)
                except Exception as e:     # one bad update must not stop the bot
                    log.error("update_failed", err=f"{type(e).__name__}: {e}")


def read_from(p: Any, offset: int) -> tuple[str, int]:
    with open(p) as fh:
        fh.seek(offset)
        return fh.read(), fh.tell()


async def main(cfg: Config) -> None:
    if not cfg.telegram_token:
        raise SystemExit("LBOT_TELEGRAM_TOKEN is not set in lighter/.env")
    api = Api(cfg.telegram_token)
    await api.call("setMyCommands", commands=[
        {"command": c, "description": d} for c, d in (
            ("top3", "🚀 Most volume within your budget"), ("cheapest", "💎 Cheapest per dollar"),
            ("maxvolume", "🔥 Most volume"), ("run", "🎛 Run a setup"), ("auto", "🧭 Autopilot"),
            ("status", "📊 What runs"),
            ("dashboard", "📺 Live screen"), ("balance", "📒 Account"), ("pause", "Stop new orders"),
            ("closeall", "Close the position and stop"), ("settings", "⚙️ Settings"), ("help", "All commands"))])
    bot = Bot(cfg, api)
    try:
        await asyncio.gather(bot.poll(), bot.refresh(), bot.alerts())
    finally:
        await api.close()


__all__ = ["Bot", "Form", "main", "replace"]
