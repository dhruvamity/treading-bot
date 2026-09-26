"""`bot doctor`: every check that can be made BEFORE money is at risk, with the fix for each failure.

`run --live` runs it and refuses to start on any FAIL. Checks only read (public REST, local files); nothing is
signed or sent. Levels: PASS, INFO (what the bot will do), WARN (works, but look at it), FAIL (blocks live).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from bot.common import sizing
from bot.common.config import AppConfig, MMSession
from bot.common.errors import BotError, ConfigError
from bot.common.secrets import SecretStore
from bot.core.calendar import TradingCalendar
from bot.core.creds import (
    ArcusKey,
    arcus_address,
    arcus_private_keys,
    discover_arcus_keys,
    ed25519_public_hex,
    key_for_account,
)
from bot.core.heartbeat import heartbeat_process_alive, read_heartbeat_age_s
from bot.core.livelock import RunMode
from bot.core.runner import arcus_account_index, leverage_plan
from bot.strategies import quoting as qt
from bot.venues.base import Market, Venue

BOT_CLIENT_ID_PREFIX = "al"  # every Arcus clientId this bot sends starts with this (common/ids.py)


@dataclass(frozen=True, slots=True)
class Check:
    level: str   # PASS | INFO | WARN | FAIL
    area: str
    message: str
    fix: str = ""


class Report:
    def __init__(self) -> None:
        self.checks: list[Check] = []

    def add(self, level: str, area: str, message: str, fix: str = "") -> None:
        self.checks.append(Check(level, area, message, fix))

    @property
    def failed(self) -> list[Check]:
        return [c for c in self.checks if c.level == "FAIL"]

    def render(self) -> str:
        icon = {"PASS": "ok  ", "INFO": "info", "WARN": "WARN", "FAIL": "FAIL"}
        lines = []
        for c in self.checks:
            lines.append(f"[{icon[c.level]}] {c.area:<18} {c.message}")
            if c.fix and c.level in ("WARN", "FAIL"):
                lines.append(f"{'':26}fix: {c.fix}")
        n = {lv: sum(1 for c in self.checks if c.level == lv) for lv in ("FAIL", "WARN")}
        lines.append("")
        lines.append("READY" if not n["FAIL"] else f"NOT READY: {n['FAIL']} failing check(s)")
        if n["WARN"]:
            lines[-1] += f" ({n['WARN']} warning(s))"
        return "\n".join(lines)


def _mid(bbo: dict[str, Any]) -> Decimal | None:
    b, a = bbo.get("bestBid") or {}, bbo.get("bestAsk") or {}
    if b.get("price") and a.get("price"):
        return (Decimal(str(b["price"])) + Decimal(str(a["price"]))) / 2
    return None


def sizing_lines(s: MMSession, m: Market, mid: Decimal) -> tuple[str, str, str]:
    """(level, message, fix) for what one order will really be at this price."""
    vmin = float(max(m.min_notional, m.min_size * mid))
    levels = s.levels_per_side if isinstance(s.levels_per_side, int) else 3
    q = qt.order_size_usd(s.order_size_usd, venue_min_usd=vmin, inventory_cap_usd=s.inventory_cap_usd, levels=levels)
    fits = int(s.inventory_cap_usd // q) if q > 0 else 0
    msg = (f"{s.market} min order ${vmin:.2f} at {mid:.2f}; each order ${q:.2f}; inventory cap ${s.inventory_cap_usd:.0f} "
           f"holds {fits} filled order(s) per side")
    if fits < 1:
        return "FAIL", msg, f"raise inventory_cap_usd to at least {q:.0f} (one order) or trade a market with a smaller minimum"
    if s.order_size_usd != "auto" and float(s.order_size_usd) < vmin * 1.2:
        return "WARN", msg + f" (configured ${float(s.order_size_usd):.2f} is raised to 1.2x the minimum)", \
            f"set order_size_usd: {q:.0f} so the file says what is traded"
    return "PASS", msg, ""


NEW_LISTING_DAYS = 21        # Arcus addedTimestamp this recent: a new listing (thin history, small OI cap)


def _new_market_checks(r: Report, s: MMSession, m: Market, calendar: TradingCalendar, now_us: int,
                       live: bool) -> None:
    """What a recently listed market, or any single stock, needs before it is traded."""
    added = m.extra.get("addedTimestamp") if m.extra else None
    try:
        age_d = (now_us / 1e6 - float(str(added))) / 86400 if added else None
    except (TypeError, ValueError):
        age_d = None
    if age_d is not None and 0 <= age_d < NEW_LISTING_DAYS:
        cap = f", open-interest cap ${float(m.oi_cap_usd):,.0f}" if m.oi_cap_usd else ""
        r.add("WARN", "market", f"{s.market} was listed {age_d:.0f} days ago{cap}: little history, and listing-week "
              "flow is unusual", "prefer a setup the scout has passed on 3+ full days")
    if str(m.category).upper() == "EQUITIES" and "earnings" in set(getattr(getattr(s, "session", None), "skip_events",
                                                                           []) or []):
        nxt = calendar.next_event(now_us, {"earnings"}, s.market.upper())
        if nxt is None:
            r.add("WARN" if live else "INFO", "calendar", f"no {s.market} earnings date in "
                  "config/calendars/earnings.csv: the bot will not pause around its earnings",
                  f"add the next {s.market} report date (symbol,date,session: bmo or amc)")


async def run_doctor(sessions: list[MMSession], *, mode: RunMode, app: AppConfig, secrets: SecretStore,
                     calendar: TradingCalendar, arcus_rest: Any, markets: dict[Venue, dict[str, Market]],
                     adopt_positions: bool = False, now_us: int | None = None,
                     account_index: int | None = None, replacing: bool = False) -> Report:
    """replacing: Telegram's /run is about to replace the running bot, which closes its orders and position before
    this one starts, so a running bot and its position are expected here."""
    r = Report()
    live = mode is RunMode.LIVE
    need_keys = mode is not RunMode.PAPER
    miss = "FAIL" if need_keys else "WARN"
    now = now_us or int(time.time() * 1e6)
    testnet = mode is RunMode.TESTNET

    # ---- sessions and plan
    for s in sessions:
        r.add("PASS", "session", f"{s.session_id}: {s.mode} on {s.venue}, {s.market}")
        v = Venue(s.venue)
        m = markets.get(v, {}).get(s.market.upper())
        if m is None:
            r.add("FAIL", "market", f"{s.market} is not listed on {v.value}", "fix the market in the session file")
        elif str(m.status).upper() not in ("ONLINE", "ACTIVE", "TRADING", "OPEN", "1"):
            r.add("FAIL" if live else "WARN", "market", f"{v.value} {s.market} status is {m.status}: orders "
                  "would be rejected", "wait until it is trading (new listings start OFFLINE)")
        else:
            _new_market_checks(r, s, m, calendar, now, live)
    try:
        idx = account_index if account_index is not None else arcus_account_index(sessions) if sessions else -1
    except ConfigError as e:
        r.add("FAIL", "plan", str(e), "run sessions on different Arcus subaccounts as separate commands")
        idx = 0
    try:
        plan = leverage_plan(sessions, markets)
        for v, b, lev in plan:
            r.add("INFO", "leverage", f"{v.value} {b}: the bot sets {lev}x cross before its first order "
                                       "(the venue default is the market maximum)")
    except ConfigError as e:
        r.add("FAIL", "plan", str(e), "use the same leverage for a market in every session")
    if live:
        hb_path = app.heartbeat_for(mode.value)
        hb = read_heartbeat_age_s(hb_path)
        if hb < 30 and heartbeat_process_alive(hb_path):
            if replacing:
                r.add("INFO", "already running", f"the running {mode.value} bot is closed (orders and position) "
                                                 "before this one starts")
            else:
                r.add("FAIL", "already running", f"a {mode.value} bot wrote a heartbeat {hb:.0f} s ago",
                      "stop it first (/stop, or bot down); two bots on one account fight each other")

    # ---- environment
    try:
        comp = await arcus_rest.compliance()
        geo = comp.get("geo") or {}
        if (geo.get("restrictions") or {}).get("perpetuals"):
            r.add("FAIL", "region", f"Arcus blocks perpetuals from {geo.get('country')}/{geo.get('region')}",
                  "run from an allowed region (see deploy/scripts/region_check.py)")
        else:
            r.add("PASS", "region", f"Arcus perps allowed from {geo.get('country')}/{geo.get('region')}")
    except BotError as e:
        r.add("WARN", "region", f"compliance check failed: {e}")
    try:
        t0 = time.time_ns()
        server = int((await arcus_rest.server_time())["timeNs"])
        t1 = time.time_ns()
        off_ms = (server - (t0 + t1) / 2) / 1e6
        lvl = "FAIL" if abs(off_ms) > 5000 else "WARN" if abs(off_ms) > 1000 else "PASS"
        r.add(lvl, "clock", f"local clock is {off_ms:+.0f} ms from Arcus", "enable NTP time sync (chrony / System Settings > Date & Time)")
    except (BotError, KeyError, TypeError, ValueError) as e:
        r.add("WARN", "clock", f"could not read Arcus server time: {e}")
    if calendar.coverage_days(now, "fomc") < 30 or calendar.coverage_days(now, "cpi") < 14:
        r.add("WARN", "calendar", "event calendar is short (CPI/FOMC): the bot cannot pause around missing events",
              "add dates to config/calendars/events.csv")
    if not (secrets.get("TELEGRAM_BOT_TOKEN") and secrets.get("TELEGRAM_CHAT_ID")):
        r.add("WARN" if live else "INFO", "alerts", "Telegram not configured: alerts go to the log only",
              "set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env")
    else:
        r.add("PASS", "alerts", "Telegram configured")

    # ---- Arcus account (with no sessions named: the credentials and the key's own subaccount)
    await _arcus_checks(r, sessions, idx, secrets=secrets, rest=arcus_rest, markets=markets, testnet=testnet,
                        miss=miss, live=live, adopt=adopt_positions, now_ms=now // 1000, replacing=replacing)
    return r


async def _arcus_checks(r: Report, sessions: list[MMSession], idx: int, *, secrets: SecretStore, rest: Any,
                        markets: dict[Venue, dict[str, Market]], testnet: bool, miss: str, live: bool, adopt: bool,
                        now_ms: int, replacing: bool = False) -> None:
    try:
        address = arcus_address(secrets, testnet)
        privs = arcus_private_keys(secrets, testnet)
    except BotError as e:
        r.add(miss, "arcus key", str(e), "put ARCUS_ADDRESS and ARCUS_API_PRIVATE_KEY in .env")
        return
    given_pub = (secrets.get("ARCUS_API_KEY") or "").lower().removeprefix("0x")
    if given_pub and given_pub not in {ed25519_public_hex(p) for p in privs.values()}:
        r.add("FAIL", "arcus key", "ARCUS_API_KEY (public key) does not belong to ARCUS_API_PRIVATE_KEY",
              "copy the private key again, or delete ARCUS_API_KEY (it is not needed)")
    try:
        keys: list[ArcusKey] = await discover_arcus_keys(rest, address, privs)
    except BotError as e:
        r.add("FAIL", "arcus key", f"could not list API keys: {e}")
        return
    for k in keys:
        if not k.active:
            r.add("WARN" if len(keys) > 1 else miss, "arcus key",
                  f"{k.var} (public {k.public_key[:8]}…) is {'not registered for this address' if k.account_index is None else k.status}",
                  "register it in the Arcus web app (API Keys) for ARCUS_ADDRESS, or remove it from .env")
            continue
        h = k.hours_left(now_ms)
        lvl = "FAIL" if h is not None and h < 24 else "WARN" if h is not None and h < 7 * 24 else "PASS"
        r.add(lvl, "arcus key", f"{k.var} '{k.name}' -> subaccount {k.account_index}, "
                                f"{'no expiry' if h is None else f'{h / 24:.1f} days left'}",
              "rotate the key (Arcus web app > API Keys) and update .env")
    if idx < 0:  # no session named: check the subaccount the (first active) key is bound to
        active = [k for k in keys if k.active]
        idx = active[0].account_index if active and active[0].account_index is not None else 0
    try:
        key_for_account(keys, idx)
        r.add("PASS", "arcus subaccount", f"sessions trade subaccount {idx}, which has an active key")
    except BotError as e:
        r.add(miss, "arcus subaccount", str(e))
        return

    # account state (public reads)
    try:
        acct = await rest.account(address, idx)
        equity = Decimal(str(acct.get("equity") or 0))
        free = Decimal(str(acct.get("freeCollateral") or 0))
    except BotError as e:
        if "no activity" in str(e).lower():
            r.add("FAIL" if live else "INFO", "arcus funds", f"subaccount {idx} has never been funded",
                  "deposit USDG to this subaccount (Arcus web app) or transfer from another subaccount")
        else:
            r.add("WARN", "arcus funds", f"could not read the account: {e}")
        await _arcus_sizing(r, sessions, rest, markets)
        return
    follow = {id(s) for s in sessions if s.sizing is not None and s.sizing.follow_equity}
    need = sum(float(s.capital_usd) for s in sessions if id(s) not in follow)
    lvl = "FAIL" if live and equity <= 0 else "WARN" if float(equity) < need else "PASS"
    r.add(lvl, "arcus funds", f"equity ${equity:.2f}, free collateral ${free:.2f}"
          + (f"; fixed-size sessions plan ${need:.2f}" if need else ""),
          "deposit more, or lower capital_usd in the session file to what is really there")
    sized: list[MMSession] = []
    for s in sessions:
        if id(s) in follow:
            assert s.sizing is not None
            z = s.sizing
            cap = sizing.target_capital(float(equity), frac=z.capital_frac, max_capital=z.max_capital_usd,
                                        covered=z.backtest_capital_usd)
            if cap < z.min_capital_usd:
                r.add("FAIL" if live else "WARN", "arcus funds",
                      f"{s.market}: ${cap:,.2f} usable is under the ${z.min_capital_usd:,.2f} this setup needs "
                      "(its smallest order would fall under 1.2x the Arcus minimum)",
                      f"deposit at least ${z.min_capital_usd / z.capital_frac:,.2f}, or pick a lower-minimum setup")
                s = s.model_copy(deep=True)
            else:
                s = s.model_copy(deep=True)
                out = sizing.apply(s, cap)
                r.add("PASS", "sizing", f"{s.market}: sizes follow the equity: ${out.capital:,.2f} of ${equity:,.2f} "
                      f"(backtested at ${z.backtest_capital_usd:,.2f}; at most 1.25x a GO backtest) -> order "
                      f"${s.order_size_usd:,.2f}, cap ${s.inventory_cap_usd:,.2f}, stops ${s.pos_stop_usd:,.2f} / "
                      f"${s.daily_stop_usd:,.2f} / ${s.kill_usd:,.2f}")
        sized.append(s)
    sessions = sized

    try:
        orders = await rest.open_orders(address, idx)
    except BotError as e:
        orders = []
        r.add("WARN", "arcus orders", f"could not list open orders: {e}")
    foreign = [o for o in orders if not str(o.get("clientId") or "").startswith(BOT_CLIENT_ID_PREFIX)]
    ours = len(orders) - len(foreign)
    if foreign:
        r.add("FAIL" if live else "WARN", "arcus orders",
              f"{len(foreign)} open order(s) on subaccount {idx} were not placed by this bot; the bot cancels unknown "
              "orders on its subaccount at start", "cancel them yourself, or give the bot its own subaccount")
    if ours:
        r.add("INFO", "arcus orders", f"{ours} order(s) left by a previous bot run will be reconciled (cancelled)")
    if not orders:
        r.add("PASS", "arcus orders", "no open orders")

    try:
        pos = (await rest.positions(address, idx)).get("positions") or {}
    except BotError as e:
        pos = {}
        r.add("WARN", "arcus positions", f"could not read positions: {e}")
    bases = {s.market.upper() for s in sessions}
    by_id = {m.venue_market_id: b for b, m in markets.get(Venue.ARCUS, {}).items()}
    open_pos = {by_id.get(int(k), str(k)): Decimal(str(p.get("size") or 0)) for k, p in pos.items()
                if Decimal(str(p.get("size") or 0)) != 0}
    for b, sz in sorted(open_pos.items()):
        if b in bases and replacing:
            r.add("INFO", "arcus positions", f"{b} position {sz}: the running bot closes it before this one starts")
        elif b in bases and not adopt:
            r.add("FAIL" if live else "WARN", "arcus positions",
                  f"an open {b} position ({sz}) exists; the bot would treat it as its own inventory and trade it down",
                  "close it first, or start with --adopt-positions if the bot should manage it")
        elif b in bases:
            r.add("INFO", "arcus positions", f"{b} position {sz} will be adopted as session inventory")
        else:
            r.add("WARN", "arcus positions", f"{b} position {sz} shares cross margin with the bot",
                  "consider a dedicated subaccount for the bot")
    if not open_pos:
        r.add("PASS", "arcus positions", "no open positions")

    try:
        rl = await rest.rate_limit(address, idx)
        low = []
        for pool in ("order", "cancel"):
            p = rl.get(pool) or {}
            cap, used = int(p.get("cap") or 0), int(p.get("used") or 0)
            if cap and (cap - used) / cap < 0.1:
                low.append(f"{pool} pool at {cap - used}/{cap}")
        if low:
            r.add("WARN", "arcus budget", "; ".join(low), "wait for it to refill before starting")
        else:
            r.add("PASS", "arcus budget", "order and cancel pools healthy")
    except BotError as e:
        r.add("WARN", "arcus budget", f"could not read rate limits: {e}")
    await _arcus_sizing(r, sessions, rest, markets)


async def _arcus_sizing(r: Report, sessions: list[MMSession], rest: Any,
                        markets: dict[Venue, dict[str, Market]]) -> None:
    for s in sessions:
        m = markets[Venue.ARCUS].get(s.market.upper())
        if m is None:
            continue
        try:
            mid = _mid(await rest.bbo(m.venue_symbol))
        except BotError:
            mid = None
        if mid is None:
            r.add("WARN", "sizing", f"{s.market}: no top of book to size against")
            continue
        lvl, msg, fix = sizing_lines(s, m, mid)
        r.add(lvl, "sizing", msg, fix)


__all__ = ["Check", "Report", "run_doctor", "sizing_lines"]
