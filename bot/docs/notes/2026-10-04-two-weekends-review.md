# 2026-10-04: two live weekends and ten recorded days: what to change

The owner ran the bot live over two weekends (26–27 Sep, 3 Oct) and asked: read all of the recorded data, backtest
different strategies, say what to improve and what to remove, and whether a faster bot (or a Rust one) would help.

Research scripts are in `antigravity/` (not part of the bot): `analyze_live.py`, `research_sessions.py`,
`report_sessions.py`, `research_quote_speed.py`, `tape_gaps.py`. Costs are in bp of maker volume ($1 per $10,000).

## 1. The data

- **Tape:** one continuous tape in `bot/data/scout/tape`: books 19 Sep – 3 Oct (60 markets from 23 Sep), top-10
  depth from 24 Sep, trades from 25 Jun. The server's count matches after import: 5,292 market-day-kinds complete.
- **Gaps:** 48 stretches with no book, 181 minutes in all (0.9% of the period). Listed in
  `antigravity/reports/data/recorder_gaps.csv`.
  - The longest: 23 Sep 06:12 (24 min), 29 Sep 04:30 (20 min), 24 Sep 21:14 (12 min), 24 Sep 02:51 (10 min).
  - 3 Oct 04:57 and 05:11: one of the recorder's two connections at a time (8 and 3 min).
  - A 61-second gap at 08:00 UTC on most weekdays, on one or both connections: a bug in the bot's own stream
    client (section 8).
- **Books cannot be backfilled** (Arcus serves no historical book). The trades inside each gap were backfilled from
  Arcus's public trade history (`antigravity/fill_trade_gaps.py`, parts named `trades-restfill-*`).

## 2. Where the live money went

3,379 fills and $2.50M of volume, every fill lined up against the recorded book (`antigravity/reports/live/`).

| SPY session | Maker volume | Cost | Edge at the fill | 1 min later | 5 min later |
|---|---|---|---|---|---|
| Weekend | $1,693k | 0.36 bp | +0.08 bp | −0.15 | −0.31 |
| After hours (16:00–20:00 ET) | $260k | 0.0 bp | +0.14 | −0.33 | −0.56 |
| Overnight (20:00–04:00 ET) | $239k | 1.50 bp | +0.18 | −0.51 | −1.37 |
| Premarket | $17k | 3.0 bp | +0.94 | −1.15 | −0.85 |
| Regular hours | $43k | 3.1 bp | +1.21 | −1.19 | −1.14 |

- **SPY was 93% of the volume**, and the weekend was three quarters of that.
- **The loss is inventory, not fees.** A fill earns about a tenth of a bp against the mid and is 0.3 bp worse five
  minutes later; the position is held that long because the other side fills slowly. Taker exits were 3% of SPY's
  volume.
- **Other markets cost more:** BTC 1.5 bp on $125k (the first weekend), AAPL, HYPE and QQQ 1–2.5 bp on a few
  thousand dollars each.
- **The largest single loss** was SPY Mid 0 on 29 Sep 04:51–05:58 UTC with $7,200 orders: 2.3 bp, of which $22k was
  taker exits. It ran straight after a deposit, overnight, at 50x.

## 3. How close the backtest is

`bot diagnose --replay` on the largest run (SPY Mid −1 at 50x, $5,000 orders, 2 Oct 22:08 – 3 Oct 14:18 UTC):

| | Volume | Fills | PnL |
|---|---|---|---|
| Live | $975,502 | 947 | −$36.02 |
| Backtest, queue model (what the scan uses) | $867,175 | 689 | −$37.03 |

So for SPY at the weekend the scan's model is right on cost and 11% low on volume. The earlier "about 4x" gap
(note 2026-09-27) came from the trade-through fill model, replaced on 26 Sep.

The **playbook** is not as close: it gives SPY Mid 0 at the weekend $15k an hour at 1.55 bp, against $60k an hour
at 0.36–0.39 bp live. It averages 42 days, most of them with books rebuilt from trades, from weeks when Arcus's
weekend SPY was far thinner. Section 5, item 5 is the fix.

## 4. Every setup, per session, on recorded books

17 markets × 10 days (24 Sep – 3 Oct) × 11 setups × 3 position stops: 23,001 backtests over whole session stretches
(not single hours), $240 at each market's maximum leverage, no daily stop and no kill.

**SPY, position stop 1% (maker volume an hour, cost in bp):**

