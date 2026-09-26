"""PnL ledger (P2 task 8, A6.1).

    NetPnL = SpreadCapture + InventoryMTM + Funding - Fees - LiquidationLoss

Decomposition used (exact identity by construction, tested in C5/D2):
- every fill's edge vs the mid at fill time, e = (mid - p) x size for buys and (p - mid) x size for sells,
  goes to SpreadCapture, or to -LiquidationLoss for liquidation fills;
- InventoryMTM = trading PnL - sum(edges), where trading PnL = -sum(signed notional) + position x mark;
- Fees: venue-reported fees on non-liquidation fills (+ = paid); a liquidation fill's fee goes to LiquidationLoss;
- Funding: venue-reported payments (+ = received; Arcus pays on oracle).
Also: volume, OI-hours (integral of |position| x mark dt), CPM = -NetPnL / Volume x 1e6, FIFO realised PnL
(reported, not part of the identity).
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from bot.venues.base import Fill, Venue

Z = Decimal(0)
Key = tuple[Venue, str]


@dataclass
class Book:
    position: Decimal = Z
    cash: Decimal = Z  # -sum(signed notional)
    edges_spread: Decimal = Z
    edges_liq: Decimal = Z
    fees: Decimal = Z
    liq_fees: Decimal = Z
    funding: Decimal = Z
    volume: Decimal = Z
    maker_volume: Decimal = Z
    fills: int = 0
    oi_hours_usd: Decimal = Z
    last_mark: Decimal | None = None
    last_mark_us: int = 0
    fifo: deque[tuple[Decimal, Decimal]] = field(default_factory=deque)  # (signed size, price)
    fifo_realized: Decimal = Z


@dataclass(frozen=True, slots=True)
class PnLBreakdown:
    spread_capture: Decimal
    inventory_mtm: Decimal
    funding: Decimal
    fees: Decimal
    liquidation_loss: Decimal
    volume: Decimal
    maker_volume: Decimal
    oi_hours_usd: Decimal
    fills: int
    fifo_realized: Decimal
    position: Decimal

    @property
    def net(self) -> Decimal:
        return self.spread_capture + self.inventory_mtm + self.funding - self.fees - self.liquidation_loss

    @property
    def cpm(self) -> Decimal | None:
        """Cost per $1M of volume (positive = cost)."""
        return (-self.net / self.volume * Decimal(1_000_000)) if self.volume > 0 else None

    def as_dict(self) -> dict[str, str]:
        d = {k: str(getattr(self, k)) for k in self.__slots__}
        d["net"] = str(self.net)
        d["cpm"] = str(self.cpm)
        return d


def _edge(f: Fill, mid: Decimal) -> Decimal:
    return (mid - f.price) * f.size if f.side.value == "buy" else (f.price - mid) * f.size


class Ledger:
    def __init__(self) -> None:
        self.books: dict[Key, Book] = defaultdict(Book)

    def on_fill(self, f: Fill, mid_at_fill: Decimal) -> None:
        b = self.books[(f.venue, f.base)]
        signed = f.size * f.side.sign
        b.cash -= signed * f.price
        e = _edge(f, mid_at_fill)
        if f.liquidation:
            b.edges_liq += e
            b.liq_fees += f.fee
        else:
            b.edges_spread += e
            b.fees += f.fee
        b.volume += f.notional
        if f.is_maker:
            b.maker_volume += f.notional
        b.fills += 1
        self._fifo(b, signed, f.price)
        b.position += signed

    @staticmethod
    def _fifo(b: Book, signed: Decimal, price: Decimal) -> None:
        rem = signed
        while rem != 0 and b.fifo and (b.fifo[0][0] > 0) != (rem > 0):
            q0, p0 = b.fifo[0]
            take = min(abs(q0), abs(rem))
            direction = 1 if q0 > 0 else -1
            b.fifo_realized += take * (price - p0) * direction
            q0 = q0 - take * direction
            rem = rem + take * direction
            if q0 == 0:
                b.fifo.popleft()
            else:
                b.fifo[0] = (q0, p0)
        if rem != 0:
            b.fifo.append((rem, price))

    def sync_position(self, venue: Venue, base: str, size: Decimal, mark: Decimal) -> Decimal:
        """Make the book hold `size` (the position the bot knows: restored at start, adopted, reconciled with the
        venue), as if the difference traded at `mark`: no PnL now, and PnL from here on moves with the real position.
        Without it a restart while holding a position left the book flat, and closing the position booked a phantom
        opposite one whose PnL moved with the price. Returns the difference (0 when they already agree)."""
        b = self.books[(venue, base)]
        diff = size - b.position
        if diff:
            b.cash -= diff * mark
            self._fifo(b, diff, mark)
            b.position = size
        return diff

    def on_funding(self, venue: Venue, base: str, payment: Decimal) -> None:
        self.books[(venue, base)].funding += payment

    def mark(self, venue: Venue, base: str, mark: Decimal, ts_us: int) -> None:
        """Advance OI-hours with the position held since the previous mark."""
        b = self.books[(venue, base)]
        if b.last_mark is not None and b.last_mark_us and ts_us > b.last_mark_us:
            hours = Decimal(ts_us - b.last_mark_us) / Decimal(3_600_000_000)
            b.oi_hours_usd += abs(b.position) * b.last_mark * hours
        b.last_mark = mark
        b.last_mark_us = ts_us

    def breakdown(self, venue: Venue, base: str, mark: Decimal | None = None) -> PnLBreakdown:
        b = self.books[(venue, base)]
        m = mark if mark is not None else (b.last_mark or Z)
        trading = b.cash + b.position * m
        edges = b.edges_spread + b.edges_liq
        return PnLBreakdown(
            spread_capture=b.edges_spread, inventory_mtm=trading - edges, funding=b.funding, fees=b.fees,
            liquidation_loss=-b.edges_liq + b.liq_fees, volume=b.volume,
            maker_volume=b.maker_volume, oi_hours_usd=b.oi_hours_usd, fills=b.fills, fifo_realized=b.fifo_realized,
            position=b.position)

    def total(self, marks: dict[Key, Decimal] | None = None) -> PnLBreakdown:
        parts = [self.breakdown(v, b, (marks or {}).get((v, b))) for (v, b) in list(self.books)]
        if not parts:
            return PnLBreakdown(Z, Z, Z, Z, Z, Z, Z, Z, 0, Z, Z)
        def s(k: str) -> Decimal:
            return sum((getattr(p, k) for p in parts), Z)

        return PnLBreakdown(s("spread_capture"), s("inventory_mtm"), s("funding"), s("fees"),
                            s("liquidation_loss"), s("volume"), s("maker_volume"), s("oi_hours_usd"),
                            sum(p.fills for p in parts), s("fifo_realized"), s("position"))


def daily_report_md(date: str, ledger: Ledger, marks: dict[Key, Decimal], extra: dict[str, object] | None = None) -> str:
    lines = [f"# Daily report {date}", "", "| Venue | Market | Net | Spread | InvMTM | Funding | Fees | Liq | "
             "Volume | Maker vol | OI-h $ | CPM | Pos |", "|" + "---|" * 13]
    for (v, b) in sorted(ledger.books, key=lambda k: (k[0].value, k[1])):
        p = ledger.breakdown(v, b, marks.get((v, b)))
        lines.append(f"| {v.value} | {b} | {p.net:.4f} | {p.spread_capture:.4f} | {p.inventory_mtm:.4f} | "
                     f"{p.funding:.4f} | {p.fees:.4f} | {p.liquidation_loss:.4f} | "
                     f"{p.volume:.2f} | {p.maker_volume:.2f} | {p.oi_hours_usd:.2f} | "
                     f"{'' if p.cpm is None else f'{p.cpm:.1f}'} | {p.position} |")
    t = ledger.total(marks)
    lines += ["", f"**Total net:** ${t.net:.4f} on ${t.volume:.2f} volume; CPM "
              f"{'n/a' if t.cpm is None else f'${t.cpm:.1f}'} per $1M; OI-hours ${t.oi_hours_usd:.2f}."]
    if extra:
        lines += ["", "## Notes"] + [f"- **{k}:** {v}" for k, v in extra.items()]
    return "\n".join(lines) + "\n"


def write_daily_report(root: Path, date: str, md: str) -> Path:
    d = root / "daily"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{date}.md"
    p.write_text(md)
    return p
