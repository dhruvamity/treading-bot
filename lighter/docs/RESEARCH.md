# Lighter on Robinhood Chain: what the Arcus setups do there, and what changes

2026-09-27. The question: take the Arcus bot's market-making setups (Mid, Grid, Smart, a directional bias, run
limits), move them to Lighter on Robinhood Chain, where a standard account pays no fees at all, and find what works
better or worse there.

Costs are in bp: dollars lost per $10,000 traded. A negative cost is a profit. "At $100" means orders sized from $100
of capital at the market's maximum leverage, the way the bot sizes itself (order = capital × leverage ÷ 2.5).

**Short version.**
- **Lighter is 3–10 times cheaper per dollar traded than Arcus for the same kind of setup.**
  - The best Arcus setups cost 0.8–1.7 bp. On Lighter the same ideas cost −0.1 to 0.3 bp on SPY, ETH, NVDA, QQQ
    and BTC.
  - Why: no taker fee, spreads a few ticks wide in bp, and flow that is less informed.
- **Smart and "Mid a little away from the touch" are the best setups there.**
  - SPY Smart +1: −0.11 bp on $2.2M a day. ETH Smart +0.5: 0.00 bp on $9.3M a day. NVDA Smart +1: 0.01 bp.
  - That is at $100 of capital, steady state, on one recorded day.
- **The constraint is the request budget, not fees.**
  - A standard account may send 60 requests a minute in all.
  - Fresher quotes cost less: a 0.5 s loop with 54 requotes a minute cut costs by 0.05–0.35 bp against a 1 s loop
    with 44. The bot now runs that way.
- **Taking liquidity is free, but it still costs the spread.**
  - Taker exits and position stops are cheap on Lighter.
  - Deliberately crossing (closing any position after N seconds) still lost to plain quoting.
- **HYPE, TSLA and SOL are expensive** (0.4–3 bp): their flow is informed.
- **The evidence is one recorded day** (Sep 24, 18 hours, 8 markets, full depth).
  - The rankings held under every variation tried, but dollar figures need the week of recording the server will
    now make.
  - Paper first, then small live.

## 1. What Lighter on Robinhood Chain is, for a market maker

From the API docs in `lighter_rh_docs/` and the live API:

| | Arcus | Lighter RH (standard account) |
|---|---|---|
| Maker fee | 0 | 0 |
| Taker fee | 2.25 bp | **0** |
| Speed | orders in ~150–180 ms | a **speed bump**: maker orders, modifies and cancels land 200 ms after Lighter receives them, taker orders 300 ms. Premium accounts skip it but pay 1.2 bp maker / 3.5 bp taker |
| Request limit | an order pool that grows with volume | **60 requests a minute** for everything (reads and transactions) |
| Batches | batch place/cancel | `sendTxBatch`: up to 50 transactions in one request |
| Modify | refused live (cancel + place) | a real modify, by our own client order id |
| Dead man's switch | `scheduleCancel` | a scheduled cancel-all, at least 5 minutes ahead |
| Ticks | one-tick books on BTC and SPY | BTC tick 0.012 bp and a spread of ~90 ticks (1 bp); SPY 0.13 bp and 2 ticks |
| Leverage | BTC 40x, SPY 50x, stocks 10–20x | BTC, ETH, SPY, QQQ 50x; SOL, gold, silver 25x; big stocks 20x; others 3–10x |
| Minimum order | $5 | $10 (or the minimum size × price, e.g. BTC $17) |
| Signing | EIP-712 | Schnorr over a Goldilocks field with Poseidon2, through Lighter's own library |
| Points | — | a live points program (`livePoints/total`) |

57 perps were listed on 2026-09-27. By 24-hour volume: SPY $44M, ETH $42M, BTC $41M, QQQ $31M, gold $23M, and
ANTHROPIC $22M (a pre-IPO perp). All fees read 0 and none has session hours.

## 2. Data

