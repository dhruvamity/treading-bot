# 2026-09-27: Why the setups lose, what the profitable traders do, and Smart

The owner ran SPY Mid 0 live overnight (26–27 Sep). It traded far more than the backtest said and cost far less than
BTC, but most trades closed at a loss. They asked:
- where the setups are lacking;
- whether there is a strategy that is profitable and still fast;
- for a way to see the account's all-time volume and fees.

The existing setups stay as they are. This note is the research behind the new **Smart** setup
(bot/strategies/smart.py) and `/account` (bot/core/account_stats.py).

Costs are in bp: dollars lost per $10,000 traded; a negative cost is a profit.

## 1. Data

| Source | What | Period |
|---|---|---|
| The scout's recorded books (bbo, trades) | Every Arcus perp | Sep 19–26 (8 days) |
| Arcus `/v1/fills` for any address (public) | Our fills; the fills of profitable accounts on the leaderboard | Sep 19–26 |
| Arcus `/v1/leaderboard` (public) | Volume, fees, realized PnL of the top 100 accounts | All-time, 30 days |
| Binance 1-second klines (public) | BTCUSDT and ETHUSDT | Sep 19–26 |

- **Backtests** use the scout's simulator (queue model) at the live run's sizes: capital about $90 at each market's
  maximum leverage.
- **SPY's backtest overstates its cost.** The live SPY run cost about 0.35 bp against 1.5 bp in the backtest at the
  same stop. So the backtest is used to compare setups, not to predict dollars.

## 2. Where the money goes

The live SPY run, about $330k of maker fills:
- lost about 0.35 bp, all of it to adverse selection, because maker fees are 0;
- its three taker exits (about 1% of the volume) cost about 5 bp each and made up a sixth of the loss.

The BTC run before it lost more on 8 taker exits (about $17k) than on $125k of maker fills.

**Why a quote at the best price cannot pay on Arcus:**
- **The most a quote can earn is half the spread.** BTC and SPY sit at a one-tick spread most of the time, and half
  a tick is about 0.07 bp on SPY.
- **Makers are paid nothing else.** The maker fee is 0 and there is no rebate below the VIP tier ($1B in 30 days).
- **The takers who hit the best price are the better informed.** Every trade at the best price, Sep 19–26:

| Market | Fills at the best price | Price move against the maker after 1 s | After 10 s | After 60 s |
|---|---|---|---|---|
| SPY | $9.5M | −0.59 bp | −0.73 | −0.86 |
| BTC | $377M | −0.94 bp | −1.40 | −1.43 |
| ETH | — | −1.28 bp | −1.69 | −1.77 |

So at the best price the question is only how little it loses. The dials are:
- **the queue** (our fills are the ones that make it through the queue ahead, which are larger and more informed);
- **the stops** (a taker exit costs 2.25 bp plus the spread);
- **which seconds we quote.**

## 3. What the profitable accounts do

The all-time leaderboard has profitable accounts with very large volume:

| All-time rank | Volume | Fees paid | Realized PnL | Maker share (last 1,000 fills) |
|---|---|---|---|---|
| #1 | $1,065M | 0.38 bp | **+4.4 bp** | 68% |
| #2 | $827M | 0.15 bp | **+2.1 bp** | 90% |
| #3 | $446M | 0.66 bp | **+8.7 bp** | 56% |

Their fills, lined up against our recorded books (Sep 19–26):
- **They are rarely at the front of the book.**
  - #2's BTC maker fills are on average 1.15 bp behind the best price and 1.2 bp from the mid.
  - They are resting orders that large takers sweep into.
- **The first minute costs them what it costs us.**
  - #2's BTC fills lose 1.3 bp within a minute, which cancels their distance from the mid.
  - A 100%-maker BTC/ZEC account loses 2–5 bp a fill within a minute and still shows a small realized profit.