| Setup | Weekend | US evening | Asia | London | US open | US afternoon |
|---|---|---|---|---|---|---|
| Mid 0 | $36k, 0.59 | $90k, 1.08 | $82k, 0.80 | $124k, 1.61 | $238k, 2.06 | $250k, 1.14 |
| Smart 0 | $29k, 0.49 | $66k, 0.68 | $63k, 0.66 | $115k, 1.06 | $242k, 1.30 | $227k, 0.68 |
| Mid +2 | $3k, 0.45 | $10k, 0.72 | $9k, 0.49 | $16k, 1.73 | $53k, 1.56 | $23k, −0.09 |
| Smart +2 | $2k, 0.23 | $9k, 0.46 | $7k, −0.07 | $11k, 2.08 | $30k, 1.25 | $12k, −0.07 |

**BTC, position stop 1%:**

| Setup | Weekend | US evening | Asia | London | US open | US afternoon |
|---|---|---|---|---|---|---|
| Mid 0 | $55k, 1.66 | $215k, 2.16 | $90k, 2.51 | $115k, 1.97 | $221k, 2.59 | $134k, 2.73 |
| Smart 0 | $69k, 1.13 | $281k, 1.40 | $131k, 1.74 | $166k, 1.49 | $339k, 2.00 | $215k, 1.95 |

**All 17 markets pooled, by setup and position stop (cost in bp; taker share of volume):**

| Setup | Stop 1% | Stop 3% | No stop | Taker share at 1% → 3% |
|---|---|---|---|---|
| Smart 0 | 1.64 | 1.52 | 1.51 | 6.7% → 3.4% |
| Smart +1 | 1.72 | 1.54 | 1.51 | 7.4% → 3.1% |
| Mid +1 | 2.08 | 1.81 | 1.75 | 13.8% → 7.1% |
| Mid 0 | 2.14 | 1.83 | 1.78 | 14.7% → 7.9% |
| Mid −1 | 2.17 | 1.89 | 1.82 | 14.0% → 7.9% |
| Grid 0 | 2.66 | 2.19 | 1.94 | 22.2% → 13.6% |
| Grid +2 | 2.83 | 2.36 | 2.11 | 24.1% → 15.5% |

What it says:
- **Smart 0 is cheaper than Mid 0 on every market and in every session.** SPY: 0.87 against 1.28 bp at 88% of the
  volume. BTC: 1.63 against 2.25 bp with 43% **more** volume.
- **A 3% position stop is cheaper than 1% on every market and setup**, with a little more volume; 3% and no stop are
  the same. SPY Mid 0: 1.28 → 0.92 bp. The 1% stop turns small swings into taker exits.
- **Grid is the dearest family everywhere,** and Mid −1 is Mid 0 on a one-tick book (no gain).
- **Sessions matter as much as setups.** SPY touch setups cost about 0.5 bp at the weekend and in Asia, 1.1–1.6 in
  London, 1.3–2.1 at the US open. Volume an hour is the other way round: $36k at the weekend, $240k in US hours.
- **Nothing with real volume makes money.** Cells that did (NVDA Smart +3 at weekends, SPY Mid +2 in the US
  afternoon, QQQ Smart +2 at the open) trade $2–40k an hour and rest on 5–7 stretches each: some cells
  will look good by chance (there are about 1,100).

## 5. What was changed

