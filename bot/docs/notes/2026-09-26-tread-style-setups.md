# 2026-09-26: Tread.fi-style setups (Mid / Grid, spread, bias), and where volume is cheapest

The owner asked for:
- the bot's strategies cleaned up into what Tread.fi offers: a reference price, a spread, a directional bias;
- the same form in Telegram, backed by the engine;
- every setup backtested on the server's recorded data;
- the most volume at the most efficient cost, with no conservative options;
- an answer on weekends: run the stock perps (lower leverage off-hours) or BTC and the other crypto?

Data: the server's copy of `data/scout/tape` (59 Arcus markets):
- best bid/offer and trades from 2026-09-19 16:00 to 09-26 12:45 UTC;
- 12 weeks of public trades before that;
- the live fills of the three BTC runs;
- the owner's own Tread.fi history (Paradex, January 2026), from screenshots;
- the 46 posts collected in `research/treadfi_bot_configs.md`.

Unless noted, backtests use the scout's simulator with queue fills, on about $110 of capital at each market's maximum
leverage (sized as the live bot does), with a 3% position stop and no daily stop: the cost over whole days, not the
first minutes. Weekdays are 09-21 to 09-25; weekend days are 09-19 (8 h), 09-20 and 09-26 (12.7 h).

## 1. How Tread.fi's market-making bot is built

From the owner's screenshots of the order form and the posts:

| Field | Values | What it does |
|---|---|---|
| Margin, leverage | e.g. $100 at 50x | Position size |
| Volume | e.g. $100,000 | The run ends once it has traded this much |
| Participation rate / duration | Aggressive 1 h, Normal 3 h, Passive 24 h (or 1–240 h) | How fast it tries to reach the volume |
| Reference price | Mid, Grid, DGrid, RGrid, Blend, Signal | What the quotes are placed around |
| Spread | slider around 0 bps (negative = inside) | Distance from the reference |
| Directional bias | Short, Neutral, Long | Long "has positive PnL if the price goes up while the bot is running" |
| Stop loss, take profit | % of margin (10% SL = $10 max loss on $100) | End the run |

Each reference price:
- **Mid** follows the book mid (the "volume machine", no memory).
- **Grid** quotes around the last fill, so a sell never goes below the last buy. A soft reset closes a stalled leg.
- **RGrid** buys high and sells low relative to its reference (trend-following, mostly taker).
- **DGrid** picks the spread and Grid/RGrid from a volatility regime.
- **Blend** mixes the book mid with an outside price.
- **Signal** skews the spread by RSI.

Run history labels runs as "Mid +1", "Grid 0", "DGrid +2" and reports cost per $1M.

**The owner's own Tread history** (Paradex, January 2026; 27 runs, about $9.7M, about $74 per $1M overall; the
exchange/builder fee was about 2.8 bp, so every run starts at about $28 per $1M):

| Mode | Markets | Volume | Cost per $1M | Stop losses |
|---|---|---|---|---|
| Grid 0 | PAXG, BTC (18 runs) | $4.8M | about $23 (the spread capture paid a little of the fee) | 1 of 18 |
| Mid +1, Long | BTC (2) | $1.6M | about $29 | 0 |
| Grid +1 | PAXG (2) | $1.6M | about $135 | 2 of 2 |
| Mid +2, Long | SOL (1) | $0.1M | about $134 | 0 |
| Mid −1, Short | PAXG (2) | $1.4M | about $186 | 1 of 2 |
| Grid 0 | SOL, AAVE (2) | $0.14M | $500–750 | 2 of 2 |

What carried: Grid 0 on calm, anchored markets was cheap there. The expensive runs were the stop losses, the
directional bets, and the alts.

## 2. What Arcus is different in

- **The maker fee is 0 and the taker fee is 2.25 bp.**
  - Every cost below is trading loss, not fees.
  - Any mode that crosses the spread on purpose (RGrid, "Mid −1" on a venue that lets it take) is expensive here.
- **BTC on Arcus follows faster venues.** A fill at the touch loses 0.6–1.1 bp within a minute (markouts, previous
  note). Grid 0 was nearly free on Paradex; on Arcus BTC it costs about 2 bp, more than Mid 0.

## 3. Live BTC runs against the backtest

