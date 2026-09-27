# 2026-09-26: the test suite started a real guardian against the live account

## Impact

- **Two stray guardians, one on each machine:**
  - one from a test run on the development Mac;
  - one on the server, from the server's own test run.
- **They ran from the repository with the real `.env`, on mainnet.** The Mac one ran for about a day.
- **The Mac guardian sent two real cancel-alls to the account (09-26, UTC):**
  - 11:14 UTC (16:44 IST): at start, it found no heartbeat file on the Mac ("bot heartbeat silent"), so it cancelled
    every order;
  - 13:12 UTC (18:42 IST): account equity had fallen more than 10% below what it read at start. It cancelled all
    orders again, then tried to close the positions (reduce-only, maker first).
- **The server agent found and stopped two stray guardians on the server** (22:22 and 22:3x IST).
- **What it cost: about $1.** Arcus's fill history for the account (`/v1/fills`, checked 2026-09-27) shows:
  - the running bot opened a BTC short of 0.0286 at 13:12:33–41 UTC;
  - 32 s after the guardian fired, at 13:13:21 UTC (18:43 IST), a taker buy of exactly that size closed it. That
    matches the guardian's "maker, then taker after 30 s";
  - the position was down about $0.5 then, under the bot's own $0.90 stop, so the bot did not close it itself;
  - the close cost about $1 with the taker fee;
  - the bot quoted again from 13:15;
  - the 11:14 UTC cancel-all cost nothing (the bot re-quoted).

## Timeline (UTC)

- 09-26 11:14:55 A test run on the Mac starts `bot guardian` (pid in the repository's `state/guardian.pid`); it
  cancels all orders a second later.
- 09-26 13:12:49 Its drawdown check fires: cancel-all, flatten.
- 09-26 13:13:21 Its taker close of the bot's BTC short (0.0286) fills.
- 09-26 16:5x–17:0x The server runs the suite after pulling PR #19 and gets its own stray guardian (twice). The server
  agent reports it.
- 09-27 The Mac one is found still running (since 09-26 16:44 IST) and stopped. It sent nothing on exit.

## Root cause

- **The leaking test:** `tests/unit/test_telegram.py::test_a_new_run_clears_an_old_pause_and_waits_for_a_closing_bot`
  deployed a LIVE run through the real `Pilot.deploy`.
- **The path to a real process:** after a live start, `Pilot.deploy` calls `ops.start(app, "guardian")` to bring up
  the live bot's watchdog. The test replaced the pilot's own process start, but not `ops.start`. That spawned
  `.venv/bin/bot guardian`, detached (`start_new_session`).
- **Why it did damage:** the process outlived pytest and read the real `.env`. A guardian on a machine with no live
  bot fires at once: there is no heartbeat to watch.

## Fix and prevention

- **The test** now replaces `ops.start` and checks that the guardian is asked for.
- **A guard for every test:** `tests/conftest.py` makes any attempt to start a real `bot` process fail with an
  error, whatever the test.
  - The only test that exercises the spawning code points it at a harmless command.
  - Running the suite with the guard found no other leak.

## Should a kill switch have fired earlier?

No. The guardian did what it is built to do, on the wrong machine, so the prevention is in the tests.

A further safeguard is proposed, not built: a guardian could exit instead of firing when there is no heartbeat file at
all (no live bot has ever run on that machine). Today a missing file counts as a dead bot on purpose
(tests/unit/test_risk_ledger.py), so this is the owner's call.