The owner agreed to all of it on 4 Oct ("keep a dynamic stoploss as default all the time which will be overridden
if I set any"), so items 1, 5 and 6 are in the code; 2 to 4 are how to use it.

1. **The position stop is dynamic by default** (`bot/common/sizing.py: dynamic_stop`, `config/app.yaml`):

   `stop = 2 × the market's hourly move × the inventory cap`, never under 1% of the account and never over 5%.

   - It widens when the market moves more and narrows to the 1% floor when it is quiet, so a normal swing does not
     become a taker exit.
   - `/set position_stop 3` fixes it at 3% and switches the dynamic part off; `/set position_stop auto` gives it
     back. `position_stop_k` and `position_stop_max_pct` in `config/app.yaml` are the 2 and the 5%.
   - A fixed stop may not be larger than the daily stop (2% by default), so `/set position_stop 3` needs
     `/set daily_stop 3` or more first. The dynamic stop needs nothing: only its 1% floor is held to that rule.
   - The backtest, the scan and the live engine use the same function.

   Six setups (Mid and Smart 0, +1, +2) on the same 17 markets × 10 days:

   | Position stop | Cost | Taker share | Stops fired | SPY cost | BTC cost |
   |---|---|---|---|---|---|
   | 1% (the old default) | 1.89 bp | 9.5% | 18,971 | 1.12 bp | 1.76 bp |
   | 3% | 1.67 bp | 5.0% | 2,436 | 0.81 bp | 1.61 bp |
   | **Dynamic, 2 hourly moves, 1–5%** | **1.66 bp** | **4.5%** | **722** | **0.77 bp** | **1.61 bp** |
   | Dynamic, 2 hourly moves, 1–8% | 1.65 bp | 4.4% | 237 | 0.77 bp | 1.61 bp |
   | None | 1.63 bp | 4.2% | 0 | 0.76 bp | 1.61 bp |

   The dynamic stop costs what 3% costs with a third of the stops, and 1 to 3 hourly moves give the same result.
   The 5% ceiling is kept over 8%: the gain is 0.01 bp and the worst single loss is 60% larger. The kill and the
   guardian still cap the day.

   **With the daily stop and the kill switched on** (the table above has neither, which is how a `/run … sl=` run
   behaves: `sl=` lifts both to the run's limit). Whole days, Mid 0 and Smart 0 on SPY, QQQ, NVDA, GLD, BTC and ETH,
   24 Sep – 2 Oct, $240 at maximum leverage (`antigravity/research_daily_stop.py`):

   | Position stop, daily stop | Maker volume a day | Cost | Taker share | Days ended by the daily stop | Hours quoted |
   |---|---|---|---|---|---|
   | 1% fixed, 2% (the old default) | $47k | 1.30 bp | 5.9% | 98% | 3.1 |
   | **Dynamic 1–5%, 2% (the default now)** | **$52k** | **1.24 bp** | **4.4%** | 97% | 3.3 |
   | Dynamic held under the daily stop (1–2%), 2% | $51k | 1.26 bp | 4.7% | 98% | 3.3 |
   | 3% fixed, 2% | $52k | 1.24 bp | 4.5% | 98% | 3.3 |
   | 1% fixed, 5% | $110k | 1.18 bp | 4.9% | 94% | 5.8 |
   | Dynamic 1–5%, 5% | $129k | 1.04 bp | 1.8% | 94% | 6.5 |

   - The dynamic stop is better here too (10% more volume, 0.06 bp cheaper), and holding its ceiling under the
     daily stop changes nothing, so it is not held there.
   - **The daily stop is what ends almost every day at maximum leverage**, after about 3 hours of quoting (BTC and
     ETH within 20 minutes, SPY after 2 hours): at 50x a full position reaches 2% of the account on a 5 bp move.
     A run started from the lists or by the autopilot with no `sl=` trades a fraction of the day. `/set daily_stop
     5` gives 2.5 times the volume at a lower cost per dollar, and still ends 94% of the days early; a run with
     `sl=` is not ended by it at all. This is the owner's choice of how much a day may lose, not a default changed
     here.
2. **Smart 0 in place of Mid 0 and Mid −1** for volume runs. (It could not start live until now: section 8.)
   - SPY in London and US hours: a third less cost for about the same volume.
   - SPY in the US evening and Asia: 20–35% less cost, a quarter less volume.
   - SPY at the weekend: 20% less volume for up to 17% less cost. Mid 0 is the better choice there if volume is
     the aim.
   - BTC: cheaper and more volume in every session.
3. **Pick the session, not only the setup.** For SPY volume at a known cost: weekend and Asia with Mid 0 or Smart 0
   (about 0.5–0.8 bp), the US afternoon with Smart 0 (0.5–0.7 bp, $230k an hour). Leave the US open and London to the
   wider setups (Smart +2: 0.4–1.3 bp) or stay out.
4. **Size after a deposit as before it.** The dearest hour of the fortnight was the first hour at nine times the
   order size, overnight. `/run … sl=` below the guardian's 10% keeps the run stop the first to fire (the guardian
   fired first on 3 Oct: that run's `sl=` was 14% of the account). The LIVE STARTED message now says so when it applies.
5. **The playbook uses recorded books only once a market has 10 recorded days, 2 of them at a weekend**
   (`bot/scout/playbook.py: recorded_first`). Before, it under-stated weekend SPY volume four times and over-stated
   its cost four times, which steered the autopilot away from the session the owner trades most. BTC and SPY have
   that many days now; a market with fewer keeps the rebuilt-book days until it does. The simulator's version
   changed with the stop, so the scout re-runs every day once after the update: the first scan and the first
   playbook build take longer than usual, later ones are cached as before.
6. **BTC runs on a 0.5 s loop** (`sizing.LOOP_MS`, `loop_ms` in the session file; note in
   `antigravity/reports/quote_speed/`): 0.2 bp cheaper on 6 of 6 days. Every other market stays at 1 s, where a
   faster loop changed nothing. The once-a-second work (risk, heartbeat, reconcile) still runs once a second.

## 6. What was removed

From the **scan's menu** (`bot/strategies/setup.py: menu`), which went from 37 settings to 8: Mid 0, +1, +2, +3
and Smart 0, +1, +2, +3, all Neutral. A scan is about 4.5 times shorter.

- **Grid** (0 to +5, Long and Short): dearest on all 17 markets, 22–24% taker volume.
- **Mid −1:** the same quotes as Mid 0 on one-tick books, dearer elsewhere.
- **Long and Short bias** as scanned settings: in the server's 7-day scan they cost more than Neutral for the same
  spread on the 60-market total (Mid 0: 2.88 bp Neutral, 2.98 Short, 3.08 Long).
- **Mid +5:** $550k a day across all 60 markets at 3.65 bp.

Nothing was deleted from the bot: `/run` and the Telegram form still accept every one of them (any mode, spread
and bias), unbacktested as before. Only the scan, the lists and the autopilot stop spending time on them.

## 7. Speed, and whether to rewrite in Rust

Measured:
- **The bot's own work for one decision** (risk checks, strategy, order diff) on the live engine: median 0.45 ms,
  99th percentile 0.9 ms. Applying one book update: 0.6 µs.
- **Send to first order status, live:** median 180 ms (173–196 ms for 80% of orders). From India a plain request to
  Arcus takes 156 ms; the connection ends at Cloudflare in Mumbai in 8 ms and the rest is the trip to Arcus's servers.
- **The loop** wakes once a second, so a quote reacts to a book change after 0.5 s on average.

So of roughly 700 ms from a price change to a new quote on the book, the Python code is under 1 ms. **A Rust rewrite
would save about 0.4 ms of 180: nothing that shows in fills.** What would:
1. the loop: 0.5 s or event-driven saves 250–400 ms on average (worth 0.2 bp on BTC, nothing on SPY);
2. the server's location: a machine near Arcus's servers would cut 150 ms to perhaps 10–30 ms (worth 0.03–0.08 bp
   in the backtest; check the country is allowed first);
3. time on the book: live quotes rested 68–77% of the time, and a price change takes the order off the book for a
   round trip.

Orders over the WebSocket instead of REST gain nothing (measured: both 156 ms).

## 8. Bugs fixed with this note

- **Smart never started live** (`bot/common/ids.py`). The client-id table had no code for `smart`, so the engine died
  while it was being built: "unknown strategy code for 'smart'". All five live Smart starts failed this way (SPY on
  27 Sep and 2 Oct, QQQ on 28 Sep). The tests built the strategy and the backtest, never the live engine; one does now
  for every mode.
- **A failed start showed shutdown noise instead of the error** (`bot/scout/pilot.py`): the last 12 log lines were
  "Unclosed client session" notices. The message now shows the doctor's FAIL lines or the error line.
- **A run stop wider than the guardian's limit** is said in the LIVE STARTED message ("The guardian stops it first,
  near −$30.01 (10% of $300)" on a $300 account with `sl=42`).
- **Book gaps re-subscribed once per update** (`bot/venues/arcus/ws.py`). Arcus resets the stock books at 00:00 and
  08:00 UTC; each of about 900 following updates sent its own unsubscribe and subscribe, the client-message window
  filled and the send that waited on it held the connection. The recorder lost about a minute of every market at
  08:00 UTC, and a live bot's book went stale then. Now one re-subscribe at a time, again after 5 s if no snapshot
  came.
- **A stale position adopted at a reconcile** (`bot/core/state.py`, `runner.py`): mid-run, Arcus's REST position is
  taken only when it differs by the same amount 15 s later (28 Sep 07:40 and 29 Sep 04:22 UTC).
- **A refused `/set` showed a web link instead of the reason** (`bot/common/settings.py`). `/set position_stop 3`
  with a 2% daily stop answered "not saved: For further information visit https://errors.pydantic.dev/…". It now
  says "the stops must stay position ≤ daily ≤ kill, and this would make them 3% / 2% / 10%. Change the other one
  first, for example /set daily_stop 3". The note of 27 Sep suggested that very command.
- **A zombie counted as running** (`bot/common/proc.py`): a guardian that had stood down showed as running in
  `bot status` while the Telegram process, its parent, lived. The same check is now used for services, runs and the
  heartbeat. The Lighter bot had the same check and got the same fix
  (`lighter/lbot/ops.py`).
