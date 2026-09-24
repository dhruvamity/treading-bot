# Runbook

Operations for the VPS deployment (`/opt/bot`, systemd). Commands assume `cd /opt/bot` and
`A=.venv/bin/bot`. Commands that touch mainnet ask you to type a confirmation.

## 1. Processes

| Unit | What it does | Needs keys |
|---|---|---|
| `bot-recorder` | Records public books, trades, prices and funding to `data/` as Parquet. Runs 24/7 from day one. | no |
| `bot-maintenance.timer` | Daily: `compact` closed days, then `dq-report` for yesterday. | no |
| `bot` | `bot run $BOT_RUN_ARGS` (e.g. `arcus_btc_mm --live --yes`): engines, DMS refresher, reconciliation, heartbeat. | live |
| `bot-guardian` | Separate process watching the LIVE heartbeat (`state/heartbeat.live`). If it is silent for 60 s, or the drawdown limit is hit, it cancels all orders and alerts. | yes (read + cancel) |
| `bot-telegram` | Telegram control bot (§9): status, pause/stop/run, cancel-all/flatten and live alerts on your phone. | yes, for cancel-all / flatten / doctor |

```bash
sudo systemctl status bot-recorder bot bot-guardian
journalctl -u bot -f -o cat | jq -c 'select(.level!="DEBUG")'     # JSON logs; secrets are redacted
```

## 2. Daily checks (2 minutes)

1. `$A status` shows, per mode, whether the bot is running, open orders, positions, and any pending resume flag.
   `$A doctor arcus_btc_mm` should still say READY (key expiry, funds, clock).
2. `$A report --date $(date -u -d yesterday +%F)` (live by default; `--mode paper`) shows the PnL split:
   `Net = SpreadCapture + InventoryMTM + Funding − Fees − HedgeCost − LiquidationLoss`.
   It also shows volume, OI-hours and budget.
3. `reports/dq/<yesterday>.md` should show 0 issues. Silence over 60 s, sequence gaps or missing funding hours mean
   the recorder needs a look.
4. `$A keys` should show every Arcus key ACTIVE with more than 7 days left.
5. `df -h /opt` should show more than 30% free. Raw recording is about 1.5 GB/day; compaction shrinks closed days.

## 3. Start, stop, change a session

```bash
sudo systemctl stop bot           # pulls every quote on the way out (cancel-all); positions are KEPT
sudoedit /etc/bot.env                 # BOT_RUN_ARGS="arcus_btc_mm"                (paper)
                                           # BOT_RUN_ARGS="arcus_btc_mm --live --yes"   (live, unattended)
sudo systemctl start bot
```

An unattended live start (`--live --yes`) needs `live_enabled: true` in the session file. It refuses to start if
`bot doctor` reports any FAIL, and the reason is in `journalctl -u bot`. From a terminal, leave out
`--yes`: you get the doctor report and type `LIVE`. To go back to paper, remove `--live` and restart. Credentials
live in `/opt/bot/.env`. `/etc/bot.env` only carries the run arguments.

## 4. Emergency procedures

