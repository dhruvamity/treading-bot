"""Signal (RSI mean reversion) with maker entries.

One position at a time. Long entry when RSI(n) on 1-minute bars < low and the trend filter is flat
(|EMA20 - EMA60| < z x sigma, sigma in price units from the 1 h vol); short mirrors it above `high`.
Entry: maker at the touch. Exit: take-profit maker at +tp bps, reduce-only stop at -sl bps (IOC intent), or max
holding time (maker then IOC). Cooldown after each trade. Few requests: suits weekend crypto.
"""

from __future__ import annotations

from decimal import Decimal

from bot.common.config import MMSession
from bot.common.indicators import ema, rsi
from bot.strategies import quoting as qt
from bot.strategies.base import StrategyContext, StrategyOutput
from bot.strategies.mm_base import MMBase
from bot.venues.base import TIF, Fill, OrderRequest, Side


class SignalStrategy(MMBase):
    name = "signal"

    def __init__(self, params: MMSession) -> None:
        super().__init__(params)
        self.entry_px: float | None = None
        self.entry_us: int = 0
        self.cooldown_until_us: int = 0
        self.trades = 0

    def on_fill(self, ctx: StrategyContext, fill: Fill) -> None:
        super().on_fill(ctx, fill)
        if fill.tag.startswith("sig_entry"):
            self.entry_px = float(fill.price)
            self.entry_us = fill.ts_us
        elif fill.tag.startswith(("sig_tp", "sig_sl", "sig_time", "exit")):
            self.trades += 1

    def on_tick(self, ctx: StrategyContext) -> StrategyOutput:
        s = self.p.signal
        mid, bbo = self.mid(ctx), self.bbo(ctx)
        out = StrategyOutput()
        if mid is None or bbo is None:
            return self.exit_book(ctx, "no book")
        bb, ba = bbo
        m = ctx.market
        closes = [b.c for b in ctx.view.closed_bars()]
        r = rsi(closes, s.rsi_len)
        e20, e60 = ema(closes[-120:], 20), ema(closes[-180:], 60)
        sigma_px = mid * ctx.view.sigma_1h()
        flat = e20 is not None and e60 is not None and sigma_px > 0 and abs(e20 - e60) < s.trend_z * sigma_px
        inv = ctx.inventory
        orders = []
        if inv != 0:
            if self.entry_px is None:
                self.entry_px = mid
                self.entry_us = ctx.now_us
            long = inv > 0
            pnl_bps = ((mid - self.entry_px) / self.entry_px / qt.BP) * (1 if long else -1)
            held_min = (ctx.now_us - self.entry_us) / 60e6
            if pnl_bps <= -s.sl_bps:
                out.ioc_intents.append(self._ioc_exit(ctx, mid, "sig_sl", f"stop {pnl_bps:.1f} bps"))
                self.cooldown_until_us = ctx.now_us + int(s.cooldown_s * 1e6)
                self.entry_px = None
                out.reason = f"signal stop-loss at {pnl_bps:.1f} bps"
            elif held_min >= s.max_hold_min:
                d = qt.to_desired(m, Side.SELL if long else Side.BUY, ba if long else bb, abs(inv), "sig_time",
                                  reduce_only=True)
                if d:
                    orders.append(d)
                out.reason = f"signal max hold {held_min:.0f} min: maker exit"
            else:
                tp = self.entry_px * (1 + s.tp_bps * qt.BP) if long else self.entry_px * (1 - s.tp_bps * qt.BP)
                tp = max(tp, bb + float(m.tick_size)) if long else min(tp, ba - float(m.tick_size))
                d = qt.to_desired(m, Side.SELL if long else Side.BUY, tp, abs(inv), "sig_tp", reduce_only=True)
                if d:
                    orders.append(d)
                out.reason = f"signal holding {'long' if long else 'short'} pnl {pnl_bps:+.1f} bps, TP at {tp:.4f}"
        else:
            self.entry_px = None
            why = self.blocked(ctx)
            if why:
                out.reason = f"signal flat; {why}"
            elif ctx.now_us < self.cooldown_until_us:
                out.reason = "signal cooldown"
            elif r is None or not flat:
                out.reason = f"signal waiting (rsi={r}, flat={flat})"
            else:
                q = qt.base_for_usd(self.q_usd(ctx, mid, 1), mid, m)
                if r < s.rsi_low:
                    d = qt.to_desired(m, Side.BUY, bb, q, "sig_entry_long")
                    if d:
                        orders.append(d)
                    out.reason = f"signal long entry: RSI {r:.1f} < {s.rsi_low}, trend flat"
                elif r > s.rsi_high:
                    d = qt.to_desired(m, Side.SELL, ba, q, "sig_entry_short")
                    if d:
                        orders.append(d)
                    out.reason = f"signal short entry: RSI {r:.1f} > {s.rsi_high}, trend flat"
                else:
                    out.reason = f"signal flat (RSI {r:.1f})"
        out.set(ctx.venue, m.base, orders)
        out.metrics = {"rsi": r if r is not None else -1.0, "trades": float(self.trades)}
        out.half_spread_ticks = 2.0
        return out

    def _ioc_exit(self, ctx: StrategyContext, mid: float, tag: str, why: str) -> OrderRequest:
        m = ctx.market
        side = Side.SELL if ctx.inventory > 0 else Side.BUY
        px = mid * (1 - 20 * qt.BP) if side is Side.SELL else mid * (1 + 20 * qt.BP)
        return OrderRequest(m.venue, m.base, side, m.round_price(Decimal(str(px)), is_bid=side is Side.BUY),
                            abs(ctx.inventory), TIF.IOC, reduce_only=True, tag=tag, reason=f"signal {why}")
