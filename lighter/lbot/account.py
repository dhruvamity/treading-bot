"""`lbot account` and Telegram /account: the account as Lighter keeps it: equity, positions, volume and PnL by day
(the pnl endpoint), and the Robinhood Chain live points (auth-gated reads need the API key)."""

from __future__ import annotations

import time

from lbot.config import Config
from lbot.venue.rest import ApiError, Rest


async def report(cfg: Config) -> str:
    rest = Rest(cfg.endpoints.rest)
    lines: list[str] = []
    try:
        from lbot.trade.live import resolve_account
        try:
            idx = await resolve_account(rest, cfg)
        except RuntimeError as e:
            return str(e)
        acc = ((await rest.account(idx)).get("accounts") or [{}])[0]
        lines.append(f"Lighter account {idx}: equity ${float(acc.get('total_asset_value') or 0):,.2f}, free "
                     f"${float(acc.get('available_balance') or 0):,.2f}, type "
                     f"{'standard (0% fees)' if int(acc.get('account_type') or 0) == 0 else acc.get('account_type')}")
        for p in acc.get("positions") or []:
            if float(p.get("position") or 0):
                sign = 1 if int(p.get("sign") or 1) >= 0 else -1
                lines.append(f"  {p.get('symbol')}: {sign * float(p['position']):+g} at {p.get('avg_entry_price')}, "
                             f"unrealized ${float(p.get('unrealized_pnl') or 0):+.2f}")
        if cfg.creds.private_key:
            from lbot.venue.signer import Signer
            s = Signer(cfg.endpoints.rest, cfg.creds.private_key, cfg.endpoints.chain_id, cfg.creds.api_key_index, idx)
            rest.auth = s.auth_token(int(time.time()) + 600)
            try:
                now = int(time.time())
                pnl = await rest.pnl(idx, now - 30 * 86400, now, "1d", 30)
                rows = pnl.get("pnl") or []
                vol = sum(float(r.get("volume") or 0) for r in rows)
                trade = sum(float(r.get("trade_pnl") or 0) for r in rows)
                lines.append(f"Last 30 days: ${vol:,.0f} traded, trading PnL ${trade:+,.2f}")
                pts = await rest.live_points(idx)
                lines.append(f"Live points: {float(pts.get('total_live_points') or 0):,.2f}")
            except ApiError as e:
                lines.append(f"(volume and points not available: {e.message})")
        else:
            lines.append("(set LIGHTER_API_PRIVATE_KEY to see volume, PnL and points)")
    except ApiError as e:
        lines.append(f"Lighter refused: {e.message}")
    finally:
        await rest.close()
    return "\n".join(lines)
