# Arbitrage

Funding-rate arbitrage between Arcus and Lighter (Robinhood Chain). One of three bots in the [repository](../README.md): installed by
`make install`, its switches in the one `.env`, and controlled from the one Telegram bot as `/arb_<command>`. It trades with the Arcus and
Lighter bots' own venue clients and keys. It scans, plans, backtests, and runs the position: on paper by default, live only behind a switch
that you set and a confirmation each time.

**`arbitrage <command>` in this guide is `.venv/bin/arbitrage <command>`**, run from `treading-bot/arcus`.

On two machines (`BOT_ROLE` in `arcus/.env`, [main README](../README.md#two-machines-one-records-one-trades)) the executor runs on the
machine that trades (`all` or `trader`): it needs nothing from the recorder, since it reads both venues itself. A `recorder` or a
`scout` machine refuses `arbitrage run` and `start`; `scan`, `plan`, `history` and `backtest` work anywhere.

> **Risk warning.** Experimental software that can place real orders with real money. Only its Lighter adapter has sent
> real orders (section 6 says exactly what is unproven, and `arbitrage livetest` tests the rest). Backtests are estimates. Nothing here is financial advice.

**What it is set up for (the owner's choice, 10 Oct 2026):** SPY alone, at the highest leverage both venues allow at
that hour, closed and reopened every three funding payments, the short venue taken from ProFunding. That is three
commands ([section 3a](#3a-spy-at-the-highest-leverage-renewed-every-three-funding-payments)); it buys volume and
open interest and costs money every day (section 2a says how much). Without those commands it holds the best-paying
market until its funding difference is gone (sections 2 and 4).

## 1. The idea

Both venues charge funding every hour: rate × position value; a positive rate means longs pay shorts. Hold the same
market **short on the venue with the higher rate and long on the other**, the same size on both: the price moves
cancel, and each hour pays the difference between the two rates on the size of one leg. No directional bet.

## 2. What the history says: how long to hold, and how much leverage

`arbitrage study` answers both from everything the two venues and ProFunding publish, for the stocks, indices and
commodities both venues list (25 markets). **Both venues pay funding every hour**, so there is no "next settlement in
three hours" to wait for: a payment is always less than an hour away, and one payment is small.

**The data (downloaded 10 Oct 2026; `arbitrage history --update` adds new hours).**

| Source | What there is | How far back |
|---|---|---|
| Arcus | every hourly funding payment, hourly oracle prices, the dollars traded each hour | its first payment, 24 Jun 2026; nothing is missing inside the files (64,504 payments on the 25 markets) |
| Lighter (Robinhood Chain) | every hourly funding payment, hourly traded prices and dollars traded | its first payment, 26 Jun 2026; candles from 30 Jun; nothing missing (63,014 payments) |
| ProFunding | its own hourly record of both venues' rates | the last 30 days only (it refuses more); `arbitrage history --profunding` |

Neither venue answers anything before those dates, and neither publishes past order books (those exist only where a
recorder ran). Arcus's candles carry its oracle's price; its perp's own price by the minute comes from its public
trades, which can be asked for any window (`arbitrage history --minutes SPY QQQ`). ProFunding is a check, not a longer history: over the 30 days it has,
a market's average rate differs from the venue's own by 0.01% a year on Arcus and 0.15% a year on Lighter (most: CRCL,
0.76%). Hour by hour its Lighter figures differ more, because it stores an unrounded snapshot while Lighter publishes
the rate it settled, rounded to 0.0001% an hour. The venues' own numbers are what the replay reads.

**Only the best market of each hour is studied**, because the bot holds one position, in the market that would pay it
most: `study` ranks every market at every hour with the bot's own rule and follows the winner. A market also has to
have traded $100,000 on each venue in the 24 hours before, as the live bot demands. (Until 10 Oct 2026 the replay
checked today's volume for the whole history. That let it open markets that were too thin at the time, and roughly
doubled what it reported: $0.094 a day at $240 then, $0.063 now, fills at 2 bp.)

2,537 hours, of which 755 (30%) had a market worth opening; the best one changed 253 times, so about 253 separate
opportunities. It was most often NVDA (19% of those hours), SLV, SNDK, SPY, USO and MU.

**What the best market paid over the next H hours** (funding on one leg, in bp of the position):

| Held | Funding collected | After fills of 4 bp | 8 bp | 16 bp | Share that beat 8 bp |
|---|---|---|---|---|---|
| 1 h (one payment) | 0.5 bp | −3.5 | −7.5 | −15.5 | 0% |
| 12 h | 3.2 | −0.8 | −4.8 | −12.8 | 8% |
| 24 h | 5.6 | +1.6 | −2.4 | −10.4 | 26% |
| 48 h | 8.7 | +4.7 | +0.7 | −7.3 | 49% |
| 72 h | 11.8 | +7.8 | +3.8 | −4.3 | 60% |
| 5 days | 16.0 | +12.0 | +8.0 | 0.0 | 73% |
| 7 days | 19.6 | +15.6 | +11.6 | +3.6 | 74% |
| 14 days | 35.5 | +31.5 | +27.5 | +19.5 | 92% |

Getting in and out is four fills: 4 bp at 1 bp a fill, 8 bp at the 2 bp the books usually show, and 16 bp on the one
paper round trip so far (a thin token).

### How long to hold

- **Never for one payment.** One payment is half a basis point against 4 to 16 for the fills. Replayed, closing every
  hour lost $1.23 a day at $240, every 3 hours $0.46, every 12 hours $0.05.
- **It pays for its fills after about 16 hours at the best fills, two days at usual ones, five days at poor ones.**
- **Hold it until the difference is gone, which is what the bot does** (at least 24 hours, then out when the last 24
  hours and the next payment both pay nothing). From the hour a market became the best, it went on paying for 74 hours
  in the middle case; a quarter ended within 44 hours, a quarter lasted more than 5.5 days. Replayed at $240:

  | Rule | Funding less fills, $ a day, fills at 1 bp | 2 bp | 4 bp |
  |---|---|---|---|
  | the bot's rule | +0.085 | +0.063 | +0.009 |
  | exactly 24 h, then pick again | +0.044 | −0.030 | −0.023 |
  | exactly 72 h | +0.084 | +0.045 | −0.005 |
  | exactly 7 days | +0.061 | +0.071 | +0.023 |
  | at least 72 h, then until it stops paying | +0.093 | +0.054 | +0.009 |
  | at least 7 days, then until it stops paying | +0.091 | +0.076 | +0.023 |

  Every rule that holds for three days or more lands within three cents a day of the others, which is inside the noise
  of 12 to 21 positions. Shorter than a day loses. The settings stay as they are (`min_hold_h 24`, `max_hold_h 0`).
- **Do not switch to a better market.** After the first day the best market pays no more than any other that passes
  the rule (11.8 bp against 11.2 over three days, 19.6 against 21.9 over seven), and a switch costs four fills.
- **Weekends pay almost nothing.** Arcus's stock funding stops while the underlying is closed: 5 to 13% a year on
  weekdays, about 1% at weekends. A position opened Monday to Wednesday collected 14 to 15 bp in its first three days,
  one opened on a Friday 7, on a Saturday 2. Holding through a weekend costs nothing; closing for it costs the fills.

### How much leverage

**Since 10 Oct 2026 the bot's default is the venues' highest leverage** (the owner's decision, for volume and open
interest on SPY: section 2a). This part is what the history says about leverage on every market the usual rule opens,
measured under the rule the bot had until then (a stop three daily moves away, at most 20x). Replayed with the new
default on those 25 markets the usual rule **loses $0.50 a day at $240**: 127 positions, 121 of them stopped, 34
transfers. `arbitrage set stop_sigmas 3`, `arbitrage set max_leverage 20` and `arbitrage set hold_off_hours 1` give the
earlier rule back, and are what to set before letting it trade anything but SPY and QQQ.

The stop sits half the way to liquidation, so more leverage means a nearer stop, and a stop is not free: it pays the
entry's fills, a taker fee and the crossing, and it leaves the money on one venue until it is sent back by hand
(section 6a); the next position is smaller until then. Every hour's best market, opened at each leverage and held by
the bot's rule, stops checked against both venues' hourly highs and lows:

| Leverage | On average | Stop at | Positions stopped | Stops in 30 days held | % a year on the capital while held, fills at 1 bp | 2 bp | 4 bp |
|---|---|---|---|---|---|---|---|
| 2x on every market | 2.0x | 21.6% | 5% | 0.3 | +10.1 | +7.7 | +2.8 |
| 3x on every market | 3.0x | 13.3% | 22% | 1.4 | +14.8 | +11.2 | +3.9 |
| stop 4 daily moves away | 5.3x | 12.6% | 11% | 0.7 | +21.3 | +13.9 | −1.0 |
| **stop 3 daily moves away (the default until 10 Oct)** | 6.2x | 9.6% | 27% | 2.0 | +24.7 | +15.7 | −2.3 |
| stop 2 daily moves away | 7.3x | 6.7% | 44% | 3.8 | +27.7 | +16.7 | −5.3 |
| stop 1.5 daily moves away | 8.1x | 5.3% | 57% | 5.7 | +28.5 | +15.4 | −10.9 |
| 10x where allowed | 8.1x | 3.6% | 69% | 9.9 | +17.5 | +0.4 | −33.6 |
| the venues' maximum | 11.0x | 3.1% | 76% | 12.3 | +15.9 | −7.6 | −54.8 |

- **The venues' maximum leverage earns less than a stop 3 moves away at every fill cost, and loses at 2 bp.** Three
  positions in four are stopped and there is a transfer to make every two or three days. The one-position replay agrees: −$0.04 a day
  at the maximum (32 of 38 positions stopped) against +$0.065 with the stop 3 moves away.
- **A stop 3 daily moves away is next to the best.** 2 moves earns one point a year more at twice the stops and
  transfers; 1.5 moves earns less again. The leverage it gives is not one number: about 2x on the most volatile
  stocks, 5 to 10x on the large ones, up to a `max_leverage` of 20 on SPY.
- **A stop sized by each market's own moves beats one leverage for every market:** 5x everywhere made 14.1% with 4.2
  stops in 30 days, the bot's rule 15.7% with 2.0.
- **If your fills cost 4 bp, only 2 to 3x is still positive.** Set `fill_cost_bp` to what `arbitrage status` shows
  your fills cost: the entry rule then asks for more funding before it opens.
- **Closing early to keep the money even did not help:** at 45% / 55% it earned a third as much, at 40% / 60% a little
  less than leaving it to the stop.

### How sure

- **The two halves of the history are not alike.** Until mid-August the venues were new and thin: a market passed the
  volume floor in 12% of hours, an opportunity lasted 46 hours in the middle case and paid 6 bp, and the rule earned
  nothing. Since then one passed in 47% of hours, lasted 82 hours and paid 19 bp, and the rule made $0.15 a day at
  $240 with fills at 2 bp ($0.25 at 1 bp). The second half is closer to today, and it is 53 days and ten positions.
- **In both halves** one payment never paid for the fills, a day was too short, and the venues' maximum leverage
  earned far less than the bot's setting. Those are the conclusions to trust; the cents are not.
- **With prices, the whole replay made $0.07 a day at $240 (10.7% a year)**: 21 positions, 5 stops, 5 transfers, and
  one 7-day SPY position made most of it. If the money is never moved back between the venues it made nothing.
- **A lower volume floor would have earned more on paper** ($0.073 a day at a $25,000 floor against $0.063), but a thin
  market's fills cost more than the 2 bp the replay charges. The floor stays at $100,000.

What the replay cannot know: real fills against the mid, the gap between the venues at a stop, and margin rules other
than today's. An earlier replay that included crypto made four fifths of its result from one token (CASHCAT) at a 40%
stop; the bot no longer trades crypto.

## 2a. SPY and QQQ at the highest leverage, closed and reopened on a clock

The idea tested (10 Oct 2026): only SPY and QQQ, because they carry the most leverage; at the highest leverage the
venues allow; closed and reopened every 3 to 6 hours with limit orders only, so that it makes volume as well as open
interest; the side chosen by the funding each time. Four commands measure it: `arbitrage fills` (what the orders
cost, on recorded order books), `arbitrage cyclecost` (the idea replayed on 106 days), `arbitrage basis` (Arcus's price
against Lighter's by the minute, and over each weekend), and `arbitrage study` (section 2).

**What the venues charge and allow** (their own API and documentation, read 10 Oct 2026):

| | Arcus | Lighter on Robinhood Chain |
|---|---|---|
| Maker fee | 0 | 0 (standard account) |
| Taker fee | **2.25 bp** (the base tier; less only at its high volume tiers) | 0 (standard account) |
| Highest leverage, SPY / QQQ | 50x / 25x while the stock market is open (Mon–Fri 04:00–20:00 New York), 33x / 16.7x while it is closed | 50x / 50x at all hours |
| A position opened at 50x, at the close | kept; it cannot be added to until the reopening (the maintenance margin does not rise) | nothing changes |
| Liquidation | on the mark price, below the maintenance margin (SPY 1.33%, QQQ 2.67%) | partly below the maintenance margin (1.2%), fully below 0.8%; a fee of up to 1% of the position |
| Price while the stock market is closed | held in a band around Friday's close: 1% for SPY at first, then 2%, 4%, 8%, each step after an hour of pressure | no band since 10 Jul 2026: its own order book's price, which "instantly converges" to the outside price when there is one again |
| Funding while the stock market is closed | fixed (4.43% a year now) | its usual formula (3.5% a year when its price is near its index) |

ProFunding's row for this pair is `LighterRH` against `Arcus` (not `Lighter`, which is a different venue). Its
"break-even days" counts a taker fee to get out on Arcus; the point of limit orders is not to pay it.

**1. Limit orders are free of fees, not free.** A limit order at the best price is filled when the market comes to it,
so the price is a little worse for it right afterwards, and the other leg is taken at that worse price.
- Arcus alone, 29 Sep – 3 Oct (its SPY volume had just risen more than ten-fold, to $18–32 million a day): an order at the best price was worth 0.3 bp
  less five seconds after its fill on SPY (0.5 bp in the cash session, 0.1 at the weekend) and 0.6 bp less a minute
  after; QQQ 0.5 and 0.8 bp.
- Both legs together, on the one day both order books were recorded (24 Sep, when Arcus was still thin): a limit order
  on Arcus and each of its fills taken on Lighter half a second later cost **1.1 bp a pair at $1,000, 1.3 bp at $5,000,
  1.6 bp at $25,000**, counting only the cases where the Arcus order was filled as maker. Getting in and out is two
  pairs.
- So one close-and-reopen costs about 1 to 2 bp of the position on SPY (1.5 used below) and about 2 bp on QQQ. SPY's
  funding difference is 2.6% a year: **0.09 bp per three hours**. The funding pays for a cycle that costs up to about
  0.1 bp; the measured cost is ten to twenty times that.

**2. How to get in and out, decided from those books** (now the bot's way, section 4):
- The limit order goes on **Arcus**, where a taker order costs 2.25 bp. **Lighter's leg is taken at once** for
  whatever Arcus has filled: a taker order there is free, cost the same as a limit order would have (1.90 against
  1.99 bp a pair at $1,000, 2.40 against 2.35 at $5,000), and left the position one-sided for half a second instead
  of ten. The two legs are then never apart for longer than one loop of the bot.
- An Arcus order at the best price was filled within 10 minutes in 100% of cases in the cash session, 96–97% in the
  early, late and night hours, and **57–67% at the weekend** (middle wait 21 s, 80–90 s, and 6–8 minutes). Waiting
  costs nothing: nothing is one-sided while it waits. `enter_timeout_s` is therefore 600 s (was 180).
- **When a market order is right on Arcus:** a stop, a leg that has vanished, `arbitrage close --now`, and an exit
  still unfinished after `enter_timeout_s`. Crossing there cost 2.4 bp at $1,000 and 3.1 bp at $25,000, almost all of
  it the fee. For an ordinary close at the weekend, set `enter_timeout_s` higher rather than cross.

**3. The highest leverage does not survive a normal day.** At 50x with 90% of the money used as margin, SPY's
liquidation is 0.89% away on Arcus and 1.02% on Lighter. The bot's stop is **0.33%** away: half of what is left of
1/50 after Arcus's maintenance margin, counted as if all the money were margin. At 33x it is 0.83%, at 30x 1.0%.
(Until 10 Oct this page said 0.45%, half the way to the liquidation price: the replay placed it there and the bot
does not. The numbers below are the bot's.)

| SPY, from the entry, stock market open | 1 h | 3 h | 6 h | 24 h |
|---|---|---|---|---|
| reached the stop (0.33%) | 11% | 35% | 53% | 90% |
| reached the liquidation price (0.89%) | 0% | 3% | 7% | 29% |

Nights and weekends are quieter (the stop, 0.83% away at the 33x allowed then, was reached in 0–1% of 6-hour holds).

**4. The idea replayed** ($240, half on each venue, kept at that size; a cycle 1.5 bp on SPY, a stop 3 bp):

| SPY | Position a leg | Volume a day | Stops a week | Transfers a week | Funding | Cycles | Stops | Net a day | Per $1M of volume |
|---|---|---|---|---|---|---|---|---|---|
| highest leverage, every 3 h | $4,538 | $155,700 | 10.7 | 4.6 | +$0.55 | −$5.18 | −$2.64 | **−$7.27 (−3.0% of the account)** | $47 |
| highest leverage, every 6 h | $3,557 | $74,100 | 11.1 | 3.5 | +$0.38 | −$2.23 | −$2.21 | −$4.06 (−1.7%) | $55 |
| highest leverage, never closed on a clock | $3,994 | $23,400 | 8.7 | 3.2 | +$0.33 | −$0.44 | −$1.75 | −$1.86 (−0.8%) | $80 |
| 30x, every 3 h | $3,015 | $96,700 | 0.5 | 2.1 | +$0.36 | −$3.61 | −$0.07 | −$3.33 (−1.4%) | $34 |
| 20x, every 3 h | $1,856 | $59,500 | 0.1 | 1.0 | +$0.20 | −$2.23 | −$0.01 | −$2.04 (−0.85%) | $34 |
| 10x, every 3 h | $1,022 | $32,700 | 0.1 | 0.1 | +$0.11 | −$1.23 | −$0.01 | −$1.13 (−0.47%) | $34 |
| 10x, every 3 h, if a cycle cost nothing | $1,022 | $32,700 | 0.1 | 0.1 | +$0.11 | 0 | −$0.01 | +$0.10 (+0.04%) | −$3 |

- At the highest leverage the stops alone cost more than the funding pays, cycle or no cycle: 155 stops in 102 days
  with 3-hour cycles, 147 of them while the stock market was open. In 5 of them the price also reached the
  liquidation price within the hour, where the stop may not have filled first.
- **30x is most of the volume at under half the cost**: the stop is 1.0% away instead of 0.33%, so there were 8
  stops in 102 days instead of 155. `/arb_lev 30 max SPY` sets it.
- Closing on a clock is a way to **buy volume**: $34 per million dollars traded on SPY at 30x and below, $47 to $55
  at the highest leverage, and $45 to $58 on QQQ, half of it on each venue. It does not earn.
- QQQ at its highest (25x, 16.7x): 7.4 stops and 3.1 transfers a week, −$3.51 a day every 3 hours.

**4a. The stop and the take profit as limit orders** (`stop_early`, on since 10 Oct 2026). Both venues accept a
stop or a take profit that becomes a limit order when triggered (Arcus: a `LIMIT` leg in the `positionTpsl` batch,
resting orders only; Lighter: order types 3 and 5). They do not help here. On Arcus a triggered limit order that
fills at once is still a taker order and pays the 2.25 bp; one priced not to cross can be left behind by the move it
was meant to stop; and Arcus refuses post-only on them. On Lighter a taker order is free already. So the venues'
own orders stay what they were, market orders at the stop, for when the bot is down. The saving comes from the bot
closing **before** the stop with its ordinary limit order:
- From 80% of the way to the stop (`stop_early` 0.8) it closes as it closes any position: a post-only order on
  Arcus at the best price, followed every few seconds, and Lighter taking whatever Arcus has filled. No fee.
- If the price reaches the stop itself before that is done, the rest goes with taker orders on both legs at once.
  The bot checks this every second while it closes, and it now does so in **every** close with limit orders (a
  3-hour renewal too): before 10 Oct such a close had no stop behind it for up to `enter_timeout_s`.

On Arcus's recorded SPY book (29 Sep – 3 Oct, stock market open, $5,000, positions held 3 hours; `arbitrage fills`):

| Closing starts at | Positions closed that way | Done before the stop: the leg that gains | the leg that loses | Middle wait | The stop followed anyway |
|---|---|---|---|---|---|
| 60% of the way | 59% | 100% | 97% | 6 s / 25 s | 58% |
| 70% | 53% | 100% | 95% | 5 s / 22 s | 65% |
| **80% (the default)** | 46% | 99% | 93% | 4 s / 17 s | 74% |
| 90% | 39% | 94% | 74% | 4 s / 14 s | 87% |
| only at the stop (off) | 33% | 0% | 0% | | |

The leg that gains is the take profit: a long sells to the buyers who are pushing the price up, so it is filled in
seconds. The leg that loses has to buy while the price runs away from its bid, which is why 90% is too late for it.
Starting earlier closes more positions that would have come back (at 80%, one in four), and each of those is one
more close-and-reopen. Replayed on the 106 days at the highest leverage, every 3 hours, $240:

| SPY | Volume a day | Taker stops a week | Limit closes a week | Transfers a week | Net a day | Per $1M |
|---|---|---|---|---|---|---|
| off: taker orders at the stop | $155,700 | 10.7 | 0 | 4.6 | −$7.27 | $47 |
| from 90% | $129,600 | 2.1 | 10.9 | 5.0 | −$4.73 | $37 |
| **from 80%** | $140,900 | 0.6 | 14.6 | 4.3 | **−$4.89** | $35 |
| from 70% | $131,700 | 0.5 | 19.8 | 4.5 | −$4.62 | $35 |
| from 60% | $182,400 | 0.4 | 25.1 | 4.1 | −$6.33 | $35 |

It takes a third off the cost and brings the price of volume at the highest leverage down to what it is at 30x. It
does not change the transfers: the money still moves from one venue to the other with the price. The share done
before the stop is from five days of one market's book; QQQ's was 99–100% from 80%, and the closed hours had too few
cases to say (20, all done).

**5. The two venues' prices, and the weekend.** Arcus's price is normally **below** Lighter's: 8 bp on SPY, 10 bp on
QQQ, at all hours. The gap wanders: over three hours it changed by 2.7 bp in the middle case and by 7.1 bp one time in
ten on SPY (2.3 and 6.3 on QQQ): a gain as often as a loss, and thirty times SPY's funding of those three hours. A
position closed on a clock takes whatever the gap did each time; one held until the funding stops paying takes it once.

| | Gap in the middle case | 1 time in 100 | Widest seen |
|---|---|---|---|
| SPY, stock market open | 10 bp | 20 bp | 53 bp |
| SPY, nights | 12 bp | 25 bp | 128 bp |
| SPY, weekends | 10 bp | 20 bp | 27 bp |
| QQQ, weekends | 11 bp | 22 bp | 38 bp |

- **The weekends were no wider than the weekdays**, on the 10 weekends there is data for (two of them since Arcus
  became busy). The widest weekend gap was 27 bp on SPY and 38 bp on QQQ, and at Monday's cash open it was inside its
  weekday range every time (+2 to −14 bp on SPY, −3 to −17 on QQQ).
- **The two prices moved together at weekends.** The furthest either went from Friday's close was 1.0% on SPY and
  1.6% on QQQ, on both venues at once. At 33x (the most Arcus allows then) SPY's stop is 0.83% away, so a position
  held from Friday's close would have reached it on one weekend of the ten; one renewed every 3 hours never did (the
  furthest 3-hour move at a weekend was 0.69%). A 50x position carried over from Friday would have been stopped on
  six of the ten, which a 3-hour cycle avoids: the renewal after the close is at 33x on both venues.
- **The one violent print was on a weekday.** On 17 Sep at 13:14 New York, Lighter's SPY traded down to 717.68 from 763
  (−5.9%) and back within the same minute, on $4 million. Nothing in the history says how the bot's stop on Lighter
  or Lighter's own mark price behaved in that minute. A leg at 50x has 1% of room.

**What was decided (10 Oct 2026, the owner).** SPY, at the venues' highest leverage, closed and reopened every three
funding payments, the side taken from ProFunding. The highest leverage is now the default; the rest is three commands
(section 3a). What that buys at $240, on the history: about $141,000 of volume a day and $3,800 of open interest a leg
for **about $4.90 a day (2% of the account)**, with the position closed near its stop about twice on every trading
day (4a above) and a transfer by hand almost every working day (4.3 a week).

| At $240, SPY, every 3 h | Volume a day | Cost a day | Closed at or near the stop, a week | Transfers a week |
|---|---|---|---|---|
| `/arb_lev max max SPY` (the highest leverage) | $141,000 | $4.90 | 15.2 (0.6 of them with taker orders) | 4.3 |
| the same with `stop_early 0` | $156,000 | $7.30 | 10.7, all with taker orders | 4.6 |
| `/arb_lev 30 max SPY` | $90,000 | $3.10 | 1.2 | 2.7 |
| `/arb_lev 10 max SPY` | $33,000 | $1.10 | 0.1 | 0.1 |
| `/arb_lev 10 max SPY` with $1,000 | $136,000 | $4.70 | 0.1 | 0.1 |

The cost follows the volume: $34 to $35 per million dollars traded at any leverage, so $1 a day is about $29,000 of
volume. What the highest leverage adds is the closes near the stop and the transfers. More money at less leverage
makes the same volume without either.

Lighter's points terms for Robinhood Chain exclude "activity primarily intended to farm points rather than participate
in markets", along with wash and self-trading, and let Lighter remove points afterwards. A position closed and
reopened on a clock for the volume is for Lighter to judge under that rule; the bot's trades are with other people's
orders on two different venues, never with itself.

What these numbers rest on: 106 days of hourly prices for the replay; five days of Arcus's order book and **one day**
of both venues' books for the fills, none of them after 3 Oct; fills of $1,000 to $25,000. Record both venues' books
for a week (`tbot up` on the recorder) and run `arbitrage fills` again before sizing anything on them.

## 3. Commands

```
cd treading-bot/arcus        # then .venv/bin/arbitrage ... ; written as `arbitrage ...` below
arbitrage scan               # rank the markets, with the accounts' own free collateral
arbitrage scan --arcus 120 --lighter 120 --feeds
arbitrage plan BABA --arcus 120 --lighter 120
arbitrage history            # download both venues' funding and price history (about an hour; arbitrage/data/history)
arbitrage history --update   # after that: only the hours since (minutes)
arbitrage history --profunding   # ProFunding's record of the same rates, the last 30 days (a check; one request per market and venue)
arbitrage backtest --capital 240   # the rules replayed on all of it
arbitrage study --capital 240      # how long to hold: every holding time, the stop's distance, by weekday (section 2)
arbitrage history --minutes SPY QQQ   # both venues' prices by the minute (slow the first time)
arbitrage fills SPY QQQ            # what a limit order on Arcus and a taker order on Lighter cost, on recorded books
arbitrage cyclecost SPY QQQ            # the highest leverage, closed and reopened on a clock: cost, stops, transfers
arbitrage basis SPY QQQ            # Arcus's price against Lighter's by the minute, and over each weekend (section 2a)

arbitrage run --arcus 120 --lighter 120    # the executor on PAPER, in this terminal: real prices and funding,
                                           # simulated orders
arbitrage start --arcus 120 --lighter 120  # the same in the background (arbitrage stop stops it; the position is kept)
arbitrage status                           # what it is doing, the last events
arbitrage close      arbitrage close --now # close the position: as maker first, or with taker orders at once
arbitrage pause      arbitrage resume      # open nothing new (an open position is kept) / look again
arbitrage skip CASHCAT   arbitrage unskip CASHCAT   # markets it must never open
arbitrage lev max max SPY                  # leverage, margin a venue, market, in one line (section 3a)
arbitrage only SPY QQQ   arbitrage only all         # the markets it may open and no others / any market again
arbitrage cycle 3        arbitrage cycle off        # close and reopen every 3 funding payments (hours) / stop that
arbitrage side profunding   arbitrage side venues   # who decides which venue is short

arbitrage livetest [SYMBOL] [--what all|lighter|arcus|engine] [--expiry] [--hold 30]
                                           # REAL MONEY, smallest size, a report per part: the Lighter leg, the Arcus
                                           # leg, then the executor on both venues, both ways round (needs ARB_LIVE=1
                                           # and LIVE typed once; a part runs only if the one before passed)

arbitrage settings                         # every setting, its value, its range
arbitrage set max_hold_h 72                # change one; the running bot uses it from its next loop
```

### 3a. SPY at the highest leverage, renewed every three funding payments

```
arbitrage lev max max SPY     # leverage, margin a venue, market: SPY, the highest leverage, all the money
arbitrage cycle 3             # close and reopen every 3 funding payments (3 hours)
arbitrage plan SPY --arcus 120 --lighter 120   # what it would open now: side, leverage, size, stop
arbitrage start --arcus 120 --lighter 120      # PAPER first; `arbitrage status` shows it working
```

In Telegram: `/arb_lev max max SPY`, `/arb_cycle 3`, `/arb_plan SPY`, `/arb_start 120 120`. Live is
`/arb_start live` (section 6), after `arbitrage livetest` has passed on the Arcus leg and on the executor: neither
has run with real money yet.

**`lev LEVERAGE MARGIN [MARKET]`** is the whole choice in one line, and the reply says back what is now set:

| Command | What it sets |
|---|---|
| `/arb_lev max max SPY` | SPY only. The highest leverage both venues allow at that hour. All the money (90% of the smaller balance). ProFunding decides which venue is short |
| `/arb_lev max max` | No market named: **ProFunding's best stock, index or commodity** for the Arcus / LighterRH pair (its highest net % a year that passes the volume floor), and its side. Looked up again at every opening |
| `/arb_lev 30 100 SPY` | SPY, at most 30x, at most $100 of each venue's money as margin (a $3,000 leg) |
| `/arb_lev 30 max QQQ` | QQQ, at most 30x (QQQ's own highest is 25x, so 25x), all the money |
| `/arb_lev 10` | The leverage alone; market, margin and side stay as they are |
| `/arb_lev` | What is set now |

- The first word is the leverage (`max` or a number), the second the margin a venue in dollars (`max` = no limit),
  and a market name can stand anywhere. With a margin or a market in the line, ProFunding is made the judge of the
  side (`/arb_side venues` gives that back to the venues' own rates).
- It does not start the bot and does not set the clock: `/arb_cycle 3` and `/arb_start` stay their own commands, so
  that changing the leverage on a running bot cannot start or stop anything. A running bot uses the new line from
  its next position.
- **With no market named and the clock on, it opens ProFunding's best market at that market's highest leverage
  whatever it pays.** Only SPY and QQQ were replayed that way. On a single stock the highest leverage is lower
  (AAPL, NVDA 13x; META 6.7x while the stock market is closed) and so is the volume.

What it then does by itself:
- **Leverage.** Nothing sets it: the highest both venues allow at that hour, the same number on both. SPY is 50x
  while the stock market is open (Mon–Fri 04:00–20:00 New York) and 33x while it is closed, because Arcus allows
  less then; Lighter is set to the same 33x. A position open at the close is kept as it is until its renewal, at
  most 3 hours later. `/arb_lev 30` (or any number) asks for less, `/arb_lev max` for the highest again.
- **Size.** The smaller of the two balances × 90% × that leverage, the same number of units on both legs. A margin
  in the line (`/arb_lev max 100 SPY`) caps what each venue puts up.
- **Side.** ProFunding's `LighterRH` against `Arcus` row, read again at every renewal (at most every 30 minutes).
  If ProFunding cannot be read or does not list the market, nothing is opened and `/arb_status` says why.
- **Stop and take profit.** 0.33% from the entry at 50x and 0.83% at 33x, on both legs. From 80% of the way there
  the bot closes with limit orders (no fee); if the price gets to the stop first, the rest goes with taker orders
  at once. Both venues also hold a stop and a take profit of their own at the full distance, as market orders, for
  when the bot is down. It opens again in its next look, within 5 minutes.
- **Uneven money.** Only when the two balances are more than 20% of the money apart (one venue under 40%): it then
  opens nothing, says how much to move and from where, repeats that every 30 minutes, and goes on by itself once
  the money has arrived.

What it costs is in section 2a: about $4.90 a day at $240 for about $141,000 of volume, or $3.10 for $90,000 with
`/arb_lev 30 max SPY`. To go back to the usual way: `/arb_cycle off`, `/arb_side venues`, `/arb_only all`, and the three
settings named in section 2 under "How much leverage".

The same from the phone, in the one Telegram bot: `/arb_scan`, `/arb_status`, `/arb_hold 72`, `/arb_close`,
`/arb_start 120 120` ([section 7](#7-telegram)).

`status`, `close`, `pause` and `resume` act on the paper bot; add `--live` for the live one.

## 4. The rules

**Which markets.** Stocks, indices and commodities only. A market Arcus classes as crypto is never opened
(`rwa_only`, on by default; `arbitrage set rwa_only 0` allows every market again). This rule is the arbitrage's alone:
the market-making bots trade what you tell them.

**When it opens.** A market is opened only when all of these hold (`arbitrage scan` lists it under "Worth holding now"):
- the next payment, the last 24 hours and the last 7 days agree on which venue pays more;
- the smallest of the three pays at least `min_edge_apr` (5% a year on the position);
- that funding pays for getting in and out within `max_breakeven_h` (48 hours), at the two books' real spreads;
- both venues traded at least `min_volume_24h` ($100k) in it, and the size is above both minimum orders.

**How big.** Dynamic, from the accounts as they are at that moment:
- the smaller of the two venues' free collateral × `margin_use` (0.9) × leverage, the same number of units on both
  legs; `max_margin_usd` caps the margin a venue puts up (the second word of `arbitrage lev`), `max_notional_usd`
  the position itself (for a first small run);
- leverage is the highest both venues allow at that hour: Arcus asks 1.5 times the margin while the stock market is
  closed (SPY: 50x open, 33x closed), and the same number is set on Lighter every time a position is opened, so the
  two never differ. A position opened while the market was open is kept through the close as it is (Arcus allows
  that), and the next one is sized for the hour it opens in. `arbitrage set max_leverage 10` (Telegram `/arb_lev 10`)
  is how to ask for less; `/arb_lev max` is the highest again. `arbitrage set hold_off_hours 1` sizes every position
  for the off-hours margin, as before 10 Oct.

**The stop, dynamic unless you set one.** A stop and a take profit sit at the same distance on both legs: half
(`stop_frac`) of 1 / leverage less the maintenance margin, which is at most half the way to liquidation. At the
highest leverage that is near: 0.33% on SPY while the stock market is open (50x), 0.83% while it is closed (33x).
**From `stop_early` of the way there (0.8) the bot closes with limit orders**, Arcus as maker and Lighter taking what
Arcus fills, so that the close pays no taker fee; **at the stop itself whatever is left goes with taker orders**, and
the same watch runs during every other close with limit orders. `arbitrage set stop_early 0` leaves only the taker
orders at the stop (section 2a, 4a, has the measurements).
`arbitrage set stop_sigmas 3` also keeps it at least three daily moves from the entry, by lowering the leverage on a
market that moves a lot (the default until 10 Oct 2026; 0 = off). `arbitrage set stop_pct 2` replaces it with 2% (never past 80% of the way to
liquidation); `arbitrage set stop_pct auto` gives the dynamic one back. Both venues hold the orders themselves, as
market orders at the full distance, so they work while the bot is down; the bot checks the same distances and closes
both legs when either is reached. While the bot closes with limit orders the venues' orders are taken off (they
would fire into the close), and the bot is the stop, every second.

**How long.** `min_hold_h` (24): kept at least this long, so the funding can pay for the fills; a stop still closes
it. After that it is closed once the last 24 hours and the next payment both pay less than `exit_edge_apr` (0).
`max_hold_h` (0 = none) closes it after that many hours whatever it pays. All three can be changed at any time and
apply to the position already open: the time limit within one loop (about 10 s; checked on paper, 4 Oct: closed 7 s
after `arbitrage set max_hold_h`), the funding rule at its next check (every 5 minutes). With `arbitrage cycle 3` the
clock alone decides: `min_hold_h` and the funding rule are not looked at.

**Getting in and out.** Arcus charges takers 2.25 bp and Lighter charges nothing, so the limit order goes on Arcus:
post-only, at the best price, following it (`requote_s`). Lighter's leg rests no order. Every loop it takes, with a
taker order, exactly what Arcus has filled so far, so the two legs are never apart for longer than a loop
(`hedge_taker`, on; section 2a says why). An entry not finished after `enter_timeout_s` (600 s) is left at what has
filled, the same on both legs. An exit works the same way with reduce-only orders; one not finished by then is
completed with taker orders. A stop, a vanished leg or `arbitrage close --now` uses taker orders on both legs at once.
With `arbitrage set hedge_taker 0`, or if Lighter ever charged takers too, both legs rest limit orders as before: when
one has been ahead for `chase_s` (20 s) the missing part crosses, provided that costs no more than `max_cross_bp`.

**Only some markets.** `arbitrage only SPY QQQ` (Telegram `/arb_only SPY QQQ`) makes it look at those markets and no
others; `arbitrage only all` lifts it.

**Closing on a clock.** `arbitrage cycle 3` (Telegram `/arb_cycle 3`) closes the position every 3 funding payments,
which is every 3 hours, and opens it again, whatever it pays: the edge floor and the break-even check do not apply,
the volume floor, the stop and the crypto rule do. It makes volume and costs the fills and the stops (section 2a: $34
to $55 per million dollars traded on SPY). `arbitrage cycle off` ends it; `arbitrage cycle` says what is set.

**Which venue is short.** By default the venues' own funding rates decide. `arbitrage side profunding` (Telegram
`/arb_side profunding`) takes ProFunding's answer for the pair instead, its `LighterRH` against `Arcus` row, and
nothing else: a market it does not list, or an hour it cannot be read, is not opened. It is asked at most every 30
minutes and needs `PROFUNDING_API_KEY`. Over the last 30 days its rates and the venues' own pointed to the same side
of SPY in 94% of hours. `arbitrage side venues` goes back. **With ProFunding as the judge and no market named, it
also decides which market:** the candidates are its Arcus / LighterRH pairs in its own order (net % a year), crypto
left out, and the first that passes the rules is opened.

**The money on the two venues.** The legs are the same size, so the position is neutral: what one leg gains the other
loses, and the gain lands on one venue while the loss lands on the other. When a position is closed and one venue holds
less than `rebalance_share` of the money (0.40 = under 40%, which is the two balances more than 20% of the money
apart), the bot says at once, in Telegram, the exact amount to move
and in which direction (`MOVE $50.00 from lighter to arcus ... after it each has $100.00`), and repeats it every 30
minutes (`uneven_remind_min`) while it stays uneven. **It opens nothing until the money has been moved** (`uneven_wait`,
on), and it needs no telling when that is done: it reads both balances every 5 minutes while flat, says "the money is
even again" and goes on. `arbitrage set uneven_wait 0` makes it keep trading instead, sized by the smaller balance,
with the reminder every 6 hours. `arbitrage set drift_close_share 0.35`
adds a close before the stop: once an open position has moved that much of the money to one venue (35% / 65%), it is
closed with maker orders in the 15 minutes after the next funding payment, and the same message follows. It is off by
default because in the history it cost more than it saved (section 2): the stop already closes a position at about
30% / 70%. The bot cannot move the money itself (section 6a).

**What it never does.** Hold one leg without the other beyond those limits; open over a position it did not make;
keep a position the venues will not take stop orders for (it closes, pauses and says so); move money between venues.

## 5. Paper

`arbitrage start --arcus 120 --lighter 120` (or `/arb_start 120 120` from the phone) runs the whole executor against the
real books and the real funding payments, with orders that exist only in memory (a resting order fills when the other side of the real book reaches it). Its
position, its money and its settings live in `state/` and survive a restart. Run it for a few days before anything
else: `arbitrage status` shows the entries, the stops and each funding payment.

## 6. Live

Not started by anyone yet. Before the first run:

1. **Money on both venues.** `arbitrage scan` shows each venue's free collateral; the smaller one sets the size.
2. **An account of its own on each venue, or the market-making bots stopped.** Two programs trading one market on
   one account each treat the other's position as theirs. On Lighter also use an API key of its own.
3. `ARB_LIVE=1` in `arcus/.env`, then `tbot down` and `tbot up` so the Telegram bot sees it.
4. **Small first:** `arbitrage set max_notional_usd 30`, then `/arb_start live` in Telegram and type back the code it
   shows (or `arbitrage start --live` in a terminal and type LIVE). Watch `/arb_status`.

What the first live run will prove or disprove, because nothing could be sent while building it:

| Piece | State |
|---|---|
| Arcus post-only and IOC orders, cancel, cancel-all, leverage, positions, balance | the market-making bot's own client, live-proven there |
| Arcus: reading one order by its id | from the documentation. `arbitrage livetest --what arcus` checks it against Arcus's own list (written 2026-10-10, **not yet run**) |
| Arcus: the position stop and take profit (`positionTpsl`, signed as trigger orders) | from the documentation; the docs disagree with themselves on the leg's price field. The same test places the pair on a real position (**not yet run**) |
| Lighter: everything that sends (orders, cancels, stops, leverage) | signs correctly offline; never sent by this program. The Lighter bot's own test sent the same kinds of request on the venue on 2026-10-09 and 10 (lighter/README.md, section 6), including a stop and a take-profit built as here. `arbitrage livetest` sends them through this program's own adapter, once, at the smallest size: a maker order with its 5.5-minute expiry, the cancel and re-place the engine does before that expiry, a taker order, the position as the adapter reads it, the stop pair, cancel-all, the close. Its report is in `arbitrage/reports/`. **Run on 2026-10-10 (SPY): 9 of 9 passed, cost $0.0003, ended flat**; Lighter gave the maker order 328 s to live, and the adapter's own reading of orders, fills and the position matched Lighter's at every step |
| Lighter: the stop pair | counted as placed only when Lighter's own list shows both orders: Lighter answers OK to a batch and leaves out a member it does not like (seen on the venue) |
| The two-leg engine on both real venues | `arbitrage livetest --what engine`: one position each way round, entry, stops on both, hold, maker and taker exits, each step checked against the venues' own reads (`arbitrage/exec/drill.py`; 5 offline tests on simulated venues; **not yet run live**) |
| Lighter and Arcus reads (book, position, balance, key check) | run against the real accounts on 2026-10-04 |
| The executor's logic | 43 offline tests on simulated venues, and paper runs on real prices (open, stops, close by command and by the time limit) |

If a venue refuses the stop orders the bot closes the position, pauses and says so, instead of holding it unprotected
or opening it again.

## 6a. Moving money between the venues

Neither venue can send to the other, and neither bot does it. Both settle on the same chain (Robinhood Chain, id
4663) in the same token (USDG), so a move is two steps through your own wallet, with no bridge:

| Step | How | What must sign |
|---|---|---|
| Lighter → your wallet | "secure" withdrawal (`withdraw` in Lighter's SDK); about 5.5 minutes (`withdrawalDelay` 328 s) | the Lighter API key is enough; it can only go to the wallet that owns the account |
| Lighter → your wallet, fast | `fastwithdraw`, 1 USDG minimum | your wallet's private key as well |
| Your wallet → Lighter | call `deposit` on Lighter's contract, or send USDG to your Lighter "intent address"; 1 USDG minimum | your wallet (an on-chain transaction, gas) |
| Arcus → your wallet | `POST /v1/withdraw`, to the owning wallet only | your wallet (a typed-data signature); an ordinary API key is trade-only |
| Your wallet → Arcus | deposit into Arcus's vault contract (the web app's Deposit button) | your wallet (an on-chain transaction, gas) |

So a transfer could be scripted, but only by a program that holds **the wallet's private key**: that key can move
everything you own on both venues to any address, which is why it is not on the server and why the bot only tells you
the amount. If you want it automatic, the safe shape is a wallet used for nothing else, holding only the arbitrage's
money, with the script on your own computer. That script is not written: Arcus's deposit contract is not in the API
documentation, and nothing here has moved a dollar between the venues. Do the first transfer by hand with the smallest
amount (1 USDG) each way and note how long each step takes.

## 7. Telegram

There is no arbitrage Telegram bot: the one Telegram bot serves it (set up once, main
[README](../README.md) section 2). Its commands start with `arb_`; `/arb status` with a space works too, and `/arb`
shows the menu and which bot, paper or live, the commands act on. Every reply starts with **FUNDING ARB**, and
the running executor's own alerts (entering, open, a stop moved, closed, a venue refusing, money to move between
the venues) arrive in the same chat.

| Command | What |
|---|---|
| `/arb_status`, `/arb_scan`, `/arb_plan SPY`, `/arb_settings`, `/arb_feeds` | Read only: answered at once |
| `/arb_hold 72`, `/arb_minhold 24` | The longest and the shortest holding time, in hours. They apply to the open position within about 10 s |
| `/arb_sl 2`, `/arb_sl auto` | The stop and take profit: 2% from the entry on both legs, or the dynamic one |
| `/arb_only SPY`, `/arb_only all` | The markets it may open and no others; any market again |
| `/arb_cycle 3`, `/arb_cycle off` | Close and reopen every 3 funding payments (hours); stop doing so |
| `/arb_side profunding`, `/arb_side venues` | ProFunding decides which venue is short; the venues' own rates do |
| `/arb_lev max max SPY` | Leverage, margin a venue, market, in one line; ProFunding decides the side. `/arb_lev max max` = ProFunding's best market; `/arb_lev 30 100 SPY` = at most 30x and $100 a venue |
| `/arb_lev 10`, `/arb_lev max`, `/arb_lev` | The leverage alone: lower than the venues' highest; the highest again; what is set now |
| `/arb_set name value` | Any setting of `/arb_settings` |
| `/arb_pause`, `/arb_resume`, `/arb_skip CASHCAT`, `/arb_unskip CASHCAT` | Open nothing new (an open position is kept); markets it must never open |
| `/arb_close`, `/arb_closenow` | Close both legs: as maker first, or with taker orders at once |
| `/arb_start 120 120` | Start the PAPER executor with that much pretend money on Arcus and on Lighter (Confirm button) |
| `/arb_start live` | Start the LIVE executor: needs `ARB_LIVE=1` and the code it shows typed back within 2 minutes |
| `/arb_stop` | Stop the executor. The position, if any, stays with the venues' own stop orders |

- With no word after it, a command acts on the live bot when one runs or holds a position, else on the paper one.
  `/arb_status paper` or `/arb_close live` picks one.
- Whatever changes a **live** bot or its position asks with a Confirm button first. Paper changes are done at once.

## 8. Data sources

- **The venues themselves** are the source of every number: Arcus `/v1/markets` (last rate, its estimate of the
  next, when it is due), `/v1/fundingRates`, `/v1/candles`, `/v1/account`; Lighter `/api/v1/orderBookDetails`,
  `/api/v1/funding-rates` (an 8-hour figure: divide by 8), `/api/v1/fundings`, `/api/v1/account`. A scan costs 1
  Arcus and 2 Lighter list calls, plus history for the candidates the first time (kept in `state/history/`).
  Lighter's 60 requests a minute are counted per IP too: the scanner sends one every 3 s at most.
- **ProFunding** (`PROFUNDING_API_KEY`): a cross-check, read once every 15 minutes at most (the free key allows 100
  requests a day). `arbitrage history --profunding` saves its last 30 days of hourly rates for both venues, which is
  all it gives; over those days a market's average differs from the venue's own by 0.01% a year on Arcus and 0.15% on
  Lighter (section 2). Its trade endpoints work by storing exchange keys on its servers; this program never sends it
  one.
- **arb.sh**: its refresh time and whether the pair is in its top 50. Undocumented, so nothing depends on it.

## 9. Files

| Path | What |
|---|---|
| `arbitrage/rank.py` | the arithmetic: which venue to short, size, leverage, stop, when to leave (pure, tested) |
| `arbitrage/scan.py`, `arbitrage/venues.py` | one scan; the venues' public endpoints |
| `arbitrage/history.py`, `arbitrage/backtest.py` | the history download; the rules replayed on it |
| `arbitrage/exec/engine.py` | the executor's state machine |
| `arbitrage/exec/venue.py` | what the executor needs from a venue; the simulated and the paper venue |
| `arbitrage/exec/arcus.py`, `arbitrage/exec/lighter.py` | the live venues, on the two bots' own clients |
| `arbitrage/exec/run.py` | the loop, its files, paper funding |
| `arbitrage/ops.py` | the executor as a background process (`arbitrage start`, `arbitrage stop`, `/arb_start`, `/arb_stop`) |
| `arbitrage/telegram.py`, `arbitrage/feeds.py`, `arbitrage/paper.py`, `arbitrage/cli.py` | its commands for the one Telegram bot; ProFunding and arb.sh; the paper note-book of `arbitrage paper`; the commands |
| `state/`, `data/` (not committed) | `position-<mode>.json`, `events-<mode>.jsonl`, `run-<mode>.pid` and `.out`, paper money; the downloaded history |
| `settings.json` (not committed) | your settings. The account ids and switches are in the one `arcus/.env` |

Install: `make install` in the repository root. Tests (offline): `make test` in the root, or from `treading-bot/arbitrage`:
`../arcus/.venv/bin/python -m pytest`.
