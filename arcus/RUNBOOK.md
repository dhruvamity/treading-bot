# Runbook

Operations for a machine that runs the bots. Commands run from `treading-bot/arcus`; `A=.venv/bin/arcus`. Anything that
touches mainnet asks you to type a confirmation. Setup, servers and export are in the [main README](../README.md); what
each setting means is in the [Arcus README](README.md).

## 1. Processes

`$A up` starts the services below in the background; `$A status` shows all of them on one screen; `$A down` stops them
(`--all` also stops every run: quotes cancelled, positions kept). Runs are started from Telegram or `$A pilot`.

| Process | What it does | Keys |
|---|---|---|
| scout | `arcus scout run`: records every Arcus perp to `data/scout/tape/` and re-ranks the setups every 30 min | no |
| Lighter scout | the same for Lighter (`lighter scout run`) | no |
| Telegram bot | status, run, stop, cancel-all, flatten and alerts on your phone, for all three bots | for cancel-all, flatten, doctor |
| run | `arcus run SESSION`: engines, dead man's switch, reconciliation, heartbeat | live |
| guardian | Started with a live run. Watches `state/heartbeat.live`: silent for 60 s, or the drawdown limit hit, and it cancels all orders and alerts. A run stopped on purpose leaves a last heartbeat saying so, and the guardian exits without alarming | read + cancel |

Logs are in `logs/` (JSON lines, secrets redacted): `tail -f logs/bot.jsonl`; decisions are in `logs/decisions.jsonl`.

## 2. Daily checks (2 minutes)

1. `$A status`: per mode, whether a run is up, open orders, positions, any pending resume flag. `$A doctor pilot` should still say READY
   (key expiry, funds, clock).
2. `$A report --date YYYY-MM-DD` (live by default; `--mode paper`): `Net = SpreadCapture + InventoryMTM + Funding − Fees − LiquidationLoss`,
   plus volume and budget.
3. `data/scout/recorder.json`: `last_msg_age_s` a few seconds, `paused_for_disk` false; `data/scout/report.txt` under 40 minutes old.
4. `$A keys`: every Arcus key ACTIVE with more than 7 days left.
5. `df -h`: more than 30% free. The scout records about 0.1–0.5 GB/day.
6. With the autopilot on: `$A auto` (or `/auto`) shows the pot, what it runs and why. `data/scout/playbook.json` and
   `state/calendars/earnings.csv` should be under a day old.
7. Lighter and the arbitrage: `/l_status`, `/arb_status` (their READMEs have the daily checks).

## 3. Start, stop, change a run