- **Their profit is not steady per fill.**
  - It comes from a few markets (ZEC, HYPE, GLD for #2 that week) and from positions held for hours.
  - The 30-minute to 4-hour moves after their fills are mixed.
  - Accounts this size very likely hedge on other exchanges or have fee terms of their own. Neither can be copied
    with one Arcus market and about $100.

The accounts with the highest PnL per dollar (+40 to +500 bp) trade little: they are directional traders, not fast
ones.

## 4. Can the bot tell a bad fill in advance?

Each trade at the best price, by what the bot could see a second before (SPY; 60-second move against the maker):

| Signal | Worst group | Best group |
|---|---|---|
| Book imbalance at the best price (ours vs theirs) | the other side 99%+ of the size: −2.17 bp | our side 96%+ of the size: +0.04 bp |
| The mid's move over the last 5 s, toward our side | moved against us by more than 0.6 bp: −2.83 bp | moved our way: +0.22 bp |
| QQQ's move over the last 2 s (for SPY) | against us: −2.56 bp | our way: −0.35 bp |
| Binance's move over the last 1 s (for BTC) | against us by more than 2 bp: −4.28 bp | our way by 0.5–2 bp: +1.39 bp |

**Binance leads Arcus, and Arcus never leads Binance:**

| | Correlation |
|---|---|
| Binance's last-second move with Arcus's next second | 0.29 |
| Arcus's last second with Binance's next 5 s | 0.02 |

Arcus BTC follows about 70% of a Binance move within 30 s.

**But it is too fast to take.** Buying on Arcus a second after Binance jumps (the bot's loop is 1 Hz) loses 1.3–2 bp
after the 2.25 bp taker fee, even with a free exit: Arcus has already covered two-thirds of the move by then.

## 5. Smart: Mid that leaves out the likely bad seconds

**The rule** (bot/strategies/smart.py). Every second Smart quotes as Mid, then leaves out a side that would add to the
position when either holds:
- the other side of the book holds far more (imbalance below −0.6, our own orders not counted);
- or the mid moved against that side by more than 0.5 bp over the last 5 s.

The side that reduces the position always stays. The live bot and the backtest run the same rule (sim.py
`SmartPolicy`), so the scout ranks it like any other setup.

**The thresholds.**
- On SPY, imbalance limits of 0.3–0.8 and move limits of 0.3–1 bp were all within 0.1 bp of each other.
- An outside price (QQQ for SPY, Binance for BTC) added little on top: SPY 1.05 → 0.98 bp, BTC 1.61 → 1.57 bp.
- So Smart uses the market's own book only: no second feed to keep alive.

**Results.** Smart 0 against Mid 0 with the scout's own configs, same sizes, Sep 19–26:

| Market | Position stop $0.90 (now): Mid 0 | $0.90: Smart 0 | Stop $2.70: Mid 0 | $2.70: Smart 0 | Smart's volume |
|---|---|---|---|---|---|
| **SPY** | 1.57 bp | **1.15 bp** | 1.06 bp | **0.82 bp** | 81–86% |
| NVDA | 1.54 | 1.28 | 1.48 | 1.41 | 81–83% |
| GLD | 2.20 | 2.03 | 1.99 | 1.68 | 74–80% |
| BTC | 1.82 | 1.63 | 1.61 | 1.54 | 76–77% |
| ETH | 2.26 | 2.02 | 1.97 | 1.89 | 74–77% |
| SOL | 2.89 | 2.65 | 2.64 | 2.46 | 70–76% |
| QQQ | 1.30 | 1.23 | 1.02 | 1.03 | 85–87% |
| HYPE | 2.69 | 2.63 | 2.52 | 2.45 | 76–81% |

- **SPY gains most:** about a quarter cheaper, keeping 80–85% of the volume.
- **The other markets gain 0–16%.** Part of the gain at the tight stop is fewer stop-outs.
- **No market turns profitable at the best price.**

## 6. The position stop is a cost too

The $0.90 stop (1% of about $90) fires about 20 times a day on SPY Mid 0 in the backtest. Each exit ends as a taker
order at the worst moment. The table above shows what a wider stop saves:
- SPY Mid 0 falls from 1.57 to 1.06 bp at $2.70 (3%).
- In a finer sweep (the research harness, safety pause on), SPY cost fell steeply up to a $2 stop, and little more
  beyond $4.50:
  - Mid 1.51 → 1.15 → 0.98 bp;
  - the filtered version 1.05 → 0.82 → 0.78 bp.

`/set position_stop 3` (3% of the capital) is the suggestion. The run stop, the daily stop and the kill still apply.

## 7. The closest thing to a profit: wider quotes on the index perps

With the scout's own configs (the inventory skew on for spreads above 0) and a $2.70 stop, the wider setups on SPY
and QQQ came out near zero or ahead:

| Setup | Volume per day | Daily PnL (8 days) | Mean | t |
|---|---|---|---|---|
| SPY Mid 0 | $357k | all 8 negative | −$38 | −3.4 |
| SPY Smart 0 | $308k | all 8 negative | −$25 | −4.1 |
| SPY Smart +2 | $62k | 3 of 8 positive | −$0.33 | −0.2 |
| **SPY Smart +3** | **$35k** | 6 of 8 positive | **+$2.07** | 1.7 |
| **QQQ Mid +3** | **$19k** | 6 of 8 positive | **+$3.23** | 2.2 |
| QQQ Smart +3 | $15k | 5 of 8 positive | +$2.08 | 1.5 |

- **Where it holds:** crypto and single stocks lose at every spread (1.5–5 bp); only the index perps come close.
- **A plausible reason:** SPY and QQQ follow a real index. After a large order pushes the perp, it comes back, so
  quotes 3 bp away are paid for providing depth.
- **When it works best:** by session, the US open (NY 09:30–12:00):
  - SPY Smart +3 +1.7 bp;
  - QQQ Smart +3 +3.0 bp;
  - QQQ Mid +3 +2.0 bp.
- **Why it is not proven:**
  - it is 8 days;
  - the t-values are 1.5–2.2;
  - about 120 market × setup cases were tried, so a few look good by chance.
- **How to test it:** paper-run SPY Smart +3 or QQQ Mid +3 for a week, and read the scout's backtest of it as the
  recorded days add up.
- **It is not fast:** $20–35k a day against $300k+ for Mid 0.

## 8. Answer

- **Profitable and fast at once does not exist here for this bot.** The one-tick books pay at most half a tick, makers
  get no rebate, and the takers who hit the best price are faster than a 1 Hz bot. The accounts that profit at scale
  have hedges, fee terms or hours-long positions we cannot copy.
- **Fast, as cheaply as possible:** SPY Smart 0 with `/set position_stop 3`.
  - Backtest: 0.82 bp against Mid 0's 1.57 bp at the current stop.
  - Live SPY has cost a quarter of its backtest so far.
- **Profitable but slow, to be tested:** SPY Smart +3 or QQQ Mid +3, ideally in the US morning.

## 9. What was built

| Part | Where |
|---|---|
| Smart (live) | bot/strategies/smart.py; the engine passes our own size at the best bid and ask (StrategyContext.own_touch) |
| Smart (backtest) | bot/scout/sim.py `SmartPolicy` (the same `left_out` rule); the scout menu has Smart 0, +1, +2, +3 |
| Playbook | Smart 0, +2, +3, from recorded days only (a rebuilt book has no real sizes); a cached day gets only the setups it is missing |
| Run form, `/run` | Mid · Grid · Smart; `/run SPY smart +3 50x paper` |
| `/account`, `bot account` | All-time and 30-day perps volume, fees paid, fees earned (maker rebates, referral commission), the fee tier and the next one, all-time realized PnL and rank |

**Spot volume is not available.** Arcus's API has none: stock tokens trade on-chain from the wallet through Arcus's
router, and `/v1/spotFills` holds only lending settlements. `/account` says so; the bot trades perps only.

## 10. Limits

- **Recorded books cover 8 days.** Every Smart number is from them; the scout's lists and the playbook will show more
  as the recorder adds days.
- **The simulator is conservative on SPY** (4x the live cost at the same stop), less so on BTC (1.25x). Rankings hold
  better than dollar amounts.
- **The leaderboard study** used public fills from 10 accounts over one week. It says what they did, not why.
