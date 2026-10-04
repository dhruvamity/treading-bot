# treading-bot

A maker (limit-order) trading bot for **Arcus perpetual futures**. Its goal is as much **maker volume** as possible at
the lowest cost per dollar traded. Its setups are Tread.fi's: **Mid** or **Grid**, a **spread** in bps and a
**directional bias** ("Mid 0", "Mid +1 Long", "Grid +3 Short"), plus **Smart**: Mid that leaves a side out for the
seconds its fill would likely lose ([section 7](#7-strategies-explained)). It sizes itself from your account: every order size, position cap and
stop is a fixed share of the capital, so the same setup runs on $20 or $20,000 ([4.6](#46-capital-the-least-and-the-most)).
The examples in this guide use $100.

It does three things:

1. **Scout.** It records every Arcus perp around the clock and, every 30 minutes, backtests 8 setups (Mid and Smart,
   at 0 to +3 bp) on every market at its maximum leverage. It ranks them by volume and by
   cost per $1,000 traded, among those that pass a set of safety checks.
2. **Pilot.** It offers you the **top 3**. You approve one, and it deploys that exact setting (paper or live), then
   keeps checking it against fresh data.
3. **Runner.** It trades the approved setting with the same risk rules the backtest used, plus kill switches, a dead
   man's switch, an independent guardian process and a Telegram control bot for your phone.
4. **Autopilot** (optional, `/auto`). Given a daily budget, it picks, starts, switches and stops setups by itself:
   - it chooses by the session of the week and each market's state now;
   - it stays flat around CPI, jobs reports, FOMC and a stock's earnings;
   - it spends the budget where volume is cheapest ([5.1](#51-the-autopilot-auto)).

> **Risk warning.** This is experimental software that can place real orders with real money. Backtests are
> estimates, not promises: markets change, and the backtest cannot see how your own orders change other traders'
> behaviour. Nothing here is financial advice. Run in paper mode first, and only trade money you can afford to lose.

### Quick start: the only commands you need

From `treading-bot/bot` on the machine that runs the bot (after the one-time install in [3.2](#32-install)):

| Terminal | What it does |
|---|---|
| `bot up` | Starts everything in the background: the scout (records and ranks), the Telegram bot, and the guardian while a live bot runs |
| `bot status` | One screen: what runs, what is deployed, the last scan and its top 3, the balance |
| `bot dashboard` | Live screen, redrawn every 10 s: today's volume and PnL, your capital's profit or loss (Ctrl-C leaves) |
| `bot pilot approve 1` | Trades the #1 setup of the Most Volume list in paper (add `--live` for real money; `--list cheapest` or `--list max` picks from those top 3, `--max-lev` runs it at the market's maximum leverage) |
| `bot pilot close` | Closes the position and stops trading |
| `bot auto` | The autopilot: what it runs now and in the next 24 h (`bot auto on --budget 5`, `--live`, `bot auto off`) |
| `bot down` | Stops the scout and the Telegram bot (`bot down --all`: the trading bot too, position kept) |

(`bot` is `.venv/bin/bot`; activate the venv with `source .venv/bin/activate`, or type the full path.)

On your phone, send `/menu` for buttons, or: `/dashboard` (a live screen that updates itself every 10 s), `/top3`
(best setups, Run), `/auto` (the autopilot), `/openpositions` (what runs), `/status`, `/balance`, `/account` (all-time volume and fees), `/pauseneworders`, `/closeall`, `/settings` and `/set` (change capital, share of the balance, stops, scan
interval without editing files). [Section 9](#9-the-telegram-bot) has them all.

---

## Contents

1. [How it works](#1-how-it-works)
2. [Repository layout](#2-repository-layout)
3. [Tutorial: from zero to a running bot](#3-tutorial-from-zero-to-a-running-bot)
4. [The scout: recording, backtesting, ranking](#4-the-scout-recording-backtesting-ranking)
5. [The pilot: approve, run, re-check](#5-the-pilot-approve-run-re-check)
6. [Risk rules and safety systems](#6-risk-rules-and-safety-systems)
7. [Strategies explained](#7-strategies-explained)
8. [Session files (configuration)](#8-session-files-configuration)
9. [The Telegram bot](#9-the-telegram-bot)
10. [Command reference](#10-command-reference)
11. [The server PC: what runs 24/7](#11-the-server-pc-what-runs-247)
12. [The live bot on a VPS (systemd)](#12-the-live-bot-on-a-vps-systemd)
13. [Daily routine and troubleshooting](#13-daily-routine-and-troubleshooting)
14. [Development](#14-development)
15. [Glossary](#15-glossary)

---

## 1. How it works

```mermaid
flowchart LR
    A[Arcus WebSocket<br/>best bid/offer + trades<br/>all perps] --> B[Scout recorder<br/>data/scout/tape]
    B --> C[Backtest every 30 min<br/>8 setups at max leverage<br/>x every market]
    C --> D[GO checks + ranking<br/>data/scout/report.txt]
    D --> E[Top 3 offered<br/>CLI or Telegram]
    E -->|you approve| F[Pilot writes<br/>config/sessions/pilot.yaml]
    F --> G[Runner trades it<br/>paper or live]
    D -->|after every scan| H[Review: still GO?<br/>pause / resume / suggest]
    H --> G
```

- **One strategy on one market at a time.** The bot never runs several deployments in parallel, and it never switches
  markets without your approval.
- **The backtest and the live bot share their rules.** Capital, order sizes, leverage, the stops, the safety pause
  and the order-budget governor are the same numbers in both, computed by the same code (`bot/common/sizing.py`).
- **Paper mode** uses live market data with simulated orders and fills, through the same code as live.
- **Arcus only.** The bot trades Arcus perps alone: no hedging on another venue, no spot. (The two-venue Lighter
  code, the Autopilot and the old research recorder were removed on 2026-09-25; they are in the git history.)

---

## 2. Repository layout

```
treading-bot/
  README.md                   this guide
  lighter/                    a separate bot for Lighter on Robinhood Chain (package `lbot`; its own guide:
                              lighter/README.md). It shares no code, settings or credentials with `bot/`
  arb/                        funding arbitrage between Arcus and Lighter (package and command `arb`; its own guide:
                              arb/README.md): scanner, backtest, and an executor that runs on paper unless switched
                              to live. It uses the two bots' venue clients and changes neither
  bot/                        the bot (Python 3.12 package `bot`, command `bot`)
    bot/scout/                tape (data store), record (recorder), sim (backtest), scan (menu + ranking), pilot, service
    bot/core/                 runner, engine (the stops), risk engine, order manager, state, ledger, guardian, doctor
    bot/strategies/           setup (Mid / Grid, spread, bias), mid, grid
    bot/telegram/             the Telegram control bot
    bot/venues/               Arcus (REST + WebSocket + signing) and the paper venue (queue-aware fill model)
    config/                   app.yaml (risk limits), venues/, sessions/, calendars/ (CPI, FOMC, NFP, earnings)
    deploy/                   systemd units and a VPS bootstrap script
    docs/RUNBOOK.md           operations handbook; docs/incidents/ what went wrong live and why
    tests/                    offline tests (no network, no keys); tests/sim/ replays synthetic markets through the
                              live engine
    Dockerfile, docker-compose.yml   the scout for a server
    .env.example              the credentials template (copy to .env)
```

Created at run time and never committed: `bot/.env` (credentials), `bot/data/` (recordings and backtest cache),
`bot/state/` (databases, heartbeats, pilot state), `bot/logs/`, `bot/reports/`.

---

## 3. Tutorial: from zero to a running bot

### 3.1 Requirements

- macOS or Linux, Python **3.12**, and [uv](https://docs.astral.sh/uv/) (`brew install uv`, or
  `curl -LsSf https://astral.sh/uv/install.sh | sh`).
- An Arcus account and an **API key** (only needed for live trading and for the account checks; recording and paper
  trading need no keys).
- Money: any amount above the chosen setup's minimum. The floor is under $10 for every market at its maximum leverage,
  but setups only start passing the checks somewhat higher; see [4.6](#46-capital-the-least-and-the-most) for the
  measured numbers.
- Disk: the scout needs about 0.1 GB per day (0.2–0.5 GB with depth recording). Recording pauses by itself under
  5 GB free.
- Optional: a Telegram account for phone control.

### 3.2 Install

```bash
git clone https://github.com/dhruvamity/treading-bot.git
cd treading-bot/bot
make install
.venv/bin/bot --help
```

`make install` creates `.venv` with Python 3.12 and installs the bot plus the dev tools; `bot --help` lists every
command.

All commands below are run from `treading-bot/bot`. `bot` means `.venv/bin/bot` (or activate the venv with
`source .venv/bin/activate`).

> **Copying commands:** the command blocks in this guide contain commands only, so paste them as they are. Run a
> block line by line when its lines are alternatives. (On macOS, zsh does not treat `# ...` as a comment when
> typed or pasted, so a trailing comment would be passed to the command as extra arguments.)

### 3.3 Credentials

```bash
cp .env.example .env
chmod 600 .env
```

Fill in only what you use:

| Variable | What it is | Needed for |
|---|---|---|
| `ARCUS_ADDRESS` | The wallet address (0x…) that owns the API key. `ARCUS_WALLET_ADDRESS` is also accepted. | live, `doctor`, `keys`, `selftest` |
| `ARCUS_API_PRIVATE_KEY` | The API key's private part (64 hex). Arcus web app → API Keys → Generate, or `.venv/bin/python scripts/arcus_register_key.py --env mainnet --account N` on your own machine. | live, `doctor`, `keys`, `selftest` |
| `ARCUS_API_PRIVATE_KEY_2`… | Keys for other subaccounts (one key per subaccount) | optional |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | The Telegram bot and your chat ([section 9](#9-the-telegram-bot)) | Telegram |
| `TELEGRAM_ALLOWED_USER_IDS` | Only these Telegram users may send commands | recommended |
| `BOT_PILOT_LIVE` | `1` allows LIVE deployments from the pilot; empty = paper only | live via the pilot |

The bot asks Arcus for everything else (which subaccount a key trades, when it expires). Check with:

```bash
bot keys
bot doctor
```

`bot keys` lists your API keys as Arcus sees them (subaccount, status, days left). `bot doctor` checks credentials,
account, clock and region, and places no orders.

`.env` is in `.gitignore`: never commit it. For an encrypted alternative see `bot secrets --help`.

### 3.4 Start the scout (recording + backtests)

```bash
.venv/bin/bot up
```

- `bot up` starts the scout in the background (with the Telegram bot if it is set up); logs go to `logs/scout.out`,
  its process id to `state/scout.pid`. `bot status` shows it, `bot down` stops it.
- It connects to Arcus's public WebSocket, subscribes to every online perp and writes data every 5 minutes.
- About 10 seconds after it starts, and then every 30 minutes, it ranks every market and writes
  `data/scout/latest.json` and `data/scout/report.txt`. It stays light enough to share a machine with the trading bot
  ([11.2](#112-what-it-backtests-every-30-minutes)): the full search over all 3,204 setups runs once a day, and each
  30-minute scan re-checks only the few that pass on their full days.
- It backtests at **your capital**: before each scan it reads the subaccount's equity (with `ARCUS_ADDRESS` in `.env`;
  an unfunded account or no address means the $100 paper capital). `--capital 250` sizes for a fixed amount instead,
  e.g. for a deposit you have not made yet.
- A market needs at least one **full day** of data (the recorder up for 20+ hours of a UTC day) before it can be
  ranked. The checks get more reliable as the history grows towards 7 days.
- `bot down` stops it cleanly within seconds: a scan in progress stops (the days it finished stay cached) and the
  recorder writes out its buffers.
- To record and backtest 24/7 on another machine instead, see [section 11](#11-the-server-pc-what-runs-247).

### 3.5 Read the ranking

```bash
cat data/scout/report.txt
bot pilot status
```

`report.txt` is the latest ranking (`bot scout scan` runs one scan now and prints it instead). `bot pilot status`
shows what is deployed and the current top 3.

An example from 2026-09-26 (the server's data at $110 with stops of 3% / 6% / 15%; the numbers change every scan):

```
MOST VOLUME top 3 (the most volume for at most your cost per $1,000 traded, at most $0.20 lost per $1,000; /set volume_cost):
   market       setting                   uses  order fills/d  volume/d   pnl/d   worst    24h  cost
 1 SPY-USD      Mid +1 @ 50x               110  2,200     102    79,556   -7.95   -9.50  -8.73  $0.10 per $1,000
 2 BTC-USD      Grid +1 @ 40x              110  1,760      99    50,711   -8.46   -9.34 -13.82  $0.17 per $1,000
 3 NVDA-USD     Mid 0 Short @ 20x          110    880     324    40,127   -6.80   -7.67 -12.16  $0.17 per $1,000
```

| Column | Meaning |
|---|---|
| setting | The setup ([section 7.2](#72-the-scout-menu-8-setups)) and the leverage it was sized at |
| order | Dollar size of each order |
| fills/d, volume/d | Average maker fills and maker volume (USD) per full day |
| pnl/d, worst | Average and worst daily PnL in USD, after fees and after closing any leftover position |
| 24h | PnL over the last 24 hours (re-run on every scan) |
| cost | In the lists: dollars lost per $1,000 traded (the lists' measure). In the full ranking the column is `why not`: `GO`, or the checks it failed ([section 4.4](#44-go-checks)) |

A second table in the report shows each market **at its maximum leverage**, even when that fails the checks.

### 3.6 Run the #1 setup in paper

```bash
bot pilot approve 1
```

This writes `config/sessions/pilot.yaml` (the exact backtested setting, leverage and stops) and starts
`bot run pilot` in **paper** mode: live market data, simulated orders. The paper account starts with the capital
the scan used, and its sizes follow the paper equity the same way live sizes follow the real one. Watch it:

```bash
bot status
bot report --mode paper
tail -f logs/runs/pilot-paper-*.log
```

`bot status` shows the heartbeat, open orders and positions for each mode; `bot report` shows today's PnL split and
volume.

Or from Telegram: `/top3` → **▶️ 1** → a leverage → **📝 Paper** → Confirm. Let paper run for a few days and compare its daily PnL
and volume with the backtest (`/openpositions` shows both).

### 3.7 Go live (real money)

Do these in order:

1. **Self-test while the account is still empty:** `bot selftest`. It sends every kind of signed request the bot uses
   (dead man's switch, cancel-all, a post-only order 3% away from the price, a modify, a batch, set-leverage). On an
   unfunded subaccount Arcus checks each request and then rejects the orders as `UNDERCOLLATERALIZED`, so nothing can
   trade. Every line should say `PASS` or `INFO`.
2. **Use a dedicated subaccount** if you also trade by hand: the bot treats every order and position on its
   subaccount as its own (it cancels orders it did not place and trades existing positions down).
3. **Deposit USDG** into that subaccount (Arcus web app): at least the minimum for the setup you want
   ([4.6](#46-capital-the-least-and-the-most)); more only adds size once the scout has backtested at the new amount.
4. Put `BOT_PILOT_LIVE=1` in `.env`, and wait for the next scan: the scout then backtests at the account's equity.
5. `bot doctor pilot` must end in **READY**. Its `sizing` line shows the order size, cap and stops for your equity.
   It fails a live start on a market that is not ONLINE, and warns about a market listed in the last 21 days and
   about a stock with no earnings date in `config/calendars/earnings.csv` (the file ships empty: add the next report
   date of the stock you trade as `SYMBOL,YYYY-MM-DD,bmo,source` (or `amc`), so the bot pauses around it).
6. `bot pilot approve 1 --live`, read the summary, and type `LIVE`. From Telegram: **▶️ 1** → a leverage →
   **🔴 LIVE** (or `/openpositions` → **🔴 Go LIVE with this setup** after a paper run), the bot runs `doctor`, then
   you type back the one-time code it sends.

Before the first order the runner sets the session's leverage on Arcus (cross margin) and arms the dead man's switch.

### 3.8 Stop, pause, close

| Want to | CLI | Telegram |
|---|---|---|
| Stop placing new orders (exits keep working) | — | `/pauseneworders [MARKET]`, `/unpause` |
| Close the position and stop the pilot's bot | `bot pilot close` | `/openpositions` → Close & stop |
| Stop the bot (cancels quotes, keeps positions) | Ctrl-C, or `systemctl stop bot` | `/stop` |
| Cancel every open order on the account now | `bot cancel-all --venue arcus` | `/cancelall` |
| Close every position now | `bot flatten --venue arcus [--taker]` | `/closeall [taker]` |
| Clear a safe mode / kill stop after checking why | `bot resume --all` | `/resumeaftersl` |

---

## 4. The scout: recording, backtesting, ranking

### 4.1 What it records

Per market per UTC day, compact NumPy files under `data/scout/tape/<MARKET>/<YYYY-MM-DD>/`:

- **bbo**: the best bid and ask with their sizes: a row whenever a price changes, or sizes change and a second
  has passed.
- **trades**: every trade with price, size, which side the taker was on, and Arcus's `sequenceNumber` (all prints of
  one taker order share it).
- **depth** (optional, `--depth`): the top 10 book levels on each side, sampled once a second when the book changed.
  Used later for a queue-position fill model; the Docker setup records it by default.

It uses two WebSocket connections (three with depth), re-reads the market list every 10 minutes and pauses recording below 5 GB
of free disk. `data/scout/recorder.json` shows its health.

### 4.2 How the backtest works

For each market, each setting and each UTC day (each day starts flat, with 2 hours of warm-up), the simulator in
`bot/scout/sim.py` replays the tape one second at a time, the way the live bot decides once a second (BTC: twice a
second, live and in the backtest, since 2026-10-04; it was 0.2 bp cheaper on 6 of 6 days and changed nothing elsewhere):

- **Timing.** New orders go live and cancels take effect 150 ms later. A post-only order that would cross the book on
  arrival is rejected.
- **Fills (conservative).** A resting order fills only when a taker trades **through** its price. A trade exactly at
  its price does not count, because the queue position there is unknown. One taker order fills us for at most what
  it printed **beyond** our price: with our order resting there, the taker would first have used up the better levels
  and the queue at our price. This is a lower bound; assuming we were first in the queue adds only about 10–15%
  volume at 3 bps from the mid.
- **Costs.** Maker fee 0, taker fee 2.25 bps (Arcus's current fees; the live bot reads them from the API). A taker
  exit pays the opposite best price plus 5 bps slippage on any size beyond what the best level shows. A position left
  at the end of a window is charged the half-spread plus the taker fee.
- **Arcus order budget.** Each subaccount has an order pool of 20,000 actions, growing by one per $0.10 filled. The
  governor doubles the requote tolerance when actions per filled dollar get too high and allows cancels only when the
  pool is nearly empty, exactly like the live governor.
- **Risk rules.** The same stops as live ([section 6.1](#61-the-stops-backtest-and-live)), plus the safety
  pause (its move and spread rules; the recorded books carry no depth, so pilot sessions run without the live
  thin-depth rule too), the liquidation-distance cut and liquidation itself.
- **Skip windows.** The "skip US session" settings place no new quotes from 09:00 to 16:30 New York time on NYSE
  trading days (DST handled, full holidays excluded); a position is worked off with a reduce-only maker order at
  the touch, as during a safety pause. The live engine applies the same rule (`session.skip_et`).
- **Session hours.** Stock, index and commodity perps (RWA) need 1.5× the initial margin to open positions outside
  their session (04:00–20:00 New York time on weekdays; weekends and NYSE holidays are off-hours). The backtest
  shrinks the inventory cap and the order size there, as the live bot does.

Completed days are cached in `data/scout/cache/`, so a scan only re-runs the current 24 hours.

### 4.3 Leverage and order size

Every market is tested at its **maximum Arcus leverage** only (1 / `initialMarginFraction`, e.g. BTC 40x, SPY 50x,
QQQ/GLD/SLV 25x, NVDA 20x, most stocks and alts 10x; `/set crypto_lev` can cap BTC and ETH). `/run` runs any other
leverage without a backtest at it; `bot scout run --ladder` also backtests 20x, 10x, 5x and 2x (5x the work). The
leverage sets the size:

| Quantity | Rule | QQQ at 10x, $100 capital |
|---|---|---|
| Largest position Arcus allows | capital × leverage | $1,000 |
| Inventory cap (`inventory_cap_usd`) | that ÷ 1.25, so the risk engine's hard cap (1.25 × cap) lands on the venue limit | $800 |
| Order size (`order_size_usd`) | half the cap, per level per side | $400 |
| Off-hours (RWA) | cap and order × (off-hours leverage ÷ leverage) | unchanged at 10x (QQQ allows 16.7x off-hours) |
| Stops | position 1–5% (it follows the market), daily 2%, kill 10% of the capital ([6.1](#61-the-stops-backtest-and-live)) | $1–5 / $2 / $10 |

Leverage does not create fills by itself; it lets you post bigger orders, and bigger orders capture more of each
taker order that reaches them. The stops grow with the **capital**, not with the leverage, so at high leverage a
small price move reaches them.

### 4.4 GO checks

A setting is **GO** only when all three windows pass:

| Window | Checks |
|---|---|
| Long: up to the last 7 full days | at least **3 full recorded days** (every market); average PnL/day ≥ −0.25% of the capital; at most one daily stop; never the kill or a liquidation; at least half the days not negative; at least 5 fills a day |
| Short: last 24 h (re-run every scan) | 24 h PnL ≥ −0.25%; last 6 h ≥ −0.50%; no kill in the last 24 h; at least 30% of its usual fills (the flow is still there) |
| Now: last 60 one-minute prices | not trending (efficiency ratio < 0.5); volatility and spread under 2× their usual level; data less than 5 minutes old |

The percentages are of the capital the setting uses: −0.25% is −$0.25 a day on $100 and −$2.50 on $1,000.

**New markets.** Arcus pre-lists markets as OFFLINE (September 2026: F, BAC, CCL, VT, SGOV, RVI) and switches them
on later, and a fresh listing can go back OFFLINE (KBONK did, hours after listing). The scout handles this:
- a market's first recorded day counts as a full day only if its own data covers 20 hours of it;
- every market needs **3 full recorded days** before it can be in a list. One or two days say little (on
  2026-09-26 one-day markets ranked beside five-day ones), and it also covers new listings, whose first week trades
  unusually and which start with small open-interest caps ($100k for CPER, GME, QNT, MRNA). `/run` can still start a
  market with less; its cards warn "1 day of data";
- a deployment whose market goes offline is paused, and resumes after two GO scans once it trades again.

**Ranking:** GO settings rank by maker volume per day, then PnL. The best setting per market is kept, and the top 3
markets are offered.

### 4.5 Files

| File | What |
|---|---|
| `data/scout/report.txt` | The latest ranking, readable |
| `data/scout/reports/<day>.txt` | The last scan of each UTC day |
| `data/scout/latest.json` | The full latest scan (the pilot reads it) |
| `data/scout/scans/<time>.json` | Every scan, without the per-setting list |
| `data/scout/recorder.json` | Recorder health: markets, rows, message age, free disk |
| `data/scout/markets.json` | Arcus market parameters (ticks, minimums, margins, status, listing time), refreshed every 10 minutes |

### 4.6 Capital: the least and the most

Nothing in the bot is tied to a fixed amount. Every size and stop is a share of the **capital**
(`bot/common/sizing.py`, used by the backtest, the session file, the live engine and the doctor):

| Quantity | Formula | $50 at QQQ 20x | $1,000 at QQQ 20x |
|---|---|---|---|
| Order size | capital × leverage ÷ 2.5 | $400 | $8,000 |
| Inventory cap | 2 × order | $800 | $16,000 |
| Position stop (the risk per trade) | 1% of capital | $0.50 | $10 |
| Daily stop | 2% of capital | $1 | $20 |
| Kill | 10% of capital | $5 | $100 |

**Where the capital comes from.** `sizing.capital_usd` in `config/app.yaml`:
- `auto` (default): the subaccount's equity × `capital_frac`, capped at `max_capital_usd`. With no `ARCUS_ADDRESS`
  or an unfunded account it is `paper_capital_usd` ($100).
- A number: exactly that, e.g. to rank setups for a deposit you have not made yet (`bot scout run --capital 500`).

The capital is rounded **down** to a fixed series (…, 90, 100, 110, 125, 140, 160, …; about 20 steps per decade), so
the backtest and the live bot size from exactly the same number and cached backtests stay valid while equity moves.

**The scout's capital settles** (`state/scout_capital.json`): every new capital means backtesting the whole week
again, so the scout keeps scanning at the capital it has while the balance stays within 25% of it, and moves at most
once per UTC day when it leaves that band, at the day's first scan (never mid-day: that re-runs every recorded day).
A fixed amount, the first deposit (paper → a funded account), or a Telegram `/set` of a sizing setting applies at
once. Runs do not wait for it: `/run` reads the balance first and sizes for it. Every reading of the balance is kept
([the balance history](#balance-history)).

**How the live bot follows the account** (sessions written by the pilot have `sizing.follow_equity: true`):
1. At start, at every 00:00 UTC, and within the day once the equity has moved 25% or more from what the sizes were
   taken on (a deposit, a withdrawal, a large loss; checked hourly) it re-computes the order size, caps and dollar
   stops from the equity.
2. Every run starts sized for the balance when you start it. **Your own pick** (`/run`) then follows the balance.
   **A list's pick** grows past **1.25×** its starting capital only as far as the scans confirm it: every scan in
   which it is still GO records the capital it used, so growth is followed one validated step at a time.
3. Your Telegram settings (`trade_share`, `capital`, `max_capital`, the stops) apply at the same re-size.
4. Losses shrink the sizes the same way, and the stops shrink with them.
5. If the equity falls below the setup's **least capital**, it stops quoting and closes what is left (re-checked
   hourly, so a deposit lifts it).

<a id="balance-history"></a>**The balance history** (`state/balances.jsonl`): the scout logs the account before every
scan, the live bot every 5 minutes, and Telegram's `/balance` each time you ask. Each row has the time, the equity,
the free collateral and Arcus's **net deposits** (deposits minus withdrawals), so trading profit (equity − net
deposits) is never confused with money you moved in or out. `/balance` shows the latest reading with its 1-, 7- and
30-day change; `bot status` shows the last one.

<a id="dashboard"></a>**The live dashboard** (`/dashboard` in Telegram, `bot dashboard` in a terminal) refreshes
every 10 s:
- **Volume today:** the bot's own fills since 00:00 UTC, exact to the last fill.
- **Pace:** today's volume scaled to a full day. It shows after 30 minutes of trading, next to the backtest's volume
  per day.
- **PnL today:** trading PnL now minus trading PnL at 00:00 UTC, taken from the balance history. It counts fees,
  funding and the open position's mark-to-market. Deposits never count, and restarting the bot during the day does
  not reset it.
- **Capital P/L:** equity minus net deposits, since your first deposit.
- **Where equity comes from:** the running bot reads it every 15 s. With no bot running, the dashboard reads the
  account itself, at most once a minute.

**The floor: least capital.** The smallest order the bot places, the off-hours one on RWA perps, must stay at least
1.2× the Arcus minimum order (max($5, minimum size × price)). So the least capital is 3 × minimum order ÷ off-hours
leverage: $0.90 for QQQ or GLD at their maximum (25x, 16.7x off-hours), $7.50 for most markets at 2x, $8.11 for
SNDK at its maximum. A leverage that the capital cannot fund is not backtested, and the report lists it as "capital
too small". `bot scout limits` prints the table for every market.

**The ceiling: market liquidity.** An order larger than almost every taker order does not fill more; it only takes on
more risk. So one order never exceeds the market's **order ceiling**: the 99th-percentile taker order over the
recorded days (September 2026: QQQ $11k, GLD $25k, SPY $9k, NVDA $4k, BTC $22k). Past it the sizes stop growing and
the stops apply to the capital actually used; the rest of the account is margin cushion. QQQ at 20x therefore uses at
most about $1,375 of capital, and at 2x about $13,750. There is no upper limit on the account, but one deployment on
one market can only use what that market trades. (Running several markets at once would use more; the bot runs one.)

**Measured: the whole menu at nine capital levels.** Every market and setting was backtested on the 4 full days
recorded 2026-09-20 to 09-23, with the checks as of 2026-09-24 02:50 UTC. These are backtests on a short history,
not promises (the settings' names from before 2026-09-26: `deep 3bp` is Mid +3 today, `deep 3bp, skew` Mid +3 with
its skew):

| Capital | Setups that pass | Best by maker volume (capital it uses) | Order | Maker volume/day | PnL/day (best) | Worst day |
|---|---|---|---|---|---|---|
| $5 | 4 markets | NVDA deep 2bp ×2 @ 5x ($5) | $10 | $1,008 | +$0.05 | −$0.04 |
| $10 | 6 | NVDA deep 3bp, skew @ 5x ($10) | $20 | $1,256 | −$0.00 | −$0.11 |
| $25 | 7 | NVDA deep 3bp, no pause @ 5x ($25) | $50 | $2,080 | +$0.37 | −$0.01 |
| $50 | 8 | QQQ deep 3bp, skew @ 20x ($50) | $400 | $11,452 | −$0.01 | −$0.60 |
| $100 | 8 | QQQ deep 3bp, skew @ 20x ($100) | $800 | $20,961 | +$0.29 | −$1.60 |
| $250 | 9 | QQQ deep 3bp, skew @ 25x ($250) | $2,500 | $42,959 | −$0.00 | −$5.50 |
| $1,000 | 13 | QQQ deep 1.5bp, no pause @ 10x ($1,000) | $4,000 | $102,561 | +$6.29 | −$6.87 |
| $5,000 | 14 | QQQ deep 1bp @ 5x ($5,000) | $10,000 | $204,963 | +$8.75 | −$47.35 |
| $25,000 | 16 | HYPE deep 3bp, skew @ 2x ($11,250) | $9,000 | $404,706 | +$81.34 | −$237.82 |

What it says:
- **The least money that works is about $5**, but only four markets pass and each order is $10, so volume is about
  $1k a day. HOOD, INTC and SNDK need more than $5 (their minimum order is $12–18); from $10 every market can be
  funded.
- **Around $50 is where it gets useful:** QQQ at 20x can then post $400 orders and does about $11k of maker volume a
  day near breakeven. From $50 to about $1,000, volume grows almost in step with the capital (100–230× the capital
  a day).
- **Past about $1,000 the growth slows:** at $5,000 the best setup does about 40× the capital a day, and at $25,000
  about 16×. Orders hit the markets' liquidity ceilings, so extra capital becomes margin cushion (the $25,000 row uses
  $11,250), and the best market changes (HYPE at $25,000).
- **The worst day grows with the capital** (the stops are a percentage of it), as do the stakes of a four-day sample
  being wrong. Scan at your own capital and let the scout's history grow before sizing up.

---

## 5. The pilot: approve, run, re-check

**Approving** (`bot pilot approve N [--live]`, or the Telegram Run buttons):

1. It re-reads the latest scan and refuses if it is over 90 minutes old (is the scout running?). From Telegram it
   also refuses if the top 3 changed between your tap and your confirm.
2. It writes `config/sessions/pilot.yaml`: the strategy, leverage, order size, caps and stops at the backtested
   capital, quoting 24/7, plus the `sizing` recipe the engine uses to re-size from the account's equity.
3. If a bot is already running, it first sends it the **close** command (reduce-only maker exit, then a taker order;
   the bot stops even if a position is still open after 10 minutes, with a critical alert).
4. It starts `bot run pilot` (paper, or live with the checks in [3.7](#37-go-live-real-money)).

**Reviewing**, after every scan (`bot scout run` does this automatically):

| Situation | What the pilot does |
|---|---|
| Nothing running | Offers the top 3 whenever they change |
| Running and still GO | Records the capital it was backtested at (kv `sizing_ok`): the engine may size up to 1.25× that at the next 00:00 UTC |
| Running and no longer GO | **Pauses quoting** on that market (reduce-only exits close the position) and tells you why, with the current top 3 |
| Paused, then GO on two scans in a row | Resumes quoting by itself |
| Another GO setting with ≥ 1.5× the maker volume | Suggests a switch, once. It never switches without you |

State lives in `state/pilot.json`; every event is a line in `state/pilot_events.jsonl`, which the Telegram bot posts.

### 5.1 The autopilot (`/auto`)

With a small account the question is less *which* setup than *when* to spend the day's loss budget.
- **BTC Mid 0 costs 0.6 bp in a calm weekend hour and 2.7 bp in a wild weekday one.**
- **SPY Mid +3 costs 0.6 bp in the US afternoon and over 2 bp the rest of the day.**

The autopilot spends a budget only where the backtests say volume is cheapest now. The research, with every number, is
in the [research note](bot/docs/notes/2026-09-27-autopilot.md).

**What it looks at, every minute** (inside the scout, on the server):

| Input | What it is |
|---|---|
| The pot | `budget` dollars are added at 00:00 UTC. Unspent money carries over, up to a week of it, so a quiet weekend can use what a busy weekday did not. Each run's result comes out of it |
| The session | Weekend (New York Fri 17:00 to Sun 18:00), Asia (from Tokyo 09:00), London (from 08:00 London), US open (09:30–12:00 New York), US afternoon (12:00–16:00), US evening. Each city's own clock, so daylight saving is handled |
| Each market's state | The last hour's volatility against its usual level at that hour: **calm** (under 0.75x), **normal**, **busy** (1.25–2x), **wild** (over 2x). A one-minute move over 6x usual is a **shock**: that market is left out for 30 minutes |
| Events | Flat from 45 minutes before CPI, the jobs report and FOMC until 30 minutes after (the dates are in `config/calendars/events.csv`). A stock is also left out from 24 h before its earnings to 24 h after; the scout fetches those dates from Nasdaq every day |
| The playbook | Every hour of the last 42 days backtested from flat, for 8 setups (Mid 0 to +3, and Smart 0 to +3 on days with a recorded book). Once a market has 10 days with a recorded book, 2 of them at a weekend, only those days are used (older days have a book rebuilt from trades, which made weekend SPY look four times dearer than it ran live) on 8 markets (BTC, SPY, ETH, SOL, QQQ, NVDA, GLD, HYPE). The result is the volume per hour and cost per dollar for each market, setup, session and state. Rebuilt daily (`bot scout playbook` prints it) |

**What it does:**

1. **Nothing running:** starts the setup with the most volume per hour whose predicted cost is within the **cost
   ceiling**, at the market's maximum leverage and sized from the account. The run stop (`sl=`) is what is left of
   the pot, at most 3 days of budget.
2. **Running:** keeps it while its own prediction stays within the ceiling (plus 15%). It stops when:
   - the market turns busy or wild;
   - an event or a shock comes;
   - the pot is gone.
3. **Switching:** it switches when another setup gives 1.5x the volume and the run has lasted 20 minutes.
4. **After a stop:** it rests 10 minutes before the next start.

**The ceiling** is tuned daily for your budget. The playbook replays this rule over the last 4 weeks at every ceiling
and keeps the one that bought the most volume:
- a small budget buys most by waiting for the cheapest hours (about 1.3 bp in the backtest, about 1.0 bp live);
- a large one needs a higher ceiling to be spent.

`/auto cost 1.6` fixes it yourself; `/auto cost auto` gives the tuning back.

Rehearsed over Sep 12–25 on the server's data, with the playbook and the ceiling from earlier days only:

| Budget | Autopilot | BTC Mid 0 from 00:00 UTC until spent |
|---|---|---|
| $5/day | $40k/day at 1.23 bp | $26k/day at 1.91 bp |
| $10/day | $74k/day at 1.35 bp | $52k/day |
| $20/day | $125k/day, spending $16.84 | $107k/day for $20 |

Backtest dollars; live BTC has cost about 0.8x the backtest.

**Turning it on:** `/auto` → **📝 On (paper)** or **🔴 On (LIVE)**. You can also type `/auto on live budget=5`.
- LIVE needs `BOT_PILOT_LIVE=1` and a typed code.
- It runs the doctor before every live start; a failing doctor skips that market for 30 minutes and alerts you.
- `/auto` shows the pot, what it is doing and why, each market's state, today and the last 7 days, and what it would
  run in each session of the next 24 hours.
- It posts one message per start, switch, stop and run end, and a summary each day.
- It turns itself **off** when you take over: your `/run`, `/stop`, `/closeall`, `/cancelall` or `/pilotclose`, or a
  run you start from the shell. `/auto off` closes its run and stops.
- While it is on, the pilot's offers and pauses ([5](#5-the-pilot-approve-run-re-check)) stand aside.

State: `state/autopilot.json`. Fetched earnings dates: `state/calendars/earnings.csv`. The playbook:
`data/scout/playbook.json`.

---

## 6. Risk rules and safety systems

### 6.1 The stops (backtest and live)

The stops are percentages of the capital (`sizing` in `config/app.yaml`). The pilot writes them into every session
file both as dollars (for the capital it was backtested at) and as percentages (the `sizing` block), and the engine
re-computes the dollars from the account's equity at start and at 00:00 UTC ([4.6](#46-capital-the-least-and-the-most)).

| Rule | Default | On $100 | What happens |
|---|---|---|---|
| Position stop | dynamic, 1–5% | $1–5 | The open position is down by the stop from its average entry: cancel quotes, exit with a reduce-only maker order at the touch, cross the spread with a taker order after 20 s if it has not filled, then pause 60 s. The stop follows the market: 2 × the market's 1-hour move × the inventory cap, never under 1% of the capital nor over 5%. `/set position_stop 3` fixes it at 3% (the daily stop must be at least as large: `/set daily_stop 3` or more first, or the reply says so); `/set position_stop auto` gives the dynamic one back |
| Daily stop | 2% | $2 | The day's PnL is below −2%: close the position the same way, no new orders until 00:00 UTC, then resume by itself |
| Kill | 10% | $10 | Equity more than 10% below its peak: close everything with a taker order and stop until you resume it |
| Safety pause | on | — | Spread over 3× its 1-hour median (and more than 1 bp above it) or a 1-second move over 6σ: no quotes for 30 s. Hand-written sessions also pause when the depth within 25 bps falls under 30% of its median; pilot sessions do not, because the backtest cannot model it |
| Liquidation distance | 4σ | — | Distance to liquidation below 4σ of 1-hour moves: cut half the position at market; re-armed above 6σ |
| Position caps | 1.2× / 1.25× | — | No new order that could take the position past 1.2× the cap; the risk engine rejects anything past 1.25× |

- **Why the position stop follows the market** (since 2026-10-04): a fixed 1% turned ordinary swings into taker
  exits (19,000 stops in the 10-day study against 700, 1.89 bp against 1.66 bp;
  [note](bot/docs/notes/2026-10-04-two-weekends-review.md), section 5). The backtest, the scan and the live engine
  compute it with the same function (`bot/common/sizing.py: dynamic_stop`).
- **At maximum leverage the daily stop ends most days early** when the run has no `sl=`: at 50x a full position
  reaches 2% of the capital on a 5 bp move. A run with `sl=` lifts the daily stop and the kill to that limit
  ([7.6](#76-run-limits-stop-take-profit-volume-target)); `/set daily_stop 5` widens it for every run.

### 6.2 Other kill switches (live engine)

| Trigger | Automatic action | Resume |
|---|---|---|
| Session loss ≥ `stop_loss_pct` of capital | Cancel quotes, flatten (maker, then IOC) | next session |
| Event window (CPI, FOMC, NFP ±30 min; earnings ±24 h) | No new quotes | window end |
| Arcus off-hours price band in its expansion zone, or open-interest cap reached | Stop quoting that market | cleared |
| The market is not ONLINE (Arcus's live `markets` channel: halted, delisted, a listing switched off) | Stop quoting that market; the exit book closes any position once it trades | ONLINE again |
| Heartbeat silent 60 s | Dead man's switch and guardian cancel everything | manual, after reconciliation |
| Order pool < 5% | Cancels only | pool recovered |
| Dead man's switch refresh fails twice | Safe mode | manual |
| `SELF_TRADE` or `GEO_RESTRICTED` rejection | Stop the venue, critical alert | manual |
| Unexpected error in the trading loop | Safe mode: cancel quotes, keep positions | manual |
| The same rejection 5 times in 60 s (e.g. `UNDERCOLLATERALIZED`) | Pause that market | 60 s, doubling up to 10 min |

When a session has no stops of its own, the app-wide limits in `config/app.yaml` apply: daily loss 3% of capital,
drawdown 10%.

### 6.3 Safety systems

- **Live lock.** A real order needs `--live` on the command line **and** your approval (typing `LIVE`, or
  `live_enabled: true` in the session for unattended `--yes` starts) **and** a `doctor` with no FAIL. Only this module
  can open mainnet writes; a live request is never silently turned into paper.
- **Dead man's switch.** Every 20 s the bot tells Arcus "cancel all my orders in 60 s unless I check in again"
  (`scheduleCancel`). If the bot or the machine dies, Arcus cancels everything by itself.
- **Guardian** (`bot guardian`, systemd `bot-guardian`). A separate process with its own connections. It cancels
  everything if the live heartbeat is silent for 60 s, and flattens (reduce-only) past the drawdown limit. It never
  sends an order that increases risk.
- **Reconciliation.** Every 5 minutes, and on every start, the venue is the source of truth: unknown orders are
  cancelled and positions are taken from Arcus.
- **Separate state per mode.** Paper, testnet and live each have their own database and heartbeat.

---

## 7. Strategies explained

### 7.1 Key ideas first

- **Mid** m = (best bid + best ask) / 2. **1 bp** (basis point) = 0.01% of the price.
- **Maker vs taker.** A maker order rests on the book (post-only, "ALO" on Arcus) and pays no fee. A taker order
  crosses the spread, fills immediately and pays 2.25 bps.
- **Where the edge comes from.** Research on the recorded Arcus books showed that quoting **at** the best bid or ask
  loses on most liquid markets: whoever trades against you there often knows the price is about to move. Orders a few
  bps **deeper** get filled mostly by large taker orders that sweep through several levels and then partly bounce
  back, and those fills have earned on average. The effect is strongest on quiet stock, index and gold perps; it is
  near zero or negative on BTC and ETH.
- **Inventory skew.** With inventory I (in dollars) and cap I_cap, the skew is u = clamp(I / I_cap, −1, 1). The
  **reservation price** r = m × (1 − κ × u × h) shifts both quotes away from the side you are already heavy on (h is
  the half-spread as a fraction, κ the `skew_kappa`). Order sizes skew too: bid size = q × (1 − u), ask size =
  q × (1 + u). At u = ±1 the side that would add inventory stops.
- **Requote tolerance.** A live order is kept (keeping its queue place) while it is within max(2 ticks,
  0.25 × half-spread) of the wanted price and within 20% of the wanted size; otherwise it is replaced.

### 7.2 The scout menu (8 setups)

A setup is named the way Tread.fi names its runs: the mode, the spread in bps, and the bias when it is not Neutral.
The scout backtests these 8 on every market at its maximum leverage, so the report labels look like `Mid +1 @ 50x`
or `Smart 0 @ 40x` (`bot/strategies/setup.py`).

| Mode | Spreads backtested | Bias | What it quotes |
|---|---|---|---|
| **Mid** | 0, +1, +2, +3 | Neutral | both sides `spread` bps from the book's mid, following it ([7.3](#73-mid-mode-mid)) |
| **Smart** | 0, +1, +2, +3 | Neutral | as Mid, less a side while its fill would likely lose ([7.9](#79-smart-mode-smart-and-where-the-profit-is)) |

- **Grid, Mid −1, Mid +5 and the Long and Short biases left the scan on 2026-10-04** (37 setups before): on ten
  recorded days Grid was the dearest family on all 17 markets studied, Mid −1 quoted what Mid 0 quotes, and a bias
  cost more than Neutral at the same spread ([note](bot/docs/notes/2026-10-04-two-weekends-review.md), section 6).
  They still run from `/run` and the run form, without a backtest; a scan is about 4.5 times shorter.
- Any other spread or bias (e.g. Mid +0.5, Grid +4, Smart +5) runs from `/run` without a backtest.
- The names from before 2026-09-26 still work in `/run` and old buttons: `touch 0bp` = Mid 0, `touch 1bp` = Mid +1,
  `deep 3bp` = Mid +3, `improve touch` = Mid −1, `anchor 3bp` = Grid +3.
- RGrid, the RSI signal, the static grid and the "skip US session" variants were retired: they lost on every market in
  the backtests, or measured nothing new
  ([research note](bot/docs/notes/2026-09-26-tread-style-setups.md), section 6).

Defaults that are not knobs, chosen from the backtests (same note, section 6):

| Default | Why |
|---|---|
| Mid runs **without** the safety pause | The same cost per dollar, 13% (BTC) to 66% (ETH) more volume |
| Mid +1 and wider skew the quotes against the position (κ 1) | Cheaper in 19 of 30 market × spread cases |
| Grid **keeps** the safety pause | Cheaper on 6 of 10 markets |
| One order per side | A second level added only 8–14% volume |

### 7.3 Mid (`mode: mid`)

Tread's **Mid**, the volume machine: both sides sit exactly `spread` bps from the mid (the passive anchor with
`passive_k_sigma: 0`) and follow it as it moves. On a one-tick book such as BTC:
- **Mid 0** joins the best bid and ask;
- **Mid −1** is the same (a post-only order cannot go further);
- **Mid +1** sits 1 bp away.

On a wide book a negative spread goes inside the spread, one tick from the other side at most.

- Quotes skew against the position (the reservation price r = m × (1 − κ × u × h), [7.1](#71-key-ideas-first)) for
  spreads above 0.
- A post-only guard keeps the bid below the best ask and the ask above the best bid.
- The session file still accepts the older execution styles (`normal`: at least the touch; `aggressive`: one tick
  inside) for hand-written sessions; the pilot always writes `passive`.
- **Off-hours** (an RWA perp outside its session): the pilot allows Mid (`off_hours.allow_mid: true`), with the smaller
  off-hours position cap.

### 7.4 Grid (`mode: grid`)

Tread's **Grid**, the profit locker: the reference price is your **last fill**, not the mid.

- **Flat:** one bid and one ask at mid ± max(spread, half the book's spread).
- **Holding a position:** bid = last fill × (1 − spread), ask = last fill × (1 + spread).
  - A sell never goes below the last buy + spread, and a buy never above the last sell − spread.
  - Each fill moves the reference, so a falling market fills one more order every spread, up to the position cap.
  - Grid 0 sells no lower than it bought.
- **Soft reset:** once the mid has run 0.5% (`reset_threshold_pct`) against the position from the last fill, the grid
  has stalled. It stops adding and closes with a reduce-only maker order at the touch. Flat again, it starts over
  around the mid. The position, daily and kill stops still apply on top.
- After a restart with a position, its average entry stands in for the last fill.
- (`mode: anchor` in an older session file means this Grid. The static geometric grid it replaces is gone.)

### 7.5 Directional bias (`bias: long | short`)

Tread's **Long / Short** bias: the bot holds a position on that side while still quoting both sides.

- The target is `bias_frac` (0.5) of the position cap: about one full order. At 40x on about $110 that is about
  $1,760 of BTC, so a 1% move is about $18 either way.
- Order sizes skew toward the target: flat with a Long bias, the bid is 1.5 orders and the ask half an order. Once
  there, it trades around it.
- The target follows the cap, so it re-sizes with the account and shrinks outside an RWA perp's session.
- Backtests: it changed the cost by under 0.1 bp on BTC and ETH and cut the volume by about 8%. It is a view on the
  price, not an edge, so Neutral is the default.

### 7.6 Run limits: stop, take profit, volume target

Tread's form ends a run on a stop loss, a take profit or a volume target. So does `/run`:

| Limit | `/run` | What happens |
|---|---|---|
| Run stop | `sl=10` | The run may lose $10 in all (across restarts). It lifts the daily stop and the kill to at least $10; past it the bot flattens and stops |
| Take profit | `tp=5` | Once the run is up $5, it closes the position and stops |
| Volume target | `vol=100k` | Once the run has traded $100,000 (maker and taker, across restarts), it closes the position and stops |

- A take profit or volume target closes with a reduce-only maker order at the touch, then a taker order after 20 s,
  and the bot stays up with nothing on the book.
- The dashboard shows ✅ RUN DONE and "This Run: Volume $101.2k / $100.0k target".
- `/resumeaftersl` does not restart a finished run: start a new one with `/run`.

### 7.7 Weekends: stocks or crypto?

From 8 weeks of Arcus trades and the recorded weekend books
([research note](bot/docs/notes/2026-09-26-tread-style-setups.md), section 5):
- **Crypto on weekends keeps about half its flow and moves about half as much.**
  - BTC takers: $25M a day on weekends against $43M on weekdays.
  - BTC Mid 0 at 40x: about 55% of its weekday speed, 15% cheaper per dollar (backtest 1.41 bp against 1.69).
- **The stock and index perps trade at their off-hours leverage inside Arcus's price bounds:** SPY 33x, QQQ and GLD
  16.7x, other stocks 6.7x.
  - Their flow falls to a quarter or half.
  - Their price barely moves, so they are the cheapest per dollar on weekends (SPY Mid 0 about 0.8 bp, Mid +1 about
    0.5 bp).
  - They are 15–35x slower than BTC.

| You want | Weekend pick | Why |
|---|---|---|
| Volume fast (a run of minutes to an hour) | **BTC Mid 0 at 40x** | The only market that fills $100k+ in an hour at about $110 of capital |
| The most volume per dollar, running all day | **SPY Mid 0 or Mid +1 at 33x** | About twice as much volume per dollar lost as BTC, at $5–11k an hour |

### 7.8 What Tread.fi users run, and what carries over

In September 2026 the owner collected 46 posts from Tread.fi users and the Tread team (settings, screenshots, costs
per $1M) and screenshots of the form and of their own Tread history. The research note, sections 1–2, has the details.
Arcus differs from their venues in two ways that matter:
- **The maker fee is 0 and the taker fee is 2.25 bp.** Their costs include 1–3 bp of fees; ours are only the
  trading loss. Modes that cross the spread on purpose (RGrid) are expensive here.
- **Arcus BTC follows faster venues.** A fill at the touch loses about 1 bp within a minute, so Grid 0, nearly free on
  Paradex in January, costs about 2 bp on Arcus BTC.

| Tread | Here |
|---|---|
| Mid / Grid, spread, Long / Neutral / Short | The same three fields (Mid and Grid, [7.2](#72-the-scout-menu-8-setups)–[7.5](#75-directional-bias-bias-long--short)) |
| Stop loss, take profit, volume | `sl=`, `tp=`, `vol=` ([7.6](#76-run-limits-stop-take-profit-volume-target)) |
| DGrid (the bot picks the setup by regime) | The autopilot picks market and setup per session and market state, every minute ([5.1](#51-the-autopilot-auto)) |
| RGrid (trend, mostly taker) | Retired: taker fills at 2.25 bp lost on every market |
| Blend (an outside price) | Tested with Binance's BTC price (2026-09-27): Arcus follows it within seconds, but at the bot's once-a-second pace it adds little beyond Arcus's own book, which Smart reads ([7.9](#79-smart-mode-smart-and-where-the-profit-is)) |
| Signal (RSI skew) | Retired: about $80k a day on BTC |
| Participation rate, duration | Not built: on a maker-only venue the spread sets the speed (Mid 0 fastest) |
| "Avoid NYC hours" | The US open is BTC's dearest session (2.3 bp), but the market's state matters more than the clock: a calm hour costs half a wild one in any session. The autopilot judges both ([5.1](#51-the-autopilot-auto)) |
| Several bots, delta-neutral bots | Not allowed: one setup at a time, Arcus only |

### 7.9 Smart (`mode: smart`), and where the profit is

The first live SPY run (26–27 Sep) raised a fair question: after all the backtests, why does almost every trade close at
a loss? The research is in the [research note](bot/docs/notes/2026-09-27-profitability.md).

**Why a quote at the best price loses on Arcus:**
- **The most it can earn is half the spread.** BTC and SPY sit at one tick most of the time, and half a tick is about
  0.07 bp on SPY.
- **Makers get nothing else.** The maker fee is 0, with no rebate below Arcus's VIP tier ($1B in 30 days).
- **The traders who hit it know more.** Across every trade at the best price (Sep 19–26), the price moved against the
  maker by 0.9 bp on SPY and 1.4 bp on BTC within a minute.
- **The profitable accounts are a different game.** The biggest ones on Arcus's leaderboard are mostly makers and earn
  2–9 bp overall, but their fills lose about as much in the first minute as ours. Their profit comes from positions
  held for hours and, very likely, hedges on other exchanges or fee terms of their own. None of that is available to
  one Arcus market and about $100.
- **An outside price is too slow for us.** Binance leads Arcus BTC, but by the time a once-a-second bot acts, Arcus has
  already covered two-thirds of the move.

**What does help: choosing the seconds.** A fill loses most when:
- the other side of the book holds far more than ours (a bid facing 10x its size on the ask is about to be traded
  through);
- the price has just moved against that side (a bid right after a drop).

**Smart** quotes as Mid and, each second, leaves out a side that would add to the position when either holds (imbalance
beyond 0.6, or 0.5 bp against it over 5 s; our own orders are not counted in the book). The side that reduces the
position always stays. The backtest runs the same rule, so the scout ranks Smart like any other setup.

| Backtest, Sep 19–26 | Mid 0 | Smart 0 | Smart's volume |
|---|---|---|---|
| SPY, position stop $0.90 (1%) | 1.57 bp | **1.15 bp** | 81% |
| SPY, position stop $2.70 (3%) | 1.06 bp | **0.82 bp** | 86% |
| Other markets (NVDA, GLD, BTC, ETH, SOL, QQQ, HYPE) | 1.0–2.9 bp | 0–16% less | 70–87% |

- **The position stop matters as much.** At 1% it fires about 20 times a day on SPY and every exit ends as a taker
  order; 3% cut SPY Mid 0 from 1.57 to 1.06 bp in the backtest. Since 2026-10-04 the default follows the market
  between 1% and 5% ([6.1](#61-the-stops-backtest-and-live)), which costs what 3% costs.
- **Wider quotes on the index perps are the closest thing to a profit.** SPY Smart +3 (about $35k a day) and QQQ Mid +3
  (about $19k a day) made money on 6 of 8 days (+$2 and +$3 a day, backtest), best in the US morning.
  - SPY and QQQ follow a real index, so after a large order pushes the perp it tends to come back.
  - Eight days is not proof, and about 120 market × setup cases were tried. Paper-run one for a week first.

| You want | Run | Backtest |
|---|---|---|
| Volume, as cheaply as possible | **SPY Smart 0** (the default position stop) | 0.82 bp, about $300k a day |
| A small profit, slowly (to be proven) | **SPY Smart +3** or **QQQ Mid +3**, in paper first | +$2–3 a day, $20–35k a day |

---

## 8. Session files (configuration)

A session is one YAML file in `bot/config/sessions/`. The pilot writes `pilot.yaml`; the others are examples
(`bot sessions` lists them). The main fields of a market-making session:

| Field | Meaning |
|---|---|
| `session_id`, `venue`, `market` | Name, `arcus`, and the base asset (e.g. `QQQ` for QQQ-USD) |
| `account_index` | Arcus subaccount 0–9 (must match the one your key is bound to; `bot keys`) |
| `mode` | `mid`, `grid` or `smart` (Tread's Mid and Grid, and Smart; `anchor` in an older file means `grid`) |
| `bias`, `bias_frac` | `neutral`, `long` or `short`, and the share of the position cap it holds (0.5; [7.5](#75-directional-bias-bias-long--short)) |
| `live_enabled` | Part of the live lock: required for unattended live starts |
| `capital_usd`, `leverage_max` | Capital, and the leverage the runner sets on Arcus before quoting |
| `order_size_usd`, `inventory_cap_usd` | Order size per level per side, and the inventory cap (`auto` = derived) |
| `inventory_cap_off_usd` | Cap outside an RWA session (higher off-hours margin) |
| `spacing_bps` | The spread: Mid 0 = `0`, Mid +1 = `1`, Mid −1 = `-1`, Grid +3 = `3` |
| `execution_style`, `levels_per_side`, `level_step_bps`, `offset_bps` | Quote placement ([7.3](#73-mid-mode-mid)); the pilot writes `passive`, 1 level |
| `skew_kappa`, `passive_k_sigma` | Inventory skew strength; passive extra distance in 1-minute σ |
| `pos_stop_usd`, `daily_stop_usd`, `kill_usd`, `exit_taker_after_s`, `cooldown_s` | The stops in dollars for `capital_usd` ([6.1](#61-the-stops-backtest-and-live)) |
| `sizing` | Pilot sessions: `follow_equity`, `backtest_capital_usd`, `capital_frac`, `max_capital_usd`, `leverage` / `leverage_off` (the leverage the sizes use), `order_max_usd` (the liquidity ceiling), the stops in %, `min_capital_usd`. With `follow_equity: true` the engine rewrites the dollar sizes and stops from the account's equity at start and at 00:00 UTC ([4.6](#46-capital-the-least-and-the-most)) |
| `stop_loss_pct`, `take_profit_pct` | Session-level stop and take-profit, in % of capital |
| `participation_cap_pct` | Widen when your share of market volume is higher than this |
| `safety_pause` | `move_sigma_1s`, `spread_x_median`, `depth_frac_min`, `resume_s` |
| `session` | `duration`, `repeat`, `windows_ist` (trading windows, IST), `skip_et` (New York windows on NYSE trading days with no new quotes, e.g. `["09:00-16:30"]`) and `skip_events` (`cpi`, `fomc`, `nfp`, `earnings`) |
| `off_hours` | `spacing_mult`, `size_mult`, `allow_mid` for RWA perps outside their session |
| `reset_threshold_pct` | Grid's soft reset ([7.4](#74-grid-mode-grid)) |
| `take_profit_usd`, `volume_target_usd`, `max_loss_usd` | The run's own limits (`tp=`, `vol=`, `sl=`; [7.6](#76-run-limits-stop-take-profit-volume-target)) |
| `auto_spacing` | The Grid spacing when `spacing_bps: auto` |

To change the account-wide numbers the scout uses (capital, stops), edit `Risk` in `bot/scout/sim.py`; to change the
leverage cap for BTC and ETH, `/set crypto_lev`; the ladder below the maximum (`--ladder`), `LADDER` in
`bot/scout/scan.py`. App-wide limits are in
`config/app.yaml`. Event dates are in `config/calendars/events.csv` (keep FOMC 30 days and CPI 14 days ahead).

---

## 9. The Telegram bot

Control and alerts from your phone. It reads the runner's state, writes flags the runner applies on its next tick,
and holds no trading state of its own.

Every message has the same layout: one emoji and a bold title, a blank line, then short monospace lines in groups,
with bold labels over sections (the dashboard's Today, Quotes, Position, Capital, This Run). The running bot's own
alerts (position stop, daily stop, safety pause, run stop, safe mode) and the guardian's use it too. The layout lives
in `bot/common/tgfmt.py`.

### 9.1 Setup

1. In Telegram, message **@BotFather**, send `/newbot`, and copy the **token** into `TELEGRAM_BOT_TOKEN` in `.env`.
2. Send any message to your new bot, then open `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser and
   copy `chat.id` into `TELEGRAM_CHAT_ID`.
3. Start it: `bot telegram` (keep it running; systemd unit `bot-telegram`).
4. Send `/whoami` and put your user id in `TELEGRAM_ALLOWED_USER_IDS` (comma-separated), then restart it.
5. Optional: `BOT_PILOT_LIVE=1` to allow LIVE deployments from the Run buttons. `bot telegram --read-only` refuses
   every control and only shows status and alerts.

### 9.2 Commands

Send `/menu` for buttons. Telegram's `/` list shows the everyday commands; the rest work too.

**Every day**

| Command | What it does |
|---|---|
| `/top3` | 🚀 **Most Volume**: the 3 setups with the most volume that cost at most your budget per $1,000 traded (`/set volume_cost`), with ▶️ buttons |
| `/cheapest` | 💎 **Cheapest**: the lowest cost per $1,000 traded within the budget, among setups that trade at least 50x the capital a day |
| `/maxvolume` | 🔥 **Max Volume**: the most volume whatever it costs; every safety check still applies (no kill or liquidation in the backtest, enough fills, 3 full days of data, market not trending now). Its last 24 h may not be re-checked: the card says so |
| ▶️ **k** | That setup in the **run form** at the leverage the list backtested |
| `/run` | 🎛 **The run form**, Tread's order form as buttons: pick a market, then one tap per field (Mid, Grid or Smart · spread −1…+5 bp · Short / Neutral / Long · leverage · run stop · volume target). The message shows the sizes, how it quotes, and the backtest of exactly that setup (or that it has none), then **📝 Paper** or **🔴 LIVE**. In one line: `/run BTC mid 0 40x live sl=10`, `/run BTC mid +1 long 40x paper`, `/run SPY grid 3 short max live sl=15 vol=100k tp=5`, `/run SPY smart +3 50x paper` (any part left out opens the form with the rest filled in; the old names such as `touch 0bp` still work). It never waits for a scan: it sizes for the capital the scout uses now. A setup picked from a list is judged by that list (paused if it drops out); anything else runs as **your pick**, never paused for its numbers, only if Arcus takes the market offline |
| `/openpositions` | What is deployed: state, today's PnL and volume vs the backtest, the last check, **🔴 Go LIVE with this setup** (on a paper run), **Close & stop** |
| `/dashboard` | A live screen that updates itself every 10 s, pinned at the top of the chat: today's volume (and its maker pace vs the backtest), how much of the day it quoted and what blocked it (e.g. "Quoting 39% · safety pause 61%"), how often it rested at the best bid and ask (else how many ticks behind), today's PnL, the position, and your capital's profit or loss (equity minus deposits). ⏹ stops it, ▶️ starts it again; a newer `/dashboard` replaces the old one |
| `/status` | Is it running, today's PnL, fills, volume |
| `/balance` | The account now (read live and logged): equity, free collateral, deposits vs trading PnL, the 1/7/30-day change, and the capital the scout and the bot size for |
| `/account` | 📒 The account as Arcus keeps it: futures (perps) volume all-time, 30 days, 24 h and 7 days; fees paid; fees earned (maker rebates, referral commission); the fee tier and the volume the next one needs; all-time realized PnL and rank. Spot volume is not in Arcus's API (stock tokens trade on-chain from the wallet), and the bot trades perps only. Also in the menu (📒 Account) and `bot account` |
| `/positions`, `/orders` | What you hold; what is waiting on the book |
| `/yesterdayreport [YYYY-MM-DD]` | The daily report: yesterday's, or the date given |

**Autopilot** ([5.1](#51-the-autopilot-auto))

| Command | What it does |
|---|---|
| `/auto` | What it runs now and why, the pot, each market's state, and the plan for the next 24 h, with On/Off buttons |
| `/auto on paper\|live [budget=5] [cost=1.5]` | Turn it on (paper: Confirm button; LIVE: `BOT_PILOT_LIVE=1` and a typed code) |
| `/auto off` | Turn it off; it closes its run |
| `/auto budget 5` | Dollars added to the pot each day |
| `/auto cost 1.5` / `/auto cost auto` | Fix the cost ceiling (bp, backtest) / let the playbook tune it for the budget |

**Control and emergencies**

| Command | What it does |
|---|---|
| `/pauseneworders [MARKET]`, `/unpause [MARKET]` | Stop / restart placing new orders; orders that close a position keep working. The pause survives restarts; a new `/run` clears it (its confirm screen says so) |
| `/stop` | Shut the bot down: quotes cancelled, positions kept (Confirm button) |
| `/resumeaftersl` | Trade again after a safety stop (safe mode, the kill or the daily stop), once you know why (Confirm button). It does not lift your own pause: `/unpause` does (the reply says when one holds) |
| `/cancelall` | Cancel every open order on the account (Confirm button) |
| `/closeall [taker]` | Close every position, maker or with IOC (typed code) |

**Settings** (no file editing, no restart)

| Command | What it does |
|---|---|
| `/settings` | Each setting, its value, what it does and when a change applies |
| `/set <name> <value>` | Change one (Confirm button); `/set <name> default` undoes it |
| `/scannow` | Scan now instead of waiting for the next one |

A list asks the scout for a scan only when the last one is older than 1.5× `scan_every`, or on 🔄 Scan. A scan that
is already running or queued is not started again, and the ETA under the list comes from the last scans' measured
times (at the same number of CPU workers; all cores but two while a bot runs). The fresh list is
posted once when the scan finishes.

| Setting | Values | What it changes |
|---|---|---|
| `capital` | `auto` or dollars | Money the bot sizes for: the account's balance, or a fixed amount (never more than the balance) |
| `trade_share` | 1–100 (%) | Share of the balance to trade; the rest is left untouched |
| `max_capital` | dollars or `none` | Never size for more than this |
| `position_stop`, `daily_stop`, `kill` | % of the capital; `position_stop` also takes `auto` | The stops ([6.1](#61-the-stops-backtest-and-live)); must stay position ≤ daily ≤ kill. `position_stop auto` (the default) follows the market between 1% and 5%; a number fixes it. Changing them means a full re-backtest (slow, low priority) |
| `scan_every` | 10–240 (minutes) | Time between scans |
| `scan_workers` | `auto` or 1–32 | CPU cores a scan may use (at most all cores but two while a bot runs on the machine) |
| `scan_budget` | 5–240 (minutes) | Most time one scan spends backtesting full days, busiest markets first (default 30); the rest continues in the next scan, and a market not backtested at the current capital yet shows as ⏳ under the lists |
| `volume_cost` | $0.01–$5 per $1,000 | The most the Most Volume and Cheapest lists may cost: dollars lost per $1,000 traded in the backtest (default $0.20, 2 bp; the backtest costs about 1.25x what live BTC runs cost, so about 1.6 bp live). The lists re-rank at once; the next scan also re-checks the last 24 h of the setups it lets in |

The scout picks up a change at its next scan (a sizing change triggers one at once); the running bot at its next
re-size (00:00 UTC, or when it restarts). Stored in `state/settings.json`. Switching live trading on stays a
deliberate step in `.env` (`BOT_PILOT_LIVE=1`), not a Telegram setting.

**More** (not in the `/` list)

| Command | What it does |
|---|---|
| `/pnl` | PnL by market |
| `/logs [n]` | The latest decisions (why it did what it did) |
| `/sessions`, `/run <session file> [live]`, `/doctor <session>` | Session files; start one (live needs `live_enabled: true`, a passing `doctor` and a typed code); the readiness check |
| `/alerts`, `/mute [minutes]`, `/unmute` | Alert settings (fills: each, hourly summary, or off) |
| `/menu`, `/help`, `/whoami` | Buttons, help, your ids |

Commands act on the running bot (live first); add `paper`, `testnet` or `live` to pick one, e.g. `/status paper`.
The earlier names (`/scout`, `/pilot`, `/report`, `/pause`, `/resume`, `/flatten`) still work.

### 9.3 Deploying from your phone

`/top3` (or `/cheapest`, `/maxvolume`) → ▶️ → the run form (change any field) → **📝 Paper** → **Confirm**. Or
`/run` → a market → the form.
For real money: **🔴 LIVE** (shown only with `BOT_PILOT_LIVE=1`); the bot runs `doctor` on the generated session and
then sends a 6-digit code that you type back within 2 minutes. After a paper run, `/openpositions` → **🔴 Go LIVE
with this setup** starts the same market, setting and leverage live (same checks and code). If a bot is already
running it first closes its position and stops.

### 9.4 Alerts it sends by itself

- The trading bot went down or came back.
- Safe mode, a drawdown stop or a daily stop appeared or cleared.
- Today's PnL reached half, then all, of the daily limit.
- Fills (each, an hourly summary, or none) and a digest shortly after 00:00 UTC.
- The pilot: a new #1 setup when nothing runs (at most every 3 hours), a deployment paused (with the reason),
  resumed, a better setup suggested, a deployment that failed to start.
- The autopilot (silent unless something is wrong):
  - each start, switch and stop, with why;
  - each run's end (PnL, volume, cost);
  - a summary after 00:00 UTC;
  - loud: it cannot start (the doctor failed), an error, or LIVE turned off on the server.

`/mute` silences everything except critical alerts.

---

## 10. Command reference

**Scout and pilot**

| Command | What |
|---|---|
| `bot up` / `bot down [--all]` / `bot status [--json]` | Start or stop the background services (scout, Telegram, guardian); one status screen |
| `bot dashboard [--once]` | The live dashboard in the terminal, redrawn every 10 s (same numbers as Telegram's `/dashboard`) |
| `bot scout run [--workers auto\|N] [--every-min M] [--depth] [--ladder] [--capital auto\|USD]` | Record everything and scan every M minutes (the daemon; `bot up` runs it), at the account's equity or a fixed capital |
| `bot scout scan [--markets …] [--ladder] [--capital auto\|USD] [--full]` | One scan now, printed as a table (`--full`: re-run the last 24 h for every setting) |
| `bot scout limits [--markets …]` | Per market: the least capital it can run on, the order ceiling, and the capital it can fully use |
| `bot scout playbook [--markets …] [--capital USD]` | Build (only the days not cached) and print the autopilot's playbook: volume per hour and cost per market, setup and session |
| `bot auto [status\|on\|off\|set] [--live] [--budget USD] [--cost BP]` | The autopilot: see it, turn it on (paper, or `--live` with `BOT_PILOT_LIVE=1` and typing LIVE), off, or change its budget or ceiling |
| `bot account` | All-time volume, fees paid and earned, the fee tier and the result, as Arcus reports them (Telegram `/account`) |
| `bot scout import PATH` | One-off import of older recordings |
| `bot pilot status` / `approve N [--live]` / `close` | See, deploy, or close the one deployment |

**Trading**

| Command | What |
|---|---|
| `bot sessions` | List the session files |
| `bot doctor [SESSION] [--paper]` | Everything a run needs: credentials, subaccount, funds, sizing, clock, region, calendar. Places no orders |
| `bot run SESSION [--live] [--yes] [--seconds N]` | Run a session (paper by default) |
| `bot status [--mode live\|paper\|testnet]` | Heartbeat, open orders, positions |
| `bot report [--date D] [--mode M]` | Daily report: Net = spread capture + inventory PnL + funding − fees − liquidation loss |
| `bot diagnose [--mode live] [--hours N \| --since "2026-09-25 20:00" --until …] [--market QQQ] [--replay]` | Why a run filled what it filled: orders sent and acknowledged, rejects and their reasons, how long a buy and a sell rested, where they rested against the best price, what blocked quoting, and how many taker trades went through a price you rested at or traded while you had no order out. `--replay` also backtests the run's own setup on the same minutes, beside the run, under each fill model: where the backtest and the run differ (for a run from before 2026-09-26 give `--setting`, `--capital` and `--leverage`; `--sl 30` for a run started with `sl=30`, which lifts its daily stop and kill). Read-only |
| `bot resume [--venue V] [--all]` | Clear safe mode / stops |
| `bot cancel-all --venue arcus [--market M] [--yes]` | Cancel all open orders (asks to confirm) |
| `bot flatten --venue arcus [--taker]` | Close all positions, reduce-only (asks to confirm) |
| `bot guardian` | The independent guardian process |

**Account and keys**

| Command | What |
|---|---|
| `bot keys` | Your API keys as Arcus sees them |
| `bot selftest [--allow-funded]` | Prove every signed request works, without trading |
| `bot probe` | Live market parameters, compliance and rate budgets |
| `bot region-check` | May this machine's IP trade Arcus perps? |
| `bot secrets …` | Encrypted secrets store (alternative to `.env`) |

---

## 11. The server PC: what runs 24/7

A second machine (a home PC or a small server) should run the **scout** around the clock, so the history keeps
growing and the ranking stays current even when your laptop is off. It needs no keys and places no orders: it only
reads Arcus's public market data. Everything runs from `bot/docker-compose.yml`.

### 11.1 What it records, continuously

For **every online Arcus perp** (60 in late September 2026; a new listing, or a pre-listed market that turns ONLINE, is
picked up within 10 minutes and added without interrupting the others):

| Stream | What is stored | What it is used for | Disk per day (all markets) |
|---|---|---|---|
| Best bid and offer | Price and size of the best bid and ask: a row whenever a price changes, or sizes change and 1 s has passed | Backtest prices and spreads, the safety pause, the "now" checks | ~50–150 MB (with trades) |
| Trades | Every trade: price, size, taker side, trade id, and the `sequenceNumber` that groups one taker order's prints | Fills in the backtest (which taker orders reached our price and how much they had left), the flow checks | included above |
| Depth (`--depth`, on by default in Docker) | The top 10 levels of each side, sampled once a second when the book changed | A queue-position fill model for larger, leveraged orders (the next backtest upgrade); not used by the scan yet | ~150–400 MB |
| Market parameters | `markets.json`, refreshed every 10 minutes: tick, minimum size, margins (so maximum leverage), open-interest caps, session hours | Order sizes, the leverage ladder, the off-hours margin | tiny |

Files: `data/scout/tape/<MARKET>/<YYYY-MM-DD>/{bbo,trades,depth}-*.npz`. Arcus serves no historical order books, so
**this recording is the only history there will be**: gaps cannot be filled later.

### 11.2 What it backtests, every 30 minutes

Each scan (the first one 10 s after start):

1. Takes every market with at least one full recorded UTC day.
2. Backtests **all 8 setups** ([7.2](#72-the-scout-menu-8-setups)) at each market's **maximum leverage**
   (`--ladder`: also 20x, 10x, 5x and 2x). It sizes them for the **capital** in
   `SCOUT_CAPITAL` (default `auto`: the Docker container has no keys, so that means the $100 paper capital; set
   `SCOUT_CAPITAL=500` to rank for a $500 account). With today's 59 markets that is 59
   market-leverage pairs and **472 backtests per window** (with the 37-setup menu used until 2026-10-04 it was
   1,947, about 2 minutes on 9 cores for all 8 recorded days).
3. The windows are each of the last **7 full days** (computed once per day, then cached) and the **last 24 hours**.
   The last 24 hours is re-run only for the setups that pass on their full days (about 3–6% of them, measured), and
   for whatever is deployed: a setup that already fails on its full days cannot become GO, so re-running it would
   change nothing. The picks are identical to re-running everything (`bot scout scan --full`), measured on 4 days of
   September data. It also reads the **last hour** for the "now" checks.
4. Applies the GO checks ([4.4](#44-go-checks)) and ranks by maker volume per day.
5. Once a day it also:
   - fetches the stock perps' earnings dates;
   - adds the finished day to the autopilot's playbook (a few minutes; the first build backtests 42 days).
6. Writes the results:

| File | Contents |
|---|---|
| `data/scout/report.txt` | The latest ranking: best setting per market, then each market at its maximum leverage |
| `data/scout/reports/<YYYY-MM-DD>.txt` | The last ranking of each UTC day: the day-by-day record to compare later |
| `data/scout/scans/<YYYYMMDD-HHMM>.json` | Every scan (top 3, ranking, at-max table), about 48 per day |
| `data/scout/latest.json` | The full latest scan, every setting (what the pilot reads) |
| `data/scout/cache/` | The per-day backtest results |
| `state/pilot_events.jsonl` | The top 3 each time they change (nothing is deployed on the server, so it only offers) |

**How much CPU.** Measured on an Apple-silicon laptop, re-running everything took about 1,180 CPU-seconds per scan;
the shortlist scan takes about 90 (12.7× less), and the simulator itself is 2× faster than before with identical
results. So a 30-minute scan is under a minute of one core, plus the once-a-day search over the new day (roughly
20 minutes of one core on an Intel MacBook). Scan workers run at the **lowest CPU priority** (nice 19, macOS
background), and use **one** worker while a trading bot runs on the same machine (else all cores but one), so the
bot never waits for the CPU. The recorder itself uses about 4% of one core.

### 11.3 What the server does not do

- **No trading and no keys.** The containers never sign a request and need no `.env`.
  - **To trade live on this same machine,** stop the containers (`docker compose down`).
  - Then run the scout natively, next to the bot: `bot scout run --depth` ([3.4](#34-start-the-scout-recording--backtests)).
  - Why: the pilot checks the running bot by its process id, which a container cannot see. A Docker scout would
    never review, pause or resume a native live bot.
  - The native scout keeps using the same `data/scout/` folder, so no history is lost.
- **No Telegram.** Alerts and control come from the machine that runs the trading bot.
- **No live decisions.** Deploying stays on the machine with the keys, after you approve ([section 5](#5-the-pilot-approve-run-re-check)).
  The autopilot ([5.1](#51-the-autopilot-auto)) runs inside the native scout (`bot up`) on that machine, next to the
  bot it starts; a Docker scout cannot start or stop runs.

### 11.4 Set it up

Hardware: 4 or more CPU cores, 4–8 GB of RAM, and disk for the length of the run. The scout with depth needs about
**15 GB per month**.

1. **Install Docker.**
   - Linux: [Docker Engine](https://docs.docker.com/engine/install/) plus the compose plugin, then
     `sudo systemctl enable docker` so it starts at boot.
   - Windows or macOS: [Docker Desktop](https://www.docker.com/products/docker-desktop/) (WSL 2 on Windows), with
     "Start Docker Desktop when you sign in" on.
2. **Keep the machine awake:** turn off sleep and hibernation, and prefer a wired network. A sleeping machine
   records nothing, and the gap cannot be filled later.
   - macOS: keep it plugged in, turn on System Settings → Battery → Options → "Prevent automatic sleeping on power
     adapter when the display is off", and **keep a laptop's lid open** (closing it sleeps the Mac unless an
     external display is attached). For extra safety, leave `caffeinate -s` running in a Terminal window.
3. **Get the code.** Every `docker compose` command below is run from `treading-bot/bot`, the folder with
   `docker-compose.yml`; from anywhere else it fails with "no configuration file provided".
   ```bash
   git clone https://github.com/dhruvamity/treading-bot.git
   cd treading-bot/bot
   ```
4. **Optional: seed it with the history you already have.** A fresh server has no full day yet, so the first scans
   rank nothing ("still recording") until it has recorded for a day. Skipping this costs nothing in the end: the scan
   reads the last 7 full days, which the server records itself within a week, and its data merges with the
   laptop's when you bring it back ([11.6](#116-bring-the-results-back-after-a-week-or-more)). Copy the laptop's
   `bot/data/scout/tape/` (a few hundred MB) into `bot/data/scout/tape/` on the server and the next scan uses every
   recorded day. This works while the containers run: the two machines write differently named part files.
   With SSH between them, on the server:
   ```bash
   rsync -a laptop:treading-bot/bot/data/scout/tape/ data/scout/tape/
   ```
   Without SSH, pack it on the laptop (from `treading-bot/bot`; the archive goes to your home folder):
   ```bash
   tar -czf ~/scout-tape.tgz -C data/scout tape
   ```
   Send `scout-tape.tgz` to the server by AirDrop (it lands in Downloads) or USB, then unpack it there, from
   `treading-bot/bot`:
   ```bash
   tar -xzf ~/Downloads/scout-tape.tgz -C data/scout
   ```
5. **Start it** with **one** of these commands:

   | Command | Runs |
   |---|---|
   | `docker compose up -d --build` | The scout only (recommended) |
   | `SCOUT_WORKERS=2 docker compose up -d --build` | The scout limited to 2 scan workers (default: all cores but one) |
   | `SCOUT_CAPITAL=500 docker compose up -d --build` | The scout, ranking for a $500 account instead of $100 |

   `restart: unless-stopped` brings the containers back after a crash or a reboot (on macOS and Windows, once Docker
   Desktop has started).

**If this machine also trades** (the live bot runs here too), skip Docker ([11.3](#113-what-the-server-does-not-do)):
run `make install`, put `.env` in `treading-bot/bot`, then start everything with:
```bash
.venv/bin/bot up
```
It starts the scout, the Telegram bot and, while a live bot runs, the guardian, all in the background; `bot status`
checks them and `bot down` stops them. They do not come back by themselves after a reboot: run `bot up` again.

### 11.5 Check on it

From `treading-bot/bot`:

```bash
docker compose ps
docker compose logs --tail 50 scout
cat data/scout/recorder.json
cat data/scout/report.txt
ls data/scout/reports/
df -h .
```

| Command | What healthy looks like |
|---|---|
| `docker compose ps` | `arcus-scout` is "Up … (healthy)": the recorder wrote within the last 15 minutes. |
| `docker compose logs` | `ws_connected`, then a `scout_scan` line every 30 minutes. `"go": 0` with `"events": ["offer"]` is normal: nothing is deployed on the server, so it only records the top 3. |
| `recorder.json` | `markets` ≈ 58, `rows_total` climbing, `last_msg_age_s` a few seconds, `paused_for_disk` false |
| `report.txt` | The time of the last scan and the ranking. For the first day on a fresh server it only lists "still recording (under a full day of data)" (see step 4 of 11.5). |
| `reports/` | One file per UTC day |
| `df -h .` | Free disk. The scout pauses recording under 5 GB free. |

If `last_msg_age_s` in `recorder.json` keeps growing, or the container shows `unhealthy`, restart it with
`docker compose restart scout`. Short outages only leave a gap; the scan ignores days with less than 20 hours
recorded.

### 11.6 Bring the results back (after a week or more)

On your laptop, from `treading-bot/bot`:

```bash
rsync -a server:treading-bot/bot/data/scout/ data/scout/
bot scout scan
bot pilot status
```

That copies the tape, cache, scans and reports, re-ranks with everything recorded, and shows the current top 3.
Without SSH, pack it on the server instead (`tar -czf ~/scout-data.tgz -C data scout`), move it over, and unpack it
on the laptop with `tar -xzf ~/Downloads/scout-data.tgz -C data`.

What to look at:
- **`data/scout/reports/`, day by day.** Does the same market and setting stay GO for most days, or does the top
  change every day? A setup that is GO day after day is more trustworthy than one good day.
- **The "each market at its MAXIMUM leverage" table.** Whether higher leverage starts passing as the history grows.
- **The markets that were new in September** (HOOD, SNDK, MSFT, META and others). They only get full days from the
  server's recording, so the first week is their first real test.

Running the laptop's scout at the same time is fine: each writes its own part files, and the store de-duplicates
when it loads them.

---

## 12. The live bot on a VPS (systemd)

For running the **trading** bot unattended (this machine needs the keys). `deploy/scripts/bootstrap.sh` prepares a
fresh Ubuntu 24.04 server (chrony, uv, Python 3.12, firewall); pick a region Arcus allows (`bot region-check`). The
units in `deploy/systemd/`:

| Unit | Runs |
|---|---|
| `bot` | `bot run $BOT_RUN_ARGS` (from `/etc/bot.env`, e.g. `pilot --live --yes`) |
| `bot-guardian` | The guardian |
| `bot-telegram` | The Telegram bot |
| `bot-scout` | `bot scout run` (the same scout as the Docker container, without depth) |

Details, daily checks and emergency procedures: [bot/docs/RUNBOOK.md](bot/docs/RUNBOOK.md).

---

## 13. Daily routine and troubleshooting

**Every day (2 minutes):** `bot status`; `/openpositions` or `bot pilot status`; `cat data/scout/report.txt`; `bot keys` (Arcus
keys expire after at most 180 days; `doctor` refuses to start within 24 h of expiry).

| Problem | What to check |
|---|---|
| `report.txt` is old | Is the scout running? `pgrep -f "bot scout run"`, `tail logs/scout.out`, `cat data/scout/recorder.json` |
| "still recording (under a full day of data)" / "1 full day of data (needs 3)" | Every market needs 3 full recorded UTC days before it can be in a list; `/run` can still start it |
| "Nothing passes all checks" | Normal in volatile hours: the "now" checks fail. Wait for the next scan |
| `doctor` FAIL "never funded" | Deposit USDG to the subaccount the key is bound to |
| `doctor` warns the calendar is short | Add CPI/FOMC/NFP dates to `config/calendars/events.csv` |
| Repeated `UNDERCOLLATERALIZED` | Not enough margin for the order size: the market is off-hours, equity fell, or leverage is too high. The bot pauses that market itself |
| The pilot refuses to approve | The scan is over 90 minutes old or the top 3 changed: check `bot pilot status` and approve again |
| Paper differs from the backtest | Expected to some degree: the backtest fill rule is conservative, and a few days are noisy. Compare over several days |
| A deployment was paused | `/openpositions` shows why; it resumes by itself after two GO scans in a row |
| `make install` fails building `cryptography` on an Intel Mac | `cryptography` 49+ ships no Intel-Mac wheels; `pyproject.toml` pins it below 49 on Intel Macs, so pull the latest code and run `make install` again |
| The scout or bot log shows `ws_error KeyError` and reconnects every few seconds | An old copy of the code meeting a market that went OFFLINE (its book snapshot is empty). Update the code: it now leaves that book empty and keeps the connection |
| `ws_degraded` in the log | Arcus marked a stream stale; the bot drops that book, re-subscribes for a fresh snapshot, and reconciles the account if it was an account stream. Occasional is normal; constant means Arcus trouble |
| `ws_error_frame "Market 'X' is not available"` | X is OFFLINE (pre-listed, halted or delisted). Harmless: recording starts when it turns ONLINE |

---

## 14. Development

```bash
cd bot
make test
make lint
make type
```

`make test` runs the offline tests (no network, no keys), `make lint` runs ruff, and `make type` runs strict mypy.

- Strategies return **desired orders**; they never call a venue. The same strategy objects run in the backtest,
  paper and live (`bot/strategies/base.py`).
- The scout's backtest policies mirror the live strategies; `tests/unit/test_scout.py` and
  `tests/unit/test_leverage.py` check that they quote identical prices and sizes.
- After changing the simulator, bump `SIM_VERSION` in `bot/scout/scan.py` so cached days are recomputed.

---

## 15. Glossary

| Term | Meaning |
|---|---|
| Perp | Perpetual future: a futures contract with no expiry, kept near the spot price by funding payments |
| Maker / taker | Adds liquidity with a resting order / removes it by crossing the spread |
| bps | Basis points: 1 bp = 0.01% |
| BBO | Best bid and offer (the top of the book) |
| Touch | The best bid or best ask |
| Sweep | One taker order that trades through several price levels |
| Adverse selection | Getting filled just before the price moves against you |
| Inventory / skew | Your current position / shifting quotes to reduce it |
| RWA perp | A perp on a real-world asset (stock, index, commodity ETF) with an underlying session |
| RTH / off-hours | The underlying's trading session (04:00–20:00 ET on Arcus) / outside it |
| IMF / MMF | Initial / maintenance margin fraction; max leverage = 1 / IMF; liquidation below MMF |
| ALO / IOC | Add-liquidity-only (post-only) / immediate-or-cancel |
| Dead man's switch | An order to the venue to cancel everything unless the bot keeps checking in |
| Capital | What the bot sizes from: the subaccount's equity × `capital_frac`, rounded down to a fixed series (about 20 steps per decade) so the backtest and the live bot use the same number |
| Least capital | The smallest capital whose smallest order still clears 1.2× the Arcus minimum order |
| Order ceiling | The 99th percentile taker order on a market; our orders never exceed it, so extra capital beyond it only adds margin |
| GO | A setting that passed every check in the latest scan |
