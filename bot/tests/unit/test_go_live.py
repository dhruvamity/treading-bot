"""Go-live safety: credential discovery, doctor verdicts, selftest guard and classification, per-mode state,
leverage planning. All offline (fake venue clients)."""

from __future__ import annotations

import asyncio
import time
from decimal import Decimal as D
from pathlib import Path
from typing import Any

import pytest

from bot.common.config import AppConfig, load_session
from bot.common.errors import AuthError, ConfigError, OrderRejected, SecretsError, VenueError
from bot.common.secrets import upsert_dotenv
from bot.core.calendar import TradingCalendar
from bot.core.creds import (
    arcus_private_keys,
    discover_arcus_keys,
    ed25519_public_hex,
    key_for_account,
    normalize_ed25519_key,
    resolve_lighter,
)
from bot.core.doctor import run_doctor, sizing_lines
from bot.core.heartbeat import write_heartbeat
from bot.core.livelock import RunMode
from bot.core.runner import arcus_account_index, leverage_plan
from bot.core.selftest import classify, probe_order_size, run_selftest
from bot.venues.base import Venue
from tests.helpers import fixture_markets, mm_session

ROOT = Path(__file__).parents[2]
ADDR = "0x" + "9d" * 20
K0 = "11" * 32
K2 = "22" * 32
MK = fixture_markets()


class Env:
    """SecretStore over a dict (no files, no process environment)."""

    def __init__(self, **kv: str) -> None:
        self.kv = kv

    def get(self, n: str, default: str | None = None) -> str | None:
        return self.kv.get(n, default)

    def names_with_prefix(self, p: str) -> list[str]:
        return sorted(k for k, v in self.kv.items() if k.startswith(p) and v)


class FakeArcus:
    def __init__(self, *, funded: bool = True, orders: list[dict[str, Any]] | None = None,
                 positions: dict[str, Any] | None = None, keys: list[dict[str, Any]] | None = None,
                 geo_blocked: bool = False, skew_s: float = 0.0) -> None:
        self.funded, self._orders, self._pos = funded, orders or [], positions or {}
        self._keys = keys if keys is not None else [
            {"apiKey": ed25519_public_hex(K0), "accountIndex": 0, "apiWalletName": "mm", "status": "ACTIVE",
             "validUntil": int(time.time() * 1000) + 40 * 86_400_000}]
        self.geo_blocked, self.skew_s = geo_blocked, skew_s

    async def api_keys(self, address: str, account_index: int | None = None) -> list[dict[str, Any]]:
        return self._keys

    async def compliance(self) -> dict[str, Any]:
        return {"geo": {"country": "ZZ", "region": "ZZ-01", "restrictions": {"perpetuals": self.geo_blocked}}}

    async def server_time(self) -> dict[str, Any]:
        return {"timeNs": time.time_ns() + int(self.skew_s * 1e9)}

    async def account(self, address: str, idx: int) -> dict[str, Any]:
        if not self.funded:
            raise VenueError("arcus", "this account has no activity yet", status=404)
        return {"equity": "50", "freeCollateral": "50"}

    async def open_orders(self, address: str, idx: int) -> list[dict[str, Any]]:
        return self._orders

    async def positions(self, address: str, idx: int) -> dict[str, Any]:
        return {"positions": self._pos}

    async def rate_limit(self, address: str, idx: int) -> dict[str, Any]:
        return {"order": {"used": 0, "cap": 20000}, "cancel": {"used": 0, "cap": 40000}}

    async def bbo(self, market: str) -> dict[str, Any]:
        return {"bestBid": {"price": "86000.0", "size": "1"}, "bestAsk": {"price": "86000.1", "size": "1"}}


def cal() -> TradingCalendar:
    return TradingCalendar.load(ROOT / "config/calendars")


def doctor(sessions: list[Any], rest: FakeArcus, env: Env, *, mode: RunMode = RunMode.LIVE, tmp: Path, **kw: Any) -> Any:
    app = AppConfig(state_dir=str(tmp))
    return asyncio.run(run_doctor(sessions, mode=mode, app=app, secrets=env, calendar=cal(),  # type: ignore[arg-type]
                                  arcus_rest=rest, lighter_rest=None, markets=MK, **kw))


def levels(rep: Any, area: str) -> set[str]:
    return {c.level for c in rep.checks if c.area == area}


