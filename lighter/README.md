# The Lighter part of the trading bot (`bot lighter`)

Maker (limit-order) trading on **Lighter perps on Robinhood Chain**, built from the Arcus part's ideas and written
again for Lighter:
- the same setups: **Mid**, **Grid**, **Smart**, a directional **bias**, run limits `sl=` `tp=` `vol=`, plus
  **Touch**, a Lighter-only mode;
- the same scout (record, backtest, rank into three lists), pilot and autopilot.

It is one part of **one bot** (`treading-bot/bot`, the main [README](../README.md)): installed by the bot's
`make install`, started by `bot up`, its keys in the bot's one `.env`, and controlled from the bot's **one Telegram
bot**, where a Lighter command is the Arcus command with `l_` in front (`/l_status`, `/l_run`, `/l_closeall`).
What stays its own is what must: its engine (Lighter's orders, fees and limits are not Arcus's), its data
(`lighter/data`), its settings and its state.

**`lbot <command>` in this guide is `bot lighter <command>`**, run from `treading-bot/bot` (`bot` is
`.venv/bin/bot`; `.venv/bin/lbot` is there too and is the same thing).

A standard Lighter account pays **0% maker and 0% taker**. The backtests on Lighter's recorded books put the best
setups at **−0.1 to 0.3 bp per dollar traded**, against 0.8–1.7 bp for the best Arcus ones. Why, and how sure that is
(one recorded day so far): [docs/RESEARCH.md](docs/RESEARCH.md). How accurate the Arcus bot's own backtests were:
[docs/ARCUS_REVIEW.md](docs/ARCUS_REVIEW.md).

> **Risk warning.** Experimental software that can place real orders with real money. Backtests are estimates. Run
> paper first, then small. Nothing here is financial advice.

## Quick start

From `treading-bot/bot`:

| Command | What it does |
|---|---|
| `make install` | One time: the whole bot, this part included, into `bot/.venv` |
| `bot up` | Everything in the background: with the Arcus services, the Lighter scout (records every Lighter perp, scans every 30 min, runs the Lighter autopilot) and the one Telegram bot |
| `bot status` | One screen; its LIGHTER lines show the scout, the recorder and the run. `bot lighter status` has the lists too |
| `bot lighter run SPY "smart +1"` | Paper run in this terminal (Ctrl-C stops it; `--bg` in the background) |
| `bot lighter pilot approve 1` | Run the Most Volume list's #1 in paper (`--list cheapest`/`max`, `--live`) |
| `bot lighter close` / `bot lighter stop` | Close the position and stop / stop with the position kept |
| `bot down` | Stop the scouts and Telegram (`--all`: the runs too, positions kept) |

From the phone, in the bot's one Telegram bot: `/l` (the Lighter menu), `/l_top3`, `/l_run`, `/l_status`,
`/l_dashboard`, `/l_auto`, `/l_closeall`.

## Contents