| Run (2026-09-26, BTC, at the touch) | Live | Backtest, same minutes |
|---|---|---|
| 1: ~$30 at 20x, 00:14–00:28 | $4.5k, 1.43 bp | 1.9 bp |
| 2: about $110 at 40x, 07:03–07:37 | $57.8k, 0.76 bp | 0.92 bp |
| 3: about $110 at 40x, 12:12–12:42 | $51.3k, 1.31 bp | 1.64 bp |

The three runs together: about $113k for about $12, **about $105 per $1M (1.05 bp)**. The backtest costs 1.2–1.3x
the live runs and fills 0.83–0.96x their volume. The lists' default budget moved from $0.15 to $0.20 per $1,000
(backtest), which is about $0.16 live.

## 4. Every mode, spread and bias, per market

Volume per hour and cost per dollar traded, with the defaults in section 6:
- weekday numbers before the slash, weekend numbers after it;
- volume in thousands of dollars an hour, then the cost in bp.

| Setup | BTC 40x | ETH 25x | SPY 50x (33x off) | QQQ 25x (16.7x off) | NVDA 20x (13.3x off) |
|---|---|---|---|---|---|
| Mid 0 | 323 1.69 / 177 1.41 | 79 2.04 / 60 2.03 | 21 1.18 / 11 0.77 | 12.5 1.04 / 3.8 1.21 | 3.6 1.91 / 3.7 0.48 |
| Mid +1 | 134 1.68 / 57 1.60 | 38 2.29 / 26 2.29 | 7.6 0.80 / 5.2 0.47 | 4.5 0.69 / 2.1 1.30 | 2.3 1.98 / 2.4 −0.10 |
| Mid +2 | 66 1.59 / 25 1.33 | 20 2.77 / 13 2.50 | 4.4 0.51 / 3.5 0.01 | 2.1 0.07 / 1.3 1.22 | 1.5 1.88 / 1.0 −0.30 |
| Mid +3 | 30 1.74 / 10 1.27 | 11 3.61 / 7 3.36 | 2.4 0.70 / 2.6 −0.09 | 1.1 −2.44 / 0.9 0.23 | 1.1 1.55 / 0.6 −0.43 |
| Grid 0 | 139 1.97 / 73 1.67 | 32 2.49 / 26 2.15 | 12 1.70 / 6.3 0.37 | 5.0 1.54 / 1.8 1.93 | 2.0 1.54 / 2.0 0.97 |
| Grid +1 | 99 2.11 / 48 1.96 | 25 2.42 / 19 2.71 | 6.8 1.63 / 3.5 0.93 | 3.3 1.32 / 0.8 2.97 | 1.8 3.14 / 0.8 0.97 |
| Grid +3 | 37 2.11 / 16 1.49 | 8 3.10 / 6 3.39 | 3.1 2.01 / 2.1 0.88 | 1.3 0.89 / 0.6 0.51 | 1.3 0.79 / 0.8 −1.78 |

Other markets, Mid 0, weekdays:

| Market | Volume per hour | Cost |
|---|---|---|
| SOL | $35k | 2.8 bp |
| HOOD | $18k | 2.5 bp |
| HYPE | $13k | 2.5 bp |
| ZEC | $35k | 4.5 bp |
| GLD | $5k | 2.2 bp |

- **On BTC, Mid 0 is the setup.** Every spread from Mid 0 to Mid +3 costs about the same per dollar (1.6–1.7 bp),
  so the one that fills most, Mid 0, gives the most volume for the same cost. Mid −1 is Mid 0 on a one-tick book.
  Grid costs more (2.0–2.1 bp) at less than half the volume.
- **Wider spreads pay only where the price is anchored** to an outside market (SPY, QQQ, NVDA, GLD): Mid +2 and
  Mid +3 are near breakeven there, at a tenth of Mid 0's volume.
- **Bias** (Long or Short, holding half the position cap) changed the cost by under 0.1 bp on BTC and ETH and cut the
  volume by about 8% (25% when it holds the whole cap). The week's drift decided which side looked better on the stocks. It is a view on the price, not
  an edge. Neutral is the default.
- **Hour of day** (BTC Mid 0, weekdays): 1.1–2.3 bp in every UTC hour.
  - The quiet hours cost less, at 170–200k an hour: 21:00–00:00 UTC (02:30–05:30 IST) at 1.1–1.5 bp.
  - The US open costs the most, at 450–530k an hour: 13:00–15:00 UTC (18:30–20:30 IST) at 1.9–2.1 bp.
  - That is not enough to be worth a time filter.

