# treading-bot

Three maker (limit-order) bots for perpetual futures. Their aim is as much **maker volume** as possible at the lowest
cost per dollar traded. They share one install, one `.env`, one Telegram chat and one start command.

| Bot | Folder | Terminal | Telegram | What it does | Guide |
|---|---|---|---|---|---|
| **Arcus** | [`arcus/`](arcus) | `arcus …` | `/status`, `/run`, `/closeall` … | Quotes both sides of one Arcus perp at a time. Its own start and stop: `arcus up`, `arcus down`, `arcus scout` | [arcus/README.md](arcus/README.md), [RUNBOOK](arcus/RUNBOOK.md) |
| **Lighter** | [`lighter/`](lighter) | `lighter …` | the same with `l_`: `/l_status`, `/l_run` | The same setups on Lighter (Robinhood Chain), a zero-fee venue | [lighter/README.md](lighter/README.md) |
| **Arbitrage** | [`arbitrage/`](arbitrage) | `arbitrage …` | `/arb_status`, `/arb_scan` … | Short the venue that pays more funding, long the other, equal size | [arbitrage/README.md](arbitrage/README.md) |

Where each stands (October 2026):
- **Arcus:** traded live with real money; the backtest matched the largest live run on cost.
- **Lighter:** live-tested on 2026-10-10 at the smallest size (a request-by-request test, then a ten-minute run that ended
  flat, [lighter/README.md](lighter/README.md) section 6). Not yet run for hours or days.
- **Arbitrage:** paper for the whole executor. Its Lighter leg passed a real-money test the same day; its Arcus leg and the
  two-leg engine have never run live. The live switch is off.

> **Risk warning.** Experimental software that can place real orders with real money. Backtests are estimates, not
> promises. Nothing here is financial advice. Run paper first, then small, and only with money you can lose.

**The five commands** (from `treading-bot/arcus`; `arcus` is `.venv/bin/arcus`):

```bash
.venv/bin/tbot up
.venv/bin/tbot status
.venv/bin/arcus pilot approve 1
.venv/bin/arcus pilot close
.venv/bin/tbot export
```

