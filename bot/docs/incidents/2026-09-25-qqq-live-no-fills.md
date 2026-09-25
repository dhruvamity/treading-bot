# 2026-09-25: a live QQQ run with no fills, and a crossed BTC book

## What happened

The owner ran `touch 1bp @ 25x` on QQQ-USD live from the ⚡ Aggressive list at 20:09 UTC (16:09 ET, just after the
US close), on a ~$30 account. After 18 minutes the dashboard showed no fills, "Running, not quoting: QQQ (safety
pause)", 0 open orders, and two warnings: `pause_quotes: spread 1.2 bps > 3.0x median 0.1` and `… 1.3 bps > 3.0x
median 0.3`. The owner expected thousands of dollars of volume a day.

## Findings

1. **The run was too short to judge, and most of it was paused.** The backtest's own rate for this setup was 48
   fills a day, about two an hour: 0.6 fills were expected in 18 minutes. After 10 minutes of warm-up the spread
   rule of the safety pause applied, and the spread widening after the US close (0.1-0.3 bp to 1.2-1.3 bp) kept it
   paused. The backtest applies the same rule; over a whole day it changes volume by about 5% (measured on 4
   markets x 4 days), but it can take most of a short run that starts at the close.
2. **`touch 1bp` rests behind the best price on a tight book.** It quotes mid ± max(1 bp, half the spread). QQQ traded
   at one tick (0.13 bp) 18-29% of the recorded days, and at ~0.4 bp during this run, so the quotes sat about 6 ticks
   behind the best bid and ask and are filled only by takers that sweep that far. `touch 0bp` (join the best price)
   and `improve touch` (one tick inside) are the settings that rest at the touch.
3. **The daily stop sets the volume ceiling.** Aggressive quoting costs about 1-1.4 bp of every dollar traded. On a ~$30 account
   with a 5% daily stop (about $1.40) the stop trips after roughly $10k of volume and the bot sits out the rest of the UTC
   day: in the backtest `improve touch` quoted only 23-29% of the day on QQQ, `touch 0bp` 33-51%. Volume per day is
   about (daily stop in $) / (cost per $). More volume needs more capital or a larger daily stop.
4. **A real bug: phantom levels in the live order book (BTC).** The live book is built from `l2OrderbookUpdates`.
   The first delta can come many sequences after the snapshot (56 on BTC-USD); the docs call this boundary gap
   expected and self-healing, but a level removed inside the gap is never mentioned again and stays in the book. On
   BTC a phantom best bid made the book read crossed in 124 of 235 seconds (read-only probe of the public feeds,
   2026-09-25 20:40 UTC). QQQ, SPY and NVDA matched the bbo channel in every second of the same probe.

## Fixes

- `ArcusBookSync.on_delta`: a level resting on one side removes levels at or through its price on the other side
  (resting orders never lock or cross on Arcus). `on_bbo`: when the bbo channel carries the book's own
  `lastSequenceId`, levels better than its best bid or ask are dropped. Replaying the capture: never crossed, top of
  book equal to the bbo at the same sequence in 3,547 of 3,547 checks. Test fixture
  `tests/fixtures/live/arcus_btc_boundary_gap.json`.
- The engine now counts, per UTC day, the seconds it quoted and what blocked it, and how often a buy and a sell
  rested at the best price (else how many ticks behind): on `/dashboard`, `/openpositions` and in the status JSON.
- `bot diagnose`: over a time window, orders sent and acknowledged, rejects, time on the book, placement against
  the best price, blocks, fills, and the taker trades that went through a resting price or traded while no order was
  out. Run it on the server for this run: `bot diagnose --since "2026-09-25 20:05" --until "2026-09-25 20:30"`.

## Still open

- The backtest counts a fill only when a taker trades through our price, so it undercounts orders that join or
  improve the best price (they also fill when the queue ahead is used up). A queue-position fill model would rank
  `touch 0bp` and `improve touch` higher; it needs a full re-backtest.

## Later the same run: stuck holding a long (reported by the server agent)

The run traded normally until about 21:56 UTC (20 fills, $3,382 of maker volume, one position stop at 21:31). It
then held a long of 0.558 QQQ (~$416) and placed no order for over an hour. After the US close the off-hours cap
(16.67x, ~$373) was below the position, so the bid was off and the only quote left was the sell that would reduce
the long. The bot's own pre-trade check refused it about 4,900 times (`free_collateral`: it wanted ~$20 of margin with ~$12 free):
it charged initial margin on the whole order as if it opened a position, and QQQ's off-hours margin had gone up.
Arcus charges initial margin only to open or add to a position; off-hours a position above the higher requirement
"can still be reduced or closed" (docs, concepts/perpetuals/margin). None of these refusals reached Arcus, and
nothing alerted.

Fixes:
- `RiskEngine.check`: only the part of an order that opens or adds to the position (after other resting orders on
  the same side) needs free collateral and OI-cap headroom; the leverage cap applies only to orders that increase the
  position.
- Refusals by the bot's own checks are counted per day (`/dashboard`: "⚠️ N orders refused by the bot's own checks:
  reason") and send one Telegram warning when 30 or more come in a minute, at most every 30 minutes per check. The
  dashboard also shows how much of the day a buy and a sell actually rested on the book.