- **2026-09-24, 03:01–21:13 UTC (18.1 h)**, 8 markets (BTC, ETH, SOL, HYPE, SPY, QQQ, NVDA, TSLA):
  - the best bid and offer on every change, every trade, the full order book's changes, mark, index and funding;
  - recorded by the old Docker-era recorder (`research/treading-bot-data/data/*/venue=lighter_rh`);
  - imported with `lbot tape import` (the depth rebuilt once a second; the rebuilt top of book matches the ticker
    93–99% of the time at the same instant).
- **2026-09-27** (Sunday): short recordings (1–2 minutes) with the new recorder, to check it (57 markets, no gaps).
  The scout on the server records from now on.

| Market | Tick | Median spread | Spread in ticks | Taker flow per hour | Median trade | 99th pct trade |
|---|---|---|---|---|---|---|
| BTC | 0.012 bp | 1.08 bp | 91 | $4.6M | $101 | $14.2k |
| ETH | 0.037 bp | 1.71 bp | 46 | $3.0M | $264 | $15.0k |
| SOL | 0.087 bp | 2.27 bp | 26 | $0.65M | $101 | $10.0k |
| HYPE | 0.108 bp | 1.96 bp | 18 | $0.62M | $49 | $6.4k |
| SPY | 0.131 bp | 0.26 bp | 2 | $5.0M | $163 | $9.4k |
| QQQ | 0.135 bp | 0.54 bp | 4 | $2.6M | $100 | $9.1k |
| NVDA | 0.447 bp | 2.68 bp | 6 | $0.29M | $66 | $9.6k |
| TSLA | 0.264 bp | 3.18 bp | 12 | $0.10M | $110 | $6.0k |

## 3. The flow is less informed than on Arcus

The first thing to check: does a maker lose on every fill, as on Arcus? Every trade on Sep 24 is a maker fill for
someone. Here is the maker's gain against the mid 1, 10 and 60 seconds later, weighted by dollars:

| Market | At fill (half the spread earned) | 1 s | 10 s | 60 s |
|---|---|---|---|---|
| BTC | +0.71 | +0.41 | +0.38 | **+0.58** |
| ETH | +1.06 | +0.67 | +0.60 | **+0.32** |
| SOL | +0.95 | +0.33 | +0.23 | +0.95 |
| HYPE | +1.68 | +0.70 | +0.50 | +1.76 |
| SPY | +0.40 | +0.25 | +0.17 | **+0.11** |
| QQQ | +0.50 | +0.29 | +0.19 | +0.08 |
| NVDA | +1.34 | +0.78 | +0.44 | +0.04 |
| TSLA | +1.70 | +0.85 | +0.81 | −1.72 |

**The average maker on Lighter keeps a profit after a minute on every liquid market.** On Arcus the same measure lost
0.9 bp (SPY) and 1.4 bp (BTC). Two reasons:
- the spreads are several ticks wide, so half the spread is worth more;
- the takers are less informed (retail flow, and flow that trades for points).

**But that average is not ours.** Most of these makers are premium accounts: no speed bump, and they pay 1.2 bp,
so their edge after fees is negative. We pay nothing, but our quotes land 280 ms late (the 200 ms bump plus the trip
to Tokyo), and a late quote is the one that gets picked off. Only the backtest can say what is left for us.

## 4. The backtest

`lbot/scout/sim.py`, written for Lighter:
- **Same code as live.** It runs the running bot's own quoting (`lbot/trade/strategy.py`) and stops
  (`lbot/trade/guard.py`), so the backtest and the live bot cannot quote differently.
- **Timing.** Decisions twice a second.
  - A new order, a modify or a cancel lands 280 ms later; until then the old order keeps filling.
  - A post-only order that would cross when it lands is cancelled, as Lighter does (`canceled-post-only`).
- **Requests.** One requote (every change of both sides) is one request. At most 54 a rolling minute; a requote that
  does not fit is skipped.
- **Fills: queue model with the recorded depth.**
  - An order at a better price than the book has nothing ahead of it.
  - One that joins a price waits behind the size shown there when it landed (never more than is shown later).
  - A taker transaction fills it for what it printed at our price beyond that queue, plus everything it printed
    through our price. With our order there, the taker would have met us first.