They start everything, show one status screen, run the best Arcus setup on paper, close it, and pack everything recorded
and traded into one file ([Export and import](#4-export-and-import)).

**New here?** Section [9](#9-tutorial-the-commands-by-what-you-want-to-do) is the step-by-step guide with the exact
command for each job, for all three bots. Section [10](#10-two-small-servers-azure-free-tier-and-the-arbitrage-alone)
covers 1 GB servers (an Azure free-tier VM, the arbitrage on its own, a recorder plus a trader).

**Which command starts what.** Each bot starts and stops only itself; `tbot` is for what belongs to the whole machine.

| To | Command |
|---|---|
| Start everything this machine is for (`BOT_ROLE`) | `tbot up` |
| Start one part | `arcus up` (or `arcus scout`), `lighter up` (or `lighter scout`), `tbot up telegram` |
| Start both scouts, nothing else | `tbot scout` (or `tbot up scouts`) |
| Stop them | `arcus down`, `lighter down`, `tbot down [parts]` (`--all` also stops the runs: positions are kept) |
| Look | `arcus status`, `lighter status`, `arbitrage status`, and `tbot status` for the whole machine |
| The Telegram bot, in a terminal | `tbot telegram` (the one bot controls all three; it has no owner among them) |
| Export, import, sync, recommend | `tbot export`, `tbot import`, `tbot sync`, `tbot recommend` |

The old spellings `arcus telegram|export|import|sync|recommend` keep working. `arcus up|down|status` now mean the Arcus part only.
In Telegram, Arcus is spelled like the other two: `/arcus_status` (or `/a_status`), `/lighter_status` (or `/l_status`), `/arb_status`;
the bare `/status` is still Arcus's.

## Contents

1. [How it fits together](#1-how-it-fits-together)
2. [Install](#2-install)
3. [Run it on a server](#3-run-it-on-a-server)
4. [Export and import](#4-export-and-import)
5. [Where everything lives](#5-where-everything-lives)
6. [Troubleshooting](#6-troubleshooting)
7. [Development](#7-development)
8. [Upgrading from the old layout](#8-upgrading-from-the-old-layout)
9. [Tutorial: the commands, by what you want to do](#9-tutorial-the-commands-by-what-you-want-to-do)
10. [Two small servers: Azure free tier and the arbitrage alone](#10-two-small-servers-azure-free-tier-and-the-arbitrage-alone)

---

## 1. How it fits together

```mermaid
flowchart LR
    AV[Arcus] -->|public market data| AS[Arcus scout<br/>record + backtest]
    LV[Lighter] -->|public market data| LS[Lighter scout<br/>record + backtest]
    AS --> AL[Lists: Most Volume,<br/>Cheapest, Max Volume]
    LS --> LL[Lighter's lists]
    AL --> YOU{You pick<br/>terminal, Telegram<br/>or the autopilot}
    LL --> YOU
    YOU --> AR[Arcus run<br/>paper or live]
    YOU --> LR[Lighter run<br/>paper or live]
    AR -->|orders| AV
    LR -->|orders| LV
    AV --> ARB[Arbitrage<br/>one position, two legs]
    LV --> ARB
    AR --> EX[tbot export<br/>one file]
    LR --> EX
    ARB --> EX
```

The loop is: **record → backtest → rank → you pick → run → export → analyse → refine.**

**What runs** (every process is an ordinary command in the background):

| Process | Started by | What it does | Needs keys |
|---|---|---|---|
| Arcus scout | `arcus up` or `arcus scout` (or `tbot up`) | Records every Arcus perp (best bid/offer, trades, top-10 depth). Every 30 min it backtests 8 setups on every market and ranks them; it also checks the running setup and runs the autopilot | no |
| Lighter scout | `lighter up` or `lighter scout` (or `tbot up`) | The same for every Lighter perp (36 setups) | no |
| Telegram bot | `tbot up telegram` (or `tbot up`), when the token is in `.env` | The one chat that controls all three bots and posts alerts | the token |
| Guardian | by itself, with a live Arcus run | A separate process: cancels everything if the live bot is silent for 60 s | yes |
| Arcus run | you (`/run`, `arcus pilot approve`) or the autopilot | Trades one setup on one market | live only |
| Lighter run | you (`/l_run`, `lighter run`) or its autopilot | The same on Lighter | live only |
| Arbitrage executor | you (`/arb_start`, `arbitrage start`) | Holds one position across both venues | live only |

**Rules that hold everywhere:**
- **Paper is the default.** Paper uses live market data and simulated orders, through the same code as live.
- **Live needs three things:** a switch in `.env`, a passing `doctor`, and a typed confirmation.
- **One market-making setup per venue at a time.** Starting another closes the first.
- **The backtest and the live bot share their rules:** sizes, stops and quotes come from the same code.
- **The three bots are independent packages.** None imports another's code at start-up; Arcus finds the other two by
  their folder names (`../lighter`, `../arbitrage`) and the arbitrage uses the other two bots' venue clients.

---

## 2. Install

Needs macOS or Linux, Python 3.12 and [uv](https://docs.astral.sh/uv/) (`brew install uv`, or
`curl -LsSf https://astral.sh/uv/install.sh | sh`). On a fresh Ubuntu server use [section 3](#3-run-it-on-a-server).

```bash
git clone https://github.com/dhruvamity/treading-bot.git
cd treading-bot/arcus
make install
cp .env.example .env
chmod 600 .env
```

`make install` builds `arcus/.venv` with all three bots. Every command in these guides runs from `treading-bot/arcus`
(the `.env` is read from the folder you run in).

**Credentials** (`arcus/.env`; fill in only what you use, and never commit it):

| Variable | What it is | Needed for |
|---|---|---|
| `ARCUS_ADDRESS`, `ARCUS_API_PRIVATE_KEY` | Your wallet address and an Arcus API key (Arcus web app → API Keys) | Arcus live, `doctor`, balance |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | From @BotFather; your chat id (`/whoami` tells you) | Telegram |
| `TELEGRAM_ALLOWED_USER_IDS` | Only these users may send commands | recommended |
| `BOT_PILOT_LIVE=1` | Allows live Arcus runs | Arcus live |
| `LIGHTER_ADDRESS`, `LIGHTER_API_PRIVATE_KEY`, `LIGHTER_API_KEY_INDEX`, `LIGHTER_ACCOUNT_INDEX` | The Lighter key ([lighter/README.md](lighter/README.md), section 3) | Lighter live |
| `LBOT_LIVE=1` | Allows live Lighter runs | Lighter live |
| `ARB_LIVE=1`, `ARCUS_ACCOUNT_INDEX` | Allows the live arbitrage; the Arcus subaccount it uses | arbitrage live |
| `BOT_ROLE` | What this machine is for: `all` (default), `trader`, `recorder` or `scout` ([two machines](#two-machines-one-records-one-trades)) | two machines |
| `BOT_SYNC_FROM`, `BOT_SYNC_KEY` | On a trader: `user@host` of the machine that makes the lists, and the key file it fetches with (default `~/.ssh/treading_bot_sync`) | a trader that fetches lists |

The switch names (`BOT_PILOT_LIVE`, `LBOT_LIVE`, `ARB_LIVE`) are unchanged from earlier versions on purpose, so an existing
`.env` keeps working. Recording, backtests and paper need **no keys at all**. Check what you filled in (both only read):

```bash
.venv/bin/arcus doctor
.venv/bin/arcus keys
```

**Telegram** (optional, once):
1. In Telegram, message **@BotFather**, send `/newbot`, and put the token in `TELEGRAM_BOT_TOKEN`.
2. Send your new bot any message, open `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser, and put
   `chat.id` in `TELEGRAM_CHAT_ID`.
3. `tbot up` starts it. Send `/whoami`, put your user id in `TELEGRAM_ALLOWED_USER_IDS`, then `tbot down` and `tbot up`.

Then follow [arcus/README.md](arcus/README.md), section 2, from zero to a live run.

---

## 3. Run it on a server

A server records around the clock and trades while your laptop is off. Recording needs no keys.

**Pick the machine.** Ubuntu 24.04, 4 or more CPU cores, 8 GB of memory, and disk for the recording: about
0.3–0.5 GB a day for both venues (10–15 GB a month). Pick a region both venues allow; `arcus region-check` asks Arcus,
and `arcus/scripts/latency_map.py` shows how far the machine is from each venue.

**Set it up** (once, as a normal user who can `sudo`):

```bash
git clone https://github.com/dhruvamity/treading-bot.git
cd treading-bot/arcus
bash deploy/scripts/bootstrap.sh
```

The script installs the bots, keeps the clock in sync, closes the firewall to everything but SSH, creates `.env` from
the template, and makes `tbot up` run after every reboot. It starts nothing and is safe to run again. (It has been
syntax-checked but not yet run on a real server: read its 5 steps as they print.)

**Fill in the keys, check, start:**

```bash
nano .env
```

```bash
.venv/bin/arcus region-check
.venv/bin/arcus doctor
.venv/bin/tbot up
```

To move your keys from the laptop instead of typing them: `scp arcus/.env USER@SERVER:treading-bot/arcus/.env`.

**Every day** (or from the phone: `/status`, `/l_status`): `.venv/bin/tbot status`.

**Update the code:**

```bash
git pull
make install
.venv/bin/tbot down
.venv/bin/tbot up
```

`tbot down` does not touch a running trade; `tbot down --all` stops the runs too (positions kept).

**Things to know:**
- **Gaps cannot be refilled.** Neither venue serves old order books, so what the recorder misses is gone. Keep a Mac
  awake (plugged in, sleep off, lid open): a sleeping machine records nothing.
- **Disk:** the recorders pause under 5 GB free. Nothing deletes old tape by itself. `tbot status` and every export
  show the free space.
- **Seed a new server** with the history you already have: `tbot export --full` on the old machine, copy the file,
  `tbot import` on the new one.

### Two machines: one records, one trades

One small server cannot record, rank and trade at once: on 2026-10-09 the two recorders together took about 210 MB,
and a single backtest of the busiest market's day peaked at 664 MB. Split the work instead: put `BOT_ROLE` in each
machine's `.env`, and `tbot up` starts only what that machine is for. All three bots follow it.

| `BOT_ROLE` | Records the tape | Ranks (scans, playbook) | Telegram, runs, guardian | Fits |
|---|---|---|---|---|
| `all` (default) | yes | yes | yes | 4 cores, 8 GB |
| `trader` | no | no | yes | 1 core, 1 GB |
| `recorder` | yes | no | no | 1 core, 1 GB plus disk |
| `scout` | yes | yes | no | 4 cores, 8 GB |

- **The keys live on the trader only.** A recorder or a scout machine needs none: leave its `.env` empty apart from
  `BOT_ROLE`. It refuses to start a run, the autopilot or the arbitrage, and it never starts the Telegram bot
  (Telegram hands each message to one poller, so two machines polling one bot would each see half of them).
- **A trader needs nothing from the other machine to trade.** It reads the market list from the venues itself, so
  `/run`, `/l_run`, the arbitrage, the stops and the guardian all work with the recorder switched off.
- **What it does not have by itself is the lists** (`/top3`, the pilot's picks, both autopilots): they are worked out
  from the tape, where the tape is. They reach the trader in one of two ways, below.

**A trader and a recorder (two small servers).** No connection between them is needed. On each:

```bash
echo "BOT_ROLE=recorder" >> .env
```

(`BOT_ROLE=trader` on the other), then `.venv/bin/tbot up`. For lists, bring the tape to a bigger computer and scan
there: `tbot export` on the recorder, `tbot import` at home ([section 4](#4-export-and-import)), then

```bash
.venv/bin/arcus scout scan
.venv/bin/arcus scout playbook
.venv/bin/tbot sync push USER@TRADER
```

`sync push` uses your own SSH login to the trader and hands it the lists you just made. The trader shows how old they
are; an old Arcus list is a warning, not a refusal, and Lighter refuses a pick from a scan over 90 minutes old.

**A trader and a scout machine.** The trader fetches the lists by itself, about every two minutes. Once:

1. On the trader: `.venv/bin/tbot sync key` makes a key and prints its public half.
2. On the scout machine: `.venv/bin/tbot sync allow 'ssh-ed25519 AAAA…'` (paste that line, in quotes).
3. On the trader: put `BOT_SYNC_FROM=USER@SCOUT-ADDRESS` in `.env`, try `.venv/bin/tbot sync pull`, then
   `tbot down` and `tbot up`. `.venv/bin/tbot sync status` (and `tbot status`) show when it last worked.

**How the two are connected, and what could go wrong.**
- The trader asks; nothing ever logs in to the trader. It opens no port for this.
- The key from step 1 can do one thing on the other machine: run `tbot sync serve`, which sends a fixed list of
  small files (the last scan and its report, the playbook, the markets' usual volatility, the Lighter lists and
  ceilings, the recorders' health). It gets no shell and cannot read any other file, whatever it asks for.
- The trader treats what arrives as untrusted: only those file names, each checked before it replaces the one here.
- If the other machine were broken into, the attacker gets no key and no way into the trader. They could hand over
  wrong lists. A run's sizes and stops are worked out on the trader from its own account and settings, a LIVE run
  still needs you, and the autopilot stays inside its daily budget; treat a list as advice, as before.
- If the other machine goes quiet for 20 minutes, or its recorder stops, the trader says so once in Telegram
  (`⚠️ TWO MACHINES`) and again when it is back. It keeps trading; it starts no autopilot run on lists that stopped
  arriving.
- SSH's first connection trusts the address you typed (`accept-new`); after that a changed machine is refused.

Checked on one computer on 2026-10-10 (each role run from its own folder, the lists handed over through the real
commands); **not yet run between two real servers**. On the first try, watch `tbot sync status`.

**A machine that only records (Docker).** The scout places no orders and needs no keys, so it can run anywhere with
Docker. From `arcus/` (the Arcus scout, container `arcus-scout`) or `lighter/` (container `lighter-scout`):

```bash
docker compose up -d --build
docker compose logs --tail 30 scout
cat data/scout/recorder.json
```

Arcus's `data/scout/report.txt` is the latest ranking; `SCOUT_CAPITAL=500 docker compose up -d` ranks for a $500 account
(the container has no keys, so the default is the $100 paper capital) and `SCOUT_WORKERS=2` caps the scan workers;
`BOT_ROLE=recorder docker compose up -d` records without scanning (a small machine). The
containers restart after a crash or reboot and report unhealthy if the recorder has not written for 15 minutes. **Do not
use Docker on a machine that trades:** a container cannot see a native bot's process, so the pilot could not review,
pause or resume a live run. Run `tbot up` there instead. To bring Docker results home:
`docker compose exec scout tbot export --out data/exports`, then `tbot import` at home.

---

## 4. Export and import

One command on the server packs the recordings, the trades, the state and the logs of all three bots into one file. One
command at home takes it in. Use it to hand the data over for analysis, error hunting or refinement.

**On the server:**

```bash
.venv/bin/tbot export
```

It prints the file, for example `treading-bot/exports/tb-20261006-1612Z.tar` (the date and time are UTC), and the `scp`
line to fetch it. **At home** (put the file in `treading-bot/exports/` or `~/Downloads`):

```bash
scp USER@SERVER:treading-bot/exports/tb-20261006-1612Z.tar ~/Downloads/
```

```bash
.venv/bin/tbot import
```

It must end with `N complete, 0 short`. Then read `arcus/data/server-export/<name>/SUMMARY.md`.

| Command | What it packs |
|---|---|
| `tbot export` | Everything **new since the last export** (the first time: everything) |
| `tbot export --full` | Everything again |
| `tbot export --days 2` | A small one: all state and trades, the logs and tape of the last 2 UTC days |
| `tbot export --since 2026-10-01` | The same, from that UTC day on |
| `tbot export --no-tape` | State, trades and logs only: a quick error report, a few MB |
| `tbot export --tag tokyo` | Adds a word to the name: `tb-tokyo-20261006-1612Z.tar` |
| `tbot export --out /mnt/disk` | Writes it somewhere else |

| Inside the file | What |
|---|---|
| `SUMMARY.md` | Read first: the status screen, the machine, how long a request takes to each venue, fills by day and market, runs and their cost, warnings and crashes in the logs, how complete the recording is |
| `records.tar.gz` | For each bot (`arcus/`, `lighter/`, `arbitrage/`): state (databases, balances, settings), logs, scans, reports, config, fills and orders as CSV; plus `system/` (git commit and local changes, package versions, processes) |
| `tape/` | The market tape of both venues, as recorded |
| `coverage.csv`, `MANIFEST.json`, `files.csv` | Rows and hours per market and day; every tape file's size and SHA-256, written last so a cut-off copy is noticed |

**What is never in it:** `.env`, `config/secrets.enc`, pid files. Every secret value in a `.env` is also blanked out of
every log and database copy. The file does hold your trading history and wallet address, so do not post it.

- **Safe while trading.** It only reads: databases through SQLite's own backup, each tape file through one handle.
- **Disk.** It refuses to take the disk under 6 GB free, and keeps the newest 3 files in `exports/` (`--keep`).
- **Import never touches this machine's own state or logs.** The tape is merged into the tape folders; a file already
  there is kept, unless the incoming one is the same recorder part with more rows.
- **Missed one?** Each plain export holds only what is new. If `tbot import` says the one before it was never
  imported, import that one too, or run `tbot export --full`.

---

## 5. Where everything lives

```
treading-bot/
  README.md  Makefile  .github/workflows/ci.yml
  arcus/             the Arcus bot, and where everything is installed, configured and started
    arcus/           the code: scout/ core/ strategies/ venues/ telegram/ common/, cli.py, ops.py, export.py
    config/          app.yaml (limits and sizing), sessions/, venues/, calendars/ (CPI, FOMC, earnings, holidays)
    deploy/          the server setup script and the region check
    scripts/         one-off tools: register an API key, move collateral, capture frames, latency map
    tests/           offline tests          README.md  RUNBOOK.md  Dockerfile  docker-compose.yml  .env.example
  lighter/           the Lighter bot (package lighter_bot): its engine, config, scripts, tests, README
  arbitrage/         the funding arbitrage (package arbitrage): its code, tests, README
```

Made at run time and never committed:

| Path | What |
|---|---|
| `arcus/.env` | Every credential and live switch |
| `arcus/data/scout/tape/<MARKET>/<UTC day>/` | The Arcus tape: `bbo-`, `trades-`, `depth-*.npz` |
| `arcus/data/scout/` | `report.txt` and `latest.json` (the last scan), `scans/`, `reports/`, `playbook.json`, `recorder.json` (recorder health) |
| `arcus/state/` | `live.sqlite` and `paper.sqlite` (orders, fills, events), `balances.jsonl`, `settings.json`, `pilot.json`, `autopilot.json` |
| `arcus/logs/` | `bot.jsonl` (everything), `decisions.jsonl` (why each order), `runs/` (one file a run), `scout.out`, `telegram.out` |
| `lighter/data/tape/`, `lighter/data/scout/` | The Lighter tape and scans |
| `lighter/state/`, `lighter/logs/` | Lighter runs (`run-<mode>.json`, `fills-<mode>.jsonl`, `status-<mode>.json`), settings, logs |
| `arbitrage/state/`, `arbitrage/data/history/`, `arbitrage/settings.json` | The arbitrage's position and events, the funding history, your settings |
| `arcus/data/server-export/<name>/` | What `tbot import` unpacked |
| `exports/` | Files made by `tbot export` |

Run-time file names such as `bot.jsonl` are unchanged from earlier versions so an existing machine keeps its history.
Anything else you keep at the top level of the repository (research folders, downloaded docs) is ignored by git.

---

## 6. Troubleshooting

| Problem | What to do |
|---|---|
| A list is empty: "still recording" or "1 full day of data (needs 3)" | Each market needs 3 full recorded days. `/run` can still start it |
| "Nothing passes all checks" | Normal in volatile hours. Wait for the next scan |
| `report.txt` is old | Is the scout up? `tbot status`, then `tail logs/scout.out` |
| The lists are old ("from a scan 3.2 h old") | Is the scout up (`tbot status`)? An old Arcus list is a warning and still runs; Lighter refuses a pick from a scan over 90 minutes old |
| "not started: this machine is a recorder" | `BOT_ROLE` in `.env` says this machine does not trade. Start the run on the trader |
| `sync: ssh failed (255)` on a trader | Can this machine reach the other on its SSH port? Was `tbot sync allow` run there with this machine's key (`tbot sync key` prints it again)? |
| `⚠️ TWO MACHINES` in Telegram | The trader cannot fetch the lists, or the other machine's recorder is silent. Look at that machine: `tbot status` there |
| `doctor` says "never funded" | Deposit USDG to the subaccount the key is bound to |
| Repeated `UNDERCOLLATERALIZED` | Not enough margin for the order size. The bot pauses that market by itself |
| A run was paused | `/openpositions` shows why. It resumes after two passing scans |
| The bot stopped after a loss | A kill or safe mode needs you: read `/logs`, then `/resumeaftersl` |
| Lighter: "rate limited" | Lighter answered 429. The bot paused 60 s and requotes less |
| After a reboot nothing runs | `tbot up`. The server setup script adds it to the crontab |
| `tbot import` says SHORT or DAMAGED | Copy the file again; if it repeats, `tbot export --full` on the server |
| `make install` fails building `cryptography` on an Intel Mac | The pin below 49 is in `arcus/pyproject.toml`: pull the latest code and run `make install` again |
| `ws_error KeyError` reconnecting every few seconds | An old copy of the code meeting a market that went OFFLINE. Update the code |
| `ws_degraded` in the log | Arcus marked a stream stale; the bot re-subscribes by itself. Occasional is normal, constant means venue trouble |
| Something else | `tbot export --no-tape`, and read its `SUMMARY.md`, section 4 |

Emergencies (orders must go now, the server is unreachable, a key leaked): [arcus/RUNBOOK.md](arcus/RUNBOOK.md),
section 4.

---

## 7. Development

From the repository root, with `make install` done:

```bash
make test
make lint
make -C arcus type
```

Offline tests for all three bots (no network, no keys), ruff, and strict mypy on Arcus's core. CI
(`.github/workflows/ci.yml`) runs the same, and fails if a credential file or a Telegram token is tracked.

- Strategies return desired orders and never call a venue; the same objects run in the backtest, paper and live.
- After changing the Arcus simulator, bump `SIM_VERSION` in `arcus/arcus/scout/scan.py` so cached days are recomputed.
- No test may start a real `arcus`, `lighter` or `arbitrage` process: `arcus/tests/conftest.py` refuses it.

---

## 8. Upgrading from the old layout

Before this layout the folders were `bot/` and `arb/`, the packages `bot`, `lbot` and `arb`, and the commands `bot …`,
`bot lighter …`, `bot arb …`. `lighter/` kept its name. Data, state, logs, `.env` and settings are **not** tracked, so
they stay where they were and must be moved. On each machine that has the old layout (this assumes the bots are stopped):

```bash
cd treading-bot/bot && .venv/bin/bot down --all   # with the OLD code, before the pull
cd .. && git pull
mv bot/.env bot/data bot/state bot/logs arcus/
mv bot/reports bot/handoffs bot/CONTEXT.md bot/OWNER_ACTIONS.md arcus/ 2>/dev/null
mv bot/config/sessions/pilot.yaml arcus/config/sessions/ 2>/dev/null
mv arb/data arb/state arb/settings.json arb/.env arbitrage/ 2>/dev/null
cd arcus && make install && .venv/bin/tbot up
```

Then delete what is left of `bot/` and `arb/` (an old `.venv` and caches). On a server also run
`bash deploy/scripts/bootstrap.sh` once: it replaces the `@reboot … bot up` crontab line with `… tbot up`. The `.env`
variable names, the log and database file names, and the export format are unchanged. Older exports still import.

---

## 9. Tutorial: the commands, by what you want to do

Read this top to bottom once; after that use it as a menu. Every terminal command runs from `treading-bot/arcus`.
`.venv/bin/arcus`, `.venv/bin/lighter` and `.venv/bin/arbitrage` are the three programs; the same jobs are `/…`,
`/l_…` and `/arb_…` in the one Telegram bot.

**The model in six lines**
1. Each bot has a **recorder** (writes the market tape, no keys) and a **scout** (backtests setups on the tape and ranks
   them into lists). Both are started by `tbot up`.
2. A **run** trades one setup on one market. You start it (Telegram, `pilot approve`, or `run`); the autopilot can too.
3. **Paper** is the default everywhere: real prices, simulated orders. **Live** needs a switch in `.env`
   (`BOT_PILOT_LIVE`, `LBOT_LIVE`, `ARB_LIVE`), a passing `doctor`, and a typed `LIVE` (or a code typed back in Telegram).
4. Stopping and closing are different: **stop** cancels quotes and keeps the position; **close** also closes the position.
5. Settings are changed from Telegram (`/set`, `/l_set`, `/arb_set`) or the terminal, never by editing files.
6. If a machine is not for trading (`BOT_ROLE`), it refuses every run. See [section 3](#two-machines-one-records-one-trades).

### 9.1 First half hour (your laptop or a server)

```bash
git clone https://github.com/dhruvamity/treading-bot.git
cd treading-bot/arcus
make install                 # one venv, all three bots
cp .env.example .env && chmod 600 .env
.venv/bin/arcus doctor       # reads only: says what is missing (keys are not needed for paper)
.venv/bin/tbot up           # both scouts, and Telegram if its token is in .env
.venv/bin/tbot status       # one screen: services, runs, last scan, balance
```

The recorders need about **3 full UTC days** before a market can appear in a list. Until then `/run`, `/l_run` and the
arbitrage work at once, because they never wait for a scan. To stop everything: `.venv/bin/tbot down` (add `--all` to
stop the runs too; positions are kept).

### 9.2 Telegram, once

1. Message **@BotFather**, `/newbot`, copy the token into `TELEGRAM_BOT_TOKEN` in `.env`.
2. Send your new bot any message, open `https://api.telegram.org/bot<TOKEN>/getUpdates`, copy `chat.id` into
   `TELEGRAM_CHAT_ID`.
3. `.venv/bin/tbot down && .venv/bin/tbot up`, send `/whoami`, put your id in `TELEGRAM_ALLOWED_USER_IDS`, then
   `.venv/bin/tbot down && .venv/bin/tbot up` again.

Then `/menu`, `/status`, `/l_status`, `/arb_status`. `/set telegram_ui menu` swaps the long command list for a Home card.

### 9.3 Arcus

| I want to | Terminal | Telegram |
|---|---|---|
| See the lists | `cat data/scout/report.txt` · `.venv/bin/arcus pilot status` | `/top3` (most volume), `/cheapest`, `/maxvolume` |
| What to run, as lines to paste | `.venv/bin/arcus recommend SPY` (add `--scan` after an import; [10.6](#106-recorder-vps--your-mac--trader-vps-the-workflow)) | |
| Run the #1 setup on paper | `.venv/bin/arcus pilot approve 1` (`--list cheapest` or `--list max` for the other lists) | ▶️ on a list |
| Run my own pick on paper | `.venv/bin/arcus run SESSION` (a file in `config/sessions/`) | `/run SPY smart 0 50x paper` |
| Watch it | `.venv/bin/arcus dashboard` | `/dashboard`, `/status`, `/positions`, `/orders` |
| Stop, keep the position | `.venv/bin/tbot down --all` | `/stop` |
| Close the position and stop | `.venv/bin/arcus pilot close` | `/closeall` (typed code) |
| Cancel every order now | `.venv/bin/arcus cancel-all --venue arcus` | `/cancelall` |
| Close every position now | `.venv/bin/arcus flatten --venue arcus` (add `--taker` for IOC) | `/closeall taker` |
| Yesterday's result | `.venv/bin/arcus report --date 2026-10-09` | `/yesterdayreport` |
| Trade again after a safety stop | `.venv/bin/arcus resume --venue arcus` | `/resumeaftersl` |

A run's limits go on the same line: `/run BTC mid 0 40x live sl=10 tp=5 vol=100k` means it may lose $10, stops once up $5
or once it traded $100,000. Setups are `mid`, `smart` or `grid`, a spread in bp, and optionally `long` or `short`.

**Going live on Arcus, in this order** (real money; the Arcus README, section 2, has the reasons):
```bash
.venv/bin/arcus selftest                 # while the account is empty: every line PASS or INFO
# deposit USDG into the subaccount your API key is bound to (use one the bot has to itself)
echo 'BOT_PILOT_LIVE=1' >> .env
.venv/bin/tbot down && .venv/bin/tbot up
.venv/bin/arcus doctor pilot             # must end in READY
.venv/bin/arcus pilot approve 1 --live   # prints the doctor's lines, then you type LIVE
```
From the phone: `/run SPY smart 0 50x live sl=10`, then type back the 6-digit code within 2 minutes. Keep `sl=` small
for the first run. To turn live off again, change the line to `BOT_PILOT_LIVE=0` (the last line of a name wins).

### 9.4 Lighter

Same ideas, a different venue (0% fees, so the bot's cost is the spread). Paper needs no keys.

| I want to | Terminal | Telegram |
|---|---|---|
| Markets and their minimum order | `.venv/bin/lighter markets` | |
| Lists | `cat ../lighter/data/scout/report.txt` · `.venv/bin/lighter status` | `/l_top3`, `/l_cheapest`, `/l_maxvolume` |
| Paper run, my pick | `.venv/bin/lighter run SPY "smart +1"` (Ctrl-C stops; `--bg` for the background) | `/l_run SPY smart +1 50x paper sl=10` |
| Paper run, list #1 | `.venv/bin/lighter pilot approve 1` | ▶️ on a list |
| Pause or resume new orders | `.venv/bin/lighter pause` · `unpause` | `/l_pause` · `/l_unpause` |
| Stop (keep position) / close | `.venv/bin/lighter stop` · `.venv/bin/lighter close` | `/l_stop` · `/l_closeall` |
| Autopilot | `.venv/bin/lighter auto on --budget 5` (paper unless `--live`) | `/l_auto` |
| Test one backtest by hand | `.venv/bin/lighter backtest SPY "smart +1" --capital 100` | |

**Going live on Lighter.** A Lighter key is registered on **your own computer** (it asks for your wallet's private key once and
never stores it), never on a server:
```bash
.venv/bin/python ../lighter/scripts/register_key.py --slot 4    # prints three lines for .env
# put them in .env, deposit USDG on Robinhood Chain in the Lighter app, then:
echo 'LBOT_LIVE=1' >> .env
.venv/bin/lighter doctor SPY             # READY, and "account tier" must say standard (0% fees)
.venv/bin/lighter livetest               # real money, minimum size, ~cents: every request the bot sends, once; type LIVE
```
Read the `PASS`/`FAIL` report it prints (also in `lighter/reports/`). When it is clean, the smallest real run:
```bash
.venv/bin/lighter run SPY "smart 0" --lev 2 --capital 16 --live --sl 1 --seconds 600 --flat
```
That is $16 at 2x: orders of about $13, at most $26 held, ten minutes, closed at the end. Afterwards read
`lighter/logs/run-live-<day>.jsonl` and `lighter/state/fills-live.jsonl`. `.venv/bin/lighter livetest --only limit,cancel-all`
runs just the steps you name (`--help` lists them). `.venv/bin/lighter leverage SPY` shows the account's leverage on a market and
`leverage SPY 2` sets it (needs `LBOT_LIVE=1`). If a `lighter/.env` exists it wins over `arcus/.env`.

### 9.5 Arbitrage (funding between Arcus and Lighter)

It holds one position: short where funding pays more, long on the other venue, the same size. It needs money on **both** venues,
and none of the recorders. It trades stocks, indices and commodities only, never crypto (`arbitrage set rwa_only 0` lifts that).
Both venues pay funding every hour and a position needs about two days to pay for its fills, so it is held for days, until the
difference is gone (`arbitrage study` shows the numbers; arbitrage README, section 2).
Since 10 Oct 2026 it uses the highest leverage both venues allow at that hour unless you set less (`arbitrage lev 10`,
Telegram `/arb_lev 10`): SPY is 50x while the stock market is open and 33x while it is closed, the same number on both venues.
For anything but SPY and QQQ set `stop_sigmas 3` first (arbitrage README, section 2).

**The owner's mode: SPY, highest leverage, renewed every three funding payments, side from ProFunding.** `lev max max SPY` and
`cycle 3` below (Telegram `/arb_lev max max SPY`, `/arb_cycle 3`). The line is leverage, margin a venue, market; with no market
(`/arb_lev max max`) ProFunding's best stock, index or commodity is opened instead. It buys volume and open interest and
costs money: at $240 about $141,000 of volume a day for about $4.90 a day, or $90,000 for $3.10 with `/arb_lev 30 max SPY` (arbitrage
README, sections 2a and 3a). Near its stop (from 80% of the way, `stop_early`) it closes with limit orders, which pay no fee; at
the stop itself it uses taker orders.
```bash
.venv/bin/arbitrage scan                          # ranks the markets with your accounts' free collateral
.venv/bin/arbitrage plan SPY --arcus 120 --lighter 120
.venv/bin/arbitrage history --update              # the funding history (the first time without --update: about an hour)
.venv/bin/arbitrage backtest --capital 240        # the rules replayed on it
.venv/bin/arbitrage history --profunding          # ProFunding's last 30 days of the same rates, as a check
.venv/bin/arbitrage study --capital 240           # the best market of each hour: how long to hold, how much leverage
.venv/bin/arbitrage fills SPY QQQ                 # what its orders cost on recorded order books
.venv/bin/arbitrage cyclecost SPY QQQ                 # the highest leverage, closed and reopened on a clock: what that costs
.venv/bin/arbitrage basis SPY QQQ                 # Arcus's price against Lighter's, by the minute and over each weekend
.venv/bin/arbitrage lev max max SPY              # leverage, margin a venue, market in one line; no market = ProFunding's best
.venv/bin/arbitrage only SPY QQQ                  # the markets it may open and no others (`only all` lifts it)
.venv/bin/arbitrage cycle 3                       # close and reopen every 3 funding payments (hours); `cycle off`
.venv/bin/arbitrage side profunding               # ProFunding decides which venue is short (`side venues` = their own rates)
.venv/bin/arbitrage start --arcus 120 --lighter 120   # PAPER, in the background, pretend $120 per venue
.venv/bin/arbitrage status                        # what it does, funding paid, last events
.venv/bin/arbitrage set max_hold_h 72             # any setting, applied to the open position within ~10 s
.venv/bin/arbitrage skip CASHCAT                  # a market it must never open
.venv/bin/arbitrage close                         # maker first; `close --now` crosses at once
.venv/bin/arbitrage stop                          # stops the program; the position and the venues' stop orders stay
```
Run paper for several days first. **Live**, in this order:
```bash
echo 'ARB_LIVE=1' >> .env
.venv/bin/tbot down && .venv/bin/tbot up        # so Telegram sees the switch
.venv/bin/arbitrage livetest SPY                  # real money, smallest size, cents; type LIVE once
```
`livetest` runs three parts in turn and writes a report for each to `arbitrage/reports/`; a part runs only if the one before
passed and ended flat:
1. **lighter**: the Lighter adapter alone (proven on 2026-10-10: 9 of 9).
2. **arcus**: the Arcus adapter alone: a resting post-only order and its replacement, an IOC order that opens a position, the
   position stop and take-profit pair, cancel-all, the reduce-only close. This is the part never run on the venue before.
3. **engine**: the executor itself on both venues: it opens a position (long Arcus, short Lighter), checks with each venue what it
   holds, waits for the stops on both, holds 30 s, closes it with maker orders; then the same the other way round, closed with taker
   orders at once (`close --now`).

Both accounts must have no order and no position on that market and a few dollars of margin; Arcus's stock perps trade in the
US session, so run it then (`arbitrage livetest ETH` if you want a 24 h market). One part alone: `--what arcus`, `--what engine`.

Only then raise the size, one step at a time, and stay at each step until a full cycle (open, hold, a funding payment, close) has
run and the money matches `/arb_status`:
```bash
.venv/bin/arbitrage set max_notional_usd 30       # a first real position
.venv/bin/arbitrage start --live                  # type LIVE.  Phone: /arb_start live, then the code
.venv/bin/arbitrage status --live
# then 100, then 300, then 1000 ...; 0 means no limit, the last step
```
**What no test here proves:** that a stop order actually fires and closes the other leg on the real venues; weeks of holding
(funding credited, margin moving, the venues' rules changing); a venue going down while a position is open; fills at a size larger
than the order book shows; and the money you must move by hand between the venues. Each size step is how you meet those.
Moving money between the venues is yours: when a position closes and one side holds under 40% of the total (the two more than 20%
apart), the bot opens nothing, sends the exact amount and direction to Telegram (`MOVE $50.00 from lighter to arcus`), repeats it
every 30 minutes and goes on by itself once the money has arrived. The venues cannot send to each other (arbitrage README, 6a). Use an account of its own
on each venue (or stop the market-making bots), because two programs on one account each treat the other's position as theirs.

### 9.6 Autopilots

Off by default. Each spends a daily loss budget only where the backtests say volume is cheapest now, and turns itself off when you
take over with your own `/run`, `/stop` or `/closeall`.
```bash
.venv/bin/arcus auto on --budget 5          # paper; add --live for real money (needs BOT_PILOT_LIVE=1, then a typed confirmation)
.venv/bin/arcus auto status
.venv/bin/arcus auto off
.venv/bin/lighter auto on --budget 5        # the same for Lighter
```
On a trader machine the Arcus autopilot also needs the playbook from a machine with the tape ([section 10.6](#106-recorder-vps--your-mac--trader-vps-the-workflow)).

### 9.7 Settings

| What | Terminal | Telegram |
|---|---|---|
| Arcus (capital, stops, scan timing, list cost) | edit nothing; use Telegram | `/settings`, `/set daily_stop 5`, `/set NAME default` |
| Lighter | `.venv/bin/lighter set` · `lighter set position_stop 2` | `/l_settings`, `/l_set NAME VALUE` |
| Arbitrage | `.venv/bin/arbitrage settings` · `arbitrage set NAME VALUE` | `/arb_settings`, `/arb_set NAME VALUE`, `/arb_hold 72` |
| Live switches and keys | `.env` only (then `tbot down && tbot up`) | never from Telegram |

### 9.8 Routine

**Daily (2 minutes):** `.venv/bin/tbot status` (or `/status`, `/l_status`, `/arb_status`), then `df -h` on a server.
**Weekly:** `.venv/bin/tbot export`, copy the file off the server (`scp`), `.venv/bin/tbot import` at home, read its `SUMMARY.md`.
**After an update:** `git pull && make install && .venv/bin/tbot down && .venv/bin/tbot up`.
**Something looks wrong:** `.venv/bin/tbot down --all`, then `cancel-all` and `flatten` (section 9.3), `/closeall`. The
[runbook](arcus/RUNBOOK.md) has the full emergency table.

---

## 10. Two small servers: Azure free tier and the arbitrage alone

### 10.1 What the free tier gives, and three things to check first

Azure's free account has offered, for the first 12 months, **750 hours a month of a `Standard_B1s` Linux VM (1 vCPU, 1 GiB)**
and two 64 GiB disks. These terms change: read your account's "free services" page before you rely on them. Three consequences:

1. **The 750 hours are shared by all your B1s VMs.** One VM running all month is about 744 hours, so it fits. A second VM
   running all month does not: from the 31st day you pay for the second (a few dollars a month). One free server, not two.
2. **Pick a region where Arcus is allowed.** Arcus is not available in the U.S., Canada, the United Kingdom and other restricted
   places, and it checks the IP of the machine that trades. Choose an Azure region outside them, then run
   `.venv/bin/arcus region-check` on the VM before anything else: it must say `OK for Arcus perps`. Lighter has no such check
   here: read its terms for your location yourself. If B1s is "not available" in a region, try another allowed one.
3. **A B1s earns CPU credits slowly (about 10% of the CPU).** Installing, `arbitrage history` and a backtest are bursts; the
   executor itself mostly waits. Do not run scans or backtests on it.

### 10.2 Can the arbitrage run alone on it? Yes, with these limits

- **Paper: yes.** **Live: yes**, because the executor is one light Python process that checks the two venues every 10 to
  15 seconds while it waits (every second while entering or leaving a position) and needs no recorder, no scan and no lists. Run
  `arbitrage livetest` first (section 9.5): it tests each venue's side and then the engine on both, at the smallest size, and then
  raise `max_notional_usd` step by step. Until you have run it, only the Lighter leg has traded real money.
- **Its protection lives on the venues, not on the VM.** Both legs carry stop and take-profit orders placed on Arcus and Lighter, so
  a dead or rebooted VM does not leave a naked position. While the program is down nothing watches the exit rules (hold time, funding),
  so restart it after a reboot (10.4).
- **Set `BOT_ROLE=trader`.** The default `all` would also start the recorders and scans, which a 1 GB machine cannot hold (one
  backtest of the busiest market's day peaked at 664 MB). A trader starts none of that.
- **Memory.** Not measured on a B1s. Importing the programs takes about 40 MB (arbitrage), 48 MB (Arcus), 27 MB (Lighter) and 57 MB
  (the Telegram bot) on a Mac, and a running process grows beyond that. Four processes plus Ubuntu should fit in 1 GiB, with little
  to spare, so **add a swap file first** (below) and measure with `free -m` and `ps -eo rss,cmd --sort=-rss | head` after a day.

### 10.3 Set up the VM

In the Azure portal: **Create a virtual machine** → image **Ubuntu Server 24.04 LTS**, size **Standard_B1s**, authentication **SSH
public key**, an allowed region, OS disk 64 GiB (it is free), inbound port **22 only**. After it exists, in its Networking page edit the
SSH rule to allow only your own IP address. Then:

```bash
ssh azureuser@VM-ADDRESS
```
```bash
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
free -m                                   # Swap: 2047 shows it worked
```
```bash
git clone https://github.com/dhruvamity/treading-bot.git
cd treading-bot/arcus
bash deploy/scripts/bootstrap.sh          # packages, clock, uv, make install, firewall (SSH only), @reboot tbot up
```
The script has been syntax-checked but not yet run on a real server: read its five steps as they print.

### 10.4 The arbitrage alone, step by step

```bash
cd ~/treading-bot/arcus
nano .env
```
Fill in only what the arbitrage uses, and keep the file private (`chmod 600 .env`, done by the script):

| Line | Value |
|---|---|
| `BOT_ROLE` | `trader` |
| `ARCUS_ADDRESS`, `ARCUS_API_PRIVATE_KEY`, `ARCUS_ACCOUNT_INDEX` | your Arcus API key and the subaccount it trades |
| `LIGHTER_ADDRESS`, `LIGHTER_API_PRIVATE_KEY`, `LIGHTER_API_KEY_INDEX`, `LIGHTER_ACCOUNT_INDEX` | your Lighter key (registered on your own computer, 9.4) |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_ALLOWED_USER_IDS` | for alerts and control from the phone |
| `ARB_LIVE` | leave empty for paper; `1` only when you go live |

For paper you need no keys at all (not even Telegram). Check, then start:
```bash
.venv/bin/arcus region-check              # OK for Arcus perps
.venv/bin/arcus doctor                    # reads only
.venv/bin/arbitrage scan                  # does the VM reach both venues?
.venv/bin/arbitrage start --arcus 120 --lighter 120    # paper
.venv/bin/arbitrage status
```
Choose one of two ways to run it:
- **With Telegram** (control and alerts from the phone): `.venv/bin/tbot up telegram`. That starts the Telegram bot alone, with no
  scouts; the executor itself is started with `arbitrage start` or `/arb_start`.
- **Without Telegram**: skip `tbot up`. The executor posts its own alerts if the token is in `.env`; control it by `ssh` with
  `arbitrage status`, `close`, `stop`.

Live is the same as section 9.5 (`ARB_LIVE=1`, `arbitrage set max_notional_usd 30`, `arbitrage start --live`, type `LIVE`; or
`/arb_start live` and the code). The cron line the script installed restarts `tbot up` after a reboot but **not the executor**. After a
reboot, start it again (`/arb_start live` and the code, or `arbitrage start --live`): it picks up the position from its `state/` files.
If you want it back by itself, add this line with `crontab -e`, knowing that `--yes` skips the typed `LIVE` and that a flat executor
may open a new position on its own:

```
@reboot sleep 60 && cd /home/azureuser/treading-bot/arcus && .venv/bin/arbitrage start --live --yes >> logs/boot.out 2>&1
```

### 10.5 What each machine role needs

| | Recorder | Trader | Scout | All |
|---|---|---|---|---|
| `BOT_ROLE` | `recorder` | `trader` | `scout` | empty |
| Keys in `.env` | none | all of yours | none | all of yours |
| Records the tape | yes | no | yes | yes |
| Scans and ranks | no | no | yes | yes |
| Telegram, runs, arbitrage | no | yes | no | yes |
| Fits | 1 vCPU, 1 GiB, big disk | 1 vCPU, 1 GiB | 4 cores, 8 GiB | 4 cores, 8 GiB |

### 10.6 Recorder VPS → your Mac → trader VPS (the workflow)

Three machines, one job each. The servers stay small because nothing heavy runs on them.

| Machine | `BOT_ROLE` | Keys | Job |
|---|---|---|---|
| Recorder VPS (24/7) | `recorder` | none | Records both venues. Nothing else |
| Your Mac | none (no `tbot up`) | none | Takes the recording in, runs the backtests, tells you what to run |
| Trader VPS | `trader` | all of yours | Telegram, your runs, the arbitrage |

**What the recorder keeps.** Not candles: finer data from which any candle can be made. Arcus: best bid and offer, every trade, the top
10 book levels. Lighter: the same with the top 20 levels, plus mark, index and funding. About 0.3–0.5 GB a day for both.

**1. Once: the recorder.** After the clone and bootstrap of 10.3:
```bash
cd ~/treading-bot/arcus
echo 'BOT_ROLE=recorder' >> .env
.venv/bin/tbot up
.venv/bin/tbot status                     # THIS MACHINE: recorder; rows climbing
```
**2. Once: your Mac.** The normal install (section 2): `make install`. You need no keys and you do not run `tbot up` there.

**3. Every few days (or whenever you want fresh advice).** On the recorder:
```bash
.venv/bin/tbot export                     # prints the file name and the scp line; only what is new since the last export
```
On your Mac, from `treading-bot/arcus`:
```bash
scp azureuser@RECORDER:treading-bot/exports/tb-XXXX.tar ~/Downloads/
.venv/bin/tbot import                     # must end "N complete, 0 short"
.venv/bin/arcus recommend --scan           # scans both bots, then prints the best setups of each list
```
`recommend` prints, for each list (🚀 Most Volume, 💎 Cheapest, 🔥 Max Volume) and each bot, the best setups with the line to paste:
```
 LIGHTER  (scan as of 2026-10-10 07:44 UTC, at $100 of capital)
  1. SPY · Smart +0.5 · 50x   $3,714,790/day, cost $0.00 per $1,000, 4,615 fills/day
       paper:  /l_run SPY smart +0.5 50x paper
       live:   /l_run SPY smart +0.5 50x live sl=5
```
For one asset, name it: `.venv/bin/arcus recommend SPY QQQ` (add `--scan` after a new import; without it, it reprints the last scan).
One list: `--list cheapest` (or `volume`, `max`). More per list: `-n 5`. Rank for the money the trader account will hold:
`--scan --capital 500`.

**4. On the phone.** Paste the line into the trader's Telegram chat: first the `paper` line, and when you like it the `live` line (it
asks for the 6-digit code). `sl=` is the most that run may lose in dollars; the line suggests the daily stop at the scan's capital.
Add `tp=5` (stop once up $5) or `vol=100k` (stop after that volume) if you want them.

**What to know when reading it.**
- The scan is made **as of the end of the recording** (`--as-of tape`): "is the market trending or wild right now" means then, not
  now. Export fresh before you act; an export a day old is fine for the long-run numbers and stale for the "now" checks.
- The numbers are backtests on recorded books, and Arcus's matched its largest live run within 11% on volume and 3% on cost. Lighter's
  rest on fewer days. Not promises.
- The first scan on a big import takes the longest; completed days are cached and later scans only redo the newest day.
- The tape grows on the Mac by the same 10–15 GB a month: keep the room, and delete old tape on the servers only after an import ended
  `0 short`.
- `recommend` only reads. It starts nothing and uses no key.
- The lists on the trader itself (`/top3`) stay empty in this workflow: it never scans. You get the advice from your Mac, as lines to paste.

### 10.7 Linking a recorder and a trader (optional)

This is the optional link between the two servers, for health alerts (or for lists from a `scout` machine). Two 1 GiB servers: the recorder keeps the tape (about 0.3–0.5 GB a day for both venues; the two recorders took about 210 MB of memory on
2026-10-09) and the trader trades. **Read this first:** a recorder does not rank, so the link between them carries only its health,
not lists. Lists come from a machine that scans, and a 1 GiB server cannot scan. You have three choices:

| Lists for the trader | How | Cost |
|---|---|---|
| None | Run your own picks: `/run`, `/l_run`, the arbitrage. They never need a list | nothing |
| From your computer | Export from the recorder, import, scan and push at home (below) | a few commands every day or two |
| From a `scout` machine | A 4 core, 8 GiB server that records and scans; the trader fetches by itself ([section 3](#two-machines-one-records-one-trades)) | a bigger server |

With the Azure free tier only one of the two is free (10.1), so expect to pay for one of them.

**1. Put both in the same region and virtual network** (the portal's default network does this). Then they can talk on private addresses
and the recorder never needs a public SSH rule for the trader. Note the recorder's private address (the VM's Networking page, for
example `10.0.0.4`).

**2. The recorder** (no keys). After the clone and the bootstrap of 10.3:
```bash
cd ~/treading-bot/arcus
echo 'BOT_ROLE=recorder' >> .env
.venv/bin/tbot up                         # both recorders, no scans, no Telegram
.venv/bin/tbot status                     # "THIS MACHINE: recorder", rows climbing
cat data/scout/recorder.json               # last_msg_age_s a few seconds, paused_for_disk false
df -h /                                    # it pauses itself under 5 GB free
```

**3. The trader.** After the same clone and bootstrap:
```bash
cd ~/treading-bot/arcus
nano .env                                  # your keys, Telegram, and the next line
echo 'BOT_ROLE=trader' >> .env
.venv/bin/tbot up
```

**4. Connect them** (the trader asks; nothing ever logs in to the trader). On the **trader**:
```bash
.venv/bin/tbot sync key                   # makes a key and prints its public line (ssh-ed25519 AAAA…)
```
On the **recorder**, paste that whole line in quotes:
```bash
.venv/bin/tbot sync allow 'ssh-ed25519 AAAA… treading-bot-sync'
```
That key can run one thing on the recorder, `tbot sync serve`, with no shell. Back on the **trader**:
```bash
echo 'BOT_SYNC_FROM=azureuser@10.0.0.4' >> .env      # the recorder's user and private address
.venv/bin/tbot sync pull                  # try it once
.venv/bin/tbot sync status                # when it last worked
.venv/bin/tbot down && .venv/bin/tbot up # from now on it fetches by itself every ~2 minutes
```
What this gives you with a recorder at the other end: the trader sees the recorder's health and sends `⚠️ TWO MACHINES` to Telegram if the
recorder goes silent for 20 minutes. If the SSH rule blocks the trader, allow port 22 from the trader's address in the recorder's
network rules. The first connection trusts the address you typed; after that a changed machine is refused.

**5. Get lists from your computer** (only if you want them). On the recorder, once a day or two:
```bash
.venv/bin/tbot export                     # prints the file and the scp line
```
At home:
```bash
scp azureuser@RECORDER:treading-bot/exports/tb-XXXX.tar ~/Downloads/
cd treading-bot/arcus
.venv/bin/tbot import                     # must end "N complete, 0 short"
.venv/bin/arcus scout scan
.venv/bin/arcus scout playbook
.venv/bin/tbot sync push azureuser@TRADER-ADDRESS
```
`sync push` uses your own SSH login to the trader and hands it the lists. The trader shows how old they are; an old Arcus list is a
warning, a Lighter list over 90 minutes old is refused. The tape also stays on the recorder: export weekly and copy the files home, because
nothing deletes old days and the disk fills (recording pauses under 5 GB free).

### 10.8 What has and has not been checked

Checked on one computer (each role run from its own folder, lists handed over through the real commands), **not on two real servers**,
and `bootstrap.sh` has not run on a real server. On the first day watch: `tbot status` on both, `tbot sync status` on the trader,
`free -m` and `df -h` on both, and a `⚠️ TWO MACHINES` message in Telegram. If something is off, run `tbot export --no-tape` on either
machine and read its `SUMMARY.md`.