## 5. Weekends: stocks or crypto?

Taker flow over the last 8 weeks (median per day), weekend as a share of weekdays:

| Market | Weekday flow | Weekend flow | Weekend / weekday | Daily range, weekday → weekend |
|---|---|---|---|---|
| BTC | $42.9M | $25.3M | 0.59 | 265 → 132 bp |
| ETH | $9.0M | $4.5M | 0.50 | 333 → 258 bp |
| SPY | $1.36M | $0.76M | 0.56 | 83 → 43 bp |
| QQQ | $0.72M | $0.19M | 0.26 | 128 → 49 bp |
| NVDA | $0.32M | $0.16M | 0.51 | 247 → 60 bp |
| GLD | $0.62M | $0.15M | 0.25 | 198 → 38 bp |

On weekends the stock and index perps trade at their off-hours leverage (SPY 33x, QQQ and GLD 16.7x, NVDA 13.3x,
other stocks 6.7x) inside Arcus's trading bounds, so they barely move. Crypto keeps about half its flow and moves
half as much.

$10 runs (your `sl=10`), started every 2 hours on every recorded day, at the maximum leverage, until the $10 was gone
or 12 hours passed (backtest; BTC live has been about 1.6x better):

| Setup | Day type | Median time to lose $10 | Volume per $1 lost |
|---|---|---|---|
| BTC Mid 0, 40x | weekday | 11 min | $5.7k |
| BTC Mid 0, 40x | weekend | 20 min | $6.1k |
| ETH Mid 0, 25x | weekday | 37 min | $4.7k |
| SPY Mid 0, 50x / 33x | weekday | 4.8 h | $8.1k |
| SPY Mid 0, 33x | weekend | 9.8 h (4 of 13 never lost $10 in 12 h) | $10.5k |
| SPY Mid +1, 33x | weekend | none lost $10 in 12 h | $23.9k |
| QQQ Mid +1, 16.7–25x | weekday | 7.9 h | $11.6k |

**The answer:**
- **For volume fast** (a run of minutes to an hour), BTC Mid 0 at 40x is the best at any time. On weekends it trades
  at about 55% of its weekday speed (about $180k an hour in the backtest) and costs about 15% less per dollar.
- **For the most volume per dollar across a whole weekend day**, SPY at 33x is about twice as efficient as BTC:
  - Mid 0: about $270k a day for about $21 in the backtest;
  - Mid +1: about $125k a day for about $6.
  - It is 15–35x slower, so it suits a bot left running all day on a fixed budget.
- QQQ, NVDA and GLD are cheaper still on weekends, but trade too little at about $100 of capital ($1–4k an hour).
- Weekend evidence is 1.5 recorded weekend days of books plus 8 weeks of trades. The 8 weeks agree: half the flow,
  half the range.

## 6. What changed in the bot

**Two modes, as on Tread.fi** (`bot/strategies/setup.py`), named the Tread way: "Mid 0", "Mid +1 Long", "Grid +3 Short".
- **Mid:** both sides `spread` bps from the mid, following it. Mid 0 joins the best bid and ask on BTC. A negative
  spread quotes inside the mid, as far as a maker order can.
- **Grid:** quotes around the last fill, with a soft reset at 0.5% against the position.
- **Bias:** Long or Short holds half the position cap on that side while quoting both sides; Neutral holds none.
  It follows the cap, so it re-sizes with the account and shrinks off-hours.

**Hidden defaults, chosen from the backtests:**

| Default | Why |
|---|---|
| Mid runs without the safety pause | The same cost per dollar with 13% (BTC) to 66% (ETH) more volume |
| Mid +1 and wider skew the reservation price by the position (κ 1) | Cheaper in 19 of 30 market × spread cases (Mid +1 to +3), by the most on BTC +2/+3 and SPY |
| Grid keeps the safety pause | Cheaper on 6 of 10 markets |
| One order per side | A second level added 8–14% volume on the liquid markets, with no clear cost gain |

