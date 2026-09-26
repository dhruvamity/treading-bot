# 2026-09-26: fast volume near breakeven, what the tape allows

Question from the owner: which settings trade fast (like the live BTC `touch 0bp` run: $4.5k of maker volume in about
14 minutes) at the highest leverage while staying near breakeven?

Method: the recorded Arcus tape on the laptop (full days 2026-09-19 to 09-23), the scout's simulator at ~$100 of
capital and each market's maximum leverage, plus markouts: how far the mid moves against a maker after each taker
print. Two new research switches in the simulator: `SimParams(queue=True)` (an order that joins the best price waits
behind the size shown there) and `Window(step_ms=...)` (the bot deciding faster than once a second). The default
model is unchanged: a fill only when a taker trades through our price, decisions at 1 Hz.

## 1. BTC fills fast; what it costs is the problem

- The scan's "$3.3k/day" for BTC `touch 0bp` at 20x was the daily stop, not slow fills: the backtest reaches the
  2% daily stop within 2–3 minutes of each day, as the live run did after 14 minutes.
- Without a daily stop, BTC `touch 0bp` trades $3–5M a day at 20–40x on ~$100. The flow is there: $64–87M of taker
  trades a day on Arcus BTC, 100k prints, a best level of about $1.5k, a one-tick spread 85% of the time.
- The cost per dollar traded does not come down with any quoting setting:

  | Market, setting, leverage | Volume/day | Cost per $ traded |
  |---|---|---|
  | BTC touch 0bp, 20x | $3.2–3.3M | 1.7 bp |
  | BTC touch 0bp, 40x | $4.8–5.0M | 2.0 bp |
  | BTC deep 1bp, 40x | $2.3M | 1.9 bp |
  | BTC deep 2bp, 40x | $1.1M | 1.9 bp |
  | ETH touch 0bp, 25x | $1.0M | 2.4 bp |
  | ETH deep 1bp, 25x | $0.5M | 2.3–2.4 bp |

  (steady state: no daily stop, no kill, no liquidation; the conservative and the queue fill models agree within
  0.1 bp.) Depth, skew, order size (from $50 to $1,760) and position stops all land between 1.2 and 2 bp. So does
  every hour of the day (1.3–2.3 bp by UTC hour).
- Live on 2026-09-26 the same setup cost about 1.4 bp and filled at about $305 of maker volume per quoting minute; the
  simulator fills $700–1,900 a minute at that hour on the recorded days, at 1.8–2.1 bp. On BTC at the touch the
  backtest overstates both the fill rate and the cost. Live orders were on the book only part of the time
  (cancel + place on every requote, ~180 ms acknowledgements), which the model does not have.

Why: markouts. A fill at the best price loses the maker 0.6–1.1 bp within 5–60 s on BTC (more on ETH and SOL); small
prints (under $300) lose the most, 1.2–1.5 bp; prints that sweep through the best price are followed by a partial
reversal (+0.2 to +0.6 bp). The bot requotes once a second, and a quote up to ~1.15 s old loses about 1 bp more than
a fresh one: fast traders pick it off after the price moves elsewhere.

Two signals predict the next move (BTC, ETH and SOL alike): the queue imbalance at the best price (bid share under 10%
→ −0.35 bp over the next 5 s) and the last 5 s of net taker flow (−0.4 to −0.6 bp after net selling). A filter that
pulls the side they point against helps at a 100–250 ms reaction time (touch fills on BTC: −1.0 → −0.5 to −0.75 bp at
5 s), but not reliably at the bot's 1 Hz. It is not in the bot.

## 2. Near breakeven: the stock and ETF perps, quoting deep

Their prices are anchored to the underlying, so sweeps overshoot and come back, and a quote 1.5–3 bp from the mid is
paid for it. Five days, ~$100 of capital, maximum leverage, normal stops (1/2/10%):

