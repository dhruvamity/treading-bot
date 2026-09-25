"""Bot runner: paper (live feeds, simulated orders), testnet (Arcus testnet orders) and live (mainnet, triple lock).

Wires venue WebSockets straight into the MarketDataHub (the hub's L2Book objects ARE the WS sync books), builds
adapters, state, risk, ledger and one SessionEngine per session file, then runs:
  tick loop (1 s) - order-update and fill consumers - heartbeat (5 s) - dead man's switch - reconciliation (5 min)
  - LiveParams (hourly) - key-expiry and calendar-coverage checks - daily report.
Unexpected exceptions in the trading loop put the affected venue in SAFE MODE (cancel quotes, keep positions,
alert, manual `bot resume`).
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

from bot.common.config import (
    AppConfig,
    ArcusVenueConfig,
    DNSession,
    LighterVenueConfig,
    MMSession,
)
from bot.common.decimal import D
from bot.common.errors import ConfigError, LiveLockError
from bot.common.logging import DecisionLog, Log
from bot.common.ratelimit import TokenBucket
from bot.common.secrets import SecretStore
from bot.common.time import now_us, utc_date_str
from bot.core.alerts import Alerter, Level
from bot.core.balances import BalanceLog
from bot.core.budget import BudgetGovernor
from bot.core.calendar import TradingCalendar
from bot.core.creds import (
    ArcusKey,
    arcus_address,
    arcus_private_keys,
    discover_arcus_keys,
    key_for_account,
    resolve_lighter,
)
from bot.core.dms import DeadMansSwitch
from bot.core.engine import SessionEngine
from bot.core.heartbeat import write_heartbeat
from bot.core.keys import check_keys
from bot.core.ledger import Ledger, daily_report_md, write_daily_report
from bot.core.livelock import RunMode, lock_state, mainnet_writes_allowed, resolve_mode
from bot.core.liveparams import LiveParams
from bot.core.marketdata import MarketDataHub
from bot.core.risk import RiskEngine
from bot.core.state import StateStore
from bot.strategies import make_strategy
from bot.venues.arcus.adapter import ArcusAdapter
from bot.venues.arcus.rest import ArcusRest
from bot.venues.arcus.signing import ArcusSigner
from bot.venues.arcus.ws import ArcusWS
from bot.venues.base import OrderState, OrderStatus, PublicTrade, Side, Venue
from bot.venues.lighter_rh.models import parse_public_trade
from bot.venues.lighter_rh.rest import LighterRest
from bot.venues.lighter_rh.ws import LighterWS
from bot.venues.paper.adapter import PaperVenue
from bot.venues.symbols import canonical_base

log = Log("runner")


def arcus_account_index(sessions: list[MMSession | DNSession]) -> int:
    """One Arcus subaccount per run (one adapter per venue). Mixed subaccounts are a config error, not a guess."""
    idxs = {s.account_index for s in sessions if isinstance(s, MMSession) and s.venue == "arcus"}
    idxs |= {s.legs["arcus"].account_index or 0 for s in sessions if isinstance(s, DNSession)}
    if len(idxs) > 1:
        raise ConfigError(f"sessions in one run must use the same Arcus subaccount (got {sorted(idxs)}); "
                          "start them as separate runs")
    return idxs.pop() if idxs else 0


def leverage_plan(sessions: list[MMSession | DNSession], markets: dict[Venue, dict[str, Any]]) -> list[tuple[Venue, str, int]]:
    """(venue, base, leverage) per traded market: MM leverage_max or DN leverage_per_leg, capped at the market max,
    at least 1. Two sessions on the same market must agree."""
    want: dict[tuple[Venue, str], int] = {}
    for s in sessions:
        pairs = ([(Venue(s.venue), float(s.leverage_max))] if isinstance(s, MMSession) else
                 [(Venue.ARCUS, float(s.leverage_per_leg)), (Venue.LIGHTER_RH, float(s.leverage_per_leg))])
        for v, lev in pairs:
            b = s.market.upper()
            m = markets.get(v, {}).get(b)
            cap = float(m.max_leverage) if m is not None and m.max_leverage else lev
            val = max(1, int(min(lev, cap)))
            if want.get((v, b), val) != val:
                raise ConfigError(f"sessions disagree on {v.value} {b} leverage ({want[(v, b)]} vs {val})")
            want[(v, b)] = val
    return [(v, b, lev) for (v, b), lev in sorted(want.items())]


class BotRunner:
    def __init__(self, sessions: list[MMSession | DNSession], *, mode: RunMode, cli_live: bool, app: AppConfig,
                 arcus_cfg: ArcusVenueConfig, lighter_cfg: LighterVenueConfig, secrets: SecretStore,
                 calendar: TradingCalendar, state_db: str | None = None, typed_confirmation: bool = False) -> None:
        self.sessions = sessions
        self.app = app
        self.arcus_cfg = arcus_cfg
        self.lighter_cfg = lighter_cfg
        self.secrets = secrets
        self.calendar = calendar
        lock = lock_state(cli_live=cli_live, session_live_enabled=all(s.live_enabled for s in sessions),
                          typed_confirmation=typed_confirmation)
        self.mode = resolve_mode(mode, lock)
        self.writes_mainnet = mainnet_writes_allowed(self.mode, lock)
        if self.mode is RunMode.TESTNET:
            arcus_cfg.env = "testnet"
            lighter_cfg.env = "testnet"
        elif self.mode is RunMode.LIVE:
            arcus_cfg.env = "mainnet"
            lighter_cfg.env = "mainnet"
        else:  # paper: public mainnet data, simulated orders
            arcus_cfg.env = "mainnet"
            lighter_cfg.env = "mainnet"
        self.hub = MarketDataHub()
        self.state = StateStore(state_db or app.state_db_for(self.mode.value))
        self.heartbeat_file = app.heartbeat_for(self.mode.value)
        self.arcus_keys: list[ArcusKey] = []
        self.ledger = Ledger()
        self.decisions = DecisionLog()
        self.governor = BudgetGovernor()
        self.alerter = Alerter(bot_token=secrets.get("TELEGRAM_BOT_TOKEN"), chat_id=secrets.get("TELEGRAM_CHAT_ID"),
                               min_interval_s=app.alerts.min_interval_s)
        self.risk = RiskEngine(limits=app.risk, calendar=calendar, decisions=self.decisions)
        self.bases = sorted({s.market.upper() for s in sessions})
        self.adapters: dict[Venue, Any] = {}
        self.engines: list[SessionEngine] = []
        self.dms: list[DeadMansSwitch] = []
        self.tasks: list[asyncio.Task[Any]] = []
        self._stop = asyncio.Event()
        self.arcus_ws: ArcusWS | None = None
        self.lighter_ws: LighterWS | None = None
        self.params: LiveParams | None = None
        self.started_us = now_us()
        self._paused_raw: str | None = None
        self._closing_since: float | None = None
        self.balances = BalanceLog(Path(app.state_dir) / "balances.jsonl")   # live equity every 5 min
        self.account: dict[str, dict[str, Any]] = {}   # venue -> last balance read (every 15 s), for the dashboard

    # ================================================================ build
    async def build(self) -> None:
        a_rest = ArcusRest(self.arcus_cfg.rest_url(), ip_bucket=TokenBucket(1200, 20))
        l_rest = LighterRest(self.lighter_cfg.rest_url(), rest_per_min=40)
        self.params = LiveParams(arcus=a_rest, lighter=l_rest, out_dir=Path(self.app.data_dir) / "param_changes_jsonl")
        await self.params.refresh()
        markets = self.params.markets
        for b in self.bases:
            for s in self.sessions:
                if s.market.upper() == b and isinstance(s, MMSession) and b not in markets.get(Venue(s.venue), {}):
                    raise ConfigError(f"{b} not listed on {s.venue}")
        # ---- market data
        self.arcus_ws = ArcusWS(self.arcus_cfg.ws_url(), n_levels=100)
        self.lighter_ws = LighterWS(self.lighter_cfg.ws_url(), readonly=self.mode is not RunMode.LIVE)
        sm = self.params.symbol_map
        for b in self.bases:
            if sm.has(b, Venue.ARCUS):
                disp = sm.get(b, Venue.ARCUS).venue_symbol
                await self.arcus_ws.subscribe_market(disp)
                self.hub.view(Venue.ARCUS, b).book = self.arcus_ws.books[disp].book
            if sm.has(b, Venue.LIGHTER_RH):
                mid = sm.get(b, Venue.LIGHTER_RH).venue_market_id
                await self.lighter_ws.subscribe_market(mid, b)
                self.hub.view(Venue.LIGHTER_RH, b).book = self.lighter_ws.books[mid].book
        await self.arcus_ws.subscribe_global(markets=True, oracle=True, attrs=True)
        self._wire_feeds()
        # ---- adapters
        await self._build_adapters(a_rest, l_rest)
        # ---- engines
        for i, s in enumerate(self.sessions):
            eng = SessionEngine(session=s, strategy=make_strategy(s), hub=self.hub, adapters=self.adapters,
                                markets=markets, state=self.state, risk=self.risk, governor=self.governor,
                                ledger=self.ledger, decisions=self.decisions, calendar=self.calendar,
                                session_num=(now_us() // 60_000_000 + i) % 10**7, alerter=self.alerter,
                                risk_limits=self.app.risk)
            eng.settings_dir = Path(self.app.state_dir)   # the owner's Telegram settings, read at each re-size
            self.engines.append(eng)
        self.by_session = {e.sid: e for e in self.engines}
        log.info("runner_built", data={"mode": self.mode.value, "mainnet_writes": self.writes_mainnet,
                                        "sessions": [s.session_id for s in self.sessions], "bases": self.bases})

    async def _build_adapters(self, a_rest_public: ArcusRest, l_rest_public: LighterRest) -> None:
        markets = self.params.markets if self.params else {}
        venues_needed: set[Venue] = set()
        for s in self.sessions:
            if isinstance(s, DNSession):
                venues_needed |= {Venue.ARCUS, Venue.LIGHTER_RH}
            else:
                venues_needed.add(Venue(s.venue))
        if self.mode is RunMode.PAPER:
            for v in venues_needed:
                mk = {b: m for b, m in markets[v].items() if b in self.bases}
                books = {b: self.hub.view(v, b).book for b in mk}
                pv = PaperVenue(v, mk, books, now_us=now_us, starting_equity=D(str(self._paper_equity(v))),
                                marks=self._mark_fn(v))
                await pv.connect()
                self.adapters[v] = pv
            return
        if Venue.ARCUS in venues_needed:
            testnet = self.mode is RunMode.TESTNET
            idx = arcus_account_index(self.sessions)
            address = arcus_address(self.secrets, testnet)
            self.arcus_keys = await discover_arcus_keys(a_rest_public, address, arcus_private_keys(self.secrets, testnet))
            key = key_for_account(self.arcus_keys, idx)
            writes = self.writes_mainnet if self.mode is RunMode.LIVE else self.mode is RunMode.TESTNET
            rest = ArcusRest(self.arcus_cfg.rest_url(), signer=ArcusSigner(key.private_key), address=address,
                             writes_allowed=writes, is_mainnet=self.mode is RunMode.LIVE)
            ws = ArcusWS(self.arcus_cfg.ws_url(), n_levels=5)
            ad = ArcusAdapter(rest, ws, address=address, account_index=idx, markets=markets[Venue.ARCUS],
                              good_til_days=self.arcus_cfg.good_til_days)
            await ad.connect()
            self.adapters[Venue.ARCUS] = ad
            self.dms.append(DeadMansSwitch(f"arcus:{idx}", ad.arm_dead_mans_switch, refresh_s=self.arcus_cfg.dms.refresh_s,
                                           deadline_s=self.arcus_cfg.dms.deadline_s,
                                           on_failure=lambda why: self._safe_mode(Venue.ARCUS, why)))
        if Venue.LIGHTER_RH in venues_needed:
            from bot.venues.lighter_rh.adapter import LighterAdapter
            from bot.venues.lighter_rh.auth import AuthTokenManager
            from bot.venues.lighter_rh.nonce import NonceManager
            from bot.venues.lighter_rh.signer import LighterSigner

            lc = await resolve_lighter(l_rest_public, self.secrets, self.mode is RunMode.TESTNET)
            signer = LighterSigner(url=self.lighter_cfg.rest_url(), chain_id=self.lighter_cfg.chain(),
                                   account_index=lc.account_index, api_key_index=lc.api_key_index,
                                   private_key_hex=lc.private_key,
                                   reserved_indices=tuple(self.lighter_cfg.reserved_api_key_indices))
            l_writes = self.writes_mainnet if self.mode is RunMode.LIVE else self.mode is RunMode.TESTNET
            l_rest = LighterRest(self.lighter_cfg.rest_url(), writes_allowed=l_writes)
            l_ws = LighterWS(self.lighter_cfg.ws_url(), readonly=False)
            nonce_file = Path(self.app.state_dir) / f"lighter_nonce_{self.mode.value}_{lc.account_index}_{lc.api_key_index}"
            ad2 = LighterAdapter(l_rest, l_ws, signer=signer, nonces=NonceManager(nonce_file), auth=AuthTokenManager(signer),
                                 account_index=lc.account_index, markets=markets[Venue.LIGHTER_RH])
            await ad2.connect()
            self.adapters[Venue.LIGHTER_RH] = ad2
            self.dms.append(DeadMansSwitch(f"lighter:{lc.account_index}", ad2.arm_dead_mans_switch, refresh_s=30,
                                           deadline_s=90, on_failure=lambda why: self._safe_mode(Venue.LIGHTER_RH, why)))
        await self._apply_leverage()

    async def _apply_leverage(self) -> None:
        """Arcus (and Lighter) default an account+market to the market's MAXIMUM leverage when none was set. Set
        each traded market to the session's leverage explicitly before the first order, cross margin."""
        markets = self.params.markets if self.params else {}
        for venue, base, lev in leverage_plan(self.sessions, markets):
            ad = self.adapters.get(venue)
            if ad is None or isinstance(ad, PaperVenue):
                continue
            await ad.set_leverage(base, lev, False)
            got = await ad.leverage(base) if hasattr(ad, "leverage") else lev
            if got is not None and int(got) != lev:
                raise ConfigError(f"{venue.value} {base}: asked for {lev}x but the venue reports {got}x; not starting")
            self.decisions.record("leverage", f"set {venue.value} {base} leverage to {lev}x cross before quoting",
                                  venue=venue.value, market=base, leverage=lev)

    def _paper_equity(self, v: Venue) -> float:
        """A paper account holds what the sessions on it were sized for (the backtested capital for pilot sessions),
        so equity-following sizes start where the backtest did."""
        total = 0.0
        for s in self.sessions:
            if isinstance(s, DNSession):
                total += s.collateral_per_leg_usd
            elif Venue(s.venue) is v:
                total += s.sizing.backtest_capital_usd if s.sizing else s.capital_usd
        return total or self.app.capital_usd_total / 2

    def _mark_fn(self, v: Venue) -> Any:
        def f(b: str) -> Decimal | None:
            view = self.hub.view(v, b)
            return view.mark or view.mid()

        return f

    # ================================================================ feeds -> hub
    def _wire_feeds(self) -> None:
        assert self.arcus_ws is not None and self.lighter_ws is not None
        aw, lw = self.arcus_ws, self.lighter_ws

        def a_book(base: str, sync: Any, res: Any, recv: int, snapshot: bool, c: Any) -> None:
            v = self.hub.view(Venue.ARCUS, base)
            v.book_ts_us = recv

        def a_trades(base: str, rows: list[dict[str, Any]], recv: int) -> None:
            v = self.hub.view(Venue.ARCUS, base)
            for t in rows:
                pt = PublicTrade(Venue.ARCUS, base, int(t["timestamp"]), D(t["price"]), D(t["size"]),
                                 Side.BUY if t["side"] == "BUY" else Side.SELL, str(t["tradeId"]),
                                 t.get("makerAddress"), t.get("takerAddress"), t.get("makerOrderId"))
                v.on_trade(pt)
                self._paper_trade(Venue.ARCUS, pt)

        def a_oracle(prices: list[dict[str, Any]], recv: int) -> None:
            for p in prices:
                base = canonical_base(Venue.ARCUS, p.get("marketDisplayName", ""))
                if base in self.bases:
                    v = self.hub.view(Venue.ARCUS, base)
                    v.oracle = D(p["price"]) if p.get("price") else v.oracle
                    v.mark = D(p["markPrice"]) if p.get("markPrice") else v.mark
                    v.price_ts_us = recv

        def a_pf(base: str, c: dict[str, Any], recv: int) -> None:
            self.hub.view(Venue.ARCUS, base).predicted_funding_h = float(c.get("rate1h") or 0)

        def a_markets(ms: dict[str, dict[str, Any]], recv: int) -> None:
            for m in ms.values():
                base = canonical_base(Venue.ARCUS, m.get("marketDisplayName", ""))
                if base not in self.bases:
                    continue
                v = self.hub.view(Venue.ARCUS, base)
                v.is_outside_rth = bool(m.get("isOutsideRth"))
                v.status = str(m["status"]).upper() if m.get("status") else v.status
                v.upper_bound = D(m["upperTradingBound"]) if m.get("upperTradingBound") else None
                v.lower_bound = D(m["lowerTradingBound"]) if m.get("lowerTradingBound") else None
                v.upper_in_zone = bool(m.get("isUpperInExpansionZone"))
                v.lower_in_zone = bool(m.get("isLowerInExpansionZone"))
                v.oi = D(m["openInterest"]) if m.get("openInterest") else None
                v.oi_cap = D(m["openInterestCapNotional"]) if m.get("openInterestCapNotional") else None
                v.last_funding_h = float(m.get("fundingRate") or 0)

        def l_book(base: str, sync: Any, res: Any, recv: int, snapshot: bool, m: Any) -> None:
            self.hub.view(Venue.LIGHTER_RH, base).book_ts_us = recv

        def l_trades(base: str, trades: list[dict[str, Any]], liq: list[dict[str, Any]], recv: int) -> None:
            v = self.hub.view(Venue.LIGHTER_RH, base)
            for t in trades + liq:
                pt = parse_public_trade(t, base)
                v.on_trade(pt)
                self._paper_trade(Venue.LIGHTER_RH, pt)

        def l_stats(base: str, s: dict[str, Any], recv: int) -> None:
            if base not in self.bases:
                return
            v = self.hub.view(Venue.LIGHTER_RH, base)
            v.mark = D(s["mark_price"]) if s.get("mark_price") else v.mark
            v.index = D(s["index_price"]) if s.get("index_price") else v.index
            v.price_ts_us = recv
            if s.get("current_funding_rate") not in (None, ""):
                v.predicted_funding_h = float(s["current_funding_rate"]) / 100
            if s.get("premium") not in (None, ""):
                v.premium = float(s["premium"]) / 100
            v.oi = D(s["open_interest"]) / (v.mark or D(1)) if s.get("open_interest") else v.oi

        aw.on("book", a_book)
        aw.on("trades", a_trades)
        aw.on("oracle", a_oracle)
        aw.on("predicted_funding", a_pf)
        aw.on("markets", a_markets)
        lw.on("book", l_book)
        lw.on("trades", l_trades)
        lw.on("market_stats", l_stats)

    def _paper_trade(self, venue: Venue, t: PublicTrade) -> None:
        ad = self.adapters.get(venue)
        if isinstance(ad, PaperVenue):
            ad.on_public_trade(t)

    # ================================================================ loops
    def _check_resume(self) -> None:
        """`bot resume` writes a kv flag; honour it here (manual resume after investigation)."""
        import orjson

        raw = self.state.kv_get("resume")
        if not raw:
            return
        req = orjson.loads(raw)
        venue = Venue(req["venue"]) if req.get("venue") else None
        self.risk.resume(venue, all_=bool(req.get("all")))
        self.state.kv_set("resume", "")
        self.decisions.record("resume", f"manual resume ({raw})", venue=venue.value if venue else None)

    async def _check_operator(self) -> None:
        """Operator controls written to the state DB by `bot telegram` (or any tool):
        kv "paused"  = JSON {BASE or "*": reason}: persistent; quoting stops for those markets and the strategy's
                       reduce-only exit book works inventory off. Survives restarts because it lives in the DB.
        kv "control" = JSON {"cmd": "stop", "by": ...}: one-shot graceful stop (cancel quotes, keep positions);
                       {"cmd": "close", ...}: close every position (maker, then taker after exit_taker_after_s),
                       then stop; after 10 minutes it stops anyway and says what is left."""
        import orjson

        raw = self.state.kv_get("paused") or ""
        if raw != self._paused_raw:
            first = self._paused_raw is None
            self._paused_raw = raw
            try:
                want: dict[str, str] = orjson.loads(raw) if raw else {}
            except orjson.JSONDecodeError:
                want = {}
            new: dict[tuple[Venue, str], str] = {}
            for e in self.engines:
                why = want.get(e.base) or want.get("*")
                if why:
                    for v in ([e.venue, e.other_venue] if e.is_dn and e.other_venue else [e.venue]):
                        new[(v, e.base)] = f"paused by operator: {why}"
            if new != self.risk.operator_paused or not first:
                self.risk.operator_paused = new
                what = ", ".join(sorted({b for _, b in new})) or "nothing (all markets quoting)"
                self.decisions.record("operator_pause", f"paused: {what}")
                await self.alerter.send(Level.INFO, "operator_pause", f"{self.mode.value}: paused {what}")
        raw = self.state.kv_get("control") or ""
        if raw:
            self.state.kv_set("control", "")
            try:
                req = orjson.loads(raw)
            except orjson.JSONDecodeError:
                return
            if req.get("cmd") == "stop":
                self.decisions.record("operator_stop", f"stop requested by {req.get('by') or 'operator'}")
                await self.alerter.send(Level.WARN, "operator_stop", f"{self.mode.value}: stopping on request of "
                                        f"{req.get('by') or 'operator'} (quotes cancelled, positions kept)")
                self._stop.set()
            elif req.get("cmd") == "close" and self._closing_since is None:
                self._closing_since = time.monotonic()
                for e in self.engines:
                    e.clock.begin_exit(now_us())
                self.decisions.record("operator_close", f"close and stop requested by {req.get('by') or 'operator'}")
                await self.alerter.send(Level.WARN, "operator_close", f"{self.mode.value}: closing positions, then "
                                        f"stopping (requested by {req.get('by') or 'operator'})")
        if self._closing_since is not None:
            left = {f"{e.base}": str(self.state.position(e.venue, e.base)) for e in self.engines
                    if self.state.position(e.venue, e.base) != 0}
            if not left or time.monotonic() - self._closing_since > 600:
                if left:
                    await self.alerter.send(Level.CRIT, "operator_close", f"{self.mode.value}: stopping with open "
                                            f"positions after 10 min of trying to close: {left}")
                self._stop.set()

    def snapshot(self) -> dict[str, Any]:
        """Everything an operator screen needs, as plain JSON. Published to kv "status" every 5 s."""
        now = now_us()
        marks: dict[tuple[Venue, str], Decimal] = {}
        for (v, b), view in self.hub.views.items():
            mk = view.mark or view.mid()
            if mk is not None:
                marks[(v, b)] = mk
        keys = set(self.ledger.books) | {k for k, p in self.state.positions.items() if p}
        for e in self.engines:
            keys.add((e.venue, e.base))
        markets = []
        for v, b in sorted(keys, key=lambda k: (k[0].value, k[1])):
            bd = self.ledger.breakdown(v, b, marks.get((v, b)))
            ok, why = self.risk.quoting_allowed(v, b, now)
            markets.append({"venue": v.value, "market": b, "position": str(self.state.position(v, b)),
                            "mark": str(marks[(v, b)]) if (v, b) in marks else None, "net": str(bd.net),
                            "spread_capture": str(bd.spread_capture), "inventory_mtm": str(bd.inventory_mtm),
                            "fees": str(bd.fees), "funding": str(bd.funding), "volume": str(bd.volume),
                            "maker_volume": str(bd.maker_volume), "fills": bd.fills,
                            "open_orders": len(self.state.open_orders(v, b)), "quoting": ok, "why": "" if ok else why})
        day = utc_date_str(now)
        sessions = []
        for e in self.engines:
            venues = [e.venue] + ([e.other_venue] if e.is_dn and e.other_venue else [])
            pnl = sum((self.ledger.breakdown(v, e.base, marks.get((v, e.base))).net for v in venues), Decimal(0))
            start = e.day_start_equity.get(day)
            sessions.append({"session": e.sid, "market": e.base, "venue": e.venue.value, "mode": e.last_mode,
                             "pnl": str(pnl), "day_pnl": str(e.capital + pnl - start) if start is not None else None,
                             "capital": str(e.capital), "size_capital": str(e.size_capital),
                             "stops": {k: getattr(e.session, f, None) for k, f in (
                                 ("position", "pos_stop_usd"), ("daily", "daily_stop_usd"), ("kill", "kill_usd"))},
                             "ticks": e.stats.ticks, "actions": e.stats.actions,
                             "rejects": e.stats.rejects, "errors": e.stats.errors})
        return {"ts_us": now, "mode": self.mode.value, "started_us": self.started_us, "markets": markets,
                "sessions": sessions,
                "account": self.account,
                "risk": {"all_stopped": self.risk.all_stopped,
                         "safe_mode": {k.value: v for k, v in self.risk.safe_mode.items()},
                         "venue_stopped_day": {k.value: v for k, v in self.risk.venue_stopped_day.items()},
                         "operator_paused": sorted({b for _, b in self.risk.operator_paused})},
                "budget": self.governor.state()}

    async def _tick_loop(self) -> None:
        last_acct = 0.0
        while not self._stop.is_set():
            t0 = time.monotonic()
            now = now_us()
            self._check_resume()
            await self._check_operator()
            self.hub.tick_1s(now)
            try:
                for ad in self.adapters.values():
                    if isinstance(ad, PaperVenue):
                        ad.process(now)
                if time.monotonic() - last_acct > 15:
                    last_acct = time.monotonic()
                    for v, ad in self.adapters.items():
                        bal = await ad.balances()
                        self.account[v.value] = {
                            "equity": float(bal.get("equity") or 0), "free": float(bal.get("free_collateral") or 0),
                            "net_deposits": float(bal["net_deposits"]) if bal.get("net_deposits") is not None else None,
                            "ts_us": now}
                        for e in self.engines:
                            e.set_account(v, D(bal.get("equity", 0)), D(bal.get("free_collateral", 0)))
                        if self.mode is RunMode.LIVE and v is Venue.ARCUS and bal.get("equity"):
                            self.balances.record(source="bot", account_index=arcus_account_index(self.sessions),
                                                 equity=float(bal["equity"]),
                                                 free=float(bal.get("free_collateral") or 0),
                                                 net_deposits=float(bal["net_deposits"])
                                                 if bal.get("net_deposits") is not None else None,
                                                 min_interval_s=300)
                for e in self.engines:
                    await e.tick(now)
            except LiveLockError:
                raise
            except Exception as ex:  # safe mode on anything unexpected in the trading loop (B3.6)
                log.error("tick_error", reason=type(ex).__name__, data={"err": str(ex)[:300]}, exc_info=True)
                for v in list(self.adapters):
                    await self._safe_mode(v, f"unexpected {type(ex).__name__} in trading loop: {str(ex)[:120]}")
            await asyncio.sleep(max(0.0, 1.0 - (time.monotonic() - t0)))

    async def _consume(self, venue: Venue) -> None:
        ad = self.adapters[venue]

        async def updates() -> None:
            async for st in ad.order_updates():
                o = self.state.orders.get(st.client_id)
                eng = self.by_session.get(o.session) if o else None
                if eng is not None:
                    await eng.on_order_update(st, now_us())
                else:
                    self.state.on_update(st)

        async def fills() -> None:
            async for f in ad.fills():
                o = self.state.orders.get(f.client_id)
                eng = self.by_session.get(o.session) if o else (self.engines[0] if self.engines else None)
                if eng is not None:
                    await eng.on_fill(f, now_us())

        await asyncio.gather(updates(), fills())

    async def _heartbeat(self) -> None:
        import orjson

        while not self._stop.is_set():
            write_heartbeat(self.heartbeat_file, mode=self.mode.value)
            try:
                self.state.kv_set("status", orjson.dumps(self.snapshot(), default=str,
                                                          option=orjson.OPT_NON_STR_KEYS).decode())
            except Exception as e:  # a status screen must never take the bot down
                log.warning("status_publish_failed", reason=type(e).__name__, data={"err": str(e)[:200]})
            await asyncio.sleep(5)

    async def reconcile_once(self) -> None:
        for v, ad in self.adapters.items():
            try:
                rep = self.state.reconcile(v, await ad.open_orders(), await ad.positions())
            except Exception as e:
                log.warning("reconcile_failed", venue=v.value, reason=type(e).__name__)
                continue
            if rep.unknown_live:
                ids = [s.client_id for s in rep.unknown_live if s.client_id]
                self.decisions.record("cancel", "reconcile_unknown", venue=v.value, client_ids=ids)
                if ids:
                    await ad.cancel(ids)
                if any(not s.client_id for s in rep.unknown_live):
                    await ad.cancel_all(None)
            if rep.position_mismatch:
                await self.alerter.send(Level.WARN, "reconcile", f"{v.value} position mismatch (venue trusted): "
                                        f"{rep.position_mismatch}")

    async def _reconcile_loop(self) -> None:
        """Every 5 minutes, and within 5 s of an adapter asking (an account stream went `degraded`)."""
        last = time.monotonic()
        while not self._stop.is_set():
            await asyncio.sleep(5)
            asked = [ad for ad in self.adapters.values() if getattr(ad, "resync_requested", False)]
            if not asked and time.monotonic() - last < 300:
                continue
            for ad in asked:
                ad.resync_requested = False
            if asked:
                log.warning("reconcile_now", reason="an account stream was degraded")
            last = time.monotonic()
            await self.reconcile_once()

    async def _housekeeping(self) -> None:
        last_day = ""
        while not self._stop.is_set():
            now = now_us()
            day = utc_date_str(now)
            keys = {f"arcus sub{k.account_index} key {k.name or k.var}": k.valid_until_ms for k in self.arcus_keys
                    if k.active and k.valid_until_ms}
            for k in check_keys(keys, now // 1000):
                if k.level != "ok":
                    await self.alerter.send(Level.CRIT if k.level == "expired" else Level.WARN, f"key:{k.name}",
                                            f"API key {k.name} {k.level}: {k.remaining_h:.0f} h left; rotate it")
            if self.calendar.coverage_days(now, "fomc") < 30 or self.calendar.coverage_days(now, "cpi") < 14:
                await self.alerter.send(Level.WARN, "calendar", "event calendar coverage is short: update "
                                        "config/calendars/events.csv (CPI/NFP/FOMC)")
            if day != last_day and last_day:
                marks = {(v, b): (vw.mark or vw.mid()) for (v, b), vw in self.hub.views.items() if vw.mid() is not None}
                md = daily_report_md(last_day, self.ledger, {k: m for k, m in marks.items() if m is not None},
                                     {"mode": self.mode.value, "budget": self.governor.state()})
                write_daily_report(self.app.reports_for(self.mode.value), last_day, md)
                await self.alerter.send(Level.INFO, "daily", f"daily report {last_day} written")
            last_day = day
            await asyncio.sleep(3600)

    async def _safe_mode(self, venue: Venue, why: str) -> None:
        d = self.risk.enter_safe_mode(venue, why)
        await self.alerter.send(Level.CRIT, "safe_mode", f"{venue.value} SAFE MODE: {d.reason}")
        ad = self.adapters.get(venue)
        if ad is not None:
            with contextlib.suppress(Exception):
                await ad.cancel_all(None)

    # ================================================================ run
    async def run(self, duration_s: float | None = None) -> None:
        await self.build()
        assert self.arcus_ws is not None and self.lighter_ws is not None
        self.arcus_ws.start()
        self.lighter_ws.start()
        await self.arcus_ws.ws.wait_connected()
        await self.lighter_ws.ws.wait_connected()
        await asyncio.sleep(3)  # let the books snapshot
        await self.reconcile_once()
        for d in self.dms:
            d.start()
        self.tasks = [asyncio.create_task(self._tick_loop()), asyncio.create_task(self._heartbeat()),
                      asyncio.create_task(self._reconcile_loop()), asyncio.create_task(self._housekeeping())]
        if self.params is not None:
            self.tasks.append(asyncio.create_task(self.params.run_forever()))
        for v in self.adapters:
            self.tasks.append(asyncio.create_task(self._consume(v)))
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, self._stop.set)
        await self.alerter.send(Level.INFO, "start", f"Bot started in {self.mode.value} mode "
                                f"({', '.join(s.session_id for s in self.sessions)})")
        try:
            if duration_s:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._stop.wait(), duration_s)
            else:
                await self._stop.wait()
        finally:
            await self.shutdown()

    async def shutdown(self) -> None:
        self._stop.set()
        for t in self.tasks:
            t.cancel()
        for v, ad in self.adapters.items():
            try:
                await ad.cancel_all(None)  # pull quotes; positions are kept (flatten is explicit)
            except Exception as e:
                log.error("shutdown_cancel_all_failed", venue=v.value, reason=type(e).__name__, data={"err": str(e)[:200]})
                continue
            # The venue confirmed cancel-all; its per-order CANCELED events may arrive after the consumers stop, so
            # record the outcome locally (the next start reconciles against the venue anyway).
            for o in self.state.open_orders(v):
                self.state.on_update(OrderState(o.req.client_id or "", o.venue_order_id, OrderStatus.CANCELED,
                                                o.filled, None, "shutdown cancel-all", now_us(), v, o.req.base))
        for d in self.dms:
            with contextlib.suppress(Exception):
                await d.stop(disarm=True)
        for ws in (self.arcus_ws, self.lighter_ws):
            if ws is not None:
                with contextlib.suppress(Exception):
                    await ws.stop()
        for ad in self.adapters.values():
            with contextlib.suppress(Exception):
                await ad.close()
        if self.params is not None:
            for c in (self.params.arcus, self.params.lighter):
                if c is not None:
                    with contextlib.suppress(Exception):
                        await c.close()
        await self.alerter.close()
        log.info("runner_stopped", data={"ledger": {f"{v.value}:{b}": str(self.ledger.breakdown(v, b).net)
                                                    for (v, b) in list(self.ledger.books)}})

    def status(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "open_orders": len(self.state.open_orders()),
            "positions": {f"{v.value}:{b}": str(p) for (v, b), p in self.state.positions.items() if p},
            "risk": {"all_stopped": self.risk.all_stopped, "safe_mode": {k.value: v for k, v in self.risk.safe_mode.items()}},
            "budget": self.governor.state(),
            "pnl": {f"{v.value}:{b}": str(self.ledger.breakdown(v, b).net) for (v, b) in list(self.ledger.books)},
        }


__all__ = ["BotRunner", "Decimal"]
