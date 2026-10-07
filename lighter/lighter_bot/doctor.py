"""`lighter doctor [MARKET] [--lev L]`: everything a live run needs, read-only (no transaction is sent).

Lines are PASS, WARN or FAIL; a live start refuses on any FAIL.
"""

from __future__ import annotations

import time
from typing import Any

from lighter_bot.config import Config
from lighter_bot.trade.sizing import Stops, min_capital, sizes, target_capital
from lighter_bot.venue import consts as C
from lighter_bot.venue.rest import ApiError, Rest


async def check(cfg: Config, market: str | None = None, leverage: float | None = None,
                stops: tuple[float, float, float] = (2.0, 5.0, 25.0)) -> tuple[bool, list[tuple[str, str, str]]]:
    lines: list[tuple[str, str, str]] = []

    def add(level: str, name: str, text: str) -> None:
        lines.append((level, name, text))

    cr = cfg.creds
    add("PASS" if cfg.live_allowed else "FAIL", "live switch",
        "LBOT_LIVE=1" if cfg.live_allowed else "LBOT_LIVE is not 1 in lighter/.env (paper only)")
    add("PASS" if cr.address else "FAIL", "wallet", cr.address[:6] + "…" + cr.address[-4:] if cr.address
        else "LIGHTER_ADDRESS is not set")
    add("PASS" if cr.private_key else "FAIL", "API key", f"slot {cr.api_key_index}" if cr.private_key
        else "LIGHTER_API_PRIVATE_KEY is not set")
    if cr.api_key_index in C.RESERVED_KEY_SLOTS:
        add("FAIL", "API key slot", f"slot {cr.api_key_index} belongs to the Lighter apps; use 4-254")
    try:
        from lighter_bot.venue.signer import _load
        _load()
        add("PASS", "signer", "Lighter's signing library loads")
    except Exception as e:
        add("FAIL", "signer", str(e))
    rest = Rest(cfg.endpoints.rest)
    account: int | None = None
    acc: dict[str, Any] = {}
    try:
        st = await rest.status()
        skew = abs(time.time() - float(st.get("timestamp") or time.time()))
        add("PASS" if skew < 2 else "WARN", "clock", f"{skew:.1f} s from Lighter's clock")
        if cr.address or cr.account_index is not None:
            from lighter_bot.trade.live import resolve_account
            try:
                account = await resolve_account(rest, cfg)
                add("PASS", "account", f"index {account}")
            except RuntimeError as e:
                add("FAIL", "account", str(e))
        if account is not None:
            r = await rest.account(account)
            acc = (r.get("accounts") or [{}])[0]
            eq = float(acc.get("total_asset_value") or acc.get("collateral") or 0)
            add("PASS" if eq > 0 else "FAIL", "funds", f"equity ${eq:,.2f}" if eq > 0
                else "no collateral: deposit USDG to this account")
            if cr.private_key:
                try:
                    from lighter_bot.venue.signer import Signer
                    s = Signer(cfg.endpoints.rest, cr.private_key, cfg.endpoints.chain_id, cr.api_key_index, account)
                    err = s.check()
                    add("PASS" if err is None else "FAIL", "key registered",
                        "Lighter holds this key's public key" if err is None
                        else f"{err} (register it: scripts/register_key.py, README section 3)")
                    if err is None:
                        rest.auth = s.auth_token(int(time.time()) + 600)
                        lim = await rest.account_limits(account)
                        tier = str(lim.get("user_tier") or lim.get("user_tier_name") or "?").lower()
                        add("PASS" if tier == "standard" else "WARN", "account tier",
                            "standard: 0% maker, 0% taker, 60 requests a minute" if tier == "standard"
                            else f"{tier}: fees apply (premium 1.2/3.5 bp, plus 0.5 bp); every backtest here assumes "
                                 "the standard account's 0%")
                except Exception as e:
                    add("FAIL", "key registered", str(e))
        if market:
            from lighter_bot.venue.market import parse_markets
            ms = parse_markets(await rest.markets())
            m = ms.get(market)
            if m is None:
                add("FAIL", "market", f"{market} is not a Lighter perp")
            else:
                add("PASS" if m.active and not m.reduce_only else "FAIL", "market",
                    f"{market}: {'open' if m.active and not m.reduce_only else 'not open for new positions'}, "
                    f"max {m.max_leverage:g}x, minimum order ${m.min_order_usd(m.last_price):,.2f}, "
                    f"fees {m.maker_fee * 1e4:g}/{m.taker_fee * 1e4:g} bp")
                lev = min(leverage or m.max_leverage, m.max_leverage)
                eq = float(acc.get("total_asset_value") or 0) if acc else cfg.sizing.paper_capital_usd
                cap = target_capital(eq, frac=cfg.sizing.capital_frac, max_capital=cfg.sizing.max_capital_usd)
                minc = min_capital(m.min_order_usd(m.last_price), lev)
                sz = sizes(cap, lev, Stops(*stops))
                add("PASS" if cap >= minc else "FAIL", "sizing",
                    sz.label() + ("" if cap >= minc else f"; needs at least ${minc:,.2f} at {lev:g}x"))
    except ApiError as e:
        add("FAIL", "api", str(e))
    except (TimeoutError, OSError) as e:
        add("FAIL", "api", f"Lighter unreachable: {e}")
    finally:
        await rest.close()
    ok = not any(level == "FAIL" for level, _, _ in lines)
    return ok, lines


def render(ok: bool, lines: list[tuple[str, str, str]]) -> str:
    out = [f"{lvl:<4}  {name:<15} {text}" for lvl, name, text in lines]
    out.append("READY" if ok else "NOT READY: fix the FAIL lines")
    return "\n".join(out)
