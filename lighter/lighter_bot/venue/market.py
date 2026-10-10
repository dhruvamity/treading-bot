"""One Lighter perp's trading rules, from GET /api/v1/orderBookDetails.

Prices and sizes travel as integers: price x 10^price_decimals and size x 10^size_decimals. The minimum order is the
larger of min_base_amount x price and min_quote_amount (Lighter applies it to maker orders only). Leverage is set per
market as an initial margin fraction in 1/10,000: 200 = 2% = 50x (the market's minimum fraction is its maximum
leverage); the account's default is the market's default fraction (5000 = 2x) until the bot sets it.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

MF_ONE = 10_000     # margin fractions are in 1/10,000


@dataclass(frozen=True)
class Market:
    market_id: int
    symbol: str
    price_decimals: int
    size_decimals: int
    min_base: float
    min_quote: float
    imf_min: int              # the lowest initial margin fraction allowed: 1 / max leverage
    imf_default: int
    mmf: int                  # maintenance margin fraction
    active: bool = True
    maker_fee: float = 0.0    # fractions (0.0001 = 1 bp)
    taker_fee: float = 0.0
    last_price: float = 0.0
    day_volume_usd: float = 0.0
    day_trades: int = 0
    open_interest: float = 0.0
    reduce_only: bool = False  # the market only takes reduce-only orders (being wound down)
    hidden: bool = False

    @property
    def tick(self) -> float:
        return 10.0 ** -self.price_decimals

    @property
    def step(self) -> float:
        return 10.0 ** -self.size_decimals

    @property
    def max_leverage(self) -> float:
        return MF_ONE / self.imf_min if self.imf_min > 0 else 1.0

    @property
    def mmf_frac(self) -> float:
        return self.mmf / MF_ONE

    def min_order_usd(self, price: float) -> float:
        return max(self.min_quote, self.min_base * price)

    # ---------------------------------------------------------------- integer conversions
    def price_int(self, price: float, side_buy: bool | None = None) -> int:
        """Price in ticks. Bids round down and asks up (never a better price than asked for); None rounds to nearest.
        Within 1e-6 of a tick counts as on it, so float noise never moves a quote a tick away."""
        x = price * 10**self.price_decimals
        if side_buy is None:
            return round(x)
        return math.floor(x + 1e-6) if side_buy else math.ceil(x - 1e-6)

    def size_int(self, size: float) -> int:
        """Size in steps, rounded down."""
        return math.floor(size * 10**self.size_decimals + 1e-6)

    def price_of(self, ticks: int) -> float:
        return ticks / 10**self.price_decimals

    def size_of(self, steps: int) -> float:
        return steps / 10**self.size_decimals

    @property
    def default_leverage(self) -> float:
        """What an account has on the market until it sets its own."""
        return MF_ONE / self.imf_default if self.imf_default > 0 else 1.0

    def leverage_fraction(self, leverage: float) -> int:
        """The initial margin fraction for a leverage, never below the market's minimum (its maximum leverage)."""
        return max(self.imf_min, math.ceil(MF_ONE / max(1.0, leverage)))

    def account_leverage(self, account: dict[str, Any]) -> float:
        """The leverage an account (one of /api/v1/account's rows) has on this market: its own setting, else the
        market's default. Lighter gives the account's initial margin fraction in percent."""
        for p in account.get("positions") or []:
            if int(p.get("market_id", -1)) == self.market_id and float(p.get("initial_margin_fraction") or 0) > 0:
                return 100.0 / float(p["initial_margin_fraction"])
        return self.default_leverage

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_api(cls, d: dict[str, Any]) -> Market:
        """One entry of orderBookDetails' order_book_details (perps)."""
        cfg = d.get("market_config") or {}
        return cls(
            market_id=int(d["market_id"]),
            symbol=str(d["symbol"]),
            price_decimals=int(d.get("supported_price_decimals", d.get("price_decimals", 2))),
            size_decimals=int(d.get("supported_size_decimals", d.get("size_decimals", 4))),
            min_base=float(d.get("min_base_amount") or 0),
            min_quote=float(d.get("min_quote_amount") or 0),
            imf_min=int(d.get("min_initial_margin_fraction") or MF_ONE),
            imf_default=int(d.get("default_initial_margin_fraction") or MF_ONE),
            mmf=int(d.get("maintenance_margin_fraction") or 0),
            active=str(d.get("status", "active")) == "active",
            # the API gives fees in percent ("0.0000")
            maker_fee=float(d.get("maker_fee") or 0) / 100,
            taker_fee=float(d.get("taker_fee") or 0) / 100,
            last_price=float(d.get("last_trade_price") or 0),
            day_volume_usd=float(d.get("daily_quote_token_volume") or 0),
            day_trades=int(d.get("daily_trades_count") or 0),
            open_interest=float(d.get("open_interest") or 0),
            reduce_only=bool(cfg.get("force_reduce_only", False)),
            hidden=bool(cfg.get("hidden", False)),
        )

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Market:
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in known})


def parse_markets(payload: dict[str, Any]) -> dict[str, Market]:
    """orderBookDetails -> {symbol: Market} for the perps."""
    out: dict[str, Market] = {}
    for d in payload.get("order_book_details") or []:
        if d.get("market_type", "perp") != "perp":
            continue
        m = Market.from_api(d)
        out[m.symbol] = m
    return out
