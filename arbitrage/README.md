# Arbitrage

Funding-rate arbitrage between Arcus and Lighter (Robinhood Chain). One of three bots in the [repository](../README.md): installed by
`make install`, its switches in the one `.env`, and controlled from the one Telegram bot as `/arb_<command>`. It trades with the Arcus and
Lighter bots' own venue clients and keys. It scans, plans, backtests, and runs the position: on paper by default, live only behind a switch
that you set and a confirmation each time.

**`arbitrage <command>` in this guide is `.venv/bin/arbitrage <command>`**, run from `treading-bot/arcus`.

On two machines (`BOT_ROLE` in `arcus/.env`, [main README](../README.md#two-machines-one-records-one-trades)) the executor runs on the
machine that trades (`all` or `trader`): it needs nothing from the recorder, since it reads both venues itself. A `recorder` or a
`scout` machine refuses `arbitrage run` and `start`; `scan`, `plan`, `history` and `backtest` work anywhere.

> **Risk warning.** Experimental software that can place real orders with real money. The live adapters have never
> sent an order (section 6 says exactly what is unproven). Backtests are estimates. Nothing here is financial advice.

## 1. The idea

Both venues charge funding every hour: rate × position value; a positive rate means longs pay shorts. Hold the same
market **short on the venue with the higher rate and long on the other**, the same size on both: the price moves
cancel, and each hour pays the difference between the two rates on the size of one leg. No directional bet.

## 2. What 99 days of history say

`arbitrage backtest` replays every hour both venues have published (26 Jun – 3 Oct 2026, 36 markets) with the rules the
live bot uses. At $240 in all, half on each venue, one position at a time (`arbitrage backtest --capital 240` prints it):

| Markets | $ a day | A year on the capital | Positions | Closed by a stop | Money moved between venues |
|---|---|---|---|---|---|
| All 36 | $0.39 | 59% | 28 | 16 | 17 times |
| Without CASHCAT | $0.07 | 11% | 23 | 8 | 10 times |
| Without the small tokens | $0.09 | 13% | 21 | 7 | 8 times |
| Stocks and ETFs only | $0.05 | 7% | 10 | 1 | 4 times |
| BTC, ETH, SOL, XRP, HYPE, ZEC | $0.00 | 0% | 11 | 4 | 4 times |

- **Four fifths of the result is one token.** CASHCAT paid about 195% a year more on Arcus than on Lighter. It also
  moves 10% a day, so it is held at 1x with a 40% stop, and 15 of its 22 positions ended at that stop.
- **On stocks Arcus pays about 7% a year more than Lighter on weekdays and nothing more at weekends** (Arcus locks
  its rate while the underlying is closed). That is the steady part: about 5 cents a day at $240.
- **It scales with the money** ($1.61 a day at $1,000 on the same rules), except the CASHCAT part, which a thin
  market limits.
- **Holding beats timing single payments.** Opening before a payment and closing after it, whenever the last payment
  covered the four fills, made $0.06 a day against $0.39.
- **The money has to be moved by hand.** A closed position leaves its gain on one venue and its loss on the other.
  The smaller balance sets the next position, so without moving money back the result halves ($0.19 a day).
- **What the replay cannot know:** real fills against the mid (1 bp a fill is assumed), the gap between the two
  venues' prices at the moment of a stop, and margin rules or volumes other than today's.
- **The fill cost matters less than it looks:** at 4 bp a fill the replay still makes $0.39 a day, because the
  opening rule then leaves out the large positions that pay little. The one paper round trip so far (PONS, 4 Oct,
  $149 a leg, all four fills as maker) cost $0.25: about 4 bp a fill, where the plan had read 2 bp from the two
  books. On the small tokens, count on the dearer figure.

## 3. Commands

```
cd treading-bot/arcus        # then .venv/bin/arbitrage ... ; written as `arbitrage ...` below
arbitrage scan               # rank the markets, with the accounts' own free collateral
arbitrage scan --arcus 120 --lighter 120 --feeds
arbitrage plan BABA --arcus 120 --lighter 120
arbitrage history            # download both venues' funding and price history (about an hour; arbitrage/data/history)
arbitrage backtest --capital 240   # the rules replayed on all of it

arbitrage run --arcus 120 --lighter 120    # the executor on PAPER, in this terminal: real prices and funding,
                                           # simulated orders
arbitrage start --arcus 120 --lighter 120  # the same in the background (arbitrage stop stops it; the position is kept)
arbitrage status                           # what it is doing, the last events
arbitrage close      arbitrage close --now # close the position: as maker first, or with taker orders at once
arbitrage pause      arbitrage resume      # open nothing new (an open position is kept) / look again
arbitrage skip CASHCAT   arbitrage unskip CASHCAT   # markets it must never open

arbitrage livetest [SYMBOL] [--expiry]     # REAL MONEY, smallest size: the Lighter leg's orders once, with a report
                                           # (needs ARB_LIVE=1 and LIVE typed; sends nothing to Arcus)

arbitrage settings                         # every setting, its value, its range
arbitrage set max_hold_h 72                # change one; the running bot uses it from its next loop
```

The same from the phone, in the one Telegram bot: `/arb_scan`, `/arb_status`, `/arb_hold 72`, `/arb_close`,
`/arb_start 120 120` ([section 7](#7-telegram)).

`status`, `close`, `pause` and `resume` act on the paper bot; add `--live` for the live one.

## 4. The rules

**When it opens.** A market is opened only when all of these hold (`arbitrage scan` lists it under "Worth holding now"):
- the next payment, the last 24 hours and the last 7 days agree on which venue pays more;
- the smallest of the three pays at least `min_edge_apr` (5% a year on the position);
- that funding pays for getting in and out within `max_breakeven_h` (48 hours), at the two books' real spreads;
- both venues traded at least `min_volume_24h` ($100k) in it, and the size is above both minimum orders.

**How big.** Dynamic, from the accounts as they are at that moment:
- the smaller of the two venues' free collateral × `margin_use` (0.9) × leverage, the same number of units on both
  legs; `max_notional_usd` caps it (for a first small run);
- leverage is the highest both venues allow (Arcus's off-hours margin, since the position is held overnight), at
  most `max_leverage` (20), and low enough for the stop below.

**The stop, dynamic unless you set one.** A stop and a take profit sit at the same distance on both legs: half the
way to liquidation (`stop_frac`), and at least three daily moves from the entry (`stop_sigmas`), which is what
lowers the leverage on a volatile market. `arbitrage set stop_pct 2` replaces it with 2% (never past 80% of the way to
liquidation); `arbitrage set stop_pct auto` gives the dynamic one back. Both venues hold the orders themselves, so they
work while the bot is down; the bot checks the same distances and closes both legs when either is reached.

**How long.** `min_hold_h` (24): kept at least this long, so the funding can pay for the fills; a stop still closes
it. After that it is closed once the last 24 hours and the next payment both pay less than `exit_edge_apr` (0).
`max_hold_h` (0 = none) closes it after that many hours whatever it pays. All three can be changed at any time and
apply to the position already open: the time limit within one loop (about 10 s; checked on paper, 4 Oct: closed 7 s
after `arbitrage set max_hold_h`), the funding rule at its next check (every 5 minutes).

**Getting in and out.** Both legs go in as post-only orders at the best price and follow it (`requote_s`). If one
leg has filled and the other has not for `chase_s` (20 s), the missing part crosses the spread, provided that costs
no more than `max_cross_bp` (5 bp: half the spread plus the taker fee, 2.25 bp on Arcus and 0 on Lighter). If it
would cost more, it keeps following, and after `enter_timeout_s` (180 s) it crosses anyway: equal legs come before
cost. Exits work the same way with reduce-only orders. A stop, a vanished leg or `arbitrage close --now` uses taker orders
on both legs at once.

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
3. `ARB_LIVE=1` in `arcus/.env`, then `arcus down` and `arcus up` so the Telegram bot sees it.
4. **Small first:** `arbitrage set max_notional_usd 30`, then `/arb_start live` in Telegram and type back the code it
   shows (or `arbitrage start --live` in a terminal and type LIVE). Watch `/arb_status`.

What the first live run will prove or disprove, because nothing could be sent while building it:

| Piece | State |
|---|---|
| Arcus post-only and IOC orders, cancel, cancel-all, leverage, positions, balance | the market-making bot's own client, live-proven there |
| Arcus: reading one order by its id | from the documentation |
| Arcus: the position stop and take profit (`positionTpsl`, signed as trigger orders) | from the documentation; the docs disagree with themselves on the leg's price field |
| Lighter: everything that sends (orders, cancels, stops, leverage) | signs correctly offline; never sent by this program. The Lighter bot's own test sent the same kinds of request on the venue on 2026-10-09 and 10 (lighter/README.md, section 6), including a stop and a take-profit built as here. `arbitrage livetest` sends them through this program's own adapter, once, at the smallest size: a maker order with its 5.5-minute expiry, the cancel and re-place the engine does before that expiry, a taker order, the position as the adapter reads it, the stop pair, cancel-all, the close. Its report is in `arbitrage/reports/`. **Run on 2026-10-10 (SPY): 9 of 9 passed, cost $0.0003, ended flat**; Lighter gave the maker order 328 s to live, and the adapter's own reading of orders, fills and the position matched Lighter's at every step |
| Lighter: the stop pair | counted as placed only when Lighter's own list shows both orders: Lighter answers OK to a batch and leaves out a member it does not like (seen on the venue) |
| Lighter and Arcus reads (book, position, balance, key check) | run against the real accounts on 2026-10-04 |
| The executor's logic | 38 offline tests on simulated venues, and paper runs on real prices (open, stops, close by command and by the time limit) |

If a venue refuses the stop orders the bot closes the position, pauses and says so, instead of holding it unprotected
or opening it again.

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
  requests a day). Its numbers agree with the venues' within a few percent. Its trade endpoints work by storing
  exchange keys on its servers; this program never sends it one.
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