- **Taker orders** land 380 ms later and walk the recorded depth. Fees are the market's (0). Hourly funding at the
  recorded rate; liquidation below the maintenance margin.
- **Two framings:**
  - steady state, used for comparing setups: whole days, a 3% position stop, no daily stop or kill, a large balance
    so nothing is liquidated;
  - the owner's stops, used for the lists: each UTC day from flat, with the position, daily and kill stops.

**How much the model's assumptions matter** (steady state, cost in bp, 1 s loop):

| Market, setup | Latency 80 ms | 280 ms (the model) | 600 ms | Queue inside the spread: none / half the touch / all of it |
|---|---|---|---|---|
| BTC Mid +0.5 | 0.30 | 0.38 | 0.45 | 0.38 / 0.39 / 0.39 |
| ETH Smart +1 | 0.06 | 0.11 | 0.15 | 0.11 / 0.11 / 0.11 |
| SPY Mid +1 | −0.05 | −0.01 | +0.06 | −0.01 / −0.01 / −0.01 |
| QQQ Mid +1 | 0.15 | 0.17 | 0.21 | 0.17 / 0.16 / 0.16 |

- **Latency matters:** about 0.05–0.1 bp per 300 ms. The bot is best run close to Tokyo, or at least not far from it.
- **Where the queue is assumed to start barely matters.** Most of our fills are takers trading through our price.
- **Rankings did not change under any variant.**

## 5. Every setup, every market (steady state, $100 at max leverage, the bot's final defaults)

Cost in bp (negative = profit):

| Setup | BTC | ETH | SOL | HYPE | SPY | QQQ | NVDA | TSLA |
|---|---|---|---|---|---|---|---|---|
| Mid 0 | 0.45 | 0.32 | 0.78 | 1.19 | 0.16 | 0.33 | 0.75 | 1.90 |
| Mid +0.25 | 0.32 | 0.15 | 0.68 | 1.17 | 0.07 | 0.21 | 0.61 | 1.46 |
| Mid +0.5 | 0.25 | 0.03 | 0.41 | 0.95 | 0.02 | 0.18 | 0.41 | 1.45 |
| Mid +1 | 0.31 | 0.03 | 0.39 | 1.38 | −0.07 | 0.12 | 0.30 | 1.67 |
| Mid +2 | 0.54 | 0.11 | 0.55 | 1.38 | 0.60 | 0.25 | 0.15 | 1.71 |
| Mid +3 | 0.72 | 0.60 | 0.67 | 1.72 | 1.98 | −0.13 | 0.25 | 3.16 |
| Smart 0 | 0.44 | 0.32 | 0.64 | 1.25 | 0.13 | 0.30 | 0.44 | 1.37 |
| **Smart +0.5** | **0.24** | **−0.00** | 0.43 | 1.20 | **−0.02** | 0.12 | 0.15 | 1.33 |
| **Smart +1** | 0.38 | 0.04 | **0.34** | 1.49 | **−0.11** | **0.07** | **0.01** | 1.29 |
| Smart +2 | 0.52 | 0.11 | 0.60 | 1.57 | 0.57 | 0.16 | 0.26 | 2.51 |
| Touch 0 | 0.41 | 0.19 | 0.66 | 1.96 | 0.16 | 0.38 | 0.78 | 1.86 |
| Touch +0.5 | 0.47 | 0.33 | 1.08 | 2.11 | 0.03 | 0.19 | 2.14 | 3.34 |
| Grid 0 | 0.67 | 0.69 | 0.84 | 1.88 | 0.26 | 0.48 | 1.93 | 2.59 |
| Grid +1 | 0.58 | 0.42 | 1.22 | 1.48 | 0.20 | 0.45 | 1.10 | 2.38 |
| Grid +2 | 0.56 | 0.29 | 1.01 | 1.00 | 0.59 | 0.11 | −0.03 | 3.35 |

Volume per day at $100 (millions):

