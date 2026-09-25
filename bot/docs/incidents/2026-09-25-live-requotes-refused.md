# 2026-09-25: the first live runs could not requote

## Impact

- First live pilot run (QQQ-USD, deep 2bp x2 @ 10x, from 08:21 UTC): orders were placed once and never moved. The
  bot sold into rallies but could not buy back near the market. Result: a small realised loss (under $1) and far less
  volume than the backtest.
- Second run (from 10:03 UTC, with the in-flight fix): every requote was refused. From 10:21 the bot held four ghost
  orders, sent ~4 requests/s and placed nothing. No trading for about 1.5 h. The order-pool allowance drained
  (roughly a quarter of the cap) until the owner stopped the bot.

## Timeline (UTC)

- 08:21 First run starts. Its acks arrive over the WebSocket before the REST reply. The order manager set
  `in_flight` after the reply, so the mark was never cleared. `plan()` never touches in-flight orders: every order
  froze where it was placed.
- ~10:00 Fixed (PR #4: mark before sending, expire after 5 s). Bot restarted on the new code.
- 10:03 Four quotes placed at the right prices. Three acks do not arrive within 5 s. From then on, every modify is
  refused:
  - 10,604 x `CannotModifyImmutableFieldTif`, although the request echoes the resting order's `ALO`;
  - ~110 x `ORDER_NOT_FOUND_FOR_MODIFY`.
- 10:13 and 10:21 Spread safety pauses cancel the quotes (by design, as in the backtest). After 10:21 the bot's state
  keeps four orders Arcus no longer has, because reconcile skipped `PENDING_NEW` orders for good. It modifies them by
  clientId every tick ("Rejected Modification ... Order could not be found" in the Arcus UI).
- ~11:40 Owner stops everything.

## Root causes

0. **The `orders` channel parser dropped every live order update.** Arcus sends one order object per
   `channel_data` frame (docs: api-reference/channels#orders). The adapter only read `{"orders": [...]}`. So no ack,
   cancel or status ever reached the bot. The orders froze in run 1 (never acknowledged, so in flight forever) and
   the acks were "missing" in run 2. The snapshot's `openOrders` / `recentClosedOrders` were ignored too. Found in the
   audit against the docs and confirmed with a live read-only capture: the `userFills` stream uses `{"fills": [...]}`,
   which is why fills were recorded while orders were not.
1. `in_flight` was set after the send (PR #4 changed it; an orders stream that never delivered was the larger
   cause).
2. Reconcile never cleared unacknowledged (`PENDING_NEW`) orders, and the order manager modified them after 5 s and
   retried refusals every tick (PR #6 fixed it).
3. Every Arcus modify was refused. The docs disagree on the modify identity:
   - the modify page says exactly one of orderId/clientId;
   - the authentication page and the testnet changelog say `id` always, plus `c` echoed.

   None of them explains a time-in-force refusal when the resting order's TIF is echoed. Modify had never been
   tested against a resting order: selftest needs `--allow-funded` for that.

## Fix and prevention

- Arcus requotes go out as cancel + place (`ArcusAdapter.use_modify = False`). Both work live, and Arcus runs every
  price-changing modify as cancel + replace, so no queue priority is lost.
- Re-enable modify only after `bot selftest --testnet --allow-funded` shows a modify of a resting ALO order landing
  (and which identity form it needs).
- Unacknowledged orders are never modified; a reconcile is requested within 5 s. Reconcile clears them 10 s after
  their last activity if Arcus does not list them.
- A refused modify or cancel backs off 5 s and requests a reconcile.
- Batch-place replies are matched by clientId, and reconciliation records venue order ids.

## Should a kill switch have fired earlier?

No money was at risk: the account was flat, and the stops still worked (taker exits do not use modify). A breaker on
repeated refusals of one request type (not only the reject breaker's venue reasons) would have stopped the request
flood within a minute. The back-off now caps it at one request per order per 5 s.

## Audit against docs.arcus.xyz (same day)

Fixed:
- `orders` updates are a single object, and the snapshot is `openOrders`/`recentClosedOrders`. The parser reads all
  of them, applying closed rows only for orders we still track. `userFills` also accepts the docs' single-object
  shape (`fillPrice`/`fillSize`/`market`).
- The `funding` channel was never subscribed and payments never reached the ledger, so the stops' day PnL left
  funding out. Now subscribed, and each payment is counted once (the state table's key).
- A 429 on an order write now waits `retryAfterMs` per pool (IP limits block both pools) instead of retrying every
  tick. A 429'd place is rejected locally, since the limiter refuses before the handler runs.
- Off-hours bounds are inclusive (fills AT a bound are rejected). `isOutsideRth: null` keeps the last value. The
  pre-trade margin check follows the live session (off-hours IMF) instead of the start-up value.
- Batch post-flight IP weight `floor(N/40)` is charged. The weight-table keys use the documented paths.
- RTH/off-hours state and the off-hours bands now come from the `marketAttributes` channel. The runner subscribed
  to it but never consumed it, and the `markets` channel does not carry `isOutsideRth` or the bands today (docs,
  local copy: market-data/markets, "Field coverage"). The bot may never have known it was off-hours during a run.
  A 5 s `markets` snapshot also reset the bands to None. Now each message applies only the fields it carries, and
  the pre-trade margin check follows the session's off-hours IMF.
- The OI cap is read under the channel's own name (`openInterestCap`) as well as the REST one
  (`openInterestCapNotional`).
- Selftest proves a modify on the book (PASS only when the order rests at the new price), else SKIP.

Compliant (checked):
- Endpoints, query parameters and units (µs; ns for signing and `X-Timestamp`).
- placeOrder, batch, cancel, batch cancel, cancelAll, scheduleCancel and setLeverage bodies.
- Scheme 1 payloads (`ad` lowercase, p/q integer ticks and quantums, `g` in ns, `r`/`s`/`t` codes, `c` omitted when
  empty). Scheme 2 for cancelAll and setLeverage.
- The IP bucket 1,500/min, the weight tiers and list add-ons, batch ≤ 39 for 0 weight, the order/cancel pools and
  the cancelAll 1,000 charge.
- DMS: µs deadline, 5 s–5 min lead, 10 fires a day.
- WebSocket: subscribe envelope, `accountIndex`, the 24 h lifetime, subscription and message limits, the order-book
  splice rule, `bbo` without gap checks.
- clientId rules. goodTilTime ≥ 1 month (35 days). The $5 minimum, which reduce-only orders are exempt from.
  maxOrderSize for every order.
- Status map, market fields, RTH from `isOutsideRth`, hourly funding, EIP-712 CreateApiKey.

Doc conflicts (so modify stays off):
- modify identity: exactly one of `id`/`c` (modify page, OpenAPI) vs `id` always plus `c` (authentication page,
  testnet changelog);
- modify size is the order's new TOTAL size (filled quantity included); the adapter sends the remaining size;
- the live `CannotModifyImmutableFieldTif` refusals despite echoing `ALO`.

## Follow-up (same day, repository audit)

- **Modify identity.** The SPY run (PR #6 code) learned the venue order ids by reconciliation and then sent 14
  modifies by orderId with no error; the QQQ run never learned them and every modify by clientId alone was refused.
  So `CannotModifyImmutableFieldTif` looks like how Arcus refuses a clientId-only modify. The adapter now only ever
  modifies by orderId, and the order manager requotes an order whose id is not known yet with cancel + place.
  Modify stays off (`use_modify: false` in `config/venues/arcus.yaml`) until `bot selftest --allow-funded` shows a
  resting order move.
- **False guardian alarm.** The SPY run was stopped from Telegram at 12:20:23 UTC; its guardian kept running and at
  12:21:22 raised CRITICAL "bot heartbeat silent for 62s" and sent a cancel-all (harmless: nothing was open). A bot
  that stops on purpose and has pulled its quotes now writes a last heartbeat saying so, and the guardian exits.
- **Order-pool brakes never engaged.** The budget governor widens requotes under 20% of the order pool and sends
  cancels only under 5%, but nothing ever gave it the pool: that is why run 2 drained about a quarter of the cap
  unnoticed. The runner now polls `GET /v1/rateLimit` every 15 s (weight 2; the docs recommend it for aggressive
  order loops) and hands the numbers to the governor.
- **Lighter was still wired into every Arcus run.** The runner read Lighter's markets at start-up (an outage there
  could stop the bot from starting) and subscribed to Lighter's book for the same market. The engine ran the safety
  pause on that book too: every Lighter spread spike was logged and alerted as a `safety_pause` (for venue
  `lighter_rh`), although it never stopped Arcus quoting. Part of the 16–24 pauses an hour counted below may be
  those. Lighter and the other two-venue code were removed.

## Still open

- The live safety pause fired about 16–24 times an hour on QQQ (08:00–12:00 UTC). On the recorded tape the simulator,
  which models the same spread rule, pauses 2–13 times an hour for those hours. First recount on the next run: only
  pauses for venue `arcus` (the Lighter ones above no longer happen). Live also pauses on thin depth (< 30% of the
  median), which the simulator does not model. Either drop that rule for pilot runs or add it to the simulator (a full
  re-backtest), so live matches the backtest.
- `bot selftest --allow-funded` with a resting order, to confirm modify by orderId before `use_modify` is turned on.