**Run limits, like Tread's form:**
- `sl=` (the run's loss limit) was already there.
- `tp=` (take profit, dollars) and `vol=` (volume target) are new. Reaching either closes the position (a maker order,
  then a taker order after 20 s) and stops the run. The dashboard says RUN DONE, and a resume does not restart it.
- The run's volume survives restarts, like its PnL.

**The scout backtests 33 setups** on every market at its maximum leverage: Mid −1, 0, +1, +2, +3, +5 and Grid 0, +1,
+2, +3, +5, each Neutral, Long and Short. Any other spread (e.g. Mid +0.5) runs without a backtest.

**Lists** (no conservative list):
- 🚀 **Most Volume** (/top3) and 💎 **Cheapest** use the budget.
- 🔥 **Max Volume** ignores the cost.
- The breakeven and "aggressive" lists are gone; their commands still work and open these.

**Telegram:** /run opens a form like Tread's. Buttons choose Mid or Grid, the spread, Short/Neutral/Long, the
leverage, a run stop and a volume target; the message shows the backtest of exactly that setup. In one line:
`/run BTC mid 0 40x live sl=10 vol=100k`. Old names still work (`touch 0bp` = Mid 0, `deep 3bp` = Mid +3,
`improve touch` = Mid −1, `anchor 3bp` = Grid +3).

**Retired** (lost on every market in the backtests, or not measurable):

| Retired | Why |
|---|---|
| RGrid | Mostly taker at 2.25 bp |
| The RSI signal | $80k a day on BTC |
| The static grid | Near breakeven, with little volume |
| The "skip US session" variants | Not a Tread setting, and a time filter barely moves the cost (section 4) |

**Not built:**

| Not built | Why |
|---|---|
| DGrid | The lists already pick the setup per market after every scan, and no regime rule beat Mid 0 on BTC |
| Blend | The scout does not record the oracle price, so it cannot be backtested |
| Participation rate | On a maker-only venue the spread sets the speed |

## 7. What the lists say on this data, and why a small budget costs more per dollar

The scout's scan on the same data at $110, with the new 33 setups:

| Stops (position / day / kill) | 🚀 Most Volume top 3 | 💎 Cheapest top 3 |
|---|---|---|
| 1 / 2 / 10 (today's defaults) | SPY Mid +3 50x ($34k/day, $0.03/k) · NVDA Mid +3 20x ($18k) · SPCX Mid +1 5x ($9k) | SPCX Mid +3 (profit) · NVDA Mid +3 · SPY Mid +3 |
| 3 / 6 / 15 (recommended) | SPY Mid +1 50x ($80k/day, $0.10/k) · BTC Grid +1 40x ($51k, $0.17/k) · NVDA Mid 0 Short 20x ($40k, $0.17/k) | NVDA Grid +5 Long (profit) · SPY Mid +3 ($55k, $0.03/k) · AAVE Grid +2 Short |

BTC is missing from the first row. The scan backtests each UTC day until the daily stop, and at 40x on $110 a stop
of a few dollars is tripped by noise, not by the drift:
- $3.5k of BTC moving 20 bp is $7;
- every stop also pays a taker exit.

BTC Mid 0 at 40x, cost per dollar traded by budget:

| Budget | Cost |
|---|---|
| $2.20 daily stop | stops within minutes |
| $6.60 daily stop (scan from 00:00 UTC, 6 days) | 2.9 bp |
| $10 runs from every hour (section 5) | 1.76 bp |
| Whole days (section 4) | 1.64 bp |

**On BTC at 40x, a bigger `sl=` per run buys more volume per dollar.** At about $110 of capital, keep `sl=` under about
a third of the balance, since liquidation is about two thirds away at 40x (previous note, section 7). On 6 days the
scan's winners inside those short windows were Grid variants and the Short bias, which is the week's drift, not an
edge.

## 8. How to run for volume

| Goal | Command | Expect (live BTC so far) |
|---|---|---|
| Volume fast, any day | `/run BTC mid 0 40x live sl=10` | about $95–130k per $10, in 10–30 min |
| A set amount | `/run BTC mid 0 40x live sl=15 vol=100k` | stops at $100k, or at the $15 limit first |
| All-day weekend, cheapest per dollar | `/run SPY mid 0 max live sl=15` (33x off-hours) | backtest: about $10k an hour at about 0.8 bp |

Before BTC at 40x: `/set daily_stop 6`, then `/set position_stop 3` (the previous note, section 7).