| Market, setting, leverage | Volume/day | PnL/day | Worst day | Daily stops |
|---|---|---|---|---|
| SPY deep 3bp x2, no pause, skew, 50x | $43k | +$0.90 | −$3.31 | 2 of 5 |
| SPY deep 3bp, no pause, 50x | $29k | +$2.49 | −$3.55 | 2 of 5 |
| QQQ deep 3bp, no pause, skew, 25x | $21k | +$1.64 | −$1.42 | 0 of 5 |
| NVDA deep 1.5bp x2, 20x | $21k | +$0.92 | −$2.68 | 2 of 5 |
| GOOGL deep 1.5bp x2, no pause, 10x | $13k | +$1.53 | −$2.16 | 1 of 5 |
| QQQ touch 1bp, 25x (for comparison) | $25k | −$1.79 | −$3.05 | 3 of 5 |

Five of these settings were not in the menu; they are now (`deep 1.5bp x2`, `deep 1.5bp x2, no pause`,
`deep 3bp x2, skew`, `deep 3bp, no pause, skew`, `deep 3bp x2, no pause, skew`). These are five days in-sample; the scan
re-tests them on every new day and lists them only while they hold. None of the stock perps has live fills for a deep
setting yet.

## 3. What this means for running

- Fast (BTC): budget the cost. At 1.5–2 bp, `sl=30` buys roughly $150–200k of BTC maker volume; the run stops for good
  at the $30 loss. `/run BTC touch 0bp 20x live sl=30`.
- Near breakeven (stock perps): $15–45k a day on ~$100, more with more capital. The scout's lists pick among them.
- Not built, the largest lever left for BTC: a faster quoting loop (react to book changes within ~100–250 ms, not once a
  second) with the imbalance filter. The markouts say it could roughly halve the cost; it needs live testing first,
  and a live measure of queue fills to calibrate the simulator.

## 4. Every mode, every liquid market (second pass, same day)

Mid (touch 0bp, improve, deep 0.5–5bp, skew, 2 levels), grid (1–5bp, 2 and 4 levels, 0.2/0.5% reset), rgrid (2–10bp),
anchor (1–5bp) and the RSI signal, ~$100 at each market's maximum leverage, normal stops.

- **BTC at 40x:** every mode costs 1.66–2.1 bp per dollar with no daily stop (58 settings, both fill models), and every
  setting hits the 2% daily stop every day. The cheapest are the RSI signal (1.67 bp, but $82k/day) and deep 1.5–2bp
  with skew (1.7 bp, $1.2–1.8M/day). Nothing is near breakeven at the bot's speed.
- **Other crypto:** ETH 1.9–2.2 bp, SOL 2.8 bp, ZEC 4.5 bp (the widest spread is the most informed flow), HYPE 1.4 bp
  (grid), XRP 2.1 bp. Three-day winners on HYPE and XRP turned negative over five days.
- **Five days, profitable with normal stops:**

  | Setup | Volume/day | PnL/day | Positive days | Daily stops |
  |---|---|---|---|---|
  | QQQ deep 3bp x2, no pause, skew, 25x | $23.7k | +$2.03 | 4 of 5 | 0 |
  | QQQ deep 3bp, no pause, skew, 25x | $21.4k | +$1.64 | 4 of 5 | 0 |
  | SPY grid 3bp x2, 50x | $23.2k | +$4.39 | 3 of 5 | 2 |
  | SPY grid 3bp x4, 50x | $32.6k | +$3.00 | 3 of 5 | 2 |
  | SLV deep 5bp, skew, 25x (silver) | $7.6k | +$1.17 | 3 of 5 | 2 |

  GLD, GOOGL, NVDA, HYPE and XRP were flat to negative over five days. The two SPY grids are in the menu now.

Where an edge shows up at all, the price is anchored to an outside market (index ETFs, silver): sweeps overshoot and
come back to it. Crypto on Arcus follows faster venues, and a quote that is up to a second old is what gets filled.

## 5. The overnight paper farm, checked out of sample

A cloud session ran a "paper farm" overnight (2026-09-25 22:17 to 09-26 07:13 UTC, 59 markets, 31 Tread.fi-style
settings on the scout's simulator, $100, stops 5/10/20%). Its headline: Mid +1, Grid +1/+3 (quotes around the last
fill with a 0.25–0.5% soft reset) and a volatility-gated Mid 0 at or past breakeven at 20x, 24–60x the capital an hour.

