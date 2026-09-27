"""/account and `bot account`: the account's figures as Arcus keeps them (bot/core/account_stats.py)."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from bot.core import account_stats
from bot.venues.arcus.rest import ArcusRest
from bot.venues.arcus.signing import ArcusSigner
from tests.unit.test_telegram import ROOT, _app, _bot, msg

Q = 1_000_000_000
TIERS = {"tiers": [
    {"level": 0, "name": "Base", "volume_threshold": 0, "maker_fee_ppm": 0, "taker_fee_ppm": 225},
    {"level": 1, "name": "Bronze", "volume_threshold": 5_000_000 * Q, "maker_fee_ppm": 0, "taker_fee_ppm": 190},
    {"level": 5, "name": "VIP", "volume_threshold": 1_000_000_000 * Q, "maker_fee_ppm": -20, "taker_fee_ppm": 100}]}


def raw(**kw: Any) -> dict[str, Any]:
    r: dict[str, Any] = {
        "read_at": 1_790_489_000.0,
        "stats": {"lifetimeVolume": 250_000 * Q, "rollingVolume": 240_000 * Q, "lifetimeFeesPaid": int(3.75 * Q),
                  "rollingFeesPaid": int(3.5 * Q),
                  "windowedStats": {"24h": {"volume": 200_000 * Q, "feesPaid": int(3.25 * Q)},
                                    "7d": {"volume": 250_000 * Q, "feesPaid": int(3.75 * Q)}},
                  "tradingFeeTier": {"level": 0, "makerFeePpm": 0, "takerFeePpm": 225}},
        "tiers": TIERS,
        "board": {"volume": 249_000 * Q, "feesPaid": int(3.7 * Q), "pnl": -int(12.5 * Q), "rank": 2000},
        "affiliate": {"totalCommission": int(1.25 * Q), "pendingCommission": int(0.5 * Q)}}
    r.update(kw)
    return r


def text_of(r: dict[str, Any]) -> str:
    head, blocks = account_stats.lines(r)
    return "\n".join([head, *(f"{label}: " + " | ".join(ls) for label, ls in blocks)])


def test_the_account_lines_read_like_the_owner_asked() -> None:
    t = text_of(raw())
    assert "read 06:03 UTC · 11:33 IST" in t
    assert "Futures (perps) volume: All-time $250.0k · 30 days $240.0k | 24h $200.0k · 7 days $250.0k" in t
    assert "Fees paid: All-time $3.75 (0.15 bp) · 30 days $3.50" in t
    assert "Maker rebates $0.00 · none at Base (from VIP: $1B in 30 days)" in t
    assert "Referral commission $1.25 all-time · $0.50 not claimed yet" in t
    assert "Spot volume: Not in Arcus's API" in t and "(its spot volume is $0)" in t
    assert "Fee tier: Base · maker 0 bp · taker 2.25 bp | Bronze at $5.00M in 30 days: $4.76M to go · taker 1.9 bp" in t
    assert "Realized PnL -$12.50 (after fees, before funding) | Rank #2,000 by volume" in t


def test_the_account_lines_cope_with_what_arcus_leaves_out() -> None:
    r = raw(affiliate=None, board={"pnl": -(2 ** 63), "rank": 3}, tiers=None)
    r["stats"]["lifetimeSpotVolume"] = 12_000 * Q
    r["stats"]["tradingFeeTier"] = {"level": 5, "makerFeePpm": -20, "takerFeePpm": 100}
    t = text_of(r)
    assert "Referral commission: not readable now" in t and "All-time result" not in t
    assert "Maker rebates: your tier pays 0.2 bp on maker volume" in t
    assert "Spot volume: All-time $12.0k" in t and "Level 5 · maker -0.2 bp · taker 1 bp" in t


async def test_the_affiliate_read_is_signed_with_the_key_when_there_is_one() -> None:
    seen: list[dict[str, Any]] = []

    class Http:
        async def request(self, method: str, path: str, **kw: Any) -> tuple[int, Any, dict[str, str]]:
            seen.append({"method": method, "path": path, **kw})
            return 200, {"totalCommission": 0}, {}

    signer = ArcusSigner("11" * 32)
    rest = ArcusRest("https://example.invalid", signer=signer)
    rest.http = Http()  # type: ignore[assignment]
    assert await rest.affiliate_info("0x" + "ab" * 20) == {"totalCommission": 0}
    h = seen[0]["headers"]
    assert seen[0]["path"] == "/v1/affiliate/info" and h["X-API-Key"] == signer.api_key
    assert h["X-Signature"] == signer.sign(h["X-Timestamp"].encode() + b"info")
    rest2 = ArcusRest("https://example.invalid")
    rest2.http = Http()  # type: ignore[assignment]
    await rest2.affiliate_info("0x" + "ab" * 20)
    assert seen[1]["headers"] is None                          # no key: unsigned (Arcus still answers those)


async def test_telegram_account_shows_the_card_or_why_not(tmp_path: Path, monkeypatch: Any) -> None:
    app = _app(tmp_path)
    (tmp_path / "config" / "venues").mkdir(parents=True)
    shutil.copy(ROOT / "config" / "venues" / "arcus.yaml", tmp_path / "config" / "venues" / "arcus.yaml")
    bot, api, _ = _bot(tmp_path, app)

    async def read(url: str) -> dict[str, Any]:
        assert url.startswith("https://")
        return raw()

    monkeypatch.setattr(account_stats, "read", read)
    await bot.handle(msg("/account"))
    text, kb = api.sent[-1][1], api.sent[-1][2]
    assert text.startswith("📒 <b>ACCOUNT</b>") and "<b>Fees earned</b>" in text and "Rank #2,000" in text
    assert kb == [[("🔄 Refresh", "account"), ("☰ Menu", "menu")]]

    async def down(url: str) -> dict[str, Any]:
        raise TimeoutError("arcus")

    monkeypatch.setattr(account_stats, "read", down)
    await bot.handle(msg("/account"))
    assert "Arcus did not answer" in api.sent[-1][1]
