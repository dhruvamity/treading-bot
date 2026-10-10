# Lighter

Maker (limit-order) trading on **Lighter perps on Robinhood Chain**, built from the Arcus bot's ideas and written again for
Lighter: the same setups (**Mid**, **Grid**, **Smart**, a directional **bias**, run limits `sl=` `tp=` `vol=`, plus **Touch**, a
Lighter-only mode) and the same scout (record, backtest, rank into three lists), pilot and autopilot.

It is one of three bots in the [repository](../README.md): installed by `make install`, started by `tbot up`, its keys in the one
`.env`, and controlled from the one Telegram bot, where a Lighter command is the Arcus command with `l_` in front (`/l_status`,
`/l_run`, `/l_closeall`). What stays its own is what must: its engine (Lighter's orders, fees and limits are not Arcus's), its data
(`lighter/data`), its settings and its state.

**`lighter <command>` in this guide is `.venv/bin/lighter <command>`**, run from `treading-bot/arcus`.

On two machines (`BOT_ROLE` in `arcus/.env`, [main README](../README.md#two-machines-one-records-one-trades)) the Lighter scout does
what the role says: a `recorder` records only, a `trader` neither records nor scans and follows the lists the other machine makes
(the same `tbot sync` fetches Arcus's and Lighter's together), and neither a `recorder` nor a `scout` machine starts a run.

A standard Lighter account pays **0% maker and 0% taker**. The backtests on Lighter's recorded books put the best setups at **−0.1 to
0.3 bp per dollar traded**, against 0.8–1.7 bp for the best Arcus ones. Only a few recorded days back that: it has never sent a live order.

> **Risk warning.** Experimental software that can place real orders with real money. Backtests are estimates. Run paper first, then
> small. Nothing here is financial advice.

## Quick start

| Command | What it does |
|---|---|
| `make install` | One time, in the repository root: all three bots into `arcus/.venv` |
| `lighter up` / `lighter scout` | The Lighter scout alone, in the background: records every Lighter perp, scans every 30 min, runs the Lighter autopilot |
| `tbot up` | Everything this machine is for: the Arcus scout, this scout and the one Telegram bot (`tbot up lighter` = this scout, `tbot up telegram` = the bot) |
| `lighter status` | This bot on one screen: the scout, the recorder, the run and the lists. `tbot status` shows all three bots |
| `lighter run SPY "smart +1"` | Paper run in this terminal (Ctrl-C stops it; `--bg` in the background) |
| `lighter pilot approve 1` | Run the Most Volume list's #1 in paper (`--list cheapest`/`max`, `--live`) |
| `lighter close` / `lighter stop` | Close the position and stop / stop with the position kept |
| `lighter down` | Stop the scout (`--all`: the run too, position kept). `tbot down` stops everything |

From the phone: `/l` (the Lighter menu), `/l_top3`, `/l_run`, `/l_status`, `/l_dashboard`, `/l_auto`, `/l_closeall`.

## Contents

1. [How it works](#1-how-it-works)
2. [Credentials](#2-credentials)
3. [The setups](#3-the-setups)
4. [Sizes and stops](#4-sizes-and-stops)
5. [The scout: record, backtest, lists](#5-the-scout-record-backtest-lists)
6. [Running: paper and live](#6-running-paper-and-live)
7. [The autopilot](#7-the-autopilot)
8. [Telegram](#8-telegram)
9. [Command reference](#9-command-reference)
10. [How it differs from the Arcus bot](#10-how-it-differs-from-the-arcus-bot)
11. [Layout and troubleshooting](#11-layout-and-troubleshooting)

Install, servers, the Docker recorder and `tbot export`/`import` are in the [main README](../README.md).

## 1. How it works

```
Lighter WebSocket (books, trades, stats)  ──►  recorder  ──►  data/tape/<MARKET>/<day>/*.npz
                                                                   │
                     every 30 min: 36 setups × every market (max leverage, your capital, your stops)
                                                                   ▼
                            lists: 🚀 Most Volume · 💎 Cheapest · 🔥 Max Volume   (data/scout/report.txt)
                                                                   │  you pick (or the autopilot does)
                                                                   ▼
             run (paper or live) twice a second: stops → setup's quotes → one batch of changes → Lighter
```

- **One implementation.** The backtest and the running bot call the same code for the quotes (`lighter_bot/trade/strategy.py`) and the
  stops (`lighter_bot/trade/guard.py`), so they cannot disagree.
- **One market, one setup at a time.** Starting a new run closes the old one first.
- **Paper** trades the live market data with simulated orders and the backtest's fill rules, including Lighter's speed bump and the
  request limit.

## 2. Credentials

Recording, backtests and paper runs need none. For live, the keys go into the repository's one file, `arcus/.env` (the "Lighter"
lines of `arcus/.env.example`):

| Variable | What |
|---|---|
| `LIGHTER_ADDRESS` | Your wallet address (the L1 address that owns the Lighter account) |
| `LIGHTER_API_PRIVATE_KEY` | An API key's private part (below) |
| `LIGHTER_API_KEY_INDEX` | Its slot, 4–254 (0–3 and 157 are the Lighter apps'); default 4 |
| `LIGHTER_ACCOUNT_INDEX` | Only to trade a sub-account; the main account is found from the address |
| `LIGHTER_ENV` | `mainnet` (default) |
| `LBOT_LIVE` | `1` allows live runs (each still needs the doctor and your typed confirmation) |

Telegram needs nothing here: the one Telegram bot (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` in the same file) serves Lighter too.

**Getting an API key** (once, on your own machine). Registering a key needs a signature from your wallet, so it is your step:

```bash
cd treading-bot/arcus && .venv/bin/python ../lighter/scripts/register_key.py --slot 4
```

It asks for the wallet's private key (hidden, used once, never stored), makes a key pair, registers it and prints the three lines for
`.env`. Then check everything:

```bash
.venv/bin/lighter doctor SPY
```

`doctor` is read-only and checks the switch, the credentials and the signer; the clock against Lighter's; the account, its funds, that
Lighter holds your key, and the account tier (it must be **standard** for 0% fees); the market, and the sizes at your equity. It must
end in READY before a live run starts.

**Funding:** deposit USDG to your Lighter account on Robinhood Chain (the Lighter app, at least 1 USDG).

The Lighter signing library comes inside the `lighter-sdk` package (macOS, Linux x86 and ARM, Windows); nothing needs compiling.

## 3. The setups

Named the Tread.fi way: a mode, a spread in bps, a bias when not Neutral ("Mid +1", "Smart +0.5", "Grid +2 Short").

| Mode | Quotes | On Lighter |
|---|---|---|
| **Mid s** | both sides `s` bps from the mid, following it; skew against the position from +1 bp (κ 1) | +0.5 to +1 is the sweet spot. Mid 0 sits at the mid, dozens of ticks inside the spread on crypto, where fills are the most informed |
| **Smart s** | Mid, less the side that would add to the position while the book leans hard against it (imbalance beyond 0.6) or the price just moved against it (0.5 bp in 5 s) | The cheapest mode on 6 of 8 markets. SPY Smart +1 −0.11 bp, ETH Smart +0.5 0.00, NVDA Smart +1 0.01 |
| **Touch s** | both sides `s` bps behind the best bid and ask (0 joins them) | For Lighter's wide-in-ticks books; dominated by Mid in the backtests, kept for `/run` |
| **Grid s** | around the last fill: a sell never below the last buy + s; soft reset at 0.5% against the position | Dearer than Mid on most markets, as on Arcus |
| **Bias** Long / Short | holds half the position cap on that side, sizes skewed toward it | A view on the price, not an edge: the lists show biased setups only once a market has 3 recorded days |

**Run limits** (any run): `sl=` the run may lose this many $ in all (it lifts the daily stop and the kill to it); `tp=` close and stop once up
this much; `vol=` close and stop after this much volume.

**Defaults that are not knobs:**

| Default | Why |
|---|---|
| Decisions twice a second, up to 54 requotes a minute | Fresher quotes cost 0.05–0.35 bp less. The budget, not the fee, is Lighter's constraint |
| Requote only when the wanted price is 0.25 bp (or 2 ticks) away | 0.1 bp was no cheaper and uses more requests |
| No skew up to +0.5 bp from the mid | Skewing there only gives up fills |
| Modify, not cancel and replace | One transaction per side, and the order keeps its id |
| One order per side | As on Arcus |

## 4. Sizes and stops

Every size and stop is a share of the **capital**: the account's equity × `trade_share`, at most `max_capital`, rounded down to a fixed
series. `/set capital 250` fixes it.

| Quantity | Rule | $100 at 50x |
|---|---|---|
| Order size | capital × leverage ÷ 2.5 | $2,000 |
| Position cap | 2 × order | $4,000 |
| Position stop | 2% of capital: close (maker at the touch, taker after 20 s), rest 60 s | $2 |
| Daily stop | 5%: close, no new orders until 00:00 UTC | $5 |
| Kill | 25% below the peak: close with a taker order and stop until `resume` | $25 |

- **Floor:** every order at least 1.2× Lighter's minimum order ($10, or the minimum size × price: BTC about $17).
- **Ceiling:** one order never larger than the market's 99th-percentile taker order (from the tape).
- **Leverage:** each market's maximum (BTC, ETH, SPY, QQQ 50x; SOL and metals 25x; big stocks 20x; the rest 3–10x). `/set max_lev 20` caps it
  everywhere.
- **Why the stops differ from Arcus's 1/2/10%:** a taker exit costs no fee here, so a stop costs only the spread; and at 50x a 15% kill
  fired on days that were well up and then gave some back.
- **Other protections:** every quote carries a 5.5-minute expiry (the shortest Lighter takes) and the bot replaces it 2 minutes
  before that: if the bot or its machine dies, Lighter drops every order by itself within 5.5 minutes (measured in the live test
  of 2026-10-09: gone 17 s past the expiry). Lighter's scheduled cancel-all, the "dead man's switch", is not sent any more:
  Lighter acts on it only when the account's next request arrives, and a dead bot sends none; when it did fire later, it
  would have taken the stop below with it.
  **A dead bot's position:** while the bot holds a position, one reduce-only stop-loss order rests on Lighter for it, triggered
  where the position has lost twice the bot's own position stop (so the bot's own exit comes first while it lives), never
  further than 10% from the entry, with a worst price 5% past the trigger. It follows the position (at most one change every
  10 s, when the side, the size by 20% or the trigger moved) and it lasts 28 days, so it is still there when the quotes have
  expired. `lighter stop` cancels it with everything else: a position you keep after that is yours to watch. Paper and the
  backtest replace their orders on the same 3.5-minute clock, so they lose the same places in the queue; they do not model
  the stop on Lighter. Also: a stale feed (no frame for 10 s) pulls the quotes; an error
  in a second cancels all orders; a 429 pauses requests 60 s and lowers the requote rate 20%; positions and orders are reconciled with
  Lighter every 5 minutes.

## 5. The scout: record, backtest, lists

`lighter scout run` (started by `tbot up`):

**Records** every active perp over two WebSockets into `data/tape/<MARKET>/<UTC day>/`: the best bid and offer on every change; every trade,
with its taker transaction and both accounts; the top 20 levels of each side, once a second; mark, index and funding, once a second. About
0.1–0.3 GB a day for all markets; it pauses under 5 GB of free disk and writes its health to `data/recorder.json`:

| In `recorder.json` | Healthy |
|---|---|
| `markets` | about 58 |
| `rows_total` | climbing between two looks |
| `last_msg_age_s` | a few seconds |
| `book_gaps`, `reconnects` | small; each one is a short hole in that market's book |
| `paused_for_disk` | false |

**Scans** every 30 minutes (`/set scan_every`) on every market with a recorded day (18+ hours), at your capital, each market's maximum
leverage and your stops; each UTC day starts flat. The menu is **36 setups**: Mid 0, +0.25, +0.5, +1, +1.5, +2, +3 × Neutral/Long/Short; Grid
0, +1, +2 × the three biases; Smart 0, +0.5, +1, +2; Touch 0, +0.5. Full days are cached; the last 24 hours are re-run for the shortlist.

**Checks** (every list): no kill or liquidation on any day, at least 5 fills a day, enough recorded days, the market not trending or unusually
wild in the last hour, its data fresh.

**Lists** (best setup per market, top 3):
- 🚀 **Most Volume:** the most volume among setups costing at most your budget (`/set volume_cost`, default $0.10 per $1,000 = 1 bp).
- 💎 **Cheapest:** the lowest cost among setups trading at least 50× the capital a day.
- 🔥 **Max Volume:** the most volume whatever it costs.

**Files:** `data/scout/report.txt` (the lists and every market's best), `latest.json` (what the pilot and Telegram read), `reports/<day>.txt`
(the last scan of each day), `ceilings.json` (the order ceilings).

## 6. Running: paper and live

- **Paper:** `lighter run ETH "smart +0.5"` runs in this terminal (`--bg` for the background); `lighter pilot approve 1` runs the #1 of Most
  Volume; or Telegram ▶️ → 📝 Paper.
- **Live**, in order:
  1. Register a key and fund the account ([section 2](#2-credentials)).
  2. Put `LBOT_LIVE=1` in `.env`.
  3. `lighter doctor SPY` must say READY, and the `account tier` line must say standard.
  4. `lighter run SPY "smart +1" --live --sl 10`, read the doctor's lines, and type `LIVE`. From Telegram: 🔴 LIVE, then type the 6-digit
     code it sends within 2 minutes.
- **Before the first real run: the live test.** Everything above has only met a stand-in for Lighter until this runs. One command
  does every kind of request the bot sends, once, with the smallest order Lighter takes, and asks Lighter after each what happened:

  ```bash
  .venv/bin/lighter livetest
  ```

  It needs `LBOT_LIVE=1`, a passing doctor and `LIVE` typed at its prompt; nothing else can start it (not Telegram, not a flag). It
  picks the liquid market with the smallest minimum order (SPY: about $11 an order, so $5 in the account is enough), and refuses an
  account that already has an order or a position on that market, before sending anything. What it does: sets the leverage (5x,
  then the maximum); rests, moves and cancels a post-only order far from the price; cancel-all; two orders in one request; the
  replacement of an order as the engine decides it 2 minutes before an expiry; a batch with a bad member; a post-only order that
  would cross; a buy (as maker, else with a taker order), a stop and a take-profit as the arbitrage places them, the bot's own
  stop on Lighter beside the open position, a close with that stop resting, the stop going when flat; a reduce-only order with
  no position; a short and its close; and the two things that should
  clear a dead bot's orders: the 5.5-minute expiry on the bot's own orders removing one, and Lighter's dead man's switch cancelling another
  (`--skip-dms` leaves that out: it can take 11 minutes; `--dms-only` runs that step alone, with two far orders and no trade; `--only renewal,long` runs just the steps you name: `leverage`, `limit`, `renewal`, `cancel-all`, `batch`, `post-only`, `long`, `reduce-only`, `short`, `dms`). It stops and closes everything when the
  account is $1 down (`--max-loss`), and always ends flat with no orders and the leverage it found. With a zero-fee account the cost is the spread on about
  four minimum orders: cents. The report is printed and written to `lighter/reports/livetest-<time>.md`: `PASS` (Lighter did it
  and the bot's own books agree), `FAIL` (a bug to fix before a real run), `INFO` (something learned, such as how far from the
  price an order may rest).

  **Runs of 2026-10-09, SPY (four, no cost, each ended flat):** orders, moves, cancels, both leverages, a long and a short with
  their closes, a stop and a take-profit all worked, and Lighter accepted a worst price 5% past the trigger. What they found:
  Lighter holds a scheduled cancel-all for the account but did not act on it for 5 minutes past its time, and then cancelled
  everything the moment the account sent another request. So it cannot clear a dead bot's orders, and the test now reports it
  as `INFO`. An order's own expiry does: the bot's quotes used to carry 28 days; they now carry 5.5 minutes and are replaced
  before that runs out, and the last run saw Lighter remove an order placed that way 31 s after its expiry. Those runs left
  SPY at 50x on the account (Lighter's default is 2x): `lighter leverage SPY 2` puts it back, and the test now does that itself.
  **Run of 2026-10-10 (no cost, ended flat, leverage back at 2x):** the replacement before an expiry worked as the engine
  decides it (one request: old order off the book, new one on it), the bot's own stop rested on Lighter beside the open
  position, the reduce-only exit order was accepted and filled next to it, and Lighter dropped the stop by itself once the
  position was closed. One line said `FAIL` wrongly: the short was filled, but Lighter's account read still said flat a
  second later. The test now waits for that read, and the bot no longer lets an account read replace a position the stream
  gave it in the last 5 seconds.
- **The first real run: smallest size, ten minutes, ends flat.** After a clean live test, this is the bot quoting by itself for
  the first time, with as little money at work as Lighter allows:

  ```bash
  .venv/bin/lighter run SPY "smart 0" --lev 2 --capital 16 --live --sl 1 --seconds 600 --flat
  ```

  $16 at 2x gives orders of about $13 and never more than $26 held; the position stop is $0.32, the stop on Lighter sits at
  twice that, and `--sl 1` ends the run if it is $1 down. `--seconds 600 --flat` ends it after ten minutes with the position
  closed (maker, then taker) instead of kept. It asks for `LIVE` like any live start. The same command without `--live` is
  the paper rehearsal. What to read afterwards: `lighter/logs/run-live-<day>.jsonl`, `lighter/state/fills-live.jsonl` and
  `status-live.json`.


  **That run, 2026-10-10:** ten minutes, 70 fills (69 as maker), $1,045 traded, +$0.003, ended flat with no orders. No
  refusal while it quoted; the stop on Lighter followed the position 53 times and never fired. What it found: at the end
  it held $9 of SPY, under Lighter's $10 minimum, and Lighter refused the maker order to close it (40 times in 20 s) until
  the taker order did. A position under the minimum is now closed with a taker order at once, quotes under the minimum
  are not sent even to reduce, and after a refused request the bot waits 2 s before it asks for orders again. Paper and
  the backtest follow the same rules.

  **A second run the same day, to see the replacement before an expiry** (`mid 5`, quotes 5 bps from the mid so that they
  rest; 8 minutes, no fill, no cost): at 210.8 s and at 421.9 s the bot replaced both quotes in one request, each with 120 s
  left; seconds later Lighter's stream showed the two new orders open with 328 s to live. None expired, nothing was
  refused. The bot logs each replacement (`renewed`) and counts them in its status file, next to the orders Lighter
  dropped at their expiry instead (`expired`, which should stay 0).
- **Before the first quote,** the run cancels your orders on that market, sets the leverage (cross margin) and withdraws a scheduled cancel-all an older version may have left.
  It treats every order on that market as its own: trade by hand on another market, or use a sub-account (`LIGHTER_ACCOUNT_INDEX`).
- **Control** (CLI, or Telegram): `lighter pause` / `unpause` (no new orders; closing orders keep working), `close` (close the position, maker
  then taker, and stop), `stop` (quotes cancelled, position kept), `resume` (trade again after the kill or a daily stop).
- **A list's pick is watched by the pilot.** It is paused while it fails the checks and resumes after two scans that pass. Your own pick is
  never paused for its numbers.
- **Files** (paper and live never mix): `state/status-<mode>.json` (what the dashboard shows), `state/run-<mode>.json` (the run, so a restart
  continues it), `state/fills-<mode>.jsonl` (every fill), `logs/`.

## 7. The autopilot

`/l_auto` or `lighter auto on [--live] [--budget 5] [--cost 0.05]`. Off by default.

- **The pot:** `budget` dollars are added at 00:00 UTC; unspent money carries over, up to 7 days; each run's result comes out of it, and a
  profit goes back in.
- **Nothing running:** it starts the Most Volume list's best setup within the cost ceiling (default: the list's budget), unless a CPI, jobs
  report or FOMC release is within 45 minutes (dates in `config/calendars/events.csv`) or that stock reports earnings within 24 hours
  (fetched from Nasdaq daily). The run's loss limit is what is left of the pot, at most 3 days of budget.
- **Running:** it closes the run 15 minutes before a release, before earnings, when the market fails the scan's checks, or when another
  setup trades 1.5× as much (after 20 minutes). It rests 10 minutes after a stop.
- **It turns itself off** when you take over: your own run, `stop` or `close`.
- **LIVE** needs `LBOT_LIVE=1` and your confirmation when you turn it on. The doctor runs before every start.

It uses the scan's backtests. Unlike the Arcus autopilot it has no weeks-long hourly playbook yet, because there are few recorded days; it
improves as the recorder adds days.

## 8. Telegram

There is no Lighter Telegram bot: the one Telegram bot serves Lighter too (set up once, main [README](../README.md) section 2). A Lighter
command is the Arcus command with `l_` in front; `/l status` with a space works too, and `/l` shows the Lighter menu. Every Lighter
message starts with **LIGHTER**, and a command it mentions is written `/l_…`, so tapping it stays on Lighter: `/closeall` closes Arcus,
`/l_closeall` closes Lighter.

| Command | What |
|---|---|
| `/l_top3`, `/l_cheapest`, `/l_maxvolume` | The lists, with ▶️ buttons that open the run form with that setup |
| `/l_run` | The run form: market, then one tap per field (Mid/Smart/Grid/Touch, spread, bias, leverage, run stop), 📝 Paper or 🔴 LIVE. In one line: `/l_run SPY smart +1 50x paper sl=10 vol=1m` |
| `/l_status`, `/l_dashboard` | What runs (the dashboard updates every 10 s) |
| `/l_balance`, `/l_account` | Equity, positions, 30-day volume and PnL, live points |
| `/l_auto`, `/l_auto budget 5`, `/l_auto cost 0.05` | The autopilot |
| `/l_pause`, `/l_unpause`, `/l_stop`, `/l_closeall`, `/l_resumeaftersl` | Control (Confirm button) |
| `/l_settings`, `/l_set NAME VALUE` | capital, trade_share, max_capital, position_stop, daily_stop, kill, volume_cost, scan_every, max_lev |
| `/l_scannow`, `/l_help` | Scan now; help |

A LIVE run needs `LBOT_LIVE=1`, a passing doctor and the code the bot shows typed back within 2 minutes. It posts by itself (each starting
with LIGHTER): a new #1 in Most Volume, a list's pick paused or resumed, a run's stop, kill or end, a run gone silent, and the autopilot's
starts, stops and ends.

## 9. Command reference

| Command | What |
|---|---|
| `tbot up` / `down [--all]` / `status` | The whole machine's services, this scout among them (`tbot up lighter`: this one) |
| `lighter up` / `scout [stop]` / `down [--all]` / `status [--json]` | This scout alone; `scout run` is the same daemon in this terminal; its own status screen with the lists |
| `lighter markets` | Every Lighter perp: max leverage, tick, minimum order, 24 h volume |
| `lighter record [--seconds N] [--markets A,B] [--no-depth]` | Record by hand (the scout does it) |
| `lighter scout run [--record-only \| --follow]` / `scout scan [--capital 250] [--markets A,B] [--full] [--as-of now\|tape]` | The scout daemon (with no flag the machine's `BOT_ROLE` decides what it does) / one scan now |
| `lighter backtest MARKET SETUP [--capital 100] [--lev 50] [--stops 2/5/25] [--days D1,D2]` | One setup, day by day, with markouts |
| `lighter run MARKET SETUP [--lev N\|max] [--capital X] [--sl X] [--tp X] [--vol 1m] [--seconds N [--flat]] [--live] [--bg]` | Run a setup (`--seconds`: stop after that long, position kept; with `--flat`, closed first) |
| `lighter pilot approve N [--list most\|cheapest\|max] [--live]` | Run a list's pick |
| `lighter pause` / `unpause` / `stop` / `close` / `resume` `[--mode paper\|live]` | Control the run |
| `lighter auto [on\|off\|set\|status] [--live] [--budget X] [--cost X]` | The autopilot |
| `lighter doctor [MARKET] [--lev N]` | Everything a live run needs (read-only) |
| `lighter leverage MARKET [X]` | The account's leverage on a market; with a number, set it (needs `LBOT_LIVE=1` and `yes` typed; a run sets its own at start) |
| `lighter livetest [MARKET] [--only STEPS] [--skip-dms] [--dms-only] [--max-loss 1] [--lev-low 5] [--wait 20]` | **Real money, minimum size:** every kind of request the bot sends, once, with a report (section 6) |
| `lighter set [NAME VALUE]` | See or change a setting (`default` undoes it) |
| `lighter account` | The account as Lighter keeps it |
| `lighter keys` | A new API key pair (local only; register it with `scripts/register_key.py`) |

## 10. How it differs from the Arcus bot

| | Arcus | Lighter |
|---|---|---|
| Fees | 0 maker / 2.25 bp taker | 0 / 0 (standard account) |
| Loop | 1 s, requote by cancel + place | 0.5 s, requote by modify in one batch; at most 54 a minute |
| Latency modelled | 150 ms | 280 ms maker (200 ms speed bump + network), 380 ms taker |
| Fill model | queue at the touch (best bid/offer only) | queue at any price, from the recorded depth |
| Modes | Mid, Grid, Smart | Mid, Grid, Smart, **Touch** |
| Stops | position 1–5% (follows the market) / 2% / 10% | 2% / 5% / 25% |
| A dead bot's orders | `scheduleCancel` 60 s, every 20 s | each quote expires in 5.5 min, replaced 2 min before (the scheduled cancel-all does not fire by itself) |
| Off-hours rules | RWA session margins, trading bounds | none: Lighter's perps trade 24/7 at one margin |

## 11. Layout and troubleshooting

```
lighter/
  lighter_bot/venue/      Lighter itself: markets, signer (official library via ctypes), nonces, REST (request budget), WebSocket, order book
  lighter_bot/trade/      strategy (the setups), guard (the stops), sizing, feed, paper and live exchanges, engine, runner
  lighter_bot/scout/      tape, recorder, sim (the backtest), scan (lists), pilot, autopilot, calendar, service
  lighter_bot/telegram/   the Lighter panel of the one Telegram bot: bot.py (cards, form, buttons, alerts), embed.py (the /l_ commands, the LIGHTER label)
  lighter_bot/cli.py      `lighter`;  ops.py (processes), doctor.py, settings.py, account.py, config.py, log.py
  config/                 app.yaml, calendars/events.csv
  scripts/                register_key.py
  tests/                  offline: no network, no keys, no processes
```

Tests and lint: `make test`, `make lint` in the repository root.

| Problem | Check |
|---|---|
| `livetest` refuses: "already has ... on SPY" | It will not touch orders or a position it did not place. Name a market you hold nothing on: `lighter livetest QQQ` |
| `livetest` says "DID NOT END CLEAN" | Open the Lighter app now: cancel the orders and close the position on that market by hand. Then send the report |
| `doctor`: key registered FAIL | Register the key (`scripts/register_key.py`); wait a minute after registering |
| `doctor`: account tier WARN | A premium or plus account pays fees. Switch back to standard in the Lighter app (once a day) |
| Log says "rate limited" | Lighter answered 429: the bot paused 60 s and requotes less. If it repeats, lower `requests.quotes_per_min` in `config/app.yaml` |
| `canceled-post-only` rejects | A quote landed after the price moved through it (the 200 ms speed bump). Occasional is normal |
| Lists empty: "1 day(s) of data" | Each market needs recorded days; let the scout record |
| "no fresh book" | The WebSocket is down or stuck: the bot pulls its quotes and reconnects by itself |