# ------------------------------------------------------------------------------------------------ creds
def test_key_discovery_and_binding() -> None:
    env = Env(ARCUS_ADDRESS=ADDR, ARCUS_API_PRIVATE_KEY="0x" + K0.upper(), ARCUS_API_PRIVATE_KEY_2=K2)
    privs = arcus_private_keys(env)  # type: ignore[arg-type]
    assert set(privs) == {"ARCUS_API_PRIVATE_KEY", "ARCUS_API_PRIVATE_KEY_2"} and privs["ARCUS_API_PRIVATE_KEY"] == K0
    keys = asyncio.run(discover_arcus_keys(FakeArcus(), ADDR, privs))
    k0 = key_for_account(keys, 0)
    assert k0.var == "ARCUS_API_PRIVATE_KEY" and k0.active and 39 * 24 < (k0.hours_left() or 0) < 41 * 24
    assert K0 not in repr(k0)  # the private key never appears in a repr
    unreg = next(k for k in keys if k.var == "ARCUS_API_PRIVATE_KEY_2")
    assert unreg.account_index is None and not unreg.active
    with pytest.raises(SecretsError, match="account_index: 0"):
        key_for_account(keys, 1)
    with pytest.raises(SecretsError, match="64-hex"):
        normalize_ed25519_key("abc", "X")
    with pytest.raises(SecretsError, match="not set"):
        arcus_private_keys(Env(ARCUS_ADDRESS=ADDR))  # type: ignore[arg-type]


def test_lighter_creds_defaults_and_reserved_slot() -> None:
    class FL:
        async def accounts_by_l1(self, a: str) -> dict[str, Any]:
            return {"sub_accounts": [{"index": 777}]}

    lc = asyncio.run(resolve_lighter(FL(), Env(LIGHTER_ADDRESS=ADDR, LIGHTER_API_PRIVATE_KEY="ab" * 40)))  # type: ignore[arg-type]
    assert (lc.account_index, lc.api_key_index) == (777, 4) and "ab" * 40 not in repr(lc)
    with pytest.raises(SecretsError, match="reserved"):
        asyncio.run(resolve_lighter(FL(), Env(LIGHTER_ADDRESS=ADDR, LIGHTER_API_PRIVATE_KEY="ab",  # type: ignore[arg-type]
                                              LIGHTER_API_KEY_INDEX="157")))


def test_upsert_dotenv(tmp_path: Path) -> None:
    p = tmp_path / ".env"
    upsert_dotenv("A", "1", p)
    p.write_text(p.read_text() + "# note\nB=2\n")
    upsert_dotenv("A", "3", p)
    upsert_dotenv("C", "4", p)
    assert p.read_text().splitlines() == ["A=3", "# note", "B=2", "C=4"] and (p.stat().st_mode & 0o777) == 0o600


# ------------------------------------------------------------------------------------------------ plan
def test_one_arcus_subaccount_per_run_and_leverage_plan() -> None:
    a0, a1 = mm_session(), mm_session(session_id="b", account_index=1)
    assert arcus_account_index([a0]) == a0.account_index
    with pytest.raises(ConfigError, match="same Arcus subaccount"):
        arcus_account_index([mm_session(account_index=0), a1])
    plan = leverage_plan([mm_session(leverage_max=5)], MK)
    assert plan == [(Venue.ARCUS, "BTC", 5)]
    capped = leverage_plan([mm_session(leverage_max=500)], MK)
    assert capped[0][2] == int(MK[Venue.ARCUS]["BTC"].max_leverage or 500)
    with pytest.raises(ConfigError, match="disagree"):
        leverage_plan([mm_session(leverage_max=5), mm_session(session_id="x", leverage_max=3)], MK)


def test_per_mode_paths() -> None:
    app = AppConfig(state_dir="st")
    assert app.state_db_for("paper") != app.state_db_for("live")
    assert app.heartbeat_for("paper") != app.heartbeat_for("live")
    assert app.reports_for("live") != app.reports_for("paper")


def test_heartbeat_files_do_not_share_a_temp_file(tmp_path: Path) -> None:
    app = AppConfig(state_dir=str(tmp_path))
    write_heartbeat(app.heartbeat_for("paper"), mode="paper")
    assert not Path(app.heartbeat_for("live")).exists()
    assert not list(tmp_path.glob("heartbeat.tmp"))


# ------------------------------------------------------------------------------------------------ doctor
def test_doctor_ready_account(tmp_path: Path) -> None:
    env = Env(ARCUS_ADDRESS=ADDR, ARCUS_API_PRIVATE_KEY=K0, TELEGRAM_BOT_TOKEN="t", TELEGRAM_CHAT_ID="c")
    rep = doctor([mm_session(account_index=0, order_size_usd="auto")], FakeArcus(), env, tmp=tmp_path)
    assert not rep.failed, rep.render()
    assert levels(rep, "arcus key") == {"PASS"} and levels(rep, "sizing") == {"PASS"}
    assert any("sets 5x cross" in c.message for c in rep.checks)