Its policies were re-run here unchanged. On its own window they reproduce its numbers (ETH `grid+1 r0.25` −$19.8 per
$1M, NVDA `mid+1 skew` −$23.6, BTC `grid+2 r0.5` −$99.4: exact). On five recorded full days (09-19 to 09-23, US
sessions included), 9 liquid markets at 20x, 45 market-days:

| Setting | Farm night | Five days, stops 1/2/10 | Five days, stops 5/10/20 |
|---|---|---|---|
| grid+1 r0.25 | −$20 per $1M | $247 | $273 |
| mid+1 | −$12 | $192 | $180 |
| grid+3 r0.5 | −$28 | $222 | $230 |
| mid0 vgate | $20 | $183 | $127 |
| dgrid | $14 | $241 | $228 |
| mid0 | $62 | $239 | $196 |

Every farm setting lost, and the same hours (22:00–07:00 UTC) on four other nights lost too ($176–782 per $1M). The
farm's night was a calm one; its per-market "best" picks were chosen after the fact from ~90 each.

What held in both datasets: deep quotes (3 bps) on the anchored perps, SPY, QQQ, NVDA and GLD, and only with room
for the reversion. `deep 3bp, no pause` at max leverage, per market-day over those four markets (20 market-days):

| Stops (position/day/kill %) | Mean | Without the 2 best days | Median | Positive days | Worst day |
|---|---|---|---|---|---|
| 1/2/10 (default) | +$1.17 | −$1.28 | −$1.32 | 7 of 20 | −$3.55 |
| 3/3/10 | +$0.48 | −$1.65 | −$0.33 | 9 of 20 | −$6.07 |
| 3/6/15 | +$3.37 | +$1.13 | +$0.23 | 13 of 20 | −$8.78 |
| 5/10/20 | +$4.24 | +$2.00 | +$0.41 | 15 of 20 | −$11.56 |

With the default stops the gain is two days; from 3/6/15 up it holds without them, and a wider daily stop matters
as much as the position stop. The typical day is still about breakeven (median +$0.23): the gains come from the days
the price runs away from the underlying and comes back. It is in the menu as `deep 3bp, no pause, 3% stop` (the
moderate profile); SLV lost with every stop. Not taken from the farm: Mid 0, the gated Mid 0, DGrid and the tight
last-fill grids (no out-of-sample support), and the skip-US-session variant of this setting (worse here).

## 6. Backtest vs the live BTC run, and the fill model

