# 2026-09-27: When to trade, not only what: sessions, market state, events, and the autopilot

The owner asked for:
- a bot that picks the setup itself by the time of the week (Asia, London and New York opens, weekdays against weekends);
- a bot that stays out of news and earnings on its own;
- a bot that copes with trending and volatile markets;
- a bot that can run for a month untouched.

This note is the research behind `/auto` (bot/scout/autopilot.py).

Costs below are backtest costs in bp (dollars lost per $10,000 traded). The backtest costs about 1.25x what live BTC
runs cost (note 2026-09-26, section 3).

## 1. Data

- **Recorded books (best bid and offer, depth):** Sep 19–26 only, all markets.
- **Trades:** late June to Sep 26 for every market (the Arcus trade history).
  - Arcus BTC was still thin until the end of July: taker flow went from $0.1M to $30M a day between weeks 27
    and 32.
  - So everything below uses **Aug 3 onward**: 8 weeks, 39 weekdays and 16 weekend days.

### A book rebuilt from trades

BTC and SPY sit at a one-tick spread 86% and 84% of the time, so their book can be rebuilt from trades alone: one tick
around the last print. Checked against the recorded book on the days that have both, same hours, same setups:

| | Weekdays (Sep 21, 22, 24, 25) | Weekend (Sep 20, 26) |
|---|---|---|
| BTC Mid 0 | volume within 1%; 1.86 vs 1.68 bp; hourly correlation 0.99 | 1.50 vs 1.42 bp |
| BTC Mid +1, +2 | volume 30–40% high, cost within 0.15 bp | same |
| SPY Mid 0 | 1.41 vs 1.18 bp; correlation 0.98 | 1.23 vs 1.03 bp |
| SPY Mid +1, +2 | close on weekdays | **overstated**: Mid +1 1.58 vs 0.65 bp |

So the rebuilt book can stand in for the weeks without a recorded one, for BTC (any setup) and SPY Mid 0. It is a
little pessimistic on cost, and more so for SPY's wider spreads at weekends. Other markets (ETH trades one tick only
53% of the time, the stocks less) use recorded books only.

## 2. The clock: sessions and weekdays

BTC Mid 0 at 40x, each hour backtested from flat. The sessions follow local clocks (bot/scout/sessions.py); summer
times are shown in UTC:

| Session (UTC, summer) | Volume per hour | Cost |
|---|---|---|
| Weekend (Fri 21:00 to Sun 22:00) | $150k | **1.36–1.50 bp** |
| Asia (00:00–07:00) | $180k | 2.27 bp |
| London (07:00–13:30) | $215k | 1.86–1.88 bp |
| US open (13:30–16:00) | $330–450k | 2.29–2.43 bp |
| US afternoon (16:00–20:00) | $240k | 2.0 bp |
| US evening (20:00–00:00) | $160k | 1.9–2.0 bp |

- Monday's opens cost the same as other days' opens (Asia 2.36 vs 2.27, London 1.86 vs 1.88, NY 2.43 vs 2.29 bp). No
  separate "Monday rule" is needed.
- Sunday evening (20:00–24:00 UTC), before the US futures reopen, costs 2.18 bp against Saturday's 1.36. It counts as
  the start of the week.
- The US open trades 2.5x as much as a quiet hour and moves 2.3x as much.

## 3. The market's state matters more than the clock

The volatility of the hour *before*, against its usual level at that hour, predicts the next hour's cost in every
session:

| Session | Calm (< 0.75x) | Normal | Busy (1.25–2x) | Wild (> 2x) |
|---|---|---|---|---|
| Weekend | **0.62 bp** | 1.22 | 1.66 | 2.29 |
| Asia | 1.77 | 1.93 | 2.27 | 2.70 |
| London | 1.57 | 1.79 | 1.99 | 2.64 |
| US open | 2.09 | 2.24 | 2.35 | 2.60 |
| US afternoon | 1.79 | 2.00 | 2.24 | 2.33 |
| US evening | 1.43 | 1.79 | 2.05 | 2.80 |

The same holds for the hour's move and its largest one-minute move. Tested out of sample (table fitted on August,
applied to September):
- Skipping only the wild hours or only the US open barely changes the cost (1.81 and 1.77 vs 1.83 bp).
- Running only where the table predicts a low cost does: 1.27–1.40 bp for the hours it keeps.

## 4. Events and earnings

- **CPI, jobs report, FOMC hours:** $400k an hour at 2.67 bp against 1.98 bp for all hours. The hour before is quiet
  and cheap.
  - The bot already stops quoting 30 minutes either side.
  - The autopilot goes flat 15 minutes before that window opens.
  - The 2026 CPI and jobs dates are now in config/calendars/events.csv, from bls.gov.