| To | Do |
|---|---|
| Stop one run | `/stop` in Telegram, or `$A down --all`. Quotes are cancelled, positions kept |
| Start a run | `/run …` in Telegram, `$A pilot approve N`, or `$A run SESSION` |
| Go live | Step 5 of the [Arcus README tutorial](README.md#2-tutorial-zero-to-a-live-run): `selftest`, deposit, `BOT_PILOT_LIVE=1`, `doctor` READY, `--live`, type `LIVE` |
| Back to paper | Start the run without `--live`, or `/run … paper` |

An unattended live start (`run SESSION --live --yes`) needs `live_enabled: true` in the session file and refuses to start when `doctor` reports
any FAIL. From a terminal, leave out `--yes`: you get the doctor report and type `LIVE`. Credentials live in `.env`, never in git.

## 4. Emergencies

| Situation | Do this |
|---|---|
| Anything looks wrong | `$A down --all`. The guardian stays up. With the autopilot on, `/auto off` first, or it starts the next run |
| The autopilot does something odd | `/auto off` closes its run and stops it. `$A auto off` only turns it off; then `$A pilot close` |
| Orders must go NOW | `$A cancel-all --venue arcus` (your key's subaccount, mainnet; `--yes` skips the prompt); `/cancelall` from the phone |
| Close positions | `$A flatten --venue arcus` (maker, reduce-only); add `--taker` for IOC; `/closeall` from the phone |
| Server unreachable | The Arcus dead man's switch (`scheduleCancel`) cancels everything within its 60 s deadline. Then use the Arcus web app: cancel all, close positions. Lighter has its own switch; use the Lighter app |
| Suspected key leak | Revoke the key in the Arcus web app (API Keys), create a new one, replace `ARCUS_API_PRIVATE_KEY` in `.env`, restart |

## 5. Safe mode and kill switches

The bot never resumes after a serious stop by itself. Look at `/logs` first, then `$A resume --venue arcus` or `$A resume --all`
(or `/resumeaftersl`): it writes a flag the running bot picks up on its next tick.

| Trigger | Automatic action | Resume |
|---|---|---|
| Position down by the stop | Cancel quotes, exit maker-first then taker after 20 s | 60 s later |
| Day down by the daily stop (2%) | Close; the venue stops for the UTC day | 00:00 UTC, or `/set daily_stop` wider |
| Equity 10% below its peak (kill) | Everything stops and flattens (taker); CRIT alert | manual only |
| Run limit `sl=`, `tp=`, `vol=` reached | Flatten and stop | start a new run |
| Liquidation distance < 4σ (1 h) | Halve the position | back above 6σ |
| Safety pause (6σ one-second move, spread > 3× median) | Cancel quotes, keep the position | 30 s after normal |
| Event window (CPI, FOMC, NFP ±30 min; earnings ±24 h) | No new quotes | window end |
| Off-hours price band expansion or open-interest cap | Stop quoting that market | cleared |
| Heartbeat silent 60 s | Dead man's switch plus guardian cancel-all | manual, after reconcile |
| Order pool < 20% left (checked every 15 s) | Requote tolerance doubles | pool recovered |
| Order pool < 5% left | Cancels only | pool recovered |
| 429 (rate limited) | Wait the `retryAfterMs` Arcus gives | after the wait |
| Dead man's switch refresh fails twice | Safe mode | manual |
| `SELF_TRADE` / `GEO_RESTRICTED` reject | Stop the venue; CRIT alert | manual |
| Unexpected exception in the trading loop | Safe mode (cancel quotes, keep positions) | manual |
| Same non-routine rejection 5× in 60 s (e.g. UNDERCOLLATERALIZED) | Pause that market's quotes | 60 s, doubling to 10 min |

Every automatic action goes to the decision log (`component=decision`) with its reason; `/logs` shows the latest.

## 6. Routine maintenance

- **Key rotation** (Arcus keys last at most 180 days; alert 72 h ahead, `doctor` refuses to start within 24 h). Create a new key for the same
  subaccount in the Arcus web app (or `scripts/arcus_register_key.py --env mainnet --account N`, which writes `.env`), replace
  `ARCUS_API_PRIVATE_KEY` in `.env` on the server, restart. `$A keys` confirms the new key is ACTIVE.
- **Calendars.** Keep `config/calendars/events.csv` at least 30 days ahead for FOMC and 14 days for CPI; the bot warns hourly while coverage
  is short. BLS publishes next year's CPI and jobs dates late in the year (bls.gov/schedule): add the 2027 CPI dates before mid-December 2026.
  Stock earnings are fetched daily from Nasdaq's public calendar into `state/calendars/earnings.csv`; `config/calendars/earnings.csv` is for
  dates you add by hand (SPY/QQQ ex-dividend dates too).
- **Venue changes.** Live parameters refresh hourly; any change to tick, step, minimum, fees or margins is logged to `data/param_changes_jsonl/`
  and quoting uses the new value at once. Read the Arcus changelog monthly.
- **Modify.** Requotes go out as cancel + place. To use `modifyOrder` instead (one request per requote), run `$A selftest --allow-funded`
  first: it rests a tiny post-only order 3% below the market, modifies it and checks the book. Only if that passes, set `use_modify: true`
  in `config/venues/arcus.yaml` and restart.
- **Backups.** `.env` stays offline, never in git. Everything else is one file: `$A export` (the tape, the state databases and the logs; main
  README, section 4). Run it weekly and copy the file off the server: recordings cannot be made again.
- **Upgrade.** `git pull`, `make install`, then `$A down` and `$A up`. The bot picks up a new release without touching positions.

## 7. After a crash or reboot

1. Start the services again with `$A up` (`deploy/scripts/bootstrap.sh` puts this in cron `@reboot` on a server).
2. On start the bot reconciles: orders on the venue that local state does not know are cancelled, and positions come from the venue.
3. Check `$A status` and the `reconcile` alerts.
4. If the bot died without a clean stop, the dead man's switch has already cancelled the quotes on Arcus.