def test_doctor_blocks_the_mistakes(tmp_path: Path) -> None:
    env = Env(ARCUS_ADDRESS=ADDR, ARCUS_API_PRIVATE_KEY=K0)
    s = mm_session(account_index=0)
    # unfunded
    rep = doctor([s], FakeArcus(funded=False), env, tmp=tmp_path)
    assert levels(rep, "arcus funds") == {"FAIL"} and levels(rep, "sizing")  # sizing still reported
    # wrong subaccount for the key
    rep = doctor([mm_session(account_index=3)], FakeArcus(), env, tmp=tmp_path)
    assert levels(rep, "arcus subaccount") == {"FAIL"}
    # manual orders on the bot's subaccount, and an existing position in the traded market
    btc_id = str(MK[Venue.ARCUS]["BTC"].venue_market_id)
    rep = doctor([s], FakeArcus(orders=[{"clientId": "web-123"}, {"clientId": "algab1"}],
                                positions={btc_id: {"size": "0.001"}}), env, tmp=tmp_path)
    assert levels(rep, "arcus orders") >= {"FAIL", "INFO"} and levels(rep, "arcus positions") == {"FAIL"}
    rep = doctor([s], FakeArcus(positions={btc_id: {"size": "0.001"}}), env, tmp=tmp_path, adopt_positions=True)
    assert levels(rep, "arcus positions") == {"INFO"}
    # geo block, clock skew, expiring key, a live bot already running
    soon = [{"apiKey": ed25519_public_hex(K0), "accountIndex": 0, "apiWalletName": "mm", "status": "ACTIVE",
             "validUntil": int(time.time() * 1000) + 3_600_000}]
    import json
    import os
    import subprocess
    import sys

    hb = AppConfig(state_dir=str(tmp_path)).heartbeat_for("live")
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])  # a "running bot" process
    try:
        Path(hb).write_text(json.dumps({"ts_us": time.time_ns() // 1000, "pid": other.pid}))
        rep = doctor([s], FakeArcus(geo_blocked=True, skew_s=9, keys=soon), env, tmp=tmp_path)
    finally:
        other.kill()
        other.wait()
    Path(hb).write_text(json.dumps({"ts_us": time.time_ns() // 1000, "pid": 999_999_999 if os.name != "nt" else 1}))
    assert not levels(doctor([s], FakeArcus(), env, tmp=tmp_path), "already running")  # crashed bot: pid gone
    for area in ("region", "clock", "arcus key", "already running"):
        assert levels(rep, area) == {"FAIL"}, (area, rep.render())
    # ARCUS_API_KEY given but for a different private key
    rep = doctor([s], FakeArcus(), Env(ARCUS_ADDRESS=ADDR, ARCUS_API_PRIVATE_KEY=K0, ARCUS_API_KEY=ed25519_public_hex(K2)),
                 tmp=tmp_path / "x")
    assert any(c.level == "FAIL" and "does not belong" in c.message for c in rep.checks)
    # paper: missing credentials are only a warning
    rep = doctor([s], FakeArcus(), Env(), mode=RunMode.PAPER, tmp=tmp_path / "y")
    assert not rep.failed


def test_sizing_lines() -> None:
    m = MK[Venue.ARCUS]["BTC"]
    lvl, msg, _ = sizing_lines(mm_session(inventory_cap_usd=5), m, D("86000"))
    assert lvl == "FAIL" and "holds 0" in msg
    lvl, msg, fix = sizing_lines(mm_session(order_size_usd=6), m, D("86000"))
    assert lvl == "WARN" and "raised" in msg and "order_size_usd" in fix


def test_session_files_pass_sizing_at_todays_prices() -> None:
    for name in ("arcus_btc_mm", "arcus_spy_blend"):
        s = load_session(ROOT / "config/sessions" / f"{name}.yaml")
        m = MK[Venue.ARCUS][s.market.upper()]
        assert sizing_lines(s, m, D("86000") if s.market == "BTC" else D("770"))[0] == "PASS", name  # type: ignore[arg-type]


# ------------------------------------------------------------------------------------------------ selftest
def test_selftest_classification() -> None:
    assert classify(None)[0] == "PASS"
    assert classify(AuthError("arcus", "invalid signature", status=401))[0] == "FAIL"
    assert classify(OrderRejected("arcus", "UNDERCOLLATERALIZED", "no margin"))[0] == "PASS"
    assert classify(VenueError("arcus", "order not found", status=404))[0] == "PASS"
    assert classify(VenueError("arcus", "price is not a multiple of tick size", status=400))[0] == "FAIL"
    assert classify(VenueError("arcus", "something else", status=400))[0] == "INFO"


class FakeAdapter:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.account_index = 0
        self._orders_q: asyncio.Queue[Any] = asyncio.Queue()
        self.rest = self

    async def arm_dead_mans_switch(self, t: int | None) -> None:
        self.calls.append(f"dms:{'arm' if t else 'disarm'}")

    async def cancel_all(self, base: str | None) -> None:
        self.calls.append("cancel_all")

    async def cancel_order(self, *a: Any, **k: Any) -> None:
        self.calls.append("cancel")
        raise VenueError("arcus", "order not found", status=404)

    async def place(self, orders: list[Any]) -> list[Any]:
        self.calls.append(f"place:{len(orders)}")
        raise OrderRejected("arcus", "UNDERCOLLATERALIZED", "insufficient collateral")

    async def batch_cancel(self, *a: Any) -> None:
        self.calls.append("batch_cancel")

    def live_orders(self) -> dict[str, Any]:
        return {}

    async def leverage(self, base: str) -> int:
        return 20

    async def set_leverage(self, base: str, lev: int, isolated: bool = False) -> None:
        self.calls.append(f"lev:{lev}")

    async def open_orders(self) -> list[Any]:
        return []


def test_selftest_funded_guard_and_flow() -> None:
    m = MK[Venue.ARCUS]["BTC"]
    ad = FakeAdapter()
    res = asyncio.run(run_selftest(ad, FakeArcus(), market=m, funded=True, allow_funded=False, settle_s=0))
    assert not any(c.startswith("place") for c in ad.calls), ad.calls  # never places on a funded account
    assert any(s.level == "SKIP" for s in res.steps) and "lev:20" in ad.calls and res.ok
    ad = FakeAdapter()
    res = asyncio.run(run_selftest(ad, FakeArcus(), market=m, funded=False, allow_funded=False, settle_s=0))
    assert "place:1" in ad.calls and "place:2" in ad.calls and res.ok, res.render()
    assert probe_order_size(m, D("80000")) * D("80000") >= m.min_notional


# ------------------------------------------------------------------------------------------------ rejects
def test_reject_breaker_pauses_and_backs_off() -> None:
    from bot.core.risk import REJECT_LIMIT, RiskEngine

    r = RiskEngine()
    t = 1_790_000_000_000_000
    for i in range(REJECT_LIMIT * 3):  # routine rejects never trip it
        assert r.on_reject(Venue.ARCUS, "POST_ONLY_WOULD_CROSS", "BTC", t + i) is None
    ds = [r.on_reject(Venue.ARCUS, "UNDERCOLLATERALIZED", "BTC", t + i * 1_000_000) for i in range(REJECT_LIMIT)]
    d = ds[-1]
    assert d is not None and d.trigger == "reject_breaker" and all(x is None for x in ds[:-1])
    ok, why = r.quoting_allowed(Venue.ARCUS, "BTC", t + 10_000_000)
    assert not ok and "UNDERCOLLATERALIZED" in why
    assert r.quoting_allowed(Venue.ARCUS, "SPY", t + 10_000_000)[0]  # other markets unaffected
    assert r.quoting_allowed(Venue.ARCUS, "BTC", t + 70_000_000)[0]  # 60 s back-off over
    t2 = t + 100_000_000
    for i in range(REJECT_LIMIT):
        d = r.on_reject(Venue.ARCUS, "UNDERCOLLATERALIZED", "BTC", t2 + i)
    assert d is not None and "120 s" in d.resume  # doubled


def test_arcus_synchronous_reject_leaves_no_ghost_order() -> None:
    from bot.venues.arcus.adapter import ArcusAdapter
    from bot.venues.base import TIF, OrderRequest, OrderStatus, Side

    class Rest:
        async def place_order(self, *a: Any, **k: Any) -> Any:
            raise OrderRejected("arcus", "UNDERCOLLATERALIZED", "no margin")

        async def batch_place(self, idx: int, chunk: Any) -> Any:
            return {"responses": [{"clientId": chunk[0][1], "orderId": "1", "status": "ACK"},
                                  {"clientId": chunk[1][1], "orderId": "2", "status": "REJECTED",
                                   "rejectionReason": "OPEN_INTEREST_CAP_EXCEEDED"}]}

    ad = ArcusAdapter(Rest(), None, address=ADDR, account_index=0, markets=MK[Venue.ARCUS])  # type: ignore[arg-type]

    def req(c: str) -> OrderRequest:
        return OrderRequest(Venue.ARCUS, "BTC", Side.BUY, D("80000.0"), D("0.0002"), TIF.POST_ONLY, client_id=c)

    out = asyncio.run(ad.place([req("alg1")]))
    assert out[0].status is OrderStatus.REJECTED and out[0].reject_reason == "UNDERCOLLATERALIZED"
    assert "alg1" not in ad.live_orders() and ad._orders_q.qsize() == 1
    out = asyncio.run(ad.place([req("alg2"), req("alg3")]))
    assert [o.status for o in out] == [OrderStatus.PENDING_NEW, OrderStatus.REJECTED]
    assert set(ad.live_orders()) == {"alg2"}