| Setup | BTC | ETH | SOL | HYPE | SPY | QQQ | NVDA | TSLA |
|---|---|---|---|---|---|---|---|---|
| Mid 0 | 18.9 | 13.3 | 3.5 | 2.0 | 28.7 | 14.7 | 1.2 | 0.5 |
| Mid +0.5 | 12.3 | 11.5 | 2.9 | 1.8 | 6.6 | 4.6 | 0.9 | 0.4 |
| Mid +1 | 7.5 | 7.9 | 2.4 | 1.6 | 3.0 | 2.7 | 0.7 | 0.4 |
| Smart +0.5 | 10.0 | 9.3 | 2.4 | 1.4 | 5.0 | 3.5 | 0.8 | 0.4 |
| Smart +1 | 5.5 | 5.9 | 1.9 | 1.2 | 2.2 | 1.9 | 0.5 | 0.3 |
| Touch 0 | 11.4 | 7.7 | 1.8 | 1.5 | 17.7 | 9.1 | 0.5 | 0.3 |
| Grid 0 | 7.7 | 6.7 | 1.9 | 1.2 | 5.6 | 4.6 | 0.5 | 0.3 |

What it says:
- **Mid 0 is not the best Lighter setup, unlike Arcus BTC.**
  - On Arcus, Mid 0 joined a one-tick touch. On Lighter's crypto books it sits at the mid, dozens of ticks inside
    the spread, where the fills are the most informed (edge −0.16 bp at the fill on BTC, then −0.46 bp at a minute).
  - Half a bp to 1 bp from the mid, near or just behind the touch, is the cheapest.
- **Smart pays more here than on Arcus.**
  - It leaves out the side the book leans against, or the one the price just moved against.
  - It beats Mid at the same spread on 6 of 8 markets at +0.5 bp and 5 of 8 at +1 bp, by up to 0.3 bp (NVDA).
  - On Arcus it saved 0–25%.
- **Touch (join the best price) is dominated by Mid** at a similar volume. It stays available in `/run`.
- **Grid costs more than Mid everywhere except NVDA Grid +2**, as on Arcus.
- **SPY and QQQ at +1 to +3 bp are at or past breakeven**, the same index-anchoring effect the Arcus research found
  (SPY Smart +3, QQQ Mid +3 there). On Lighter it comes with 5–20 times the volume.

## 6. What was tried to make it cheaper

All steady state, 6 markets (BTC, ETH, SOL, SPY, QQQ, NVDA), 1 s loop unless noted. Cost in bp.

| Idea | Result | Kept? |
|---|---|---|
| **Faster quotes: 0.5 s loop, 54 requotes a minute** (vs 1 s, 44) | Cheaper on every market. ETH Smart +0.5 0.11 → −0.02; BTC Mid +0.5 0.33 → 0.24; NVDA Smart +1 0.36 → −0.02; QQQ Smart +1 0.22 → 0.08; SPY Smart +1 0.00 → −0.10. With 30 requotes a minute costs rise 0.05–0.25 | **Yes: the default** |
| Inventory skew κ (reservation price) | At +0.5 bp none is cheapest (BTC 0.33 vs 0.38; SPY 0.04 vs 0.07); from +1 bp, κ 0.5–1 is best | κ 0 up to +0.5, 1 above |
| Smart's thresholds (book imbalance 0.3–0.9, move 0.3–1 bp over 5 s) | Within 0.05 bp of each other | Arcus's 0.6 and 0.5 bp |
| Requote tolerance 0.1 / 0.25 / 0.5 / 1 bp | 0.1–0.25 equal, 1 bp clearly worse (BTC 0.38 → 0.54) | 0.25 bp (fewer requests) |
| Close any position after 10 / 30 / 120 s with a taker order (free on Lighter) | More volume (up to 1.6x) but always dearer (ETH Mid +0.5 0.16 → 0.46) | No |
| Position stop 1% / 3% / 10% | Similar cost: exits are free of fees here. Smart +0.5 at 1% was a little cheaper on BTC and NVDA | 2% |
| Long / Short bias | Mixed, following the day's drift (Sep 24 fell): a view on the price, not an edge, as on Arcus | Neutral; the lists show a bias only once a market has 3 recorded days |
| Lower leverage (5x, 10x, 20x) | About the same cost per dollar, proportionally less volume | Max leverage, as on Arcus |
| Hour of day | Crypto is flat across the day. SPY and QQQ are dearest around the US open (Sep 24, 15–17 UTC: SPY Mid +1 +0.4 bp against −0.3 to 0 in the European hours) | The autopilot waits for calm markets |

