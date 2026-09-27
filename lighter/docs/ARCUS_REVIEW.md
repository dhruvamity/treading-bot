# The Arcus bot's strategies: how accurate, how well they did

2026-09-27. A review of `treading-bot/bot` (unchanged by this work):
- what its setups are;
- how well its backtest predicts live trading;
- what the live runs actually made.

Sources:
- the code (`bot/strategies/`, `bot/scout/sim.py`, `bot/scout/scan.py`, `bot/scout/autopilot.py`);
- the research notes in `bot/docs/notes/` and the incident reports in `bot/docs/incidents/`;
- the server's data copy in `research/treading-bot-data/` (live fills, balances, tape to Sep 26);
- an independent check of those fills against the recorded tape.

## 1. What the Arcus bot runs

| Setup | What it quotes | Backtested |
|---|---|---|
| Mid s | both sides `s` bps from the mid, following it; skew against the position for s ≥ 1 | −1, 0, +1, +2, +3, +5 |
| Grid s | around the last fill: a sell never below the last buy + s; soft reset at 0.5% | 0, +1, +2, +3, +5 |
| Smart s | Mid, less the side that would add to the position while the book leans against it (imbalance < −0.6) or the price just moved against it (0.5 bp in 5 s) | 0, +1, +2, +3 |
| Bias | Long or Short holds half the position cap on that side | each Mid and Grid |
| Run limits | `sl=` loss limit, `tp=` take profit, `vol=` volume target | — |
| Autopilot | picks market and setup by session, market state and events, within a daily budget | rehearsed on Sep 12–25 |

Sizes come from capital (order = capital × leverage ÷ 2.5), stops are % of capital, and every market is backtested
at its maximum leverage. The scout backtests 37 setups on every recorded market each scan and ranks them into Most
Volume / Cheapest / Max Volume.

## 2. How accurate the backtest is

The backtest replays recorded best bid/offer and trades once a second, with 150 ms latency, Arcus's 2.25 bp taker
fee, the order-pool governor and the same stops as live.

**The fill model changed once, and that change mattered most.**
- Until 2026-09-26 only trades *through* our price counted. That understated volume at the touch by 30–60% and
  overstated cost 2–3 times.
- The queue model (an order waits behind the size shown when it joined) is used since SIM_VERSION 8.

**Backtest against live, same minutes** (`bot diagnose --replay`, from the notes):

| Run | Live | Backtest (queue model) | Backtest ÷ live |
|---|---|---|---|
| BTC Mid 0 20x, ~$30, Sep 26 00:10–00:45 | $4,493, 1.3 bp | $3,893, 1.9 bp | volume 0.87x, cost 1.5x |
| BTC Mid 0 40x, ~$100, `sl=30`, 07:02–07:38 | $57,836, 0.76 bp | $48,267, 0.92 bp | volume 0.83x, cost 1.2x |
| BTC Mid 0 40x, 12:12–12:42 | $51.3k, 1.31 bp | 1.64 bp | cost 1.25x |
| SPY Mid 0, overnight Sep 26–27 | ~$330k, ~0.35 bp | ~1.5 bp at the same stop | cost ~4x |

What this says:
- **Rankings are more reliable than dollar amounts.** The backtest is conservative:
  - about 1.25x the live cost on BTC, about 4x on SPY;
  - 0.83–0.96x the live volume.
- **The SPY gap is the largest and least understood.** One overnight run in a quiet market is one data point.
- **Things live had that the model does not:**
  - orders off the book while Arcus confirmed a cancel-and-replace (live orders rested 29–64% of the time on each
    side);
  - the bot's own pre-trade margin check refusing reducing orders (fixed);
  - a broken orders-stream parser in the first QQQ runs (fixed; `docs/incidents/2026-09-25-live-requotes-refused.md`).

**An independent check** (this review). The 159 live fills (BTC and QQQ, Sep 25–26), lined up against the recorded
Arcus tape (mid before each fill, then 1 s to 5 minutes later):

| | Fills | Volume | Edge at the fill | 1 s | 10 s | 60 s | 5 min |
|---|---|---|---|---|---|---|---|
| BTC maker | 121 | $103.6k | +0.02 bp | −0.28 | −0.47 | **−0.85** | −1.20 |
| BTC taker (stops) | 5 | $10.0k | −0.02 | 0.00 | −0.21 | +0.03 | +2.90 |
| QQQ maker (deep quotes) | 31 | $3.7k | +0.80 | +0.42 | +0.27 | **+0.18** | +0.16 |

This matches the notes:
- at a one-tick touch BTC earns nothing at the fill and loses ~0.9 bp within a minute;
- the deeper QQQ quotes kept a small edge (a small sample).

**The account as a whole** (`state/balances.jsonl`, Sep 26 12:42 UTC): net deposits $120.70, equity $108.48, a loss
of $12.22 on about $118k traded. That is **1.03 bp all in**, $2.32 of it taker fees. The research notes' "about
$105 per $1M" is the same figure.

## 3. How well the strategies did

- **Nothing is profitable at speed on Arcus** (note 2026-09-27):
  - a one-tick book pays at most half a tick (0.07 bp on SPY);
  - makers get no rebate below $1B a month;
  - the takers who hit the touch are better informed than a 1 Hz bot (BTC −1.4 bp and SPY −0.9 bp within a minute,
    on all touch fills Sep 19–26).
- **BTC Mid 0 at 40x** is the volume machine: about $300k an hour at 1.6–2 bp in the backtest and about 1 bp live.
- **SPY at 33–50x** is about twice as efficient per dollar, 15–35x slower.
- **Smart** saved 0–25% of Mid's cost (SPY the most) at 80–85% of its volume. It made no market profitable.
- **The only near-profits** are wide quotes on the index perps (SPY Smart +3, QQQ Mid +3): +$2–3 a day on
  $20–35k, on 8 days in sample, t 1.5–2.2. Unproven.
- **Grid, RGrid, the RSI signal and the static grid** lost on every market. The last three were retired.
- **The position stop is a cost of its own:**
  - at 1% it fired about 20 times a day on SPY, each exit a 2.25 bp taker order;
  - 3% cut SPY Mid 0 from 1.57 to 1.06 bp in the backtest.
- **The autopilot** bought 17–54% more volume per dollar than "BTC Mid 0 from midnight" in its rehearsal on
  Sep 12–25, on the backtest's own numbers. It has no live record yet.

## 4. Verdict

- **Use the backtest to rank, not to promise.** It is conservative on cost and slightly short on volume:
  - about 1.2–1.5x the live cost on BTC, about 4x on SPY;
  - 0.83–0.96x the live volume.
- **The Arcus bot does what it was built for:** maker volume at a known cost, about 1 bp live. Profit at speed is
  not available on Arcus with one market and about $100.
- **The live problems were in the plumbing** (orders parser, margin checks, a test that started a real guardian),
  **not the strategies.** Each is fixed and has an incident report.

The same ideas on Lighter (0% taker fee, several-tick spreads, less informed flow) are 3–10 times cheaper per dollar
in the backtest: [RESEARCH.md](RESEARCH.md).