- **Earnings, on the stock perps** (18 reports, July to September, dates from Nasdaq's calendar):

| | Earnings day, against a normal day |
|---|---|
| Median volatility of the worst of the day and the next | 2.5x |
| Largest one-minute moves | 3–6x normal: AMD 912 bp (Aug 4), INTC 747, ORCL 751, CRWV 716, META 671 |

A 9% minute is a liquidation at 20x, so staying out is not optional. `config/calendars/earnings.csv` was empty until
now: nothing stopped the bot. The scout now fetches the dates every day (bot/core/earnings.py). The bot and the
autopilot stay out of a stock from 24 h before its report to 24 h after.

## 5. Trending and volatile markets

Tested on 8 weeks of BTC, whole days so the signals have history:

| Idea | Result |
|---|---|
| Bias with the last hour's trend (automatic Long/Short, several lookbacks and thresholds) | 2.20 vs 2.22 bp on trend days (days moving over 2%), nothing overall |
| A fixed bias with the previous hour's trend, hour by hour | Mid 0 2.11 vs 2.19 bp (neutral) vs 2.26 bp (against it); Grid +3 1.82 vs 2.68 bp, on a fifth of the volume |
| Wider spreads when wild (Mid +2, Mid +3) | About 20% cheaper (2.12 vs 2.59 bp), at a quarter to a third of the volume |

A market maker loses in a trend through its fills: the takers who hit its quotes know where the price is going, so
every fill is marked down within a minute. That happens to a Long bias as much as a Neutral one, so a bias hardly
helps. Trend days cost 23% more than range days whatever the setup.

For a small budget, the answer to a trending or wild market is:
1. wait (a calm hour costs half as much);
2. or trade a market that is calm now.

Wider spreads help only when the budget is large enough that it would not be spent otherwise. The playbook covers
that case, since Mid +2 and +3 are in it.

## 6. Other markets, by session

SPY is expensive most of the day (about 2.2 bp) but cheap in the **US afternoon** (16:00–20:00 UTC), when the perp
follows the index:

| Setup | Volume per hour | Cost |
|---|---|---|
| Mid +3 | $16k | 0.64 bp |
| Grid +3 | $10k | 0.36 bp |
| Mid +2 | $19.5k | 1.15 bp |

QQQ, NVDA and GLD look cheap in some sessions on their 6 recorded days. Their tables fill in as the scout records
more.

## 7. The real constraint: a small budget

At about $100 of capital, running BTC Mid 0 all day would cost about $1,100 a day in the backtest. The account can
spend a few dollars a day, so the question is **when** to spend them.

With a fixed daily budget (September, BTC Mid 0, table fitted on August):

| Daily budget | Start 00:00 UTC, run until spent | Random start | Only predicted-cheap hours, weekly pot | Best hours in hindsight |
|---|---|---|---|---|
| $6 | $34k/day | $33k | **$45k** | $68k |
| $10 | $56k | $68k | **$76k** | $111k |
| $20 | $113k | $114k | **$174k** | $211k |

"Weekly pot" means unspent money carries over, so a quiet weekend can use what a busy weekday did not.

## 8. The autopilot, rehearsed

The real code (bot/scout/autopilot.py):
- ran minute by minute over Sep 12–25;
- used the server's tape for the market's state;
- used a playbook built from earlier days only;
- simulated runs from the hourly backtests.

Its cost ceiling was tuned the same way, on earlier days only.

| Budget | Autopilot | BTC Mid 0 from 00:00 UTC until spent |
|---|---|---|
| $5/day | **$40k/day at 1.23 bp** | $26k/day at 1.91 bp |
| $10/day | **$74k/day at 1.35 bp** | $52k/day at 1.91 bp |
| $20/day | **$125k/day for $16.84 a day** | $107k/day for $20 |

Across the three budgets that is 17–54% more volume for the same or less money.

What it did:
- **Crypto:** BTC Mid 0 on calm weekends; BTC Mid +2/+3 on some weekday evenings.
- **SPY:** Mid +2/+3 in quiet Asia, London and US-afternoon hours.
- **Events:** flat around the Sep 16 FOMC.

The best ceiling depends on the budget:
- a small budget buys most by waiting for the cheapest hours (about 1.3 bp);
- a large one needs a higher ceiling to be spent at all.

So the playbook replays the rule over the last 4 weeks at every ceiling, each day, and keeps the best one for each
budget.

## 9. What was built

| Part | Where | What it does |
|---|---|---|
| Sessions | bot/scout/sessions.py | Weekend, Asia, London, US open, US afternoon, US evening, by each city's clock |
| Market state | bot/scout/regime.py | Last hour's volatility vs usual → calm / normal / busy / wild; a one-minute move over 6x usual → shock, 30 min out |
| Playbook | bot/scout/playbook.py | Hourly backtests (last 42 days, 8 markets, 7 setups) → volume and cost per market, setup, session and state; the tuned ceilings |
| Earnings | bot/core/earnings.py | The stock perps' report dates from Nasdaq, daily; the live bot re-reads them hourly |
| Autopilot | bot/scout/autopilot.py | Every minute: the pot, the market states, the events, the pick; starts, switches, stops; one message per action |
| `/auto` | Telegram, `bot auto` | On/off (LIVE: typed code, doctor before each start), budget, ceiling, what it does now and in the next 24 h |

Also:
- The pilot's offers and pauses stand aside while the autopilot runs.
- Your own `/run`, `/stop`, `/closeall`, `/cancelall` or `/pilotclose` turns it off, and so does a run you start from
  the shell.

## 10. Limits

- **The playbook is a backtest.** It estimates hours from the past 6 weeks. A new kind of market (a crash, a listing
  frenzy) will be misjudged until it shows up in the data. The shock rule and the stops still apply.
- **Only BTC and SPY have weeks of history.** For the other markets, the rebuilt book is not trusted, so they rely on
  recorded books; those days add up as the scout records.
- **Fees and the maker/taker split are modelled.** Real fills can differ; live BTC has cost 0.8x the backtest so far.
- **Each run stops at what is left of the pot, and at most 3 days of budget.** The daily stop and the kill (% of the
  capital) still apply on top of that.