1. [How it works](#1-how-it-works)
2. [Install](#2-install)
3. [Credentials](#3-credentials)
4. [The setups](#4-the-setups)
5. [Sizes and stops](#5-sizes-and-stops)
6. [The scout: record, backtest, lists](#6-the-scout-record-backtest-lists)
7. [Running: paper and live](#7-running-paper-and-live)
8. [The autopilot](#8-the-autopilot)
9. [Telegram](#9-telegram)
10. [The server PC](#10-the-server-pc)
11. [Command reference](#11-command-reference)
12. [How it differs from the Arcus part](#12-how-it-differs-from-the-arcus-part)
13. [Layout and development](#13-layout-and-development)
14. [Troubleshooting](#14-troubleshooting)

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

- **One implementation.** The backtest and the running bot call the same code for the quotes
  (`lbot/trade/strategy.py`) and the stops (`lbot/trade/guard.py`), so they cannot disagree.
- **One market, one setup at a time.** Starting a new run closes the old one first.
- **Paper** trades the live market data with simulated orders and the backtest's fill rules, including Lighter's
  speed bump and the request limit.

## 2. Install

macOS or Linux, Python 3.12 and [uv](https://docs.astral.sh/uv/). There is one install for the whole bot:

```bash
cd treading-bot/bot
make install
.venv/bin/bot lighter --help
```

The Lighter signing library comes inside the `lighter-sdk` package, for macOS (Apple and Intel), Linux (x86 and
ARM) and Windows. Nothing needs compiling. (`make install` inside `lighter/` still builds this part alone into
`lighter/.venv`, for working on it by itself; the bot does not use that environment.)

## 3. Credentials

Recording, backtests and paper runs need none. For live, the keys go into the bot's one file, `treading-bot/bot/.env`
(the "Lighter" lines of `bot/.env.example`), next to the Arcus ones:

| Variable | What |
|---|---|
| `LIGHTER_ADDRESS` | Your wallet address (the L1 address that owns the Lighter account) |
| `LIGHTER_API_PRIVATE_KEY` | An API key's private part (below) |
| `LIGHTER_API_KEY_INDEX` | Its slot, 4–254 (0–3 and 157 are the Lighter apps'); default 4 |
| `LIGHTER_ACCOUNT_INDEX` | Only to trade a sub-account; the main account is found from the address |
| `LBOT_LIVE` | `1` allows live runs (each still needs the doctor and your typed confirmation) |

Telegram needs nothing here: the bot's one Telegram bot (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` in the same file)
serves Lighter too ([section 9](#9-telegram)). A `lighter/.env` from before is still read and wins where it sets
something.

**Getting an API key** (once, on your own machine). Registering a key needs a signature from your wallet, so it is
your step:

```bash
.venv/bin/python ../lighter/scripts/register_key.py --slot 4
```

It asks for the wallet's private key (hidden, used once, never stored), makes a key pair, registers it and prints the
three lines for `.env`. Then check everything:

```bash
.venv/bin/bot lighter doctor SPY
```

`doctor` is read-only. It checks:
- the switch, the credentials and the signer;
- the clock against Lighter's;
- the account, its funds, that Lighter holds your key, and the account tier (it must be **standard** for 0% fees);
- the market, and the sizes at your equity.

It must end in READY before a live run starts.

**Funding:** deposit USDG to your Lighter account on Robinhood Chain (the Lighter app, or the `deposit` method; at
least 1 USDG).

## 4. The setups

Named the Tread.fi way: a mode, a spread in bps, a bias when not Neutral ("Mid +1", "Smart +0.5", "Grid +2 Short").

| Mode | Quotes | On Lighter |
|---|---|---|
| **Mid s** | both sides `s` bps from the mid, following it; skew against the position from +1 bp (κ 1) | +0.5 to +1 is the sweet spot. Mid 0 sits at the mid, dozens of ticks inside the spread on crypto, where fills are the most informed |
| **Smart s** | Mid, less the side that would add to the position while the book leans hard against it (imbalance beyond 0.6) or the price just moved against it (0.5 bp in 5 s) | The cheapest mode on 6 of 8 markets. SPY Smart +1 −0.11 bp, ETH Smart +0.5 0.00, NVDA Smart +1 0.01 |
| **Touch s** | both sides `s` bps behind the best bid and ask (0 joins them) | New for Lighter's wide-in-ticks books; dominated by Mid in the backtests, kept for `/run` |
| **Grid s** | around the last fill: a sell never below the last buy + s; soft reset at 0.5% against the position | Dearer than Mid on most markets, as on Arcus |
| **Bias** Long / Short | holds half the position cap on that side, sizes skewed toward it | A view on the price, not an edge: the lists show biased setups only once a market has 3 recorded days |

**Run limits** (any run): `sl=` the run may lose this many $ in all (it lifts the daily stop and the kill to it);
`tp=` close and stop once up this much; `vol=` close and stop after this much volume.

**Hidden defaults** (the research behind each: [docs/RESEARCH.md](docs/RESEARCH.md), section 6):

| Default | Why |
|---|---|
| Decisions twice a second, up to 54 requotes a minute | Fresher quotes cost 0.05–0.35 bp less. The budget, not the fee, is Lighter's constraint |
| Requote only when the wanted price is 0.25 bp (or 2 ticks) away | 0.1 bp was no cheaper and uses more requests |
| No skew up to +0.5 bp from the mid | Skewing there only gives up fills |
| Modify, not cancel and replace | One transaction per side, and the order keeps its id |
| One order per side | As on Arcus |

## 5. Sizes and stops

Every size and stop is a share of the **capital**: the account's equity × `trade_share`, at most `max_capital`,
rounded down to a fixed series. `/set capital 250` fixes it.

| Quantity | Rule | $100 at 50x |
|---|---|---|
| Order size | capital × leverage ÷ 2.5 | $2,000 |
| Position cap | 2 × order | $4,000 |
| Position stop | 2% of capital: close (maker at the touch, taker after 20 s), rest 60 s | $2 |
| Daily stop | 5%: close, no new orders until 00:00 UTC | $5 |
| Kill | 25% below the peak: close with a taker order and stop until `resume` | $25 |

- **Floor:** every order at least 1.2× Lighter's minimum order ($10, or the minimum size × price: BTC about $17).
- **Ceiling:** one order never larger than the market's 99th-percentile taker order (from the tape).
- **Leverage:** each market's maximum (BTC, ETH, SPY, QQQ 50x; SOL and metals 25x; big stocks 20x; the rest 3–10x).
  `/set max_lev 20` caps it everywhere.
- **Why the stops differ from Arcus's 1/2/10%:**
  - a taker exit costs no fee here, so a stop costs only the spread;
  - at 50x, a 15% kill fired on days that were well up and then gave some back.
- **Other protections:**
  - Lighter's own dead man's switch: a scheduled cancel-all 5.5 minutes ahead, moved every minute. If the bot or
    its machine dies, Lighter cancels everything by itself within about 5 minutes;
  - a stale feed (no frame for 10 s) pulls the quotes;
  - an error in a second cancels all orders;
  - a 429 from Lighter pauses requests 60 s and lowers the requote rate 20%;
  - positions and orders are reconciled with Lighter every 5 minutes.

## 6. The scout: record, backtest, lists

`lbot scout run` (started by `bot up`):

**Records** every active perp over two WebSockets into `data/tape/<MARKET>/<UTC day>/`:
- the best bid and offer on every change;
- every trade, with its taker transaction and both accounts;
- the top 20 levels of each side, once a second;
- mark, index and funding, once a second.

It is about 0.1–0.3 GB a day for all 57 markets (the 8 busiest took 72 MB over 18 hours on Sep 24), pauses under
5 GB of free disk, and writes its health to `data/recorder.json`.

**Scans** every 30 minutes (`/set scan_every`):
- **The menu, 36 setups:**
  - Mid 0, +0.25, +0.5, +1, +1.5, +2, +3 × Neutral/Long/Short;
  - Grid 0, +1, +2 × the three biases;
  - Smart 0, +0.5, +1, +2;
  - Touch 0, +0.5.
- **How:** on every market with a recorded day (18+ hours), at your capital, each market's maximum leverage and
  your stops. Each UTC day starts flat.
- **Cost:** full days are cached; the last 24 hours are re-run for the shortlist.

**Checks** (every list):
- no kill or liquidation on any day, and at least 5 fills a day;
- enough recorded days;
- the market not trending or unusually wild in the last hour;
- its data fresh.

**Lists:**
- 🚀 **Most Volume:** the most volume among setups costing at most your budget (`/set volume_cost`, default $0.10
  per $1,000 = 1 bp).
- 💎 **Cheapest:** the lowest cost among setups trading at least 50× the capital a day.
- 🔥 **Max Volume:** the most volume whatever it costs.

Best setup per market, top 3.

**Files:**
- `data/scout/report.txt`: the lists and every market's best;
- `latest.json`: what the pilot and Telegram read;
- `reports/<day>.txt`: the last scan of each day;
- `ceilings.json`: the order ceilings.

**Old recordings:** `lbot tape import ../research/treading-bot-data/data` imports the Docker-era recorder's Lighter
Parquet files (Sep 24).

## 7. Running: paper and live

- **Paper:**
  - `lbot run ETH "smart +0.5"` runs in this terminal; add `--bg` for the background;
  - `lbot pilot approve 1` runs the #1 of Most Volume;
  - Telegram ▶️ → 📝 Paper.
- **Live**, in order:
  1. Register a key and fund the account ([section 3](#3-credentials)).
  2. Put `LBOT_LIVE=1` in `.env`.
  3. `lbot doctor SPY` must say READY. Check the `account tier` line says standard.
  4. `lbot run SPY "smart +1" --live --sl 10`, read the doctor's lines, and type `LIVE`. From Telegram: 🔴 LIVE,
     then type the 6-digit code it sends within 2 minutes.
- **Before the first quote,** the run cancels your orders on that market, sets the leverage (cross margin) and arms
  the dead man's switch. It treats every order on that market as its own: trade by hand on another market, or use
  a sub-account (`LIGHTER_ACCOUNT_INDEX`).
- **Control** (CLI, or Telegram):
  - `lbot pause` / `unpause`: no new orders; closing orders keep working;
  - `lbot close`: close the position (maker, then taker) and stop;
  - `lbot stop`: quotes cancelled, the position kept;
  - `lbot resume`: trade again after the kill or a daily stop.
- **A list's pick is watched by the pilot.** It is paused while it fails the checks and resumes after two scans
  that pass. Your own pick is never paused for its numbers.
- **Files** (paper and live never mix):
  - `state/status-<mode>.json`: what the dashboard shows;
  - `state/run-<mode>.json`: the run, so a restart continues it;
  - `state/fills-<mode>.jsonl`: every fill;
  - `logs/`.

## 8. The autopilot

`/auto` or `lbot auto on [--live] [--budget 5] [--cost 0.05]`. Off by default.

- **The pot:**
  - `budget` dollars are added at 00:00 UTC;
  - unspent money carries over, up to 7 days;
  - each run's result comes out of it, and a profit goes back in.
- **Nothing running:** it starts the Most Volume list's best setup within the cost ceiling (default: the list's
  budget), unless:
  - a CPI, jobs report or FOMC release is within 45 minutes (dates in `config/calendars/events.csv`);
  - that stock reports earnings within 24 hours (fetched from Nasdaq daily).

  The run's loss limit is what is left of the pot, at most 3 days of budget.
- **Running:** it closes the run 15 minutes before a release, before earnings, when the market fails the scan's
  checks, or when another setup trades 1.5× as much (after 20 minutes). It rests 10 minutes after a stop.
- **It turns itself off** when you take over: your own run, `stop` or `close`.
- **LIVE** needs `LBOT_LIVE=1` and your confirmation when you turn it on. The doctor runs before every start.

It uses the scan's backtests. Unlike the Arcus autopilot it has no weeks-long hourly playbook yet: there is one
recorded day. It gets better as the recorder adds days.

## 9. Telegram

There is no Lighter Telegram bot: the trading bot's **one** Telegram bot serves Lighter too (set up once, main
[README](../README.md) section 2). A Lighter command is the Arcus command with `l_` in front; `/l status` with a
space works too, and `/l` shows the Lighter menu. Every Lighter message starts with **LIGHTER**, and a command it
mentions is written `/l_…`, so tapping it stays on Lighter: `/closeall` closes Arcus, `/l_closeall` closes Lighter.

| Command | What |
|---|---|
| `/l_top3`, `/l_cheapest`, `/l_maxvolume` | The lists, with ▶️ buttons that open the run form with that setup |
| `/l_run` | The run form: market, then one tap per field (Mid/Smart/Grid/Touch, spread, bias, leverage, run stop), 📝 Paper or 🔴 LIVE. In one line: `/l_run SPY smart +1 50x paper sl=10 vol=1m` |
| `/l_status`, `/l_dashboard` | What runs (the dashboard updates every 10 s) |
| `/l_balance`, `/l_account` | Equity, positions, 30-day volume and PnL, live points |
| `/l_auto`, `/l_auto budget 5`, `/l_auto cost 0.05` | The autopilot |
| `/l_pause`, `/l_unpause`, `/l_stop`, `/l_closeall`, `/l_resumeaftersl` | Control (Confirm button) |
| `/l_settings`, `/l_set NAME VALUE` | capital, trade_share, max_capital, position_stop, daily_stop, kill, volume_cost, scan_every, max_lev |
| `/l_scannow`, `/l_help` | |

A LIVE run needs `LBOT_LIVE=1`, a passing doctor and the code the bot shows typed back within 2 minutes.

**It posts by itself** (each starting with LIGHTER):
- a new #1 in Most Volume;
- a list's pick paused or resumed;
- a run's stop, kill or end;
- a run gone silent;
- the autopilot's starts, stops and ends.

## 10. The server PC

Record around the clock on the server, as the Arcus scout does. **Recording needs no keys, no `.env` and no money,
and places no orders**: it only reads Lighter's public market data. The code is on `main` (since pull request 23).

### 10.1 On the machine that runs the bot

Nothing separate to start: it is part of `bot up`. From the folder that holds `treading-bot` on the server:

```bash
cd treading-bot && git pull
```
```bash
cd bot && make install
```
```bash
.venv/bin/bot down
```
```bash
.venv/bin/bot up
```

`make install` adds this part to the bot's environment (once, and after an update). `bot down` and `bot up` restart
the services on the new code; a running Arcus trading bot is not touched by either (`bot down --all` would stop it).
`bot up` then starts the Lighter scout next to the Arcus one: it records every Lighter perp (best bid and offer,
trades, the top 20 levels, the market statistics) and backtests every 30 minutes. Its data is `lighter/data/`, and
Lighter's request limits are separate from Arcus's.

Check it (a minute after starting, then whenever you like):

```bash
.venv/bin/bot status
```
```bash
cat ../lighter/data/recorder.json
```

`bot status` has a LIGHTER block: the scout, the recorder's markets and rows, the run. From the phone: `/l_status`.

| In `recorder.json` | Healthy |
|---|---|
| `markets` | about 58 |
| `rows_total` | climbing between two looks |
| `last_msg_age_s` | a few seconds |
| `book_gaps`, `reconnects` | small; each one is a short hole in that market's book |
| `paused_for_disk` | false (recording pauses under 5 GB free) |

- **After a reboot** run `.venv/bin/bot up` again: it does not come back by itself.
- **To stop it:** `.venv/bin/bot down`.
- **Keep a Mac awake** ([reference](../bot/docs/REFERENCE.md), 8.4 step 2): a sleeping machine records nothing and the gap cannot be
  filled later.
- **Disk:** about 0.1–0.3 GB a day for all markets.

### 10.2 On a machine that only records (Docker)

```bash
cd treading-bot/lighter
docker compose up -d --build
docker compose logs --tail 30 scout
cat data/recorder.json
```

The container is `lighter-scout`, separate from the Arcus `arcus-scout`: both can run on the same machine. Do not
use it on a machine that runs the bot natively (`bot up` already records there), nor on one that will trade Lighter
live: a container cannot see a native bot's process.

### 10.3 Bring the tape back

One command packs it with everything else the bot recorded and traded, Arcus included (main
[README](../README.md), section 9). On the server, from `treading-bot/bot` (the recorder keeps running):

```bash
.venv/bin/bot export
```

Copy the file it names (`treading-bot/exports/tb-<date>-<time>Z.tar`) to the machine that trades, into
`treading-bot/exports/` or `~/Downloads`, then there, from `treading-bot/bot`:

```bash
.venv/bin/bot import
```
```bash
.venv/bin/bot lighter scout scan
```

`bot import` merges the tape into `lighter/data/tape` and must end with `0 short`. Every recorder start writes its
own part files (`bbo-rec<number>-00001.npz`), and a day is joined, sorted and de-duplicated when it is read, so
nothing is lost or doubled. The next `bot export` sends only what is new.

To trade Lighter live on the server itself, put the Lighter lines into `bot/.env` there (section 3) and use `/l_run`,
`bot lighter run` or the pilot as on any machine. The pilot and the autopilot start runs as processes on the same
machine.

## 11. Command reference

Each is `bot lighter <command>` from `treading-bot/bot` (`bot lighter markets`, `bot lighter doctor SPY`).

| Command | What |
|---|---|
| `bot up` / `bot down [--all]` / `bot status` | The whole bot's services, this part's scout among them; one status screen |
| `lbot up` / `down [--all]` / `status [--json]` | This part's scout alone; its own status screen with the lists |
| `lbot markets` | Every Lighter perp: max leverage, tick, minimum order, 24 h volume |
| `lbot record [--seconds N] [--markets A,B] [--no-depth]` | Record by hand (the scout does it) |
| `lbot scout run` / `scout scan [--capital 250] [--markets A,B] [--full]` | The scout daemon / one scan now |
| `lbot backtest MARKET SETUP [--capital 100] [--lev 50] [--stops 2/5/25] [--days D1,D2]` | One setup, day by day, with markouts |
| `lbot tape import PATH` | Import the old recorder's Parquet files |
| `lbot run MARKET SETUP [--lev N\|max] [--capital X] [--sl X] [--tp X] [--vol 1m] [--live] [--bg]` | Run a setup |
| `lbot pilot approve N [--list most\|cheapest\|max] [--live]` | Run a list's pick |
| `lbot pause` / `unpause` / `stop` / `close` / `resume` `[--mode paper\|live]` | Control the run |
| `lbot auto [on\|off\|set\|status] [--live] [--budget X] [--cost X]` | The autopilot |
| `lbot doctor [MARKET] [--lev N]` | Everything a live run needs (read-only) |
| `lbot set [NAME VALUE]` | See or change a setting (`default` undoes it) |
| `lbot account` | The account as Lighter keeps it |
| `lbot keys` | A new API key pair (local only; register it with `scripts/register_key.py`) |

## 12. How it differs from the Arcus part

| | Arcus part | Lighter part |
|---|---|---|
| Fees | 0 maker / 2.25 bp taker | 0 / 0 (standard account) |
| Loop | 1 s, requote by cancel + place | 0.5 s, requote by modify in one batch; at most 54 a minute (60 requests in all) |
| Latency modelled | 150 ms | 280 ms maker (200 ms speed bump + network), 380 ms taker |
| Fill model | queue at the touch (best bid/offer only) | queue at any price, from the recorded depth |
| Modes | Mid, Grid, Smart | Mid, Grid, Smart, **Touch** |
| Best setups | BTC Mid 0 (volume, ~1 bp live), SPY Smart 0 | SPY/ETH/NVDA/QQQ Smart +0.5 to +1, Mid +0.5 to +1 (−0.1 to 0.3 bp, backtest) |
| Stops | position 1–5% (follows the market) / 2% / 10% | 2% / 5% / 25% |
| Dead man's switch | `scheduleCancel` 60 s, every 20 s | scheduled cancel-all 5.5 min ahead, every 60 s |
| Off-hours rules | RWA session margins, trading bounds | none: Lighter's perps trade 24/7 at one margin |
| History | 8 weeks of trades, 8 days of books | 1 day of books (recording from now on) |

## 13. Layout and development

```
lighter/
  lbot/venue/      Lighter itself: markets, signer (official library via ctypes), nonces, REST (request budget),
                   WebSocket, order book
  lbot/trade/      strategy (the setups), guard (the stops), sizing, feed, paper and live exchanges, engine, runner
  lbot/scout/      tape, recorder, importer, sim (the backtest), scan (lists), pilot, autopilot, calendar, service
  lbot/telegram/   the Lighter panel of the bot's one Telegram bot: bot.py (cards, form, buttons, alerts),
                   embed.py (how it sits inside that bot: /l_ commands, the LIGHTER label)
  lbot/cli.py      `lbot`;  ops.py (processes), doctor.py, settings.py, account.py, config.py, log.py
  config/          app.yaml, calendars/events.csv
  scripts/         research.py (the sweeps behind docs/RESEARCH.md), register_key.py
  docs/            RESEARCH.md (Lighter), ARCUS_REVIEW.md (the Arcus bot's accuracy)
  tests/           offline: no network, no keys, no processes
```

```bash
make test
make lint
```

## 14. Troubleshooting

| Problem | Check |
|---|---|
| `doctor`: key registered FAIL | Register the key (`scripts/register_key.py`); wait a minute after registering |
| `doctor`: account tier WARN | A premium or plus account pays fees. Switch back to standard in the Lighter app (once a day) |
| Log says "rate limited" | Lighter answered 429: the bot paused 60 s and requotes less. If it repeats, lower `requests.quotes_per_min` in config/app.yaml |
| `canceled-post-only` rejects | A quote landed after the price moved through it (the 200 ms speed bump). Occasional is normal |
| Lists empty: "1 day(s) of data" | Each market needs recorded days; let the scout record |
| "no fresh book" | The WebSocket is down or stuck: the bot pulls its quotes and reconnects by itself |
