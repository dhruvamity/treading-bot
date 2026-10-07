# Arcus

The Arcus bot: maker (limit-order) trading on [Arcus](https://arcus.xyz) perpetuals, with the scout that records and
backtests every market, the pilot and autopilot that run setups, and the services the other two bots share: `arcus up`,
the one Telegram bot, `arcus export` and `arcus import`. Install, credentials, servers and export are in the
[main README](../README.md); operations (daily checks, emergencies, kill switches) are in the [RUNBOOK](RUNBOOK.md).

Commands run from `treading-bot/arcus`; `arcus` is `.venv/bin/arcus`. Paths such as `data/scout/` are inside `arcus/`.

## Contents

1. [How it works](#1-how-it-works)
2. [Tutorial: zero to a live run](#2-tutorial-zero-to-a-live-run)
3. [Setups](#3-setups)
4. [Sizes, stops and safety](#4-sizes-stops-and-safety)
5. [The scout: recording, backtesting, ranking](#5-the-scout-recording-backtesting-ranking)
6. [The pilot and the autopilot](#6-the-pilot-and-the-autopilot)
7. [Telegram](#7-telegram)
8. [Terminal commands](#8-terminal-commands)
9. [Session files](#9-session-files)
10. [Venue facts](#10-venue-facts)
11. [What the research found](#11-what-the-research-found)
12. [Layout and glossary](#12-layout-and-glossary)

---

## 1. How it works

```
Arcus WebSocket ──► scout recorder ──► data/scout/tape/<MARKET>/<day>/*.npz
                                           │ every 30 min: 8 setups × every market, at its maximum leverage,
                                           ▼ at your capital and stops, on the last 7 full days and the last 24 h
                    lists: 🚀 Most Volume · 💎 Cheapest · 🔥 Max Volume   (data/scout/report.txt)
                                           │ you pick (terminal, Telegram) or the autopilot does
                                           ▼
                      run (paper or live), once a second: stops → the setup's quotes → orders → Arcus
```

- **One implementation.** The backtest and the live bot call the same code for quotes, sizes and stops, so they cannot
  disagree. Paper runs the live bot's code on live data with simulated fills.
- **One market, one setup at a time.** Starting a new run closes the old one first.
- **Backtests are cheap, live is real.** A market needs 3 full recorded days before it appears in a list; `/run` works
  on any market at once, with or without a backtest.

---

## 2. Tutorial: zero to a live run

**Step 1. Start it.** `.venv/bin/arcus up` starts both scouts and the Telegram bot. The first lists appear after about 3 days
of recording; `/run` works at once and never waits for a scan.

**Step 2. Look.** `.venv/bin/arcus status` is one screen for all three bots; `cat data/scout/report.txt` is the latest
ranking. On the phone: `/status`, `/top3`.

**Step 3. Run the best setup on paper.** `.venv/bin/arcus pilot approve 1`, or your own pick from Telegram:
`/run SPY smart 0 50x paper`. Watch it with `.venv/bin/arcus dashboard` (today's volume, PnL and position every 10 s; `/dashboard`
on the phone).

**Step 4. Stop it.** `.venv/bin/arcus pilot close` closes the position and stops. On the phone: `/closeall`, or `/stop` to stop
and keep the position.

**Step 5. Go live** (real money), in this order:
1. `.venv/bin/arcus selftest` while the account is still empty: every line must say PASS or INFO.
2. Deposit USDG into the Arcus subaccount your key is bound to. Use a subaccount the bot has to itself: it treats every
   order and position there as its own.
3. Put `BOT_PILOT_LIVE=1` in `.env`, then `arcus down` and `arcus up`.
4. `.venv/bin/arcus doctor pilot` must end in **READY**.
5. `.venv/bin/arcus pilot approve 1 --live`, and type `LIVE` when asked. From Telegram: `/run SPY smart 0 50x live sl=10`, then
   type back the 6-digit code. `sl=10` means the run may lose $10 in all.

**Step 6. The other two bots** work the same way: [lighter/README.md](../lighter/README.md), [arbitrage/README.md](../arbitrage/README.md).
**Step 7. Bring the data home** for analysis: `arcus export` (main README, section 4).

---

## 3. Setups

A setup is a **mode**, a **spread** in basis points (1 bp = 0.01%) and a **bias**: "Mid 0", "Smart +1", "Grid +3 Short".

| Mode | What it quotes | In the scan |
|---|---|---|
| **Mid** | Both sides `spread` bps from the book's mid, following it. Mid 0 joins the best bid and ask on a one-tick book (BTC, SPY) | 0, +1, +2, +3 |
| **Smart** | Mid, less a side that would add to the position while its fill would likely lose (below) | 0, +1, +2, +3 |
| **Grid** | Around your **last fill**, not the mid: a sell never goes below the last buy + spread. Flat: mid ± max(spread, half the book's spread). Once the mid runs 0.5% against the position from the last fill, it stops adding and closes at the touch, then starts over | `/run` only |
| Bias Long / Short | Holds half the position cap on that side while quoting both; sizes skew toward it | `/run` only |

- **Smart** quotes as Mid and, each second, leaves out the side that would add to the position when the other side of the
  book holds far more than ours (imbalance beyond 0.6; our own orders are not counted) or the mid moved against that side by
  more than 0.5 bp over the last 5 s. The side that reduces the position always stays. The backtest runs the same rule.
- **Why this works at all.** A quote at the best price earns at most half a tick (0.07 bp on SPY), and takers who hit it
  know more: the price moves against the average fill by 0.9–1.4 bp within a minute. Choosing the seconds helps; it does
  not make a one-market, $100 account profitable.
- **Inventory skew.** With inventory I and cap I_cap, u = clamp(I / I_cap, −1, 1). The reservation price
  r = m × (1 − κ × u × h) moves both quotes away from the heavy side (h the half-spread, κ = 1 for spreads above 0), and sizes
  skew too: bid size = q × (1 − u), ask size = q × (1 + u). At u = ±1 the side that would add stops.
- **Requote tolerance.** A live order keeps its queue place while it is within max(2 ticks, 0.25 × half-spread) of the wanted
  price and within 20% of the wanted size; otherwise it is replaced.
- **Defaults that are not knobs:** Mid and Smart run without the safety pause (same cost per dollar, 13–66% more volume);
  from +1 bp the quotes skew against the position; Grid keeps the safety pause; one order per side.
- Any other spread (Mid +0.5, Grid +4, Smart +5) and the older names (`touch 0bp` = Mid 0, `deep 3bp` = Mid +3,
  `anchor 3bp` = Grid +3) run from `/run` without a backtest.

**Run limits.** `/run … sl=10 tp=5 vol=100k`: the run may lose $10 in all (it lifts the daily stop and the kill to that
limit; past it the bot flattens and stops), closes and stops once up $5, or once it has traded $100,000. A take profit or
volume target closes with a reduce-only maker order, then a taker order after 20 s. `/resumeaftersl` does not restart a
finished run: start a new one.

**Off-hours.** Stock, index and commodity perps (RWA) need 1.5× the initial margin to open outside their session
(04:00–20:00 New York, weekdays); the cap and order size shrink there, in the backtest and live.

---

## 4. Sizes, stops and safety

Every size and stop is a share of the **capital** (`arcus/common/sizing.py`, used by the backtest, the session file, the live
engine and the doctor), so the same setup runs on $20 or $20,000.

| Quantity | Rule | $100 at 50x |
|---|---|---|
| Largest position Arcus allows | capital × leverage | $5,000 |
| Inventory cap | that ÷ 1.25 (the risk engine's hard cap, 1.25 × cap, lands on the venue limit) | $4,000 |
| Order size | half the cap, per side | $2,000 |
| Position stop | 1–5% of capital, following the market (below) | $1–5 |
| Daily stop | 2%: close, no new orders until 00:00 UTC | $2 |
| Kill | 10% below the equity peak: close with a taker order, stop until you resume | $10 |

- **Leverage.** Every market is backtested at its **maximum Arcus leverage** only (1 / `initialMarginFraction`: BTC 40x, SPY 50x,
  QQQ/GLD/SLV 25x, NVDA 20x, most stocks and alts 10x). `/set crypto_lev` can cap BTC and ETH; `/run` runs any leverage.
  Leverage does not create fills; it lets you post bigger orders. The stops grow with the capital, not the leverage.
- **Capital** comes from `sizing.capital_usd` in `config/app.yaml`: `auto` (the subaccount's equity × `capital_frac`, at most
  `max_capital_usd`; $100 of paper capital without a funded account) or a number. It is rounded **down** to a fixed series
  (…, 90, 100, 110, 125, 140, 160, …), so the backtest and the live bot size from the same number. The scout keeps its capital
  while the balance stays within 25% of it and moves at most once per UTC day.
- **The live bot follows the account.** Pilot sessions re-size at start, at every 00:00 UTC, and within the day once the equity
  moved 25% (a deposit, a withdrawal, a large loss). A list's pick grows past 1.25× its backtested capital only as far as the
  scans confirm it. Below the setup's **least capital** it stops quoting and closes what is left.
- **Floor and ceiling.** Every order is at least 1.2× the Arcus minimum order (max($5, minimum size × price)): `arcus scout
  limits` prints the least capital per market. One order is never larger than the market's 99th-percentile taker order, so
  extra capital past that is margin cushion.

**The stops (backtest and live).**

| Rule | Default | What happens |
|---|---|---|
| Position stop | dynamic, 1–5% | The open position is down by the stop from its average entry: cancel quotes, exit with a reduce-only maker order at the touch, cross with a taker order after 20 s, pause 60 s. It follows the market: 2 × the market's 1-hour move × the inventory cap, never under 1% of the capital nor over 5%. `/set position_stop 3` fixes it (the daily stop must be at least as large); `/set position_stop auto` gives the dynamic one back |
| Daily stop | 2% | The day is down 2%: close the same way, no new orders until 00:00 UTC, then resume by itself. **At maximum leverage this ends most days early** unless the run has `sl=`; `/set daily_stop 5` widens it for every run |
| Kill | 10% | Equity more than 10% below its peak: taker flatten and stop until you resume |
| Safety pause | on (hand-written sessions) | Spread over 3× its 1-hour median (and 1 bp above it) or a 1-second move over 6σ: no quotes for 30 s |
| Liquidation distance | 4σ | Distance to liquidation below 4σ of 1-hour moves: cut half the position at market; re-armed above 6σ |
| Position caps | 1.2× / 1.25× | No new order that could take the position past 1.2× the cap; the risk engine rejects anything past 1.25× |

Other automatic stops (live engine): event windows (no new quotes ±30 min around CPI, FOMC and jobs reports; ±24 h around a
stock's earnings); a market that is not ONLINE; an off-hours price band in its expansion zone or an open-interest cap; a silent
heartbeat (60 s); order pool under 20% (requote tolerance doubles) or 5% (cancels only); a failed dead-man's-switch refresh
twice, `SELF_TRADE` or `GEO_RESTRICTED` rejections and any unexpected error (safe mode, manual resume); the same rejection
5 times in 60 s (pause that market, 60 s doubling to 10 min). Every automatic action goes to the decision log with its reason.

**Safety systems.**
- **Live lock.** A real order needs `--live` **and** your approval (typing `LIVE`, or `live_enabled: true` in the session for
  unattended `--yes` starts) **and** a `doctor` with no FAIL. A live request is never silently turned into paper.
- **Dead man's switch.** Every 20 s the bot tells Arcus "cancel all my orders in 60 s unless I check in again"
  (`scheduleCancel`). If the bot or the machine dies, Arcus cancels everything by itself.
- **Guardian** (`arcus guardian`): a separate process with its own connections. It cancels everything if the live heartbeat is
  silent for 60 s, and flattens (reduce-only) past the drawdown limit. It never sends an order that increases risk.
- **Reconciliation.** Every 5 minutes and on every start the venue is the source of truth: unknown orders are cancelled and
  positions are taken from Arcus.
- **Separate state per mode:** paper, testnet and live each have their own database and heartbeat.

---

## 5. The scout: recording, backtesting, ranking

**Records** (per market per UTC day, under `data/scout/tape/<MARKET>/<day>/`): `bbo` (the best bid and ask with sizes, a row
when a price changes or sizes change and a second passed), `trades` (every trade with price, size, taker side and Arcus's
`sequenceNumber`, shared by all prints of one taker order) and `depth` (the top 10 levels, once a second when the book
changed). Two WebSocket connections (three with depth), the market list re-read every 10 minutes, recording paused under
5 GB free disk; `data/scout/recorder.json` shows its health. Arcus serves no old order books, so **what is not recorded is gone**.

**Backtests** (`arcus/scout/sim.py`): for each market, setup and UTC day (each day starts flat, with 2 hours of warm-up), the
simulator replays the tape one second at a time (BTC twice a second), as the live bot decides.
- **Timing.** New orders go live and cancels take effect 150 ms later. A post-only order that would cross on arrival is rejected.
- **Fills (queue model).** A resting order fills when a taker trades **through** its price, or **at** its price once trades there
  used up the size shown ahead of it when it joined. One taker order fills us for at most what it printed beyond the queue. This
  matched the largest live run (SPY, 2–3 Oct) within 11% on volume and 3% on cost.
- **Costs.** Maker fee 0, taker fee 2.25 bps. A taker exit pays the opposite best price plus 5 bps slippage on any size beyond
  what the best level shows; a leftover position is charged the half-spread plus the taker fee.
- **Arcus order budget.** The order pool is 20,000 actions, growing by one per $0.10 filled. The governor doubles the requote
  tolerance when actions per filled dollar get too high and allows cancels only when the pool is nearly empty, as live.
- **Risk rules:** the same stops as live, the safety pause, the liquidation-distance cut and liquidation itself.

Completed days are cached in `data/scout/cache/`, so a scan re-runs only the current 24 hours.

**GO checks.** A setting is GO only when all three windows pass:

| Window | Checks |
|---|---|
| Long: last 7 full days | at least **3 full recorded days**; average PnL/day ≥ −0.25% of the capital; at most one daily stop; never the kill or a liquidation; at least half the days not negative; at least 5 fills a day |
| Short: last 24 h (re-run every scan) | 24 h PnL ≥ −0.25%; last 6 h ≥ −0.50%; no kill in the last 24 h; at least 30% of its usual fills |
| Now: last 60 one-minute prices | not trending (efficiency ratio < 0.5); volatility and spread under 2× usual; data under 5 minutes old |

A market's first recorded day counts as full only if its own data covers 20 hours of it. A deployment whose market goes offline
is paused and resumes after two GO scans once it trades again.

**Lists** (each shows the best setting per market, top 3):

| List | Shows |
|---|---|
| 🚀 Most Volume (`/top3`) | The most volume that costs at most `volume_cost` (default $0.20 per $1,000 traded) |
| 💎 Cheapest (`/cheapest`) | The lowest cost, among setups trading at least 50× the capital a day |
| 🔥 Max Volume (`/maxvolume`) | The most volume whatever it costs; every safety check still applies |

`report.txt` columns: `order` (dollars per order), `fills/d`, `volume/d` (maker, USD), `pnl/d` and `worst` (daily PnL after fees and
closing any leftover), `24h`, and `cost` (dollars lost per $1,000 traded; in the full ranking it is `why not`: `GO` or the failed
check). A second table shows each market at its maximum leverage even when it fails.

**Files:** `data/scout/report.txt` (readable), `reports/<day>.txt` (the last scan of each UTC day), `latest.json` (what the pilot
reads), `scans/<time>.json` (every scan), `cache/`, `recorder.json`, `markets.json` (refreshed every 10 minutes).
**Scan cost:** workers run at the lowest CPU priority, one worker while a bot trades on the machine, otherwise all cores but one;
the 24 h re-run covers only setups that pass on their full days. `/set scan_every`, `scan_workers`, `scan_budget` tune it.

---

## 6. The pilot and the autopilot

**Approving** (`arcus pilot approve N [--live]`, or the Telegram Run buttons): it re-reads the latest scan and refuses if it is over
90 minutes old (and from Telegram, if the top 3 changed between your tap and your confirm); writes `config/sessions/pilot.yaml`
with the strategy, leverage, sizes and stops at the backtested capital plus the `sizing` recipe the engine re-sizes from; closes
a running bot first (reduce-only maker exit, then taker; it stops after 10 minutes even if a position is left, with a critical
alert); and starts `arcus run pilot`.

**Reviewing**, after every scan: a running setup that is still GO records the capital it was backtested at; one that is no longer
GO is **paused** (reduce-only exits close the position) with the reason and the current top 3, and resumes by itself after two
GO scans; another GO setting with ≥ 1.5× the volume is suggested once. It never switches without you. State is in
`state/pilot.json`; events in `state/pilot_events.jsonl`, which the Telegram bot posts.

**The autopilot** (`/auto`, off by default) spends a daily loss budget only where the backtests say volume is cheapest *now*.
The same BTC Mid 0 costs 0.6 bp in a calm weekend hour and 2.7 bp in a wild weekday one.
- **The pot:** `budget` dollars are added at 00:00 UTC; unspent money carries over, up to a week; each run's result comes out.
- **Every minute** it looks at the session (weekend, Asia, London, US open, US afternoon, US evening, each on its city's clock),
  each market's state (the last hour's volatility against its usual level at that hour: calm, normal, busy, wild; a 6× one-minute
  move is a **shock** that leaves the market out for 30 minutes), events (flat from 45 minutes before CPI, jobs and FOMC until 30
  minutes after; a stock from 24 h before its earnings to 24 h after), and the **playbook**: every hour of the last 42 days
  backtested from flat for 8 setups (only days with a recorded book once a market has 10 of them, 2 at a weekend), rebuilt daily
  (`arcus scout playbook`).
- **Nothing running:** it starts the setup with the most volume per hour whose predicted cost is within the cost ceiling, at the
  market's maximum leverage; the run stop `sl=` is what is left of the pot, at most 3 days of budget. **Running:** it keeps it while
  its prediction stays within the ceiling (+15%), and stops when the market turns busy or wild, an event or shock comes, or the pot
  is gone. It switches when another setup gives 1.5× the volume after 20 minutes, and rests 10 minutes after a stop.
- **The ceiling** is tuned daily for your budget (the playbook replays the rule over 4 weeks at every ceiling); `/auto cost 1.6`
  fixes it, `/auto cost auto` gives the tuning back.
- **Turning it on:** `/auto` → 📝 On (paper) or 🔴 On (LIVE), or `/auto on live budget=5`. LIVE needs `BOT_PILOT_LIVE=1` and a typed
  code, and the doctor runs before every live start. It turns itself **off** when you take over (your `/run`, `/stop`, `/closeall`,
  `/cancelall`, `/pilotclose`, or a run from the shell); `/auto off` closes its run and stops. State: `state/autopilot.json`,
  `data/scout/playbook.json`, `state/calendars/earnings.csv`.

---

## 7. Telegram

One Telegram bot controls all three bots: set it up once ([main README](../README.md), section 2). It reads the runner's state,
writes flags the runner applies on its next tick, and holds no trading state, so restarting it never touches a run. Every message
has the same layout (an emoji and a bold title, then short monospace lines; `arcus/common/tgfmt.py`). Replies from Lighter start
with **LIGHTER** and the arbitrage's with **FUNDING ARB**; an unlabelled one is Arcus. `/menu` shows buttons; `bot telegram
--read-only` refuses every control.

**See**

| Command | What it does |
|---|---|
| `/status`, `/dashboard` | Is it running, today's volume, PnL and position; a live screen edited every 10 s (⏹ / ▶️ buttons; a newer `/dashboard` replaces the old) |
| `/balance`, `/account` | Equity, deposits vs trading PnL, 1/7/30-day change; all-time perps volume, fees paid and earned, fee tier |
| `/positions`, `/orders`, `/openpositions` | What you hold; what is on the book; what is deployed and today's PnL vs the backtest |
| `/pnl`, `/logs [n]`, `/yesterdayreport [date]`, `/sessions` | PnL by market; the latest decisions; the daily report; session files |

**Run**

| Command | What it does |
|---|---|
| `/top3`, `/cheapest`, `/maxvolume` | The three lists, with ▶️ buttons that open the run form |
| `/run` | The run form: market, then one tap per field (Mid, Grid or Smart · spread −1…+5 · Short / Neutral / Long · leverage · run stop · volume target), then 📝 Paper or 🔴 LIVE. In one line: `/run BTC mid 0 40x live sl=10 vol=100k tp=5`, `/run SPY grid 3 short max live`, `/run SPY smart +3 50x paper`. It shows the sizes, how it quotes, and the backtest of exactly that setup (or that it has none). A list's pick is judged by that list; anything else is **your pick** and is never paused for its numbers |
| `/auto` | The autopilot: `/auto on paper\|live budget=5 cost=1.5`, `/auto off`, `/auto budget 5`, `/auto cost auto` |
| `/doctor NAME` | Live readiness check (reads only) |

For real money the form's **🔴 LIVE** (shown only with `BOT_PILOT_LIVE=1`) runs the doctor on the session and sends a 6-digit code to
type back within 2 minutes. After a paper run, `/openpositions` → **🔴 Go LIVE with this setup** starts the same market, setting and
leverage live with the same checks.

**Control**

| Command | What it does |
|---|---|
| `/pauseneworders [MARKET]`, `/unpause [MARKET]` | Stop / restart new orders; closing orders keep working. The pause survives restarts; a new `/run` clears it |
| `/stop` | Shut the run down: quotes cancelled, positions kept (Confirm button) |
| `/resumeaftersl` | Trade again after a safety stop (safe mode, the kill, the daily stop), once you know why (Confirm button) |
| `/cancelall`, `/closeall [taker]` | Cancel every order (Confirm button); close every position, maker or IOC (typed code) |

Commands act on the running bot (live first); add `paper`, `testnet` or `live` to pick, e.g. `/status paper`. Anything that changes a
live bot asks first.

**Settings** (no file editing, no restart): `/settings` lists each setting; `/set NAME VALUE` changes one (Confirm button), `/set NAME
default` undoes it; `/scannow` scans now. Stored in `state/settings.json` over the defaults in `config/app.yaml`; the scout picks a
change up at its next scan, the running bot at its next re-size. Switching live trading on stays a deliberate step in `.env`.

| Setting | Values | What it changes |
|---|---|---|
| `capital` | `auto` or dollars | Money the bot sizes for: the account's balance, or a fixed amount (never more than the balance) |
| `trade_share` | 1–100 (%) | Share of the balance to trade |
| `max_capital` | dollars or `none` | Never size for more than this |
| `position_stop`, `daily_stop`, `kill` | % of the capital; `position_stop` also takes `auto` | The stops (section 4); position ≤ daily ≤ kill. Changing them means a full re-backtest |
| `crypto_lev` | number | Cap BTC and ETH leverage |
| `scan_every` | 10–240 (minutes) | Time between scans |
| `scan_workers` | `auto` or 1–32 | CPU cores a scan may use |
| `scan_budget` | 5–240 (minutes) | Most time one scan spends backtesting full days (default 30); the rest continues in the next |
| `volume_cost` | $0.01–$5 per $1,000 | The most the Most Volume and Cheapest lists may cost (default $0.20 = 2 bp) |

**Alerts it sends by itself** (critical ones ignore `/mute`; `/alerts`, `/mute [min]`, `/unmute`): the bot went down or came back; safe
mode, a drawdown stop or a daily stop appeared or cleared; day PnL at half, then all, of the daily limit; fills (each, an hourly
summary, or none) and a digest after 00:00 UTC; the pilot's new #1, pauses (with the reason), resumes and better setups; the
autopilot's starts, switches, stops and run ends.

**Lighter and the arbitrage in the same bot:** the Lighter commands are these with `l_` in front (`/l_status`, `/l_run`, `/l_closeall`;
`/l` shows its menu); the arbitrage's start with `arb_` (`/arb_status`, `/arb_scan`; `/arb` shows its menu). A command a Lighter
message mentions is written `/l_…`, so tapping it stays on Lighter: `/closeall` closes Arcus, `/l_closeall` closes Lighter.

---

## 8. Terminal commands

| Command | What it does |
|---|---|
| **Run the machine** | |
| `arcus up` | Start both scouts, the Telegram bot, and the guardian while a live bot runs |
| `arcus down [--all]` | Stop them. `--all` also stops every run (quotes cancelled, positions kept) |
| `arcus status [--json]` | One screen: services, runs, last scan, balance, Lighter, arbitrage |
| `arcus dashboard [--once]` | Live screen every 10 s: today's volume, PnL, position |
| **Trade** | |
| `arcus pilot status` / `approve N [--live] [--list cheapest\|max] [--max-lev]` / `close` | What is deployed and the top 3; run a list's pick on paper (or live); close the position and stop |
| `arcus auto [status\|on\|off\|set] [--live] [--budget USD] [--cost BP]` | The autopilot |
| `arcus sessions` / `run SESSION [--live] [--yes] [--seconds N]` | List the session files; run one by hand |
| `arcus cancel-all --venue arcus [--market M] [--yes]` / `flatten --venue arcus [--taker]` | Cancel every open order now / close every position now (reduce-only; asks to confirm) |
| `arcus resume [--venue V] [--all]` | Trade again after a kill or safe mode, once you know why |
| **Check** | |
| `arcus doctor [SESSION] [--paper]` | Everything a run needs: credentials, subaccount, funds, sizing, clock, region, calendar. Places no orders |
| `arcus selftest [--allow-funded]` | Proves Arcus accepts every signed request, without trading |
| `arcus keys` / `account` / `probe` / `region-check` | Your API keys as Arcus sees them; all-time volume, fees and tier; live market parameters and rate budgets; may this IP trade Arcus perps? |
| `arcus report [--date D] [--mode M]` | The daily report: Net = spread capture + inventory PnL + funding − fees − liquidation loss |
| `arcus diagnose [--hours N \| --since … --until …] [--market M] [--replay]` | Why a run filled what it filled: orders, acks, rejects, how long quotes rested, what blocked quoting. `--replay` backtests the same minutes beside the run. Read-only |
| **Scout** | |
| `arcus scout run [--workers auto\|N] [--every-min M] [--depth] [--ladder] [--capital auto\|USD]` | The recorder + scanner daemon (`arcus up` runs it) |
| `arcus scout scan [--markets …] [--full]` / `limits` / `playbook [--capital USD]` | One scan now, printed; the least and most capital each market can use; the autopilot's table |
| **Data** | |
| `arcus export …` / `import [FILE]` | One file with everything new since the last export / take such a file in (main README, section 4) |
| `arcus telegram [--read-only]` / `guardian` / `secrets …` | The Telegram bot / the independent guardian / an encrypted secrets store (alternative to `.env`) |

`arcus --help` lists every option. One-off tools in `scripts/`: `arcus_register_key.py` (register or rotate an API key; run it on your own
machine, it asks for the wallet key and never stores it), `arcus_transfer.py` (move collateral between your subaccounts),
`ws_capture.py` (capture public WebSocket frames for test fixtures), `latency_map.py` (where each venue is hosted and how far this machine is).

---

## 9. Session files

A session is one YAML file in `config/sessions/`. The pilot writes `pilot.yaml`; `arcus_btc_mm.yaml` is an example (`arcus sessions` lists
them). Main fields:

| Field | Meaning |
|---|---|
| `session_id`, `venue`, `market`, `account_index` | Name; `arcus`; the base asset (`QQQ`); the subaccount 0–9 your key is bound to (`arcus keys`) |
| `mode`, `bias`, `bias_frac` | `mid`, `grid` or `smart`; `neutral`, `long` or `short` and the share of the cap it holds (0.5) |
| `live_enabled` | Part of the live lock: required for unattended live starts |
| `capital_usd`, `leverage_max`, `order_size_usd`, `inventory_cap_usd`, `inventory_cap_off_usd` | Capital, leverage, order size, cap (`auto` = derived), cap outside an RWA session |
| `spacing_bps`, `execution_style`, `levels_per_side`, `level_step_bps`, `offset_bps` | The spread (Mid +1 = `1`) and quote placement; the pilot writes `passive`, 1 level |
| `skew_kappa`, `passive_k_sigma`, `reset_threshold_pct` | Inventory skew strength; passive extra distance in σ; Grid's soft reset |
| `pos_stop_usd`, `daily_stop_usd`, `kill_usd`, `exit_taker_after_s`, `cooldown_s` | The stops in dollars for `capital_usd` |
| `sizing` | Pilot sessions: `follow_equity`, `backtest_capital_usd`, `capital_frac`, `max_capital_usd`, `leverage`, `order_max_usd` (liquidity ceiling), the stops in %, `min_capital_usd` |
| `stop_loss_pct`, `take_profit_pct`, `take_profit_usd`, `volume_target_usd`, `max_loss_usd` | Session-level limits (`tp=`, `vol=`, `sl=`) |
| `safety_pause` | `move_sigma_1s`, `spread_x_median`, `depth_frac_min`, `resume_s` |
| `session` | `windows_ist`, `skip_et` (New York windows on NYSE days with no new quotes) and `skip_events` (`cpi`, `fomc`, `nfp`, `earnings`) |
| `off_hours` | `spacing_mult`, `size_mult`, `allow_mid` for RWA perps outside their session |

The account-wide numbers are settings (`/set`), not session fields. Event dates are in `config/calendars/events.csv` (keep FOMC 30 days and
CPI 14 days ahead).

---

## 10. Venue facts

Where early design notes and the venue disagreed, the code follows the venue (`config/venues/arcus.yaml`; `arcus/core/liveparams.py`
refreshes the live values hourly and logs any change to `data/param_changes_jsonl/`).

| Item | Fact |
|---|---|
| Minimum order | $5 **and** a minimum size: BTC 0.0001 (≈ $8.6) binds, so the bot's 1.2× order is about $10.3 |
| `goodTilTime` | Required ≥ about 1 month on every order, including IOC and FOK; the bot always sends 35 days |
| ALO (post-only) | Skips the 50 ms taker speed bump |
| Rate limits | 1,500 weight/min per IP (the bot targets ≤ 50%); order pool 20k and cancel pool 40k, growing by 1 unit per $0.10 filled; `batchPlaceOrders` ≤ 39 free, `batchCancel` ≤ 100 |
| `scheduleCancel` | 5 s to 5 min, **max 10 fires/day**; the bot refreshes every 20 s with a 60 s deadline |
| Modify | Every modify by clientId alone was refused live (2026-09-25); by orderId it works. The bot modifies only by orderId, and only with `use_modify: true` after `arcus selftest --allow-funded` passes |
| WebSocket | 50 connections, 100 subscriptions per connection, 1,000 per IP, 24 h lifetime; the per-market `id` is the display name (`BTC-USD`) |
| Liquidation fee | Not documented and not measurable without a liquidation; the test simulator assumes a full close at mark with a 1% fee |
| Counterparties | On BTC one taker address made about 78% of taker notional in the recorded sample: quotes face one dominant counterparty |

---

## 11. What the research found

Backtests on the recorded books (September–October 2026) and two live weekends; none of it is a promise.

- **Quoting at the touch cannot pay on Arcus.** It earns at most half a tick, the maker fee is 0 with no rebate below the VIP tier
  ($1B in 30 days), and the price moves against the average fill by 0.9–1.4 bp within a minute. The bot buys volume at a known
  cost; the biggest accounts on the leaderboard profit from hours-long inventory and hedges this bot does not have. An outside price
  (Binance leads Arcus BTC) is too slow for a once-a-second bot.
- **The backtest matches live.** On the largest live run (SPY, 2–3 Oct) the queue model gave $867k at −$37 against $975k at −$36 live.
  The loss is inventory drift (5-minute markout −0.3 bp), not fees.
- **Smart costs less than Mid** at the same spread for about 80% of the volume; the market-following position stop beats a fixed 1%
  (1.66 bp pooled against 1.89 bp, with a third of the stops of a fixed 3%); Grid is the dearest family; Mid −1 quotes what Mid 0 quotes;
  a bias costs more than Neutral. That is why the scan has 8 setups.
- **Weekends:** SPY and QQQ are the cheapest volume (SPY Mid 0 about 0.8 bp, Mid +1 about 0.5 bp) but 15–35× slower than BTC; BTC is
  the fastest, and about 15% cheaper at the weekend. After-hours and overnight SPY cost the least; pre-market and regular hours about 3 bp.
- **Volume is limited by the daily stop, not by the strategy.** In an October sweep of 207 variants over 11 recorded days (a
  volatility-adaptive spread, Avellaneda–Stoikov skew, microprice, toxicity widening, fill-run cooldown, min-spread gating, an
  inventory-aging exit, every stop and size knob), none beat the current setups out of sample, and the three best leads failed on
  held-out days. Widening the daily stop from 2% to 5% buys 2–3× the volume at roughly 0.75 bp (equity indexes) to 1.7 bp (crypto
  majors) of marginal cost per extra dollar traded: that is your dial, not a free improvement.
- **Open interest:** flat market making holds almost no position (average open notional $38–$1,700 against caps of $1.5k–$7k).
- **Speed is not the limit:** the engine's decision takes 0.45 ms; a server near Arcus is worth 0.03–0.08 bp.

---

## 12. Layout and glossary

```
arcus/
  arcus/           scout/ (tape, sim, scan, record, pilot, autopilot, playbook, regime, profiles), core/ (engine, risk, guardian,
                   order manager, budget, doctor, runner), strategies/ (mid, grid, smart, setup, quoting), venues/ (arcus, paper),
                   telegram/ (bot, views, dashboard, control, watcher), common/, cli.py, ops.py, export.py
  config/          app.yaml, sessions/, venues/arcus.yaml, calendars/
  deploy/          scripts/bootstrap.sh (server setup), scripts/region_check.py
  scripts/         one-off tools (section 8)        tests/   offline: no network, no keys, no processes
```

Tests and lint: `make test`, `make lint`, `make type` (main README, section 7). Strategies return desired orders and never call a venue.

| Term | Meaning |
|---|---|
| Perp | Perpetual future: no expiry, kept near spot by funding payments |
| Maker / taker | Adds liquidity with a resting order / removes it by crossing the spread |
| BBO, touch | Best bid and offer; the best bid or ask itself |
| Sweep | One taker order that trades through several price levels |
| Adverse selection | Getting filled just before the price moves against you |
| RWA perp, off-hours | A perp on a real-world asset (stock, index, commodity ETF) with an underlying session (04:00–20:00 ET) / outside it |
| IMF / MMF | Initial / maintenance margin fraction; max leverage = 1 / IMF; liquidation below MMF |
| ALO / IOC | Add-liquidity-only (post-only) / immediate-or-cancel |
| Dead man's switch | An order to the venue to cancel everything unless the bot keeps checking in |
| Least capital / order ceiling | The smallest capital whose smallest order clears 1.2× the minimum / the 99th-percentile taker order, above which extra capital only adds margin |
| GO | A setting that passed every check in the latest scan |