The live BTC `touch 0bp` run (2026-09-26 00:14–00:33 UTC, 20x, sized for ~$30) and the bot's own engine in paper
mode (the farm's overnight run, same setting, $100) were replayed on the same recorded minutes:

| | Volume | Fills | PnL | Daily stop |
|---|---|---|---|---|
| Live run | $4,493 | 31 | −$0.64 (1.4 bp) | 00:33 |
| Backtest, through-only fills (the scan until now) | $2,583 | 21 | −$0.97 (3.7 bp) | 00:28 |
| Backtest, queue fills | $3,535 | 21 | −$0.87 (2.5 bp) | 00:28 |
| Backtest, front of queue | $7,039 | 68 | −$0.83 (1.2 bp) | 00:28 |

Against the paper engine (to its daily stop, two UTC days): engine $19.6k and $15.7k; through-only $25.8k and $8.1k;
queue $18.8k and $16.6k. Found:

- **The scan's fill model was the main gap.** Counting only trades through our price missed the fills at our price
  that the live and paper runs got, and kept only the most adverse ones: at the touch it understated volume by 30–60%
  and overstated the cost 2–3x. The queue model (an order waits behind the size shown when it joined) tracked the
  paper engine within ~5% and came closer to the live run. The scan uses it now (SIM_VERSION 8). Multi-day results
  barely move (anchored `deep 3bp, no pause`, 3/6/15: +$1.15 a market-day without its best two days, was +$1.13; BTC
  `touch 0bp` 20x steady state 1.73 bp, was 1.71).
- **Live did better than every backtest on cost** (1.4 bp) and hit the daily stop 5 minutes later. Live orders sat on
  the book only 29% (bid) and 62% (ask) of the time: each requote is a cancel and a new order (~180 ms to acknowledge),
  which no model has. One 19-minute run is one data point: `bot diagnose --replay` now prints this comparison for any
  run (the pilot records each run's setup, and the engine the sizes it traded).

## 7. Two live BTC runs against the backtest, and the position stop at 40x

`bot diagnose --replay` on the server, `touch 0bp`, both runs on 2026-09-26 (UTC):

| | Run 1: ~$30 at 20x, 00:10–00:45 | Run 2: about $100 at 40x, `sl=30`, 07:02–07:38 |
|---|---|---|
| Live | $4,493, 31 fills, −$0.57 (1.3 bp) | $57,836, 53 fills, −$4.39 incl. $0.58 fees (0.76 bp) |
| Backtest, queue fills (the scan) | $3,893, 33 fills, −$0.73 (1.9 bp); daily stop 5.5 min before the run's | not comparable: replayed with a $2.20 daily stop, the run had $30 |
| Backtest, through-only | $6,112, 50 fills, −$0.63 | same problem |
| Backtest, front of queue | $7,957, 64 fills, −$0.78 | same problem |

What differs between the backtest and the account:

- **Volume and fills: the queue model holds up** (run 1: 0.87x the volume, 33 fills for 31). Through-only and
  front-of-queue miss in opposite directions, and their paths split at the daily stop.
- **Cost: both live runs were cheaper than every backtest** (1.3 bp and 0.76 bp live; 1.9–2.8 bp in the replays;
  1.7–2.0 bp steady state below). The scan is conservative on BTC cost by roughly 1.5–2.5x. Two runs and about 70
  minutes are not enough to correct the model; each new run adds a point.
- **Requotes and margin (fixed).** Arcus requotes are a cancel then a new order. Until the venue confirmed the
  cancel, the bot's own pre-trade check counted the old order as still resting beside its replacement. So a buy that
  reduced a short looked like it opened a position (~$80 of margin wanted, ~$50 free), and it was refused 14 times in run
  2. Orders whose cancel is out no longer count. The backtest never had this.
- **The replay ignored `sl=` (fixed).** A run's loss limit lifts its daily stop and kill; the replay kept $2.20, so its
  backtest stopped at 07:06 while the run went on. `--sl 30` (or the deployed setup, for new runs) applies it.
- **One side off the book.** In run 2 the sell side rested 32% of the time and the buy side 64%; 54% of the market's
  taker volume ($301k) traded while the bot had no order on that side. This comes from the inventory cap (two filled
  orders per side at 40x) and the position-stop pause.
- **Order acknowledgement** took a median 180 ms (the backtest assumes 150 ms): close enough.

**The position stop is too tight at 40x.** At about $100 the default position stop is 1% of the capital, about $1. On
a $3.5k position that is a 3 bp move. Over 4 full BTC days (09-20 to 09-23), queue fills, the 40x order and cap of a ~$100 account, no daily
stop and a large balance, so this is the cost over a whole day rather than the first minutes:

| Position stop | Cost | Volume a day | Position stops a day | Taker share | Hours quoting |
|---|---|---|---|---|---|
| 1% (about $1, today) | 1.97 bp | $4.98M | 316 | 10% | 15.4 |
| 2% | 1.76 bp | $5.58M | 129 | 3% | 18.8 |
| **3% (about $3)** | **1.70 bp** | **$5.95M** | **48** | **1%** | **20.2** |
| 5% | 1.69 bp | $6.15M | 9 | 0% | 20.9 |

It is better on every one of the 4 days (0.25–0.33 bp). Each position stop crosses the spread (taker fee plus slip)
and pauses quoting. At 3% the loss limit (`sl=`) still bounds the run, so for the same dollars lost, 3% buys about 16%
more volume. Smaller orders ($880 or $440) cost about the same as 3% (1.70–1.80 bp), with less volume. **Use
`/set position_stop 3`** before a BTC run at 40x. It applies from the next `/run` on any market (the owner's settings
are global); the anchored stock setting `deep 3bp, no pause, 3% stop` already uses 3%.

At 40x, a ~$100 account holding the full position is liquidated after losing roughly two thirds of it (the backtest
without a daily stop was, within 21 minutes of quoting). Keep `sl=` under about a third of the balance at 40x.