## 7. With the owner's stops: the lists

The scout ranks at the account's capital, each market's maximum leverage and the owner's stops (Lighter defaults:
position 2%, daily 5%, kill 25%). Each UTC day starts flat. Sep 24 at $100:

| List | #1 | #2 | #3 |
|---|---|---|---|
| 🚀 Most Volume ($0.10 per $1,000 budget) | SPY Smart +0.5 @ 50x: $3.7M a day, +$14 | ETH Mid +1.5 @ 50x: $0.8M, −$6 (the daily stop) | QQQ Smart +1 @ 50x: $0.8M, −$5 |
| 💎 Cheapest | SPY Smart +1 @ 50x: $1.7M, +$19 | NVDA Smart +1 @ 20x: $0.4M, −$0.7 | QQQ Smart +1 @ 50x |

One day, in sample: expect less. On Arcus, several 3-day winners (HYPE, XRP) turned negative over five days, and every
setting of an overnight "paper farm" lost on other nights.

**Why the kill is 25% on Lighter, not Arcus's 10%.** At 50x on $100, a full position moves $15 on a 37 bp move. A
15% kill fired on days that were well up and then gave some back: it disqualified most of the best setups for a
gain.

## 8. Lighter against Arcus, the same idea on each

| | Arcus (backtest, then live) | Lighter (backtest, this note) |
|---|---|---|
| BTC at the touch (Mid 0 on Arcus, Mid +0.5 on Lighter) | 1.7 bp backtest, ~1.05 bp live (3 runs, $113k) | 0.25 bp |
| SPY, fastest setup | Mid 0: 1.1–1.6 bp backtest, 0.35 bp live (one night, $330k) | Mid +0.5: 0.02 bp, $6.6M a day at $100 |
| SPY, cheapest | Smart +3: +$2 a day on $35k (8 days, unproven) | Smart +1: −0.11 bp on $2.2M a day (1 day) |
| ETH | 2.0–2.3 bp | Smart +0.5: 0.00 bp |
| A taker exit | 2.25 bp + the spread | the spread only |
| Queue position | a one-tick touch: behind everyone | fine ticks: a quote half a bp from the mid is often the best price |

## 9. Limits

- **One recorded day.** Sep 24 was a Thursday with a falling market. Weekends, US holidays and busy days are not in
  it. The scout on the server records every market from now on; judge the lists once each market has a week.
- **The market does not react to our orders in a backtest.**
  - At its busiest, the backtest takes 2–24% of a market's trading (Mid 0 the most, Smart +1 the least).
  - On Arcus the same kind of estimate held within 4–17% of live volume.
  - On Lighter, premium makers with no speed bump may step in front of a quote inside the spread. Their 1.2 bp fee
    makes that costly for them, but it is not measured. The setups half a bp or more from the mid depend least on
    it.
- **No live run on Lighter yet.** Paper uses the same fill rules on the live feed, so it tests the plumbing and the
  day's market, not the fill model. The first small live run is the calibration.
- **The request rules.** The docs give standard accounts 60 requests a minute. A table of per-transaction-type
  limits lists a "default" of 40 a minute whose scope is unclear. If Lighter answers 429, the bot pauses 60 s and
  requotes 20% less from then on (never under 20 a minute).
- **About 120 setup × market cases were compared on this one day,** so a few look good by chance. Sections 5–6 name
  setups that were best on most markets, not the single best cell.

## 10. Reproduce

```bash
cd treading-bot/lighter
make install
.venv/bin/lbot tape import ../research/treading-bot-data/data
.venv/bin/python scripts/research.py --setups "mid 0.5,smart 0.5,smart 1" --margin 1000000
.venv/bin/python scripts/research.py --markets SPY --setups "smart 1" --margin 1000000 --cfg step_us=500000,1000000
.venv/bin/lbot scout scan --capital 100
```