| Situation | Do this |
|---|---|
| Anything looks wrong | `sudo systemctl stop bot`. The guardian stays up. |
| Orders must go NOW | `$A cancel-all --venue arcus` (your key's subaccount, mainnet; `--yes` skips the prompt) |
| Close positions | `$A flatten --venue arcus` (maker, reduce-only). Add `--taker` for IOC. |
| VPS unreachable | The Arcus DMS (`scheduleCancel`) and the Lighter scheduled cancel-all fire by themselves within their deadlines (Arcus 60 s). Then use the venues' web apps: cancel all, then close positions. |
| Suspected key leak | Revoke the key in the Arcus web app (API Keys), create a new one, and replace `ARCUS_API_PRIVATE_KEY` in `.env`. For Lighter, re-run `scripts/lighter_register_key.py` (same slot). |

## 5. Safe mode and kill switches

The bot never resumes after a serious stop by itself. Investigate first, then run
`$A resume --venue <v>` or `$A resume --all`. It writes a flag that the running bot picks up on its next tick.

| Trigger | Automatic action | Resume |
|---|---|---|
| Session loss ≥ SL% of margin (10%) | Cancel quotes; flatten maker-first, then IOC | next scheduled session |
| Daily loss > 3% of capital | That venue stops for the UTC day | next day or manual |
| Drawdown > 10% of capital (8% for DN) | Everything stops and flattens; CRIT alert | manual only |
| Liquidation distance < 4σ (1 h) | Halve the position | back above 6σ |
| Safety pause (6σ 1-s move, spread > 3× median, depth < 30%) | Cancel quotes, keep position | after 30 s normal |
| Event window (CPI, FOMC, NFP ±30 min; earnings ±24 h; ex-div) | No new quotes | window end |
| Arcus band in expansion zone, or OI cap reached | Stop quoting that market | cleared |
| Hedge leg missing > 5 s (DN) | Cancel maker quotes; taker-flatten the unhedged leg | both venues healthy 5 min |
| Heartbeat silent 60 s | DMS plus guardian cancel-all | manual after reconcile |
| Arcus pool < 5%, or Lighter 429/405 | Cancels only, no requotes | budget recovered |
| DMS refresh fails twice | Safe mode | manual |
| `SELF_TRADE` / `GEO_RESTRICTED` reject | Stop venue; CRIT alert | manual |
| Unexpected exception in trading loop | Safe mode (cancel quotes, keep positions) | manual |
| Same non-routine rejection 5 times in 60 s (e.g. UNDERCOLLATERALIZED) | Pause that market's quotes | after 60 s, doubling to 10 min if it repeats |

Every automatic action goes to the decision log (JSON logs, `component=decision`) with its reason.

## 6. Routine maintenance

- **Arcus key rotation** (max 180 days; alert 72 h ahead, `doctor` refuses to start within 24 h). Create a new key
  for the same subaccount in the Arcus web app (or `scripts/arcus_register_key.py --env mainnet --account N`,
  which writes `.env`), replace `ARCUS_API_PRIVATE_KEY` in `.env` on the server, and restart. `$A keys` confirms
  the new key is ACTIVE.
- **Calendars:** keep `config/calendars/events.csv` at least 30 days ahead for FOMC and 14 days for CPI. The bot
  warns hourly while coverage is short. Add NVDA/TSLA earnings and SPY/QQQ ex-dividend dates.
- **Venue changes:** LiveParams refreshes hourly. Any change to tick, step, minimum, fees or margins is logged to
  `data/param_changes_jsonl/`, and quoting uses the new values immediately. Read the Arcus changelog and the Lighter
  apidocs monthly (prompt pack maintenance prompt).
- **Weekly points:** `$A points add --venue lighter_rh --week <YYYY-WW> --points <n>`.
- **Backups:** `.env` (kept offline, never in git) and `state/live.sqlite`. The Parquet data
  can be rebuilt only by re-recording, so back up `data/` if disk allows.

## 7. After a crash or reboot

1. systemd restarts the bot. On start it reconciles:
   - venue orders unknown to local state are cancelled (`reconcile_unknown`);
   - positions are taken from the venue.
2. Check `$A status` and the `reconcile` alerts.
3. If the bot died without a clean stop, the DMS has already cancelled Arcus quotes. Lighter's scheduled cancel-all
   covers Lighter.

## 8. Incidents

Write each incident to `docs/incidents/<date>-<slug>.md` with:

- impact ($, positions, duration);
- timeline;
- root cause;
- fix and prevention;
- whether a kill switch should have fired earlier.

## 9. Telegram control bot

`bot telegram` (systemd: `bot-telegram`) runs next to the bot. It reads the bot's state database and
heartbeat, writes flags the bot applies on its next tick, and uses the same code as the CLI for venue actions. It
holds no trading state, so restarting it never touches the bot.

**Set up (once)**

1. In Telegram, talk to @BotFather: `/newbot`, copy the token.
2. Put `TELEGRAM_BOT_TOKEN=` and `TELEGRAM_CHAT_ID=` in `.env` (or `/etc/bot.env`). Your chat id: start the bot
   and send it `/whoami`; it answers anyone with their own ids and nothing else.
3. Optional: `TELEGRAM_ALLOWED_USER_IDS=<your user id>` (required reading if the chat is a group: without it anyone in
   the group chat can send commands). `BOT_PILOT_LIVE=1` to allow live deployments from the Run buttons.
4. `$A telegram` (or `sudo systemctl enable --now bot-telegram`). It posts "control bot online" with the status.

**Commands** (`/menu` shows buttons; commands act on the running bot, live first; add `paper`/`testnet`/`live`)

| Command | What happens |
|---|---|
| `/scout` | The 3 best setups right now (backtested), with Run buttons: Paper = confirm button, LIVE = `BOT_PILOT_LIVE=1`, doctor, typed code |
| `/pilot` | What is deployed, today's PnL vs the backtest, the last check; Close & stop |
| `/status`, `/pnl`, `/positions`, `/orders` | What is running, today's PnL, fills and maker volume; PnL by market since start |
| `/sessions`, `/logs [n]`, `/report [date]` | Session files; latest decisions; the daily report |
| `/pause [MARKET]` | Quoting stops within a second; reduce-only exit orders keep working off any position. Persists across restarts. |
| `/unpause [MARKET]` | Quoting again |
| `/stop` | Confirm button, then a clean shutdown: quotes cancelled, positions kept. SIGINT fallback after 25 s. |
| `/resume` | Confirm button: clears safe mode / drawdown stop (same as `bot resume`). Look at `/logs` first. |
| `/run NAME` / `/run NAME live` | Paper: confirm button. Live: the session needs `live_enabled: true`, `doctor` must pass, then you type a one-time code. |
| `/doctor NAME` | Live readiness check (reads only) |
| `/cancelall [venue]` | Confirm button: cancels every open order on the account (live/testnet only) |
| `/flatten [venue] [taker]` | One-time code: cancels everything, then closes every position reduce-only (maker, or IOC with `taker`) |
| `/alerts`, `/mute [min]`, `/unmute` | Alert settings |

**Alerts it sends by itself** (critical ones ignore `/mute`)

- the bot is down (heartbeat gone) or back up;
- safe mode, drawdown stop, daily loss stop, and when they clear;
- day PnL at half, then all, of the daily loss limit;
- fills: each one, an hourly summary (default), or none;
- a digest with yesterday's report just after 00:00 UTC;
- the pilot: new top 3 when nothing runs, a deployment paused because its conditions changed (and why), resumed,
  or a clearly better setup (it never switches without you).

The trading bot still sends its own WARN/CRIT alerts with the same token; the two never conflict.

## 10. Scout on a server (Docker)

The scout needs no keys and places no orders, so it can run anywhere with Docker. On the server, in a copy of this
folder (leave out `.venv`, `.env` and `state/`):

```bash
docker compose up -d --build          # records every Arcus perp (with --depth) and scans every 30 min, 24/7
docker compose logs -f scout          # what it is doing
cat data/scout/report.txt             # latest ranking: best per market, then each market at its max leverage
ls data/scout/reports/                # the last scan of each UTC day
docker compose down                   # stop; it finishes the scan in progress and writes out its buffers
```

- Seed it with the history from the laptop first: copy `data/scout/tape/` (a few hundred MB) into the same place.
- `SCOUT_WORKERS=8 docker compose up -d` uses more cores for the scans.
- Disk: about 0.2-0.5 GB/day with depth recording, about 0.1 GB/day without. Recording pauses by itself under 5 GB free;
  the container reports unhealthy when the recorder has not written for 15 minutes.
- Bring results back with `rsync -a server:PATH/data/scout/ data/scout/` (tape, scans, reports).
- Running the laptop scout at the same time is fine: each writes its own part files and the store de-duplicates.
- `docker compose --profile research up -d` adds the full-book research recorder (`bot record`, about 1.5-2.5 GB/day;
  `docker compose run --rm recorder compact` shrinks closed days). What runs and why: the repository README, section 11.
