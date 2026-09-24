# ArcLight: Coding-Agent Prompt Pack (All Phases)

**Project:** ArcLight, a self-hosted farming bot for **Arcus** and **Lighter on Robinhood Chain (Lighter RH)**. It re-creates Tread.fi's Market Maker modes (Mid, Grid, RGrid, DGrid, Blend, Signal) and its Delta-Neutral bot, and adds an **Autopilot** that picks the strategy and settings per asset per venue.
**Pack version:** 1.0 · **As of:** 2026-09-23 · **Owner:** ddd
**Companion documents:** `ArcLight bot project spec` (the design), `THESIS_1_maker_grid_farming.md`, `THESIS_2_delta_funding_arbitrage_arcus_lighter.md`, and the delta-farmer repo (MIT) for the Lighter client reference.

---

## 0. How to use this file

### 0.1 What is in here

| Part | What it is | When to paste it |
| --- | --- | --- |
| **Part A: Global context** | Mission, hard rules, every verified venue fact, the Tread.fi behaviours, all formulas, known unknowns | **Every** phase session, first |
| **Part B: Engineering standards** | Stack, code rules, safety locks, testing, logging, hand-off format | **Every** phase session, second |
| **Part C: Phase prompts** | One prompt per phase, P0 → P7 | The one phase you are running |
| **Part D: Utility prompts** | Bug fix, audit, venue-API change, weekly review, new venue | When needed |
| **Part E: Appendices** | Interfaces, schemas, config templates, hand-off template, checklists | The agent reads them from the repo |

### 0.2 The routine for each phase

1. Put this whole file in the repo at `docs/PROMPT_PACK.md`, together with the spec and both theses in `docs/`, and delta-farmer's `clients/lighter.py` and `lib/lighter_crypto/` in `docs/reference/delta-farmer/`.
2. Start a **fresh** coding-agent session for each phase.
3. Paste: **Part A + Part B + the phase prompt** (or tell the agent: "Read `docs/PROMPT_PACK.md` Parts A, B and E, then execute Phase N exactly").
4. The agent must finish by writing `handoffs/PHASE_N_HANDOFF.md` using the template in Appendix E6.
5. Before the next phase, read the hand-off, answer any `[QUESTION FOR OWNER]` items, and tick the phase's acceptance checklist yourself.
6. Never skip a phase gate. A phase is done only when its acceptance tests pass.

### 0.3 Phase map

| Phase | Name | Weeks | Needs money? | Human gate |
| --- | --- | --- | --- | --- |
| P0 | Scaffold, config, public data clients, **recorder** | 1 | No | Choose server region |
| P1 | Venue adapters (trading, signing) | 1–2 | Testnet only; one tiny Lighter RH probe | Approve the Lighter probe |
| P2 | Core engine: state, order manager, budget governor, risk, guardian, ledger, alerts | 2–3 | No (paper adapter) | None |
| P3A | Event-driven simulator | 3–4 | No | None |
| P3B | Strategy modes (Mid, Grid, RGrid, DGrid, Blend, Signal, DN hedged MM, DN carry, points overlay) | 3–5 | No | None |
| P4 | Research, backtests, Autopilot v1, go/no-go report | 4–6 | No | Accept or reject the go/no-go report |
| P5 | Paper trading on live feeds (2 weeks) and calibration | 6–8 | No | Accept the paper report |
| P6 | Live stages 1 and 2 (≤ $100 total) | 8+ | Yes | Every stage start |
| P7 | Scale and Autopilot v2 (bandit) | After P6 gates | Yes | Every scale step |

The recorder from P0 must run continuously from day 1: neither venue publishes historical order books, and P3A/P4 need at least 2–4 weeks of recorded books.

### 0.4 Tags used in this file

- **[LIVE]**: a value that is operator config. Fetch it from the API at start-up, refresh hourly, log every change. Never hard-code it.
- **[VERIFY]**: a fact from a secondary source (third-party code, reports) or with conflicting docs. Confirm it before relying on it.
- **[HUMAN GATE]**: the agent must stop and ask the owner before continuing.
- **[QUESTION FOR OWNER]**: something the agent cannot decide; list it in the hand-off.

---
# PART A: GLOBAL CONTEXT (paste into every phase)

> You are a senior quantitative developer building **ArcLight**, a self-hosted trading bot. Read all of Part A before writing any code. Everything in Part A is binding unless a later phase prompt explicitly overrides it.

## A1. Mission

Build a bot that generates **points-eligible trading activity** (volume and open interest) on two perpetual-futures venues, **Arcus** and **Lighter on Robinhood Chain**, at **break-even or better net PnL after all costs**. It must:

1. Run every Tread.fi Market Maker mode (Mid, Grid, RGrid, DGrid, Blend, Signal) and a cross-venue Delta-Neutral (DN) bot, with Tread-style controls: spread in bps, bias, execution style, margin and leverage, stop-loss and take-profit, reset threshold, duration and repeat.
2. Run an **Autopilot** that, for a given venue and asset, picks the mode and its parameters from live market state and switches or pauses as the regime changes.
3. Hedge across Arcus and Lighter RH using **one account per venue**.
4. Report PnL decomposed into spread capture, inventory mark-to-market, funding, fees and hedge cost, plus volume, OI-hours, cost per $1M of volume (CPM) and points.
5. Run unattended 24/7 with kill switches, a dead man's switch, an independent guardian process and Telegram alerts.

Capital context: **$100 total** at first ($35 Arcus market making, $25 + $25 DN legs, $15 reserve). The design must work at this size (venue minimum order notionals are $5 on Arcus and $10 on Lighter RH) and scale 10× without redesign.

## A2. Hard rules (non-negotiable)

1. **No multi-account tricks.** Never trade between two accounts you control on the same venue, never split activity across accounts to farm points, never self-refer. Lighter RH bans this and reverses points. Arcus subaccounts may separate strategies but must never trade against each other (enable self-trade prevention; treat `SELF_TRADE` rejects as bugs).
2. **Every Lighter RH order must be genuine.** It is either a resting quote other traders can fill, or a hedge of a real Arcus fill, or a held position. No round-trips to pad volume. Lighter's rules ban "using bots, scripts, or automated systems to manipulate Program metrics"; keep Lighter behaviour boring and explainable.
3. **Log every decision with its reason** (mode chosen, parameters, why an order was placed/cancelled). The decision log must make the account's behaviour explainable to a reviewer.
4. **No live order can be sent unless the triple lock is open** (see B4). Default everything to paper or testnet.
5. **Never hard-code [LIVE] values.** Fees, tick sizes, step sizes, margin fractions, funding parameters, minimums, OI caps and session hours are fetched from the APIs and refreshed hourly.
6. **Never log or print secrets** (wallet keys, API private keys, auth tokens, Telegram tokens).
7. **Never keep the wallet (L1) private key on the trading server** once API keys are registered. Registration scripts run on the owner's machine.
8. **Candles from Arcus are oracle-priced. Never simulate fills from them.**
9. **If a venue fact in this file conflicts with the live API or the official docs, the live API wins.** Record the conflict in the hand-off.
10. **When unsure, stop and ask** ([QUESTION FOR OWNER]) rather than guess on anything that touches money, keys or compliance.

## A3. Venue facts: Arcus

Arcus is a hybrid perpetuals DEX by the dYdX team: an off-chain CLOB matching engine, a permissioned appchain that re-verifies it, and custody/settlement on an EVM rootchain, **Robinhood Chain**. Mainnet API went live 2026-06-21. Collateral is **USDG** (Paxos stablecoin). Docs: `https://docs.arcus.xyz` (also a docs MCP server at `https://docs.arcus.xyz/mcp`, OAuth; connect it if your tooling supports MCP).

### A3.1 Endpoints and environments

| Item | Mainnet | Testnet |
| --- | --- | --- |
| REST | `https://api.arcus.xyz` | `https://api.testnet.arcus.xyz` |
| WebSocket | `wss://api.arcus.xyz/v1/ws` | `wss://api.testnet.arcus.xyz/v1/ws` |
| Rootchain chain ID (EIP-712 domains) | `4663` (Robinhood Chain) | `46630` (Robinhood Chain testnet) |
| Running version | `GET /` returns it | same |

Testnet funding: the testnet web app's **Testnet Deposit** button credits about $1,000; for more, testnet USDG has an open mint on Robinhood Chain testnet, then approve the deposit proxy and call `initiateDeposit` (see docs guide "Fund a testnet account"). A new API key's account returns `404 "this account has no activity yet"` until funded.

### A3.2 Authentication and API keys

- API keys are **Ed25519** key pairs. The public key (32 bytes, 64 hex chars, no `0x`) is the `apiKey`.
- Register with `POST /v1/createApiKey`, authenticated by an **EIP-712** signature (`eth_signTypedData_v4`) from the wallet that owns the address. Domain: `{"name": "Arcus API Key", "version": "1", "chainId": 4663}` (testnet `46630`), **no `verifyingContract`**. Types:
  - `CreateApiKey(string apiWalletName,string apiWalletPublicKey,uint256 validUntil,uint8 accountIndex)` (required for non-zero `accountIndex`).
  - With replay protection (recommended): `CreateApiKey(string apiWalletName,string apiWalletPublicKey,uint256 validUntil,string nonce,uint8 accountIndex)`; a replayed nonce returns 409.
  - Send `validUntil` (epoch **ms**) explicitly and sign that value. If omitted, the server assumes now + 14 days. **Keys expire: build a key-expiry monitor and a rotation procedure.**
- A key is bound to **one `accountIndex`** (0–9). Order writes for another index return 403.
- Keys **upsert by `apiWalletName`**: re-creating with the same name revokes the old key.
- Keys from the public `createApiKey` are **trade-only**. Withdrawals need an operator-provisioned `withdraw` permission (not available to us); withdrawals and transfers use wallet EIP-712 signatures.
- Every mutating request sends three values: `X-API-Key` (public key hex), `X-Timestamp` (Unix **nanoseconds**), `X-Signature` (Ed25519, 128 hex). Over WebSocket they are the `apiKey`, `timestamp`, `signature` envelope fields.

**Signing Scheme 1 (placeOrder, cancelOrder, modifyOrder; batches sign each element):** the signed bytes are a compact, key-sorted JSON object (no whitespace) built from engine integers:

| Operation | `op` | Canonical payload |
| --- | --- | --- |
| placeOrder | 1 | `{"ad":"0x…","ai":N,"c":"…","ct":N,"g":N,"m":N,"op":1,"p":N,"q":N,"r":0,"s":N,"t":N,"v":1}` |
| cancelOrder | 2 | `{"ad":"0x…","ai":N,"c":"…","ct":N,"id":"…","m":N,"op":2,"v":1}` |
| modifyOrder | 3 | `{"ad":"0x…","ai":N,"c":"…","ct":N,"g":N,"id":"…","m":N,"op":3,"p":N,"q":N,"r":0,"s":N,"t":N,"v":1}` |

Fields: `ad` master address, **lowercase**; `ai` accountIndex; `c` clientId (**omit the key entirely when empty**, sign verbatim case); `ct` = `X-Timestamp` (ns); `g` = goodTilTime in **ns** (= the request's µs `goodTilTime` × 1000); `id` server orderId (omit on cancel-by-clientId; always required on modify); `m` marketId; `p` = price ÷ **top-level `tickSize`** (exact integer; tick tiers never change the divisor); `q` = size ÷ `stepSize` (exact integer); `r` reduce-only 0/1; `s` side 0 buy / 1 sell; `t` time-in-force 0 GTT / 1 FOK / 2 IOC / 3 ALO; `v` = 1. `op` 4 = untriggered TPSL (same fields as place). On modify, `g`, `r`, `s`, `t` echo the resting order's immutable attributes; include `c` only if the resting order had one.

**Signing Scheme 2 (cancelAllOrders, setLeverage, WebSocket authenticate):** `signature = ed25519(timestamp + action + canonical_json(body))`, concatenated with no delimiters; `action` is the camelCase last path segment (e.g. `cancelAllOrders`); canonical JSON = sorted keys, no whitespace.

**Batches:** each element is signed with Scheme 1 using the one shared `X-Timestamp` as `ct`. On REST the `X-Signature` header must still be present (use any element's signature). A TPSL `grouping` field is not signed.

### A3.3 Orders

- `POST /v1/placeOrder?address=0x…` body: `address`, `marketId`, `accountIndex`, `orderSide` (`BUY`/`SELL`), `orderType` (`LIMIT`/`MARKET`), `quantity` (string, base units, divisible by `stepSize`, ≤ `maxOrderSize`), `price` (string, divisible by the tick of its price band), `timeInForce` (`GTT`, `IOC`, `FOK`, `ALO`), `goodTilTime` (string, epoch **µs**, **at least one month ahead, required on every order including IOC/FOK**), `timestamp` (int, ns, equals `X-Timestamp`), optional `clientId`, `reduceOnly`, TPSL fields.
- `clientId`: charset `[A-Za-z0-9_-]`, length 1–36, unique among live orders, max **10,000** live client IDs per account. Cancel by clientId skips waiting for the orderId.
- Responses are asynchronous: `202` with `status: ACK` (common) or `200` with a definitive state. The lifecycle (`OPEN`, `FILLED`, `CANCELED`, `REJECTED`, fills, rejection reasons) arrives on the `orders` and `userFills` WebSocket channels. Order reads report `GTT` as `GTC` (legacy name).
- **Market orders** must be `IOC` and carry a `price` within **10% of mark** (a marketable limit). Limit prices too far from the oracle are rejected (`OracleDeviation`).
- **Taker speed bump: 50 ms.** Any order or modify that could take liquidity is held 50 ms. **`ALO` (post-only) skips it** and is the fastest path. Cancels run on a priority lane ahead of placements.
- **Modify** = cancel + replace under the same orderId (CANCELED with `MODIFY_CANCELED`, then the new outcome). Same-price size **decrease** keeps queue priority; same-price size **increase** loses it.
- **TP/SL** only via `POST /v1/batchPlaceOrders` with `grouping` `positionTpsl`, `partialTpsl` or `entryTpsl`; legs are reduce-only; `placeOrder` TPSL returns 501. At most one position-level TP and one SL per market.
- `batchPlaceOrders` / `batchModifyOrders`: IP weight `floor(N/40)` (0 up to 39 orders). `batchCancelOrders`: up to **100** per request.
- **Dead man's switch:** `POST /v1/scheduleCancel` body `{address, accountIndex, time}` where `time` is an absolute epoch **µs** deadline 5 s–5 min ahead; refresh by sending a new later deadline; omit/null `time` to disarm. On expiry the gateway runs `cancelAllOrders` for that subaccount. Max **10 auto-fires per UTC day** per subaccount. (Added 2026-08-27; the older WebSocket page still says there is no dead man's switch. Trust the changelog and test on testnet. [VERIFY])
- Rejection reasons to handle as typed errors: `POST_ONLY_WOULD_CROSS`, `SELF_TRADE`, `UNDERCOLLATERALIZED`, `IOC_CANCELED`, `FOK_FAILED`, `REDUCE_ONLY_WOULD_INCREASE`, `TOO_MANY_CLIENT_IDS`, `DUPLICATE_CLIENT_ID`, `POSITION_TPSL_ALREADY_EXISTS`, `OPEN_ORDER_CAP_EXCEEDED`, `FILL_WILL_EXCEED_TRADING_BOUND`, `OPEN_INTEREST_CAP_EXCEEDED`, `ORDER_NOT_FOUND`, `ORDER_NOT_FOUND_FOR_MODIFY`, `MODIFY_CHANGED_IMMUTABLE_FIELD`, `MODIFY_ZERO_SIZE`, `POSITION_SIZE_CAP_EXCEEDED`, `MODIFY_TPSL_NOT_SUPPORTED`, `MODIFY_SUPERSEDED_BY_CANCEL`, `MODIFY_SIZE_ALREADY_FILLED`; API-level `errorType`: `Tick`, `InvalidRequest`, `OracleDeviation`, `ReduceOnly`, `Unavailable`, `Unauthorized`, `Forbidden`, `NotImplemented`, `Transmission` (retry), `Internal`; `GEO_RESTRICTED` (403).

### A3.4 Markets [LIVE]

`GET /v1/markets` returns per market: `marketId`, `marketDisplayName` (e.g. `BTC-USD`), `status` (`ONLINE`/`OFFLINE`), `category` (CRYPTO, EQUITIES, INDICES, COMMODITIES, FOREX), `tickSize`, `stepSize`, `tickTiers[] {tick, upToPrice}`, `minOrderNotional` ($5 flat on opening orders; reduce-only exempt), `minOrderSize`, `maxOrderSize` (applies to reduce-only too), `initialMarginFraction`, `maintenanceMarginFraction` (= ⅔ × IMF on all sampled markets), `offHoursInitialMarginFraction` (1.5 × IMF on RWA today), `regularTradingHours` (`null` for crypto; RWA 04:00–20:00 ET = seconds 14400–72000, `America/New_York`), `isOutsideRth`, `currentSettlementPrice`, `upperTradingBound`/`lowerTradingBound`, `nextUpper/LowerTradingBound`, `is{Upper,Lower}InExpansionZone`, `oraclePrice`, `markPrice`, `fundingRate`, `nextFundingRate`, `nextFundingAt`, `volume24hNotional`, `openInterest`, `openInterestCap` (USD), `pythId`, `assetResolution`.

Snapshot 2026-09-23 (orientation only): 51 online perps. Crypto: BTC (40x), ETH (25x), SOL, DOGE, HYPE (10x), DYDX, ZEC, LIT, XRP, CASHCAT, ENA, PUMP, NEAR, PENGU, FARTCOIN and more. RWA: SPY (50x), QQQ (25x), NVDA (20x), TSLA, AAPL, AMZN, MSFT, GOOGL, META, AMD, INTC, MU, BABA, HOOD, CRCL, GLD, SLV, USO, SNDK, DRAM, PLTR, CRWV, ORCL, SPCX, BE, USAR, COIN, SKHY, NBIS, MSTR, MRVL, BOT, CPER, GME, QNT, MRNA. 24h volume: BTC $86.1M, ETH $13.2M, SOL $4.4M, HYPE $2.7M, QQQ $1.24M, SPY $1.19M, NVDA $0.32M. RWA books can be thin (SPY seen 13.7 bps wide on 2026-07-25, bot-dominated).

### A3.5 Fees [LIVE]

`GET /v1/feetiers` (fields in **ppm**; negative maker = rebate; `volume_threshold` is USD × 1e9, 30-day volume):

| Tier | 30d volume | Maker | Taker |
| --- | --- | --- | --- |
| Base | $0 | 0 | 225 ppm (2.25 bps) |
| Bronze | $5M | 0 | 190 |
| Silver | $20M | 0 | 160 |
| Gold | $100M | 0 | 135 |
| Platinum | $400M | 0 | 115 |
| VIP | $1B | −20 (−0.2 bps) | 100 |
| Max | $3B | −30 | 95 |

Referred users get a 5% fee discount. No Arcus fee on USDG deposits/withdrawals; minimum withdrawal $1. Funding is peer-to-peer (no exchange fee).

### A3.6 Rate limits

- **Per-IP weight bucket:** 1,500 weight, refilling 1,500/min (25/s). Weights: 0 = `health` and **all order writes** (place, modify, cancel, cancelAll, batches up to 39); 1 = `/`, `time`, `compliance`; 2 = `bbo`, `mids`, `account`, `positions`, `order`, `feeTiers`, `leverages`, `accountStats`, `rateLimit`; 20 = `prices`, `markets`, `trade(s)`, `candles`, `portfolio`, `openOrders`, `orders`, `fills`, `funding`, `fundingRates`, `accountTransferUpdates`, `apiKeys`, `createApiKey`, `revokeApiKey`; 125 = `setLeverage`, `withdraw`, `transfer`. Lists add `floor(items/N)` after the response (N = 20; 60 for candles; 50 for openOrders); `l2OrderBook` = 2 + floor(nLevels/20).
- **Per-subaccount trading pools** (keyed on address + accountIndex; independent of IP): order pool starts at **20,000** (1 per place/modify, N per batch); cancel pool starts at **40,000** (1 per cancel; `cancelAllOrders` costs 1,000). **Effective cap = start + lifetime realised notional USD ÷ 0.10** (every $1 filled adds 10 units; never decays). When empty, a drip allows **1 action per 10 s**. Check with `GET /v1/rateLimit?address=&accountIndex=` (`used`, `cap`, `nextAvailableMs`); write responses also carry `rateLimit.remaining`.
- **429 body** tells you which bucket: no `reason` = IP on a read; `reason: ip` = IP on a write; `account_empty` = pool exhausted; `account_partial` = batch too large. Use `retryAfterMs`.
- **WebSocket:** 50 connections/IP, 50 new/min, 100 subscriptions/connection, 1,000 per IP, 1,000 outbound subscribe messages/min per IP, 50 in-flight `post` per connection, **24 h connection lifetime**; server sends close code 1001 before restart drains.

### A3.7 Latency and hosting

~20 ms trade confirmations; exchange hosted in **Asia**; clients need ≥ 2 cores / 4 GB (4 / 8 preferred); trade over the WebSocket; use `ALO` for maker flow; use clientIds.

### A3.8 WebSocket

- Envelopes: `{"type":"subscribe","channel":"<name>","id":"<sub id>"}` plus optional `snapshot`, `nLevels` (1–100, default 20), `nRecentClosed`, `nFills`, `accountIndex` (0–9), `market`. The `subscribed` reply carries the initial snapshot; updates are `channel_data`. RPC: `{"type":"get"|"post","id":<int>,"request":{"type":"<method>","payload":{…}}, "apiKey","timestamp","signature"}`.
- **Subscriptions are never authenticated.** Anyone can read any address's balances, positions, orders and fills. Account channels take `address` (+ `accountIndex`).
- Channels (confirm exact names on each docs page): market `l2Orderbook`, `l2OrderbookUpdates`, `bbo`, `trades`, `markets`, `marketAttributes` (global, full block per update), `oraclePrices`, `predictedFunding` (`{market, rate1h}`), `candles`, `lendingRates`, `exchangeAttributeUpdates`; account `account` (re-snapshots every 5 s), `positions`, `orders`, `userFills`, `funding`, `accountTransferUpdates`, `accountAttributeUpdates`.
- Sequences: order book per-market `lastSequenceId` is contiguous on `l2OrderbookUpdates` once applying deltas; the first delta after a snapshot may skip ahead (expected; do not resubscribe); a **mid-stream gap** means resubscribe. A delta may list the same price twice: apply in order, last write wins. `bbo` sequence jumps are normal. `globalSequenceId` is an ordering key only. L2 publishes every 200 ms.

### A3.9 Prices, funding, margin, liquidation, sessions

- **Mark price** (sole reference for margin, PnL, liquidation). With a live oracle: `avg( median(last, bestBid, bestAsk), oracle + EWMA_2.5min(impactMid − oracle) )`, clamped to `oracle × (1 ± min(10/maxLeverage, 1)%)`. RWA off-hours: `EWMA_2.5min(impactMid)` inside the price bands. **Oracle:** Pyth (`pythId`); RWA same-day adjustment `oracle / (1 + sofr_rate_hourly × 24 × days_to_settle)`.
- **Funding:** charged **hourly on the hour UTC**; premium sampled ~once a minute; **hour's premium = MEDIAN of samples**; `premium = (max(impactBid − oracle, 0) − max(oracle − impactAsk, 0)) / oracle`.
  - Crypto: `rate_h = clamp( premium/8 + clamp(0.0000125 − premium/8, ±0.0000625), ±4% )` (base 0.01%/8h, dead-band ±0.05%/8h).
  - RWA: `rate_h = clamp( sofr_rate_hourly + premium/8, ±4% )`, `sofr_rate_hourly = (SOFR + 0.5%)/360/24` (≈ 4.4%/yr in Sept 2026; SPY base seen `0.000005034722`/h). No dead-band.
  - RWA off-hours (outside 04:00–20:00 ET, weekends, US holidays): funding **locked to the base rate**.
  - Payment = `rate × size × ORACLE price`; positive = longs pay. Dividends reach longs through funding; splits adjust positions.
  - Data: `GET /v1/fundingRates?market=&from=&to=&limit≤1000` (µs; fraction per hour; newest first). History starts **2026-06-24 05:00 UTC**. Forecast: `nextFundingRate`, WS `predictedFunding`.
- **Margin:** leverage = 1/IMF, up to 50x. Cross by default; per-market isolated available (`setLeverage` with `isolated`, `adjustIsolatedMargin`). RWA off-hours IMF = `offHoursInitialMarginFraction`; maintenance unchanged; `freeCollateral` drops at the boundary without any trade (not an error). You cannot open/increase below the off-hours IMF, but positions are not liquidated for it.
- **Liquidation:** when equity ≤ maintenance margin (mark-valued). Liquidation fee and partial vs full: **undocumented** [VERIFY].
- **Off-hours price bands (RWA):** anchor = VWAP sealed at 20:00 ET (`currentSettlementPrice`; Friday's anchor carries the weekend). `bound = anchor × (1 ± IMF × m)`, `m` on ladder 0.5 → 1 → 2 → 4 per side. A side widens one rung after its 1-hour impact-price EWMA stays at or beyond 90% of the way to the edge for a continuous hour with depth present. Bands never contract within a session. Fills beyond a bound are rejected `FILL_WILL_EXCEED_TRADING_BOUND`. Stream: `marketAttributes`.
- **OI caps:** at the cap, OI-increasing fills are rejected (`OPEN_INTEREST_CAP_EXCEEDED`). Seen: SPY $2M, NVDA $0.5M, TSLA $0.25M, HOOD $4M, GLD $1M [LIVE].

### A3.10 Accounts, data, compliance

- **Subaccounts:** `accountIndex` 0–9 per wallet, created implicitly on first use; each has its own collateral, rate pools and fee-tier volume. Move funds with `POST /v1/transfer` (EIP-712, "Arcus Transfer" domain, single-use ns nonce, async `PENDING` → outcome on `accountTransferUpdates`). API-only (not in the web UI).
- **Historical data:** trades `GET /v1/trades?market=&from=&to=&limit≤1000` (µs; includes `side`, `price`, `size`, `timestamp`, **`takerAddress`, `makerAddress`**, `makerOrderId`, `sequenceNumber`; pages overlap, dedupe by `tradeId`; history back to about July 2026). Candles `GET /v1/candles` (≤ 1,500 bars; **OHLC oracle-priced**; volume fields trade-derived). Order book: `GET /v1/l2OrderBook` snapshot only (≤ 100 levels) → **record live**.
- **Compliance:** `GET /v1/compliance` returns `geo.country`, `geo.restrictions.{perpetuals,spot}` for the calling IP. Not available in the US, Canada, UK and other restricted jurisdictions. Restricted IPs can read but not write (`403 GEO_RESTRICTED`).
- **Points:** Arcus has **no published points program**; a token is confirmed with a dYdX community allocation. Value Arcus activity at $0 in backtests.

## A4. Venue facts: Lighter on Robinhood Chain (Lighter RH)

A separate Lighter deployment on Robinhood Chain (not the main Lighter at `mainnet.zklighter.elliot.ai`; do not mix them up). zk-proven CLOB, price-time priority, fills at the maker's price. Collateral USDG. General docs: `https://docs.lighter.xyz`, API docs `https://apidocs.lighter.xyz` (written for main Lighter; the RH deployment uses the same API shape at a different host).

### A4.1 Endpoints

| Item | Value |
| --- | --- |
| REST | `https://api.rh.lighter.xyz` |
| WebSocket | `wss://api.rh.lighter.xyz/stream` |
| App | `https://robinhoodchain.lighter.xyz` |
| Signing chain ID (RH) | `466324` (from delta-farmer's signer) [VERIFY] |

### A4.2 Account model and signing (from delta-farmer's MIT client; [VERIFY] against the official `lighter-python` SDK)

- An L2 `account_index` belongs to your wallet (L1 address). API keys occupy slots (`api_key_index`; delta-farmer uses slot **3**). Register an API key with a **change-pub-key** transaction (tx type 8) that the **wallet** also signs.
- Private REST reads and account WebSocket channels use an **auth token** created by the API-key signer (delta-farmer: 7-hour lifetime, refreshed 10 min before expiry) sent as `Authorization: <token>` (+ `PreferAuthServer: true`).
- Transaction types: **8** change pub key, **12** transfer, **14** create order, **15** cancel order, **16** cancel all orders, **20** update leverage, **45** approve integrator.
- Send: `POST /api/v1/sendTx` as multipart with fields `tx_type`, `tx_info` (compact JSON), `price_protection`; or `POST /api/v1/sendTxBatch`; or WebSocket `{"type":"jsonapi/sendtx","data":{"id","tx_type","tx_info"}}` / `{"type":"jsonapi/sendtxbatch","data":{"id","tx_types","tx_infos"}}` (**≤ 15 transactions per WS batch**).
- Nonces: delta-farmer uses time-based millisecond nonces `max(now_ms, last + 1)` with `expired_at = nonce + 599,000 ms`; `GET /api/v1/nextNonce?account_index=&api_key_index=` exists. Limit orders: delta-farmer sets `order_expiry = nonce + 28 days`; market orders expiry 0.
- Order encoding: `base_amount = int(size / multiplier × 10^size_decimals)`, `price = int(price × multiplier × 10^price_decimals)` (`multiplier` defaults to 1), `is_ask` boolean, `order_type` **0 limit, 1 market**, time-in-force **0 IOC, 1 GTT**, post-only code **2** in the official SDK [VERIFY], reduce-only flag, trigger price 0.
- **Integrator approval:** the RH system config has a `fee_collector_account_index`; when non-zero, delta-farmer sends an approve-integrator tx (type 45) with `max_integrator_perps_taker_fee`, `max_integrator_perps_maker_fee` (and spot) caps before trading. Determine whether this is required for API trading and what fee it allows [QUESTION FOR OWNER if it implies a fee].
- **Deposits:** USDG on Robinhood Chain; delta-farmer uses deposit contract `0x94bAB9693Ba2f6358507eFfcbd372b0660AFfF9d`, USDG asset index 3 [VERIFY in official docs before any transfer].

### A4.3 Account types

| Type | Maker / taker fee | Latency | Limits |
| --- | --- | --- | --- |
| **Standard** (default, ours) | **0 / 0** on all RH markets (live check 2026-09-23: 126 markets) | maker **0 ms** (API docs) or **200 ms** (main docs) [VERIFY by measurement]; taker **300 ms**; cancel/modify **300 ms** | 60 weighted REST/min; 60 sendTx/min; 4 sub-accounts |
| Plus | 0.5 bps each side | taker 300 ms; maker and cancel 200 ms | 4,000 sendTx/min; 24,000 weighted reads/min |
| Premium | 0.4 bps maker / 2.8 bps taker with no LIT staked (down to 0.28 / 1.96 bps) | maker and cancel 0 ms; taker 140 ms | 4,000–48,000 sendTx/min by LIT staked |

Switch with `/changeAccountTier` (downgrades have a 24 h cooldown). Only test Plus/Premium if the backtest proves standard limits cost more than those fees.

### A4.4 Rate limits (standard)

- REST weighted budget **60/min** (weights: `sendTx`, `sendTxBatch`, `nextNonce` 6; `publicPools`, `txFromL1TxHash` 50; `accountActiveOrders`, `accountInactiveOrders`, `accountOrders`, `deposit/latest` 100; `exchangeMetrics` 120; `apikeys` 150; `trades` 200; `recentTrades` 600; `transferFeeInfo` 500; others 300). **This table implies a standard account can barely read at all over REST** (a single `recentTrades` exceeds 60), so the real semantics are unclear [VERIFY]: build an **adaptive throttle** (start at 1 REST call per 10 s per host, back off on 429/405, log every limit hit) and use the WebSocket for everything possible.
- sendTx/sendTxBatch: 60/min (standard). Per-transaction-type default 40/min (update leverage 40/min, transfer 120/min, withdraw 2/min).
- Orders: **pending 50/account, 10/market; active 250/account, 30/market**.
- WebSocket (per IP): 255 connections, 255 new/min, 500 subscriptions/connection, 500 unique accounts/connection, **200 client messages/min**, 50 in-flight; **send a frame at least every 2 minutes** (`{"type":"ping"}` → `{"type":"pong"}`).
- Exceeding: HTTP 429 or 405; firewall cooldown 60 s.

### A4.5 WebSocket channels

`{"type":"subscribe","channel":"<name>","auth":"<token if account channel>"}`. Public: `order_book/{market_id}`, `ticker/{market_id}`, `market_stats/{market_id}` or `market_stats/all`, `trade/{market_id}`, `candle/{market_id}/{res}`, `mark_price_candle/{market_id}/{res}`, `height`. Account (auth): `account_all/{account}`, `account_market/{market}/{account}`, `user_stats/{account}`, `account_tx/{account}`, `account_all_orders/{account}`, `account_orders/{market}/{account}`, `account_all_trades/{account}`, `account_all_positions/{account}`, `account_all_assets/{account}`, `notification/{account}`.

### A4.6 Markets [LIVE]

`GET /api/v1/orderBooks` (per market: `symbol`, `market_id`, `market_type` perp/spot, `status`, `taker_fee`, `maker_fee`, `min_base_amount`, `min_quote_amount` = **$10**, `supported_size_decimals`, `supported_price_decimals`) and `GET /api/v1/orderBookDetails?market_id=` (margin fractions in 1/10,000, funding parameters, `liquidation_fee`). Market IDs differ from Arcus (e.g. Lighter ETH = 0, BTC = 1, SPY = 26, QQQ = 25, NVDA = 15, TSLA = 16, HYPE = 2, SOL = 3) — **map symbols by base asset at start-up; never hard-code IDs**.

Margins (2026-09-22): BTC, ETH, SPY, QQQ IMF 2% / MMF 1.2% / close-out 0.8% (50x); HYPE, NVDA, TSLA, AAPL 5% / 3% / 2%; CRCL, COIN 10% / 6% / 4%; XAU 4%; LIT 20%. Daily quote volume: BTC ~$153M, ETH ~$122M, SPY ~$122M, QQQ ~$82M, XAU ~$45M, LIT ~$14M, HYPE ~$12M, NVDA ~$7M. Lighter RH also lists pre-IPO perps (ANTHROPIC, OPENAI, SHEIN) that Arcus does not.

**Markets on both venues (34):** crypto BTC, ETH, SOL, HYPE, ZEC, LIT, XRP, NEAR, CASHCAT; equities AMD, INTC, GOOGL, META, MU, BABA, CRCL, NVDA, TSLA, AAPL, AMZN, MSFT, SNDK, PLTR, CRWV, ORCL, SPCX, BE, USAR, COIN, SKHY; ETFs SLV, USO, SPY, QQQ. Arcus GLD vs Lighter XAU needs a ratio hedge (excluded in v1). **Phase-1 universe: BTC, ETH, SOL, HYPE, SPY, QQQ, NVDA, TSLA.**

### A4.7 Prices, funding, liquidation

- **Crypto mark:** `median(ImpactPrice, index + EMA_8min(clamp(impact − index, ±0.5% × index)), median CEX marks)`; impact notional `500 USDC / IMF`.
- **RWA:** oracle (Chainlink, Pyth, Stork, venues) when fresh; else internal pricing: impact-price EMA τ = 30 min (index) and 2 min (mark), capped at `oracle × (1 ± 1/L)`; since July 2026 SPY, QQQ, TSLA, AAPL, MSFT and others have no oracle caps. Leverage does **not** change off-hours.
- **Funding:** hourly on the hour; premium sampled once a minute at a random second; hour premium = **MEAN** of samples × multiplier (crypto 1.0, RWA 0.5, pre-IPO 0.01):
  ```
  premium_t = (max(0, ImpactBid − index) − max(0, index − ImpactAsk)) / index
  premium   = mean(premium_t) × M
  smallClamp = 0.05% × M
  scp  = premium + clamp(InterestRate − premium, −smallClamp, +smallClamp)
  rate = clamp(scp, −4%, +4%) / 8          # |rate| ≤ 0.5%/h
  payment_i = −position_i × INDEX × rate   # positive rate: longs pay
  ```
  `InterestRate` is per market (`base_interest_rate`, % per 8 h): BTC 0.0100 (→ 0.00125%/h), **SPY 0.0032** (→ 0.0004%/h ≈ 3.5%/yr). SPY dead-band: rate = base while raw premium ∈ [−0.0436%, +0.0564%].
  - Data: `GET /api/v1/fundings?market_id=&resolution=1h&start_timestamp=&end_timestamp=&count_back=` (seconds, ≤ 750). **`rate` is in percent rounded to 4 decimals; rebuild precise hourly rate as `value / index_price`** (`value` = USD per 1 base unit; SPY check: 0.0030498 / 773.3 = 3.94e-6 ✓). `direction: "long"` = longs pay. History starts ~**2026-06-26 07:00 UTC**.
- **Liquidation:** below IMF → reduce-only; below MMF → **partial liquidation**: all orders cancelled, IOC at the zero price `ZP = Mark × (1 − MMF × AccountValue / MaintenanceMarginReq)` (long) until back above MM; **fee up to 1%**; below close-out MF → full LLP takeover; ADL if LLP insufficient.
- **Historical data:** `/api/v1/recentTrades?market_id=&limit≤100`, `/api/v1/trades` (cursor), `/api/v1/candles?market_id=&resolution=&start_timestamp=&end_timestamp=&count_back=` (≤ 500; trade-based; zero-valued fields omitted); paid parquet via `historicalTrade` (100 LIT transfer; RH coverage unknown). Order books: **record live**.

### A4.8 Points program and rules

- Points in real time plus a weekly drop every Friday (first 2026-08-21). Reported pool ~11M LIT. delta-farmer counts campaign volume from 2026-09-01.
- Community reports (unverified): OI matters more than volume; stocks earn more than crypto; ~12 h holds; volume spam earns ~0; **Robinhood Wallet trades 2x vs web app 1x** (API orders probably 1x [QUESTION FOR OWNER: confirm with Lighter]).
- **Banned:** wash trading; self-trading or trading between commonly controlled accounts; inflating volume or liquidity; spoofing, layering, quote stuffing; multiple accounts or split activity; self/circular referrals; "using bots, scripts, or automated systems to manipulate Program metrics"; bug exploits; fraud. Points can be reversed.
- **Restricted regions (18):** Belarus, Canada, China, Cuba, Iran, Myanmar, North Korea, Russia, **Singapore**, South Sudan, Sudan, Switzerland, Syria, Ukraine, UAE, UK, US, Venezuela. Arcus recommends Asia hosting, so pick an Asian region **not** on this list (e.g. Tokyo) and verify both venues from that IP.

## A5. Tread.fi behaviours to replicate (definitions used by every phase)

Tread's engine is closed-source; ArcLight re-creates the observable behaviour with its own exact logic (A6).

| Tread feature | Observed behaviour | Typical user settings | ArcLight implementation |
| --- | --- | --- | --- |
| **Mid** | Two-sided maker quotes around mid ± offset (bps); negative offset quotes inside the spread | Mid−1/−2, Neutral, Aggressive, SL 5–10%, lev < 10x | 1–3 levels around skewed reservation price; hysteresis requotes. Arcus only |
| **Grid** | Buy levels below, sell above; filled buy re-lists as sell one step up; reset threshold re-centres | Grid −1…+3 bps, Normal, reset 0.125–0.25% | Geometric N-level grid, spacing δ, inventory cap, re-centre rule |
| **RGrid** | Grid that moves with price; for trends/volatility; some taker orders | RGrid +1/+2, Normal/Aggressive, reset 0.125–0.25%, SL 10%, TP uncapped | Trailing grid on EMA centre; cut wrong-side inventory (maker then IOC); optional trend tilt |
| **DGrid** | No settings; picks Grid vs RGrid and spread from volatility regime | TP 10% / SL 10% / Normal | Autopilot sub-mode: regime classifier + volatility-scaled spacing |
| **Blend** | Quotes around an external reference price ± 10/25/50 bps | Thin books | Reference = weighted other-venue mid + Pyth oracle + local mid, basis-corrected, staleness guard |
| **Signal** | RSI/indicator-gated trades; good in slow markets | Tight SL/TP | RSI(14) 1-min mean reversion, maker entries, trend filter, cooldown |
| **Execution style** | Aggressive / Normal / Passive | — | Anchor: improve/join best · r ± max(h, spread/2) · r ± (h + k·σ) |
| **Bias** | Neutral / Long skew / Short skew, front-loaded in first half, unwound by end | Neutral | Target-inventory path I*(t) |
| **Participation** | 80–90% of market volume = trading against yourself | Low | Cap our share of market volume over 5 min (default 25%) |
| **Risk** | Margin-based SL: max loss = margin × SL%; keep 10–20% extra inventory buffer; pause on fast moves | SL 5–10% | Same + inventory stop + liquidation-distance guard + safety pause |
| **Duration / repeat / campaign** | Timed runs, repeat N× (20× seen for DN), time-slot campaigns | 20× | Session scheduler with optional IST time-window file |
| **DN bot** | Opposite legs on two venues, maker entry/exit, matched fills, auto-unwind at X% from liquidation, target duration, repeat | Ondo ↔ TradeXYZ | Cross-venue module (A6.6) |
| **Algo suite** | TWAP, VWAP, POV, maker-only, iceberg | Maker TWAP for DN | Internal maker-TWAP and POV cap |
| **Builder fee** | 2.5 bps at $0–1M 14-day volume … 1 bp above $100M; TREAD discounts | — | None (own bot) |

## A6. Core math (implement exactly; unit-test each)

**A6.1 PnL decomposition** (every run reports each term separately):
```
NetPnL = SpreadCapture + InventoryMTM + Funding − Fees − HedgeCost − LiquidationLoss
Funding = Σ_h −position_h × P_pay,h × rate_h     # P_pay: Arcus oracle, Lighter index
CPM (cost per $1M volume) = −NetPnL / Volume × 1e6
```

**A6.2 Grid economics.** Round trip at spacing δ earns ≈ `q·P·δ − 2·q·P·f_m`. A k-level trend that does not revert leaves ≈ `q·P·δ·k(k+1)/2` unrealised loss. Break-even requires `E[#round trips] > E[k(k+1)/2]`. Volume = 2 × q × round trips.

**A6.3 Quoting core.** Inputs: mid `m`, half-spread `h` (fraction), inventory `I`, target `I*`, cap `I_cap` (USD), skew strength `κ ∈ [0, 2]`.
```
u     = clamp((I − I*) × m / I_cap, −1, 1)
r     = m × (1 − κ × u × h)                    # reservation price
q_bid = q × max(0, 1 − u);  q_ask = q × max(0, 1 + u)
```
Execution anchors: Aggressive = improve best by 1 tick if spread > 1 tick else join; Normal = `r ± max(h, spread/2)`; Passive = `r ± (h + k_p·σ_1m)`. Tread-style offset (bps) added; negative = inward. Post-only guard: bid < best ask, ask > best bid. Rounding: **bids round down, asks round up** to the tick of the price band.

**A6.4 DGrid spacing.** A random walk with hourly vol `σ_1h` crosses a level of spacing δ about `(σ_1h/δ)²` times per hour, so for target fills per hour `F`:
```
δ = clamp(k_δ × σ_1h / sqrt(F), δ_min, δ_max),   δ_min ≥ max(2·f_m + 1 bp, 2 ticks)
Lighter standard: δ_min ≥ 3 × σ over 300 ms as well (stale-quote protection)
```

**A6.5 Diagnostics.**
```
markout(τ)     = s × (Mid_{t+τ} − p_fill) / p_fill,  s = +1 buy / −1 sell,  τ ∈ {1,5,30,60,300} s
ER (Kaufman)   = |P_t − P_{t−n}| / Σ_{i} |P_i − P_{i−1}|,  n = 60 one-minute bars
trend_z        = (EMA20 − EMA60) / (P × σ_1h)
VarianceRatio  = Var(15-min returns) / (15 × Var(1-min returns))
OER(δ, W)      = (# δ-level crossings that reverse within W) / (net |excursion| / δ over W)
HHI (takers)   = Σ share_i²  over Arcus takerAddress volume shares
```

**A6.6 Cross-venue delta-neutral.**
```
carry_AL(h) = −r_A(h) × S × P_A^oracle + r_L(h) × S × P_L^index     # LONG Arcus / SHORT Lighter; reverse = −carry_AL
B_exec(LONG A / SHORT L) = (ask_A_eff − bid_L_eff) / mid            # size-weighted impact at planned size
EV_d(H) = H × spread_fcst_d(H) + E[basis convergence]_d − c_entry − E[c_exit] − λ × Risk_d(H)
Enter if max_H EV_d(H)/H chosen H* has EV_d(H*) > θ_enter, margins OK, venues healthy
```
Regimes: RWA in session with premium p above Lighter dead-band → Arcus `base + p/8` vs Lighter `(0.5p − 0.025%)/8` → **short Arcus / long Lighter**; discount → long Arcus / short Lighter; Arcus locked off-hours vs Lighter premium-driven → follow Lighter's sign; quiet RWA → short A / long L earns ~0.9%/yr (too small alone); crypto inside both dead-bands → no carry. Worked example (2026-09-21/22): Lighter SPY 0.0018%/h vs Arcus base → spread ≈ 0.0013%/h ≈ 11.4%/yr → break-even hold 39 / 77 / 154 / 231 h for 5 / 10 / 20 / 30 bps round-trip cost. The edge must come from timing and cheap execution.

**A6.7 Liquidation distance.** For a leg with collateral C, notional N, maintenance fraction MMF: adverse move to liquidation ≈ `(C − N × MMF) / N` (e.g. C = $25, N = $75, MMF 1.2% → 32%). Express also in σ units of 1-hour returns.

**A6.8 Arcus order budget.** `cap = 20,000 + lifetime_fill_notional_usd / 0.10`. A $6 fill funds 60 actions. Governor target: order actions per filled dollar < 8.

## A7. Units and conventions

- Internal time: **int64 microseconds UTC**. Arcus signing timestamps: ns. Arcus query `from`/`to`: µs (ms rejected). Lighter REST timestamps: seconds; Lighter `transaction_time`: µs; Lighter nonces: ms.
- Prices and sizes: `Decimal` at API boundaries; integer ticks/quantums internally; **never float for order prices or sizes**.
- Rates: store funding as a **fraction per hour**.
- Symbols: canonical `BASE` (e.g. `BTC`, `SPY`); venue symbols mapped at start-up.

## A8. Known unknowns (measure or ask; never assume)

1. Lighter RH API orders: 1x or 2x points? Is a genuine hedging bot acceptable under rule D? (ask Lighter in writing)
2. Lighter standard: does sendTx share the 60/min REST budget (→ ~10 tx/min) or have its own 60? Max txs per REST `sendTxBatch`? Do WS `jsonapi/sendtx` messages count against the 200 msgs/min WS limit?
3. Lighter standard maker latency 0 ms or 200 ms.
4. Lighter RH integrator approval: required for API trading? fee cap?
5. Lighter RH: parquet history coverage; dividend/split treatment on equity perps; hours of internal pricing; live predicted-funding field names.
6. Arcus: real starting order pool for a new subaccount; liquidation fee and mechanics; per-market impact size for the funding premium; `/v1/trades` history depth.
7. Arcus `scheduleCancel` behaviour (docs conflict).
8. Arcus maker rebates "paid in epochs" (third-party review) vs fee-tier table; watch for a points program.
9. USDG transfer time and cost between venues; Robinhood Chain gas token.
10. Which server region passes both venues' geo checks.
11. Lighter points formula (fit from own weekly points vs volume, OI-hours, hold time).
12. Whether Arcus's 20:00 ET anchor/lock coincides with a funding tick; partial-hour treatment.

## A9. Source links

Arcus: docs.arcus.xyz (concepts/perpetuals/{fees,funding,margin,prices,liquidations,order-types,real-world-assets}, concepts/subaccounts, api-reference/{authentication,rate-limits,websocket}, api-reference/exchange/*, api-reference/public/*, guides/{latency,rest-trading,websocket-trading,fund-testnet-account}, changelog, changelog/testnet); live `api.arcus.xyz/v1/feetiers`, `/v1/markets`.
Lighter: docs.lighter.xyz (trading/{funding,trading-fees,order-types-and-matching,liquidations-and-llp-insurance-fund,fair-price-marking,contract-specifications}, trading/real-world-assets-rwas/rwa-pricing-mechanism, points-program/lighter-on-robinhood-chain-points); apidocs.lighter.xyz (docs/{account-types,rate-limits,historical-data,websocket-reference,volume-quota-program}); live `api.rh.lighter.xyz/api/v1/orderBooks`, `/orderBookDetails`, `/fundings`.
Reference code: github.com/vladkens/delta-farmer (MIT) `clients/lighter.py`, `lib/lighter_crypto/{signer,poseidon,field}.py`. Official Lighter SDK: `lighter-python` (check its signer constants and chain-ID support).
Tread.fi: docs.tread.fi/bots/market-maker-bot; builder fee tiers (TIP-1).

---
# PART B: ENGINEERING STANDARDS (paste into every phase)

## B1. Stack

- **Python 3.12**, managed with **uv** (`uv sync --locked`). Async with `asyncio` + `uvloop`.
- HTTP/WS: `aiohttp` (or `httpx` + `websockets`); JSON: `orjson`. Config: `pydantic` v2 + YAML (`ruamel.yaml` or `pyyaml`).
- Crypto: `PyNaCl` (or `cryptography`) for Ed25519; `eth-account` for EIP-712 and Ethereum signing; the Lighter signer ported from delta-farmer `lib/lighter_crypto/` (Poseidon/Schnorr, MIT) **or** the official `lighter-python` signer if it supports the RH chain ID. Record which, and why, in the hand-off.
- Data: `pyarrow` + `polars` for Parquet (zstd), `duckdb` for research queries, `numpy`; `numba` for simulator hot loops.
- State and ledger: SQLite (WAL mode).
- Quality: `ruff` (lint + format), `mypy --strict` on `core/`, `venues/`, `strategies/`, `autopilot/`; `pytest`, `pytest-asyncio`, `hypothesis` for property tests.
- Ops: systemd units, a `Makefile`, GitHub Actions CI (lint, type, unit tests).

## B2. Repository layout (create exactly; extend only with reason)

```
arclight/
  pyproject.toml  uv.lock  Makefile  README.md  .env.example
  docs/                       PROMPT_PACK.md, spec, theses, reference/delta-farmer/
  handoffs/                   PHASE_N_HANDOFF.md
  config/
    venues/arcus.yaml  venues/lighter_rh.yaml       # static facts + [LIVE] field list
    universe.yaml                                   # canonical symbols to trade/record
    sessions/*.yaml                                 # bot sessions (Appendix E4)
    calendars/events.csv  calendars/earnings.csv  calendars/exdiv.csv  calendars/nyse_holidays.csv
    secrets.enc                                     # encrypted; never committed
  arclight/
    common/     time.py decimal.py ids.py logging.py config.py secrets.py errors.py
    venues/     base.py  symbols.py
                arcus/{rest.py, ws.py, signing.py, eip712.py, models.py, adapter.py}
                lighter_rh/{rest.py, ws.py, signer/, auth.py, nonce.py, models.py, adapter.py}
                paper/adapter.py   sim/adapter.py
    core/       marketdata.py book.py state.py order_manager.py budget.py risk.py
                guardian.py dms.py ledger.py alerts.py scheduler.py calendar.py
    strategies/ base.py quoting.py mid.py grid.py rgrid.py dgrid.py blend.py signal.py
                dn_hedged_mm.py dn_carry.py points_overlay.py
    autopilot/  features.py regime.py params.py allocator.py bandit.py
    research/   recorder/ loaders/ diagnostics/ sim/ eval/ reports/
    cli.py
  tests/        unit/ integration/ fixtures/
  deploy/       systemd/*.service  scripts/
```

## B3. Code rules

1. Type hints everywhere; dataclasses (`slots=True`, `frozen=True` where possible) for models.
2. **Money:** `Decimal` at every API boundary; integer ticks and quantums inside the engine; conversions in `common/decimal.py` with exactness checks (raise if `price / tick` is not an integer after rounding).
3. **Time:** one clock module. Internal int64 µs UTC. Monotonic clock for latency measurement. Log NTP offset every 10 minutes.
4. **Idempotency:** every order carries a unique `clientId` (Arcus charset `[A-Za-z0-9_-]`, ≤ 36 chars). Format: `al{strategyCode}{sessionId base36}{seq base36}`.
5. **Every network call** has a timeout, a bounded retry with exponential backoff and jitter, and a typed error. Never retry a non-idempotent write blindly: reconcile state first.
6. **No silent exception swallowing.** Unexpected errors in the trading loop trigger the risk engine's safe mode (cancel quotes, keep positions, alert).
7. **Config over constants.** All thresholds live in YAML with defaults in pydantic models; [LIVE] venue values are fetched, never typed in code.
8. **Structured JSON logs** with fields `ts, level, component, venue, market, session, event, reason, data`. A separate **decision log** stream records every mode change, parameter change, order intent and cancel reason.
9. **Secrets:** stored in `config/secrets.enc` (Fernet or age, key derived with scrypt from `ARCLIGHT_SECRETS_PASSWORD`). A redaction filter strips anything that looks like a key or token from logs. Never print secrets in exceptions.
10. Keep modules small and testable; strategy code must be pure (inputs → desired book) so the same code runs in sim, paper and live.

## B4. The triple lock for live orders

A real (mainnet) order may be sent only if **all three** are true:
1. The CLI was started with `--live`.
2. The session config has `live_enabled: true`.
3. The environment has `ARCLIGHT_LIVE=1`.

Otherwise the adapter factory returns the **paper** adapter (P2) or the **testnet** adapter. A unit test must prove that no code path reaches a mainnet write without all three. Any phase that needs a real order has a **[HUMAN GATE]**: print exactly what will be sent (venue, market, side, size, price, notional, distance from mid) and wait for the owner to type `CONFIRM`.

## B5. Testing rules

- Unit tests run offline using recorded fixtures (`tests/fixtures/`). Network tests are marked `@pytest.mark.network` and excluded by default.
- Every formula in Part A has a unit test that reproduces the documented worked example.
- Property tests (hypothesis) for: tick/step rounding, canonical JSON signing payloads (key order, compactness, lowercase address, clientId omission), order-manager diffing (never produces a crossing post-only order, never exceeds budgets).
- Coverage target: ≥ 85% on `core/`, `venues/`, `strategies/`, `autopilot/`.
- CI must be green before a phase is declared done.

## B6. Definition of done (every phase)

1. All acceptance tests in the phase prompt pass, with command lines and outputs recorded in the hand-off.
2. `make lint type test` is green.
3. README updated with how to run what was built.
4. `handoffs/PHASE_N_HANDOFF.md` written using Appendix E6, including: what was built, how to run it, test evidence, deviations from this pack, measured values for any [VERIFY] or known-unknown item touched, open issues, and `[QUESTION FOR OWNER]` items.
5. No secrets committed (`git log -p | grep -i` checks for key patterns in CI).

## B7. Working style

- Read the relevant docs pages before implementing an endpoint; cite the page in a code comment.
- When a doc and the live API disagree, write a small probe script, record the observed behaviour, and follow the live API.
- Prefer small, reviewable commits (`feat(arcus): scheme-1 signing`, `test(sim): queue fill model`).
- If blocked for more than ~30 minutes on a venue behaviour, write the question into the hand-off and continue with the next task.

---
# PART C: PHASE PROMPTS

Each prompt below is self-contained on top of Parts A and B. Paste A + B + one phase.

---

## PHASE 0: Scaffold, config, public data clients and the recorder

```text
ROLE
You are building Phase 0 of ArcLight (see Parts A and B). This phase uses PUBLIC data only. No API keys, no orders.

OBJECTIVE
Create the repository, the config and secrets system, read-only REST and WebSocket clients for Arcus and Lighter RH, and a
24/7 market-data RECORDER writing Parquet. Start the recorder as early as possible: every day not recorded is lost, because
neither venue publishes historical order books.

INPUTS
- docs/PROMPT_PACK.md (Parts A, B, E), docs/ spec and theses, docs/reference/delta-farmer/.
- Appendix E3 (Parquet schemas), E5 (venue config skeletons).

TASKS
1. Scaffold the repo exactly as in B2: pyproject (uv), Makefile targets (install, lint, type, test, run-recorder, dq-report),
   ruff + mypy config, pytest config, GitHub Actions CI, .env.example, README.
2. common/: time.py (int64 µs UTC, conversions s/ms/µs/ns, NTP offset check via `ntplib` or chrony output),
   decimal.py (tick/step rounding: bids floor, asks ceil, tick tiers; exactness checks), logging.py (JSON + redaction),
   config.py (pydantic models + YAML loader), secrets.py (encrypted store; CLI `arclight secrets init|set|list-redacted`).
3. Venue config: config/venues/arcus.yaml and lighter_rh.yaml hold STATIC facts (hosts, chain IDs, documented formulas'
   constants, rate-limit numbers) and the LIST of [LIVE] fields. A `LiveParams` service fetches markets, fee tiers
   (Arcus), orderBooks + orderBookDetails (Lighter) at start-up and hourly; diffs against the last snapshot; writes every
   change to `data/param_changes/` (Parquet) and logs it.
4. Symbol mapping (venues/symbols.py): build canonical BASE → {arcus: marketId + displayName, lighter: market_id + symbol}
   from the APIs at start-up. Never hard-code IDs. Report unmatched and ambiguous symbols (e.g. GLD vs XAU).
5. Arcus public clients:
   - REST: GET /, /v1/markets, /v1/feetiers, /v1/fundingRates, /v1/trades, /v1/candles, /v1/l2OrderBook, /v1/prices,
     /v1/compliance. Client-side IP-weight accounting (A3.6) so the recorder never exceeds 50% of the 1,500/min bucket.
   - WebSocket: subscribe per universe market to l2OrderbookUpdates (nLevels=100), bbo, trades; globally to
     marketAttributes, markets; plus oraclePrices and predictedFunding. Book rebuild with the splice rule (A3.8): seed from
     snapshot, apply deltas with lastSequenceId > snapshot's, tolerate the first-delta boundary gap, resubscribe on a
     mid-stream gap, last-write-wins for duplicate prices in one frame. Reconnect with backoff; proactively reconnect before
     the 24 h lifetime; handle close code 1001.
6. Lighter RH public clients:
   - REST: /api/v1/orderBooks, /orderBookDetails, /fundings, /candles, /recentTrades, /trades, with an ADAPTIVE throttle
     (A4.4): start at 1 call per 10 s per host, back off on 429/405, log every limit event, persist learned safe rates.
   - WebSocket: order_book/{id}, trade/{id}, market_stats/{id} (or market_stats/all), ticker/{id}. Send {"type":"ping"}
     every 60 s (limit is 2 min). Keep total client messages under 150/min. Confirm the order_book message format
     (snapshot vs delta, sequence/nonce fields) from live messages and implement the correct rebuild; write the observed
     format into docs/notes/lighter_ws_formats.md.
7. Recorder (research/recorder/):
   - Tables (Appendix E3): book_deltas, book_snapshots (every 60 s, top 100 levels), bbo, trades, mark_oracle_index,
     funding_pred, funding_paid, market_attrs, param_changes, latency_probe (placeholder), clock_offset, recorder_health.
   - Partitioning: data/{table}/venue={v}/market={BASE}/date={YYYY-MM-DD}/part-{HH}-{uuid}.parquet, zstd, flush every
     30 s or 50k rows, write-to-temp then atomic rename, hourly file roll.
   - Every row carries recv_ts_us (local, µs) and the venue's own timestamp and sequence fields.
   - Health: per market per channel "last message age"; write recorder_health every minute; alert hook (stdout + optional
     Telegram if a token exists) when any stream is silent > 60 s or a sequence gap forces a resubscribe.
   - Disk guard: warn at 80% disk, stop writing book_deltas (keep the rest) at 95%.
8. Historical loaders (research/loaders/): funding history for the universe on both venues from the start dates
   (Arcus 2026-06-24, Lighter 2026-06-26), Arcus trades paging backwards with dedupe by tradeId, Lighter trades by cursor
   (throttled), candles (Lighter trade-based; Arcus oracle-based — label the table `candles_oracle`). Lighter funding: store
   both the rounded `rate` and the precise `value/index` reconstruction (you will need index at each timestamp; if not
   available historically, store value and fill index from candles close and flag `index_source`).
9. Data-quality report (`make dq-report`): per table/market/day: row counts, time coverage, max silence, sequence gaps,
   duplicate trade IDs, timestamp-unit sanity (reject values that look like ms where µs expected), funding hours missing.
   Output Markdown to reports/dq/{date}.md.
10. Compliance and region check script (deploy/scripts/region_check.py): prints Arcus /v1/compliance for this IP; checks
    Lighter RH read access; prints the server's public IP and country. [HUMAN GATE] The owner chooses the VPS region
    (Asia, not on Lighter's restricted list; e.g. Tokyo). Do not proceed to deployment until the owner confirms.
11. Deployment: deploy/systemd/arclight-recorder.service (Restart=always, RestartSec=5, MemoryMax), log rotation, a
    `deploy/scripts/bootstrap.sh` that installs uv, syncs, creates data dirs, enables chrony. Minimum host: 4 vCPU / 8 GB.

ACCEPTANCE TESTS
- A1: `make lint type test` green; unit tests cover rounding, time conversions, symbol mapping, config diffing, book
  rebuild (including the boundary-gap and mid-stream-gap cases with fixtures).
- A2: Recorder runs 24 h on the universe (BTC, ETH, SOL, HYPE, SPY, QQQ, NVDA, TSLA) on both venues. dq-report shows:
  every market has bbo and trades data in every UTC hour; no unresolved sequence gap; restarts (if any) recovered within 60 s.
- A3: Historical funding loaded for all universe markets on both venues; Lighter precise-rate reconstruction verified on
  SPY (value/index ≈ 3.94e-6 around 2026-09-22 for value 0.0030498 at index ≈ 773.3).
- A4: region_check.py output pasted into the hand-off.
- A5: LiveParams change log shows at least the initial snapshot for both venues; a simulated param change is detected in a test.

OUT OF SCOPE
API keys, signing, orders, strategies.

HAND-OFF
handoffs/PHASE_0_HANDOFF.md per Appendix E6. Include: disk usage per day of recording (MB/day per venue), observed Lighter
WS formats, the learned Lighter REST safe rate, and any [VERIFY] results.
```

---

## PHASE 1: Venue adapters (trading, signing, keys)

```text
ROLE
You are building Phase 1 of ArcLight (Parts A and B). This phase adds authenticated trading to both venue adapters.
Arcus work happens on TESTNET. Lighter RH has no known testnet: a single tiny live probe is allowed behind a [HUMAN GATE].

OBJECTIVE
Implement the VenueAdapter interface (Appendix E2) for Arcus and Lighter RH with correct signing, order placement,
modification, cancellation, cancel-all, dead man's switch (Arcus), leverage setting, account/position/order/fill streams,
rate-budget reporting and typed errors. Also build the key-management scripts that run on the OWNER'S machine.

TASKS: ARCUS
1. signing.py
   - Ed25519 key generation (PyNaCl); public key hex (64 chars).
   - Scheme 1 canonical payload builder for op 1/2/3/4 exactly as A3.2: sorted keys, compact JSON, lowercase `ad`,
     omit `c` when empty, integers for p/q (price/tickSize, size/stepSize using TOP-LEVEL tickSize; assert exactness),
     g = goodTilTime_us × 1000, ct = X-Timestamp ns.
   - Scheme 2 message builder: timestamp + action + canonical_json(body).
   - Golden tests: build payloads for fixed inputs and assert byte-exact strings; if the docs' guides (rest-trading,
     websocket-trading) include worked examples or sample code, port them as tests.
2. eip712.py (owner-machine script `scripts/arcus_register_key.py`)
   - CreateApiKey typed data (A3.2) with domain {name "Arcus API Key", version "1", chainId 4663 | 46630} and NO
     verifyingContract; include nonce + accountIndex type; explicit validUntil (ms). Signs with the wallet key via
     eth-account, POSTs /v1/createApiKey, stores ONLY the Ed25519 private key into secrets.enc.
   - Key-expiry monitor: store validUntil; alert 72 h before expiry; `arclight keys rotate --venue arcus --account N`
     (re-register with the same apiWalletName, which revokes the old key; update secrets atomically).
   - One key per accountIndex (0 = main, 1 = market making, 2 = DN leg).
   - Internal transfer (`scripts/arcus_transfer.py`): EIP-712 "Arcus Transfer" domain per docs page
     submit-internal-transfer; single-use ns nonce; poll accountTransferUpdates for the outcome. Owner-machine only.
3. rest.py / ws.py trading
   - placeOrder, batchPlaceOrders (≤ 39 per request), modifyOrder, batchModifyOrders, cancelOrder (by orderId or clientId),
     batchCancelOrders (≤ 100), cancelAllOrders, scheduleCancel (arm/refresh/disarm), setLeverage (+isolated),
     GET rateLimit, account, positions, openOrders (paged), fills.
   - goodTilTime default = now + 35 days (µs). timeInForce ALO for maker flow.
   - WebSocket `post` RPC for placeOrder/cancelOrder/modifyOrder (TPSL is REST-only), with in-flight ≤ 40 per connection.
   - Account streams: account, positions, orders, userFills, funding, accountAttributeUpdates, all with accountIndex.
     Maintain per-account sequence ordering (sequenceNum / lastSequenceId) and re-snapshot on gaps.
   - Parse `rateLimit.remaining` from write responses into the budget model; parse 429 `reason` and `retryAfterMs`.
   - Map every rejectionReason / errorType (A3.3) to typed exceptions; `Transmission` is retryable after reconciliation.
4. adapter.py implements VenueAdapter for Arcus (testnet and mainnet selectable; mainnet writes guarded by the B4 lock).

TASKS: LIGHTER RH
5. signer/: port delta-farmer lib/lighter_crypto (field.py, poseidon.py, signer.py) OR wrap the official lighter-python
   signer if it supports RH signing chain ID 466324. Tests: port delta-farmer tests/test_lighter_crypto.py vectors; add
   cross-check against the official SDK if available. Document the choice.
6. Owner-machine script `scripts/lighter_register_key.py`: find account_index for the wallet, generate API key for slot 3
   (configurable), send change-pub-key (tx 8) signed by the wallet, wait until /apikeys shows the new public key, store the
   API private key in secrets.enc. Integrator approval: read system config; if fee_collector_account_index != 0, print the
   fee caps and [HUMAN GATE] before sending tx 45.
7. auth.py: auth token creation (API key signer), refresh 10 min before expiry, used for account WS channels and private
   REST (Authorization header, PreferAuthServer: true).
8. nonce.py: time-based ms nonces max(now_ms, last+1); on nonce errors resync via /api/v1/nextNonce (budgeted); persist
   last nonce across restarts.
9. Trading: create order (tx 14; limit GTT with order_expiry = nonce + 28 days; post-only TIF per official SDK constant),
   market/IOC (for hedges, with limit price = mid ± slippage cap), cancel (tx 15), cancel-all (tx 16), update leverage
   (tx 20). Transport: WS jsonapi/sendtx and jsonapi/sendtxbatch (≤ 15) preferred; REST sendTx/sendTxBatch fallback.
   Encode base_amount/price with size/price decimals and `multiplier` (A4.2), rounding bids down / asks up.
10. Streams: account_all_orders, account_all_trades, account_all_positions, account_all (or account_market) with auth;
    order_book and trade from P0. Order state machine keyed by client order index / nonce.
11. Budget model: a token bucket of 60 sendTx/min shared with REST reads (conservative until A8.2 is measured);
    pending ≤ 10/market and active ≤ 30/market guards; WS message counter ≤ 150/min.
12. adapter.py implements VenueAdapter for Lighter RH (mainnet only; writes behind the B4 lock).

TASKS: SHARED
13. venues/base.py: VenueAdapter Protocol and normalized models (Appendix E2): Market, OrderRequest, OrderAck, OrderState,
    Fill, Position, Balance, FundingPayment, RateBudget, VenueHealth.
14. Typed errors in common/errors.py; retry policy helpers.
15. `arclight probe` CLI: prints markets, account state, budgets for a venue/account.

ACCEPTANCE TESTS
- B1: Golden signing tests pass (Arcus Scheme 1 and 2; Lighter vectors). Property tests on canonical JSON pass.
- B2: Arcus TESTNET: script `scripts/testnet_cycle.py` runs 100 cycles of: place ALO far from touch (≥ 2% away) → modify
  (price change) → cancel by clientId; then place 20 orders via batch → cancelAllOrders; then scheduleCancel arm with a
  15 s deadline, stop refreshing, verify all orders cancelled by the gateway. Result: 0 signature errors, every lifecycle
  event observed on the `orders` channel, budgets parsed.
- B3: Lighter RH [HUMAN GATE]: one post-only limit order of the market minimum ($10–12 notional) on BTC placed ≥ 5% away
  from mid (cannot fill), observed on account_all_orders, then cancelled; measured timings (send → visible in book;
  cancel → gone) recorded as the first latency samples for A8.3. Owner types CONFIRM before sending.
- B4: Triple-lock unit test proves no mainnet write is possible without all three conditions.
- B5: Key-expiry monitor test (fake clock) raises the 72 h alert.

OUT OF SCOPE
Strategies, order manager diffing, risk engine (P2). No mainnet Arcus orders in this phase.

HAND-OFF
handoffs/PHASE_1_HANDOFF.md: signer choice, measured Lighter probe latencies, any doc/API disagreements, integrator
approval findings, key IDs (public only), and answers to A8.2/A8.3/A8.4 if learned.
```

---
## PHASE 2: Core engine (market data hub, state, order manager, budget governor, risk, guardian, ledger, alerts)

```text
ROLE
You are building Phase 2 of ArcLight (Parts A and B). Everything runs against the PAPER adapter and Arcus TESTNET.

OBJECTIVE
Build the machinery every strategy relies on: a unified market-data hub, a reconciled state store, an order manager that
turns a strategy's DESIRED BOOK into minimal venue actions within budget, a risk engine with kill switches, a dead man's
switch refresher, an independent guardian process, a PnL ledger, alerts, a scheduler and the CLI.

TASKS
1. core/marketdata.py + core/book.py
   - One in-memory L2 book per venue/market (sorted price levels, integer ticks), BBO, microprice, last trades ring
     buffer, mark/oracle/index, predicted and last funding, market attributes (Arcus RTH/bands), staleness flags.
   - Derived streams at 1 s: mid, spread (bps), depth at q and 5q, realised vol EWMA at 1 s / 1 min / 1 h.
   - Health: feed age per stream; `stale = age > 2 s` blocks quoting on that market.
2. core/state.py
   - Orders keyed by clientId and venue orderId; positions; balances; fills; per-venue sequence tracking.
   - Event-sourced log in SQLite (every ack, update, fill, cancel, reject) so state can be replayed.
   - Reconciliation: on start and every 5 min, fetch open orders, positions and balances via REST (budget-aware) and
     compare with local state. Unknown live orders → cancel (log reason `reconcile_unknown`). Mismatched positions → trust
     venue, log, alert.
3. core/order_manager.py
   - Input: DesiredBook = list of DesiredOrder(side, price_ticks, size_quantums, tag, post_only=True, reduce_only=False).
   - Diff algorithm: match live orders to desired by (side, tag); keep a live order if |price diff| ≤ max(2 ticks,
     0.25 × h) and |size diff| ≤ 20%; otherwise modify (Arcus: modifyOrder; prefer same-price size decreases, which keep
     queue priority) or cancel + place. Emit the minimal action set; batch (Arcus ≤ 39 per place batch, ≤ 100 cancels;
     Lighter WS batch ≤ 15).
   - Never emit a post-only order that would cross the current BBO (re-check at send time); on POST_ONLY_WOULD_CROSS
     re-price one tick away next cycle.
   - Enforce venue minimums (Arcus $5 opening, Lighter $10 + min base), max order size, open-order caps.
   - Every action written to the decision log with its reason.
4. core/budget.py (order-budget governor)
   - Arcus: track pool `remaining`/`cap` from write responses; poll GET /v1/rateLimit every 60 s; compute rolling
     "actions per filled dollar" (target < 8, A6.8). Thresholds: < 20% of cap → double hysteresis; < 5% → cancels only.
   - Lighter: token bucket 60 sendTx/min shared with REST; per-market pending ≤ 10, active ≤ 30; WS messages ≤ 150/min.
   - Expose `can_act(venue, market, n_actions)` and `budget_state()` for strategies and the Autopilot.
5. core/risk.py
   - Pre-trade checks (reject before send): tick/step alignment; min notional; max order size; post-only safety; free
     collateral after the order using Arcus off-hours IMF when isOutsideRth; Arcus OI-cap headroom; oracle-deviation band;
     per-market position cap (USD); inventory cap; leverage cap (MM ≤ 5x, DN ≤ 3x per leg, configurable).
   - Account limits and kill switches (implement all rows of Appendix E7 table "Kill switches").
   - Safety pause: 1-s move > 6σ_1s, spread > 3× 1-h median, displayed depth < 30% of median → cancel quotes on that
     market, keep positions, resume after 30 s normal.
   - Event windows from config/calendars (CPI, FOMC, NFP ± 30 min; earnings ± 24 h; ex-dividend dates): no new quotes.
   - Safe mode: on any unexpected exception in the trading loop → cancel all quotes on the affected venue, keep
     positions, alert, require manual `arclight resume`.
6. core/dms.py: Arcus scheduleCancel refresher per subaccount (refresh every 20 s with a 60 s deadline); track the daily
   auto-fire count (max 10/UTC day); if the refresher fails twice in a row → safe mode.
7. core/guardian.py (separate process, `arclight guardian`)
   - Own venue connections (read-only streams + cancel-all/flatten permissions).
   - Heartbeat: the bot writes a heartbeat (unix socket or file) every 5 s; if stale for 60 s → cancel-all on both venues,
     alert. If account drawdown exceeds the hard limit → cancel-all and flatten (reduce-only, maker first, IOC after 30 s).
   - Guardian never places risk-increasing orders.
8. core/ledger.py
   - From fills and funding payments: realised spread capture (FIFO round trips per market), inventory MTM (at mark),
     funding (venue-reported payments; Arcus oracle-based, Lighter index-based), fees (from fills), hedge cost (DN), volume,
     OI-hours (∫|position|·mark dt), CPM, per-session and per-day rollups.
   - Weekly points entry: `arclight points add --venue lighter_rh --week YYYY-WW --points X` for the S4 study.
   - Daily Markdown report to reports/daily/{date}.md.
9. core/alerts.py: Telegram (token + chat id from secrets); levels INFO/WARN/CRIT; rate-limited; CRIT on kill switch,
   safe mode, guardian action, hedge-missing, key expiry, recorder silence.
10. core/scheduler.py + calendar.py: sessions with duration, repeat count, optional IST time windows (e.g. from the owner's
    time-slot research file), NYSE calendar/holidays, Arcus RTH boundaries (04:00, 09:30, 16:00, 20:00 ET), UTC funding ticks.
11. venues/paper/adapter.py: implements VenueAdapter with no real writes: accepts orders, keeps them on a virtual book,
    fills them when the LIVE trade stream prints through the price (touch-only, conservative) — full calibration is P5.
12. cli.py: `arclight run --session FILE [--paper|--testnet|--live]`, `status`, `cancel-all --venue`, `flatten --venue`,
    `resume`, `report --date`, `keys status`, `guardian`.

ACCEPTANCE TESTS
- C1: Kill test on Arcus TESTNET: run a dummy quoting session (2 ALO orders per side, far from touch), `kill -9` the bot;
  the gateway cancels all orders within 60 s via scheduleCancel; the guardian also fires cancel-all; alert received.
- C2: Restart reconciliation: start the bot with 3 orphan testnet orders placed manually → all cancelled with reason
  `reconcile_unknown`, 0 orphans after 1 minute.
- C3: Order-manager property tests: never a crossing post-only order; never exceeds per-market caps; minimal action count
  on 10k randomised desired-book sequences.
- C4: Governor holds actions per filled dollar < 8 in a 24 h paper run of a dummy quoter on BTC (Arcus paper adapter).
- C5: Ledger identity on a synthetic fill/funding sequence: ΔEquity = SpreadCapture + InventoryMTM + Funding − Fees.
- C6: Every kill-switch row has a unit test triggering it.

HAND-OFF
handoffs/PHASE_2_HANDOFF.md, including measured paper-adapter fill counts, governor statistics, DMS timings on testnet.
```

---

## PHASE 3A: Event-driven simulator

```text
ROLE
You are building Phase 3A of ArcLight (Parts A and B): a deterministic, event-driven simulator that replays the recorded
Parquet data and runs the SAME strategy code as live, through a SimVenueAdapter.

OBJECTIVE
Produce trustworthy PnL for maker strategies: queue-aware fills, per-venue latency, margin and liquidation, funding,
sessions and price bands, and rate limits, with optimistic and pessimistic variants.

TASKS
1. research/sim/events.py: merge-sorted event stream from book_deltas/snapshots, trades, bbo, mark/oracle/index,
   funding_paid, market_attrs, timers and own-order events, per venue and market, ordered by (exchange ts, sequence,
   recv ts). Deterministic tie-breaking; seedable randomness.
2. research/sim/book.py: L2 reconstruction identical to live (reuse core/book.py).
3. research/sim/latency.py: per-venue action latency distributions:
   Arcus ALO place = RTT; Arcus taker/modify-that-can-take = RTT + 50 ms; Arcus cancel = RTT (priority lane);
   Lighter maker = RTT + {0 ms | 200 ms} (run BOTH variants); Lighter taker = RTT + 300 ms; Lighter cancel = RTT + 300 ms.
   RTT from P1/P5 measurements (default 40 ms Arcus from Tokyo, 150 ms Lighter; configurable). A cancel takes effect only
   after its latency: trades crossing a stale quote before then fill it.
4. research/sim/fills.py (the most important component):
   - Queue position on arrival = displayed size at our price from the latest book; decrement by trades at our price on our
     side; cancels ahead reduce the queue PRO-RATA (pessimistic) or FIFO (optimistic); fill (partially) once queue < 0.
   - Trade-through (a trade strictly better than our price on the other side) fills us fully.
   - Touch-only fallback when L2 is missing: fill only on trade-through by ≥ 1 tick; flag results "optimistic-bounded";
     also compute a pessimistic variant requiring trade-through plus queue = median displayed depth.
   - Post-only rejection if our price would cross on arrival (Arcus POST_ONLY_WOULD_CROSS; Lighter cancelled).
   - Partial fills respect step size and minimum base; remainders below minimum notional may rest but not be placed fresh.
   - Self-impact: our fills consume the simulated book for later takers.
   - Taker orders walk the recorded book at arrival time.
5. research/sim/margin.py: per-venue equity, IM, MM on mark; Arcus IMF switches to off-hours value while isOutsideRth;
   Arcus liquidation when equity ≤ MM (fee unknown → parameter, default 1%); Lighter IMF/MMF/CMF with partial liquidation
   at the zero price (A4.7) and fee up to 1%, full takeover below CMF. Check every event and at least every minute.
   Legs on different venues never share margin.
6. research/sim/funding.py: apply RECORDED paid rates at each hourly tick: payment = −position × P_pay × rate (Arcus
   oracle price, Lighter index price; Lighter precise rate = value/index). Only recompute from premiums to fill gaps (flag).
7. research/sim/sessions.py: NYSE calendar; Arcus RTH 04:00–20:00 ET; Arcus bands state machine from recorded
   marketAttributes (or the documented ladder, flagged synthetic, for pre-recorder periods); clip/reject fills beyond
   bounds; Lighter: no off-hours margin change.
8. research/sim/ratelimit.py: Arcus pools with volume replenishment and drip; Lighter 60 sendTx/min, pending/active caps.
   Strategy actions beyond budget are queued or dropped exactly as the live governor would.
9. research/sim/outages.py: inject venue outages (feed + order entry) of 60 s, 10 min, 2 h at chosen times.
10. venues/sim/adapter.py: SimVenueAdapter implementing VenueAdapter over the simulator.
11. research/eval/metrics.py: all metrics of Appendix E7 "Metrics" (net PnL decomposition, volume, CPM, OI-hours,
    drawdowns, liquidation count and minimum distance in σ, fill rate, post-only reject rate, queue position at fill,
    markouts at 1/5/30/60/300 s by taker address on Arcus, hedge latency/slippage/residual for DN).
12. Performance: replay one day of one market's full L2 in under 2 minutes on the 4 vCPU host (numba hot loops).

ACCEPTANCE TESTS
- D1: Formula tests reproduce the docs' worked examples: Arcus BTC premium 0.05% (oracle 100,000; impact 100,050);
  Arcus RWA hourly ≈ 0.0106% (premium 0.0008, sofr_rate_hourly 0.00000579); Arcus $10,000 position pays ≈ $1.06;
  Lighter SPY base 0.0004%/h from IR 0.0032; Lighter SPY dead-band edges −0.0436% / +0.0564%; value/index reconstruction.
- D2: PnL identity holds to 1e-8 on 3 recorded days: ΔEquity_A + ΔEquity_L = SpreadCapture + InventoryMTM + Funding − Fees
  − HedgeCost − LiquidationLoss.
- D3: Determinism: same seed → byte-identical results.
- D4: Fill-model sanity: a passive order at the back of a deep queue never fills without trades at its price; a
  trade-through always fills; post-only crossing orders are rejected.
- D5: Recompute each venue's hourly funding from recorded minute premiums (median Arcus, mean Lighter) and report the
  error vs the paid rate (large errors mean impact size or sampling is misunderstood → write to hand-off).
- D6: Liquidation tests: synthetic moves liquidate at the expected prices on each venue.

HAND-OFF
handoffs/PHASE_3A_HANDOFF.md with performance numbers, D5 error table, and any data gaps that limit simulation.
```

---

## PHASE 3B: Strategy modes

```text
ROLE
You are building Phase 3B of ArcLight (Parts A and B): all strategy modes as PURE functions of state → DesiredBook, used
unchanged by the simulator (P3A), the paper adapter (P2/P5) and live (P6).

OBJECTIVE
Implement Mid, Grid, RGrid, DGrid, Blend, Signal (Tread modes), the cross-venue DN modules (hedged MM, funding carry,
points overlay) and the shared quoting core, with Tread-style controls.

INTERFACE (Appendix E2)
class Strategy(Protocol):
    name: str
    def on_start(self, ctx: StrategyContext) -> None
    def on_tick(self, ctx: StrategyContext) -> StrategyOutput      # DesiredBook per venue/market + hedge intents + notes
    def on_fill(self, ctx: StrategyContext, fill: Fill) -> None
    def on_session_event(self, ctx: StrategyContext, ev: SessionEvent) -> None   # RTH open/close, funding tick, band change
    def on_stop(self, ctx: StrategyContext) -> StrategyOutput      # exit plan
StrategyContext exposes: market data snapshot, own state (inventory, orders, margin), budgets, params, clock, calendar.
Every StrategyOutput carries a human-readable `reason` for the decision log.

TASKS
1. strategies/quoting.py (shared building blocks, A6.3): reservation price and size skew; execution-style anchors
   (Aggressive / Normal / Passive) and Tread offset in bps; post-only guard; tick-band rounding (bids down, asks up);
   bias path I*(t) (Long/Short skew rises to ±B over the first half of the session, back to 0 by the end); participation
   cap (our 5-min fill volume / market volume > cap → widen h by 50%); inventory cap; safety-pause hook.
2. strategies/mid.py: 1–3 levels per side at r ± (anchor + i × level_step). Allowed venues: Arcus only (Lighter standard
   blocked: stale-quote risk from 300 ms cancels). Params: offset_bps, levels, level_step_bps, size, κ, execution_style.
3. strategies/grid.py (static): centre C = mid at start; geometric levels buy_k = C(1+δ)^−k, sell_k = C(1+δ)^k, k = 1..N;
   filled buy at k re-lists as sell at k−1 (one step up) and vice versa; no new buys once |I|·P ≥ I_cap (symmetric for
   sells); re-centre when |m − C|/C > R for longer than T_recentre; on re-centre handle inventory per
   `recentre_inventory` ∈ {skew_exit, maker_unwind, hedge_other_venue}. Lighter: N ≤ 5 per side, requote only on fills
   or re-centre.
4. strategies/rgrid.py (trailing): N = 1–3 per side centred on EMA(mid, τ); when |m − centre| > R, jump centre to m and cut
   inventory on the wrong side of the move: reduce-only maker at touch, then IOC after T_cut. Optional trend tilt
   I* = β × sign(trend_z) × I_cap. Lighter: N ≤ 2 per side.
5. strategies/dgrid.py: delegates to Grid or RGrid by regime (from the Autopilot or a local classifier when run manually);
   spacing δ from A6.4 with k_δ, F (target fills/hour, capped by the venue budget), δ_min/δ_max; defaults SL 10%, TP 10%.
6. strategies/blend.py: reference = w1 × other-venue mid + w2 × Pyth oracle (Arcus oraclePrices) + w3 × local mid, plus an
   EWMA of persistent basis (reference − local) to avoid leaning on structural gaps; if any reference is older than
   stale_ms or deviates > dev_bps from local mid → widen ×2 or pause. Primary use: Arcus RWA books around Lighter's deep mid.
7. strategies/signal.py: RSI(n) on 1-min bars built from trades/mid; trend filter |EMA20 − EMA60| < z·σ; long entry RSI <
   low, short entry RSI > high; maker entry at touch; exit maker TP at +tp_bps or reduce-only stop at −sl_bps; one position;
   cooldown; max holding time.
8. strategies/dn_hedged_mm.py (maker on Arcus, hedge on Lighter; A6.6):
   - Quote ALO on Arcus at FairPrice ± h, FairPrice = w × Lighter mid + (1 − w) × Arcus mid, h ∈ [3, 30] bps.
   - On each Arcus fill emit a HEDGE INTENT: Lighter IOC opposite side, limit = Lighter mid ± slip_cap; batch until the
     unhedged residual ≥ $10 (Lighter minimum); when Arcus fills the opposite quote, unwind the matching hedge.
   - Kill rule: hedge rejected/timeout or Lighter unreachable > T s → cancel Arcus quotes, flatten Arcus inventory by taker.
9. strategies/dn_carry.py (funding and basis carry):
   - Signals each minute: rA_pred (Arcus nextFundingRate / predictedFunding; locked rate off-hours), rL_pred (from recorded
     minute premiums with the A4.7 formula, or the venue's live field), spread_pred, spread_fcst(H) via pluggable model
     (persistence, AR(1)/exp-decay, GBM later), executable basis B_exec at planned size, margin state per leg.
   - Entry by EV rule (A6.6) over H ∈ {4, 8, 12, 24, 48, 72} h; execution: ALO on Arcus inside spread, Lighter IOC hedge
     per fill (aggregate to ≥ $10); re-price Arcus up to n times; never chase with Arcus taker unless EV still > θ after
     2.25 bps + half-spread. Alternative order (maker on Lighter first, Arcus taker hedge) selectable per market.
   - Hold: re-evaluate after each funding tick and at 04:00, 09:30, 16:00, 20:00 ET, Fri 20:00, Mon 04:00 ET.
     Margin: warn < 3× MM → emit a transfer request (manual in v1, alert); < 1.8× MM → reduce both legs proportionally.
     Never leave one leg alone. Arcus off-hours: no scaling-in; watch band expansion zones.
   - Exit (first trigger): forecast carry + basis EV < θ_exit; basis converged ≥ k_take·σ_B; risk limit; earnings or
     ex-dividend within 24 h (single stocks); max hold 7 days.
10. strategies/points_overlay.py: keep the DN pair (or hedge leg) open for hours; track volume, time-weighted OI and hold
    time for the weekly points fit; churn penalty parameter.
11. Session wrapper: duration, repeat, IST windows, skip events, SL/TP at session level (margin × SL%), end-of-session exit
    plan (maker unwind then IOC after T).
12. Params: pydantic models per strategy with defaults from Appendix E4; every param documented.

ACCEPTANCE TESTS
- E1: Unit tests on synthetic paths (range-bound sine + noise, linear trend, jump, gap at session open):
  Grid accumulates inventory in a trend and stops at I_cap; RGrid caps the trend loss (≤ one level + R per re-centre);
  Mid skews quotes against inventory; Blend pauses on a stale reference; Signal never holds 2 positions; DGrid picks
  RGrid in trend and Grid in range.
- E2: DN hedged MM on a simulated Lighter outage flattens Arcus within the kill-rule time and never leaves a residual
  > 2× minimum notional for > 5 s in normal operation.
- E3: Every strategy runs 48 h in PAPER mode on live feeds without exceptions, with decision-log reasons on every action.
- E4: Each strategy runs through the simulator on 3 recorded days and produces the full metrics set.

HAND-OFF
handoffs/PHASE_3B_HANDOFF.md: parameter defaults, test evidence, paper-run summaries per mode.
```

---
## PHASE 4: Research, backtests, Autopilot v1 and the go/no-go report

```text
ROLE
You are building Phase 4 of ArcLight (Parts A and B). You now have ≥ 2–4 weeks of recorded data (P0), a simulator (P3A)
and strategies (P3B). Your job is to find out, honestly, what works, build the rule-based Autopilot, and write the
go/no-go report. Out-of-sample, pessimistic-fill results are the only results that count.

TASKS
1. Diagnostics (research/diagnostics/), per venue × market × session (RTH, pre, post, overnight, weekend, crypto 24/7):
   - Realised volatility, spread and displayed depth by hour of week.
   - OER at δ ∈ {5, 10, 15, 25, 40, 60, 100} bps and windows W ∈ {15 min, 1 h, 4 h}.
   - ER, trend_z and variance-ratio distributions; regime frequencies.
   - Simulated maker markouts at 1/5/30/60/300 s for passive quotes at the touch and at 1–3 ticks behind.
   - Arcus taker concentration (HHI over takerAddress) and top-taker markout toxicity.
   - Cross-venue basis per matched market: mean, σ, Ornstein–Uhlenbeck half-life, extremes by session.
   - Funding panel F[m, h] = (r_A, r_L, spread, premiums, session flags, OI): distributions, run lengths of spread episodes,
     break-even hold time vs round-trip cost 0–40 bps (A6.6).
   Output reports/diagnostics/{date}/ (Markdown + Parquet + charts) and a universe recommendation.
2. DN Layer A carry study (hourly; A6.6): hypotheses H1 (RWA premium pass-through), H2 (off-hours asymmetry, including
   lock-to-unlock basis PnL), H3 (crypto divergence episodes, event study on |pred spread| > θ), H4 (basis mean reversion
   entries at |z| ≥ k), H0 (no edge). Use only information available at decision time (forecast fields recorded at t, or
   rates paid at t−1h, labelled "lagged-signal"). Cost swept 0–40 bps round trip.
3. Parameter search per mode on the simulator (pessimistic and optimistic fills, both Lighter maker-latency variants):
   δ {5, 10, 15, 25, 40, 60, 100} bps; N {3, 5, 8, 12}; size {1.1, 2, 4} × venue minimum; leverage cap {1, 2, 3, 5}x;
   k_δ and κ grids; z_trend {1.0, 1.5, 2.0, 3.0}; DN h {3, 5, 8, 12, 20, 30} bps; hedge slip cap {5, 10, 20} bps;
   off-hours size multiplier {0, 0.25, 0.5, 1.0}.
4. Evaluation protocol (research/eval/): walk-forward (tune 3 weeks, test the next week, roll); block bootstrap (1 h blocks
   for MM, 24 h blocks for DN) → distribution of 30-day net PnL, P(PnL < 0), 5th percentile; parameter-stability heatmaps
   (accept plateaus, reject spikes); multiple-testing haircut (Deflated Sharpe or Bonferroni); outage injection
   (60 s, 10 min, 2 h) on the chosen configs.
5. Autopilot v1 (autopilot/):
   - features.py: all A6.5 features + session, funding/basis, own state, budget state; computed every minute from the
     market-data hub (live) or the simulator (research) with identical code.
   - regime.py: rule pipeline:
       eligibility (market ONLINE/active; region OK; min notional fits; depth at touch ≥ 3 × q; OI headroom; no event
       window; venue healthy: feed lag < 2 s, error rate < 5%)
       → hard stops (event window, safety pause, markout < −2 bps, Arcus band in expansion zone, spread > 3× median)
         → PAUSE
       → local book thin and other venue deep → BLEND
       → trending (ER > 0.5 or |trend_z| > 2) → vol > max → PAUSE, else RGRID with tilt
       → ranging (ER < 0.3 and OER(δ*) > 1.5) → Arcus tight & calm → MID, else GRID with DGrid spacing
       → otherwise → SIGNAL or passive GRID
     Hysteresis: a new mode must win 5 consecutive evaluations and the old mode must have run ≥ 30 min, unless a hard
     stop fires. On switch: cancel quotes, hand inventory to the new mode's exit logic. All thresholds in config; the
     values above are starting points to be tuned by walk-forward.
   - params.py: δ/h from A6.4 with F capped by budget; N = floor(I_cap / (q·P)) capped by venue limits (Arcus ≤ 12,
     Lighter ≤ 5 per side); q = max(1.2 × venue min, capital × target lev / (2N)); κ rises with negative markouts;
     R = 0.5 × σ_1h clamped [0.125%, 1%]; SL/TP defaults 10%/10%; Arcus RWA off-hours δ ×2, size ×0.25, no Mid, never past
     the next trading bound.
   - allocator.py: rank (venue, market, mode) by expected net PnL per $ per day + λ × points per $ − risk penalty; at $100
     allocate one MM market on Arcus and at most one DN pair.
   - The Autopilot must be benchmarked in the simulator against every fixed mode on the same out-of-sample windows.
6. The go/no-go report reports/GO_NO_GO.md with, for each candidate config (MM and DN):

   | Gate | Market making | Delta-neutral |
   | Net PnL | ≥ 0 every 4-week window, or mean cost ≤ 1 bp of volume with 95% upper bound ≤ 2 bps | > 0 in ≥ 70% of windows; bootstrap P(30-day loss) ≤ 25% |
   | Liquidations | 0; never closer than 4σ (1 h) | 0 incl. outage tests ≤ 10 min |
   | Max drawdown | ≤ 10% of capital | ≤ 8% of capital |
   | Execution | 30–60 s maker markout not significantly negative | (checked in P5) paper costs ≤ 1.5× sim |
   | Other | hedge residual ≤ 2× min notional for ≤ 5 s | edge source (H1–H4) identified, stable in 2 consecutive windows |

   Plus: recommended universe, session configs for P5/P6 (Appendix E4 format), the Autopilot vs fixed-mode comparison, and
   if H0 holds, the cheapest carry-neutral setup's cost per week for a target Lighter OI (the "points-hedge cost").
7. Present results honestly: show losing configurations, sensitivity to the fill model and to Lighter maker latency, and
   the Lighter points-reversal scenario (points = 0).

[HUMAN GATE] The owner reads GO_NO_GO.md and approves which configs (if any) go to paper trading.

ACCEPTANCE TESTS
- F1: Diagnostics report generated for the full universe and all sessions.
- F2: Every number in GO_NO_GO.md is reproducible with one command (`make gonogo DATE=…`), seeds fixed.
- F3: No look-ahead: a unit test shifts all future data by +1 h and confirms decisions at t are unchanged.
- F4: Autopilot v1 matches or beats the best fixed mode out-of-sample with lower drawdown, OR the report says it does not
  and recommends the fixed mode.

HAND-OFF
handoffs/PHASE_4_HANDOFF.md summarising findings, the recommended configs, and every known-unknown the data answered.
```

---

## PHASE 5: Paper trading on live feeds and calibration

```text
ROLE
You are running Phase 5 of ArcLight (Parts A and B): two weeks of full-system paper trading on live feeds, with the
configs approved from GO_NO_GO.md. No real orders except optional latency probes behind a [HUMAN GATE].

TASKS
1. Upgrade venues/paper/adapter.py to a queue-aware would-fill tracker on the LIVE books (same fill logic as P3A:
   queue position on arrival, trade decrements, pro-rata cancels, trade-through, post-only rejects, latency model).
2. Run the full stack (Autopilot or approved fixed modes, risk, governor, DMS on Arcus testnet if desired, guardian,
   ledger, alerts) for 14 consecutive days on the approved sessions: one Arcus MM market, one DN pair.
3. Every night, replay the same day through the simulator (P3A) using the recorder's data and compare: fills, fill
   prices, queue positions, PnL terms, hedge latency/slippage, actions per filled dollar. Produce
   reports/paper/{date}.md with the ratio "paper cost / simulated cost" per term.
4. Calibrate: fit fill-model parameters (cancel-ahead fraction, touch-fill probability), latency distributions and hedge
   slippage from paper data; write them to config/calibration.yaml with version and date.
5. Optional [HUMAN GATE] latency probes on Lighter RH: at most 1 per 10 minutes, market-minimum post-only orders ≥ 5% from
   mid, then cancel, to measure maker/cancel latency (A8.3). Owner CONFIRMs once for the whole probe schedule.
6. Measure USDG transfer time and cost between venues (manual owner action; record results) (A8.9).
7. Write reports/PAPER_REPORT.md: day-by-day table, calibration results, divergences, incidents, and a recommendation.

ACCEPTANCE TESTS
- G1: 14 days with no unhandled exception, no safe-mode trigger without a logged cause, recorder gap-free.
- G2: Realised paper costs within 1.5× of simulated for each PnL term over the 2 weeks (else explain and recalibrate).
- G3: Governor, risk and guardian events all have decision-log reasons; daily reports produced every day.

[HUMAN GATE] Owner approves PAPER_REPORT.md before any live money.

HAND-OFF
handoffs/PHASE_5_HANDOFF.md with calibration values and the live-readiness checklist (Appendix E7) filled in.
```

---

## PHASE 6: Live stages 1 and 2 (≤ $100 total)

```text
ROLE
You are running Phase 6 of ArcLight (Parts A and B). Real money, tiny size. Every stage start is a [HUMAN GATE].
Your job is operations: configure, verify, start, monitor, report, stop when a rule says stop.

PRE-FLIGHT (all must be ticked in the hand-off before stage 1)
- Region check passes from the server IP for both venues (Arcus /v1/compliance; Lighter access).
- Keys: Arcus trade-only keys per subaccount (1 = MM, 2 = DN leg) with > 7 days to expiry and the rotation procedure
  tested; Lighter API key in slot 3; wallet key NOT on the server.
- Funding: Arcus subaccount 1 = $35 + $8 reserve (stage 1); later subaccount 2 = $25; Lighter = $25 + $7 reserve.
- DMS refresher, guardian, alerts (test message received), daily report, decision log all running.
- Triple lock (B4) set only for the approved session files.
- Kill-switch thresholds set: session SL 10% of margin; daily loss 3% of capital; drawdown 10% (hard stop);
  liquidation distance 4σ; hedge-missing 5 s.
- Owner has read the runbook (below) and knows `arclight cancel-all`, `flatten`, `resume`.

STAGE 1 (weeks 1–2): $35 Arcus maker-only Autopilot (or the approved fixed mode) on the single approved crypto market.
- Start at 50% of approved size for the first 48 h; then full size if no rule breach.
- Monitor: fills, markouts, actions per filled dollar, inventory, CPM, drawdown; daily report to Telegram.
- Gate to stage 2: 2 weeks, no rule breach, net ≥ −1 bp of volume, costs within 1.5× of paper.

STAGE 2 (weeks 3–6): add one DN pair ($25 Arcus subaccount 2 + $25 Lighter RH, 3x per leg) on the approved market.
- First entry manually confirmed ([HUMAN GATE]); subsequent entries automatic within limits.
- Margin rebalancing between venues is MANUAL in v1: the bot alerts at < 3× MM with the exact transfer suggested; it
  reduces both legs proportionally at < 1.8× MM.
- Weekly: owner enters Lighter points (`arclight points add`); the ledger updates the points-vs-activity fit.
- Gate to P7: 4 weeks meeting the pass/fail bar (spec "Goals" table).

RUNBOOK (write to docs/RUNBOOK.md)
- Start/stop procedures; how to read the daily report; what each alert means and the action; incident steps
  (cancel-all, flatten, disable session, collect logs, write an incident note); key rotation; venue maintenance handling;
  what to do if a venue changes an API field (Part D3).

AUTOMATIC STOP RULES (no human needed)
- Any kill-switch trigger; any liquidation-distance breach; guardian heartbeat loss; two consecutive DMS refresh failures;
  unexpected `SELF_TRADE` or `GEO_RESTRICTED`; a Lighter points-rule concern raised by the owner.

DELIVERABLES
- docs/RUNBOOK.md; reports/live/{week}.md weekly: PnL decomposition, volume, OI-hours, CPM, points, incidents, gate status.

HAND-OFF
handoffs/PHASE_6_HANDOFF.md at each stage gate.
```

---

## PHASE 7: Scale and Autopilot v2 (bandit)

```text
ROLE
You are running Phase 7 of ArcLight (Parts A and B), only after Phase 6 gates are met. Goals: add markets, scale capital
2–3× per step, and turn on parameter learning safely.

TASKS
1. autopilot/bandit.py: Thompson-sampling bandit over PRE-TESTED parameter sets ("arms") per mode.
   - Context bucket = ER tercile × volatility tercile × session.
   - Reward per hour = net PnL in bps of volume + λ × points proxy − μ × drawdown contribution.
   - Guardrails: an arm is eligible only if it passed P4 gates under pessimistic fills; exploration ≤ 10% of capital;
     drop an arm after 3 sessions losing more than the threshold; never explore during event windows or off-hours RWA.
   - Train offline on recorded data through the simulator first; then SHADOW mode for 1 week (bandit proposes, rules act,
     both logged); then live control of the exploration slice only.
2. Allocator upgrade: multi-market allocation with correlation-aware risk budget; per-venue order-budget awareness.
3. Universe expansion: add markets from the P4 recommendation one at a time, each through a 1-week paper → live ramp.
4. Scale steps: capital × 2 per step, each step 4 weeks at the pass/fail bar. [HUMAN GATE] per step.
5. Optional (owner decision): evaluate Lighter Plus/Premium account economics with measured data; evaluate adding Ondo
   Perps or TxFlow adapters (Part D5) — only after the core is stable.

ACCEPTANCE TESTS
- H1: Bandit in simulation beats the rule-only Autopilot out-of-sample, or it stays in shadow mode.
- H2: Shadow week shows no action the risk engine would have rejected.
- H3: Each scale step keeps every gate green for 4 weeks.

HAND-OFF
handoffs/PHASE_7_HANDOFF.md per scale step.
```

---
# PART D: UTILITY PROMPTS (use any time; paste Part A + B first)

## D1. Bug fix or incident

```text
ROLE: ArcLight maintainer. An incident or bug happened.
INPUT: the alert text, logs from logs/ for the time window, the decision log, the ledger rows, and the owner's description.
DO:
1. Stop the bleeding first: confirm the affected venue/session is in safe mode or stopped; if not, tell the owner the exact
   command (`arclight cancel-all --venue X`, `arclight flatten --venue X`).
2. Reconstruct the timeline (µs timestamps) from logs, decision log, venue streams and the recorder.
3. Identify the root cause; write a failing test that reproduces it (fixture from recorded data).
4. Fix with the smallest safe change; run the full test suite; replay the incident window in the simulator to prove the fix.
5. Write docs/incidents/{date}-{slug}.md: impact ($, positions, duration), timeline, root cause, fix, prevention, and whether
   any kill switch should change.
CONSTRAINTS: no live restarts without owner CONFIRM; no threshold loosening without an explicit owner decision.
```

## D2. Security and correctness audit

```text
ROLE: independent reviewer who did NOT write the code.
CHECK:
- Secrets: nothing in git history, logs, exceptions, crash dumps; secrets.enc permissions 600; wallet key absent from server.
- Triple lock (B4): trace every path to a mainnet write; confirm the unit test covers them all.
- Signing: Arcus Scheme 1/2 byte-exactness; lowercase address; clientId charset; ns/µs units; exact tick/step integers;
  Lighter nonce monotonicity across restarts.
- Order manager: cannot cross post-only; respects venue minimums and caps; handles partial fills and modify semantics.
- Risk: every kill switch reachable and tested; guardian independent (separate process, own connections).
- Money maths: Decimal usage; rounding direction; PnL identity tests; funding sign conventions (+ = longs pay) and payment
  price (Arcus oracle, Lighter index).
- Compliance: one account per venue; no self-trade paths; decision log complete.
OUTPUT: docs/audits/{date}.md with findings ranked by severity, each with file:line, failure scenario, and a fix.
```

## D3. Venue API change

```text
ROLE: ArcLight maintainer. A venue changed its API or parameters (changelog entry, new error, param_changes alert).
DO:
1. Read the venue changelog (Arcus: docs.arcus.xyz/changelog and changelog/testnet; Lighter: docs/apidocs) and the
   param_changes log. List every change that touches our code or assumptions (Part A facts).
2. Write probes against testnet (Arcus) or read-only endpoints to confirm the new behaviour.
3. Update code, config and Part A notes (docs/notes/venue_changes.md); add regression tests.
4. Re-run the affected backtests if economics changed (fees, margins, funding formula, speed bump, limits).
5. Report: what changed, impact on PnL/risk, what was updated, whether live sessions must pause.
```

## D4. Weekly performance review

```text
ROLE: analyst. Produce reports/weekly/{YYYY-WW}.md from the ledger, decision log and recorder.
INCLUDE: net PnL decomposition per venue/market/mode; volume; OI-hours; CPM (compare with Tread users' $90–120 on Ondo);
points earned (owner-entered) and points per $1M volume and per $·h OI; markouts; actions per filled dollar; drawdown;
kill-switch events; mode-switch counts and time in each mode; gate status vs the pass/fail bar; three concrete
recommendations with expected effect and risk. No recommendation may loosen a risk limit without saying so explicitly.
```

## D5. Add a new venue adapter (phase-2 venues such as Ondo Perps or TxFlow)

```text
ROLE: ArcLight developer adding venue X.
DO:
1. Research X's docs: auth/signing, order types (post-only?), fees, min notional, rate limits, WS channels, funding formula
   and payment price, margin/liquidation, sessions (off-hours rules), historical data, points rules and restricted regions.
   Write docs/venues/X.md in the same structure as Part A3/A4.
2. Implement venues/X/ against the VenueAdapter Protocol; recorder support; LiveParams support; symbol mapping.
3. Simulator support: latency, fill rules, margin/liquidation, funding, sessions.
4. Tests as in Phase 1; testnet or [HUMAN GATE] minimal probe.
5. Run diagnostics and backtests (Phase 4 protocol) before any strategy uses X.
Known notes: Ondo Perps (RWA only, maker 1 bp / taker 2.5 bps promo, $1M max position, weekend discovery bounds,
dead-man's-switch WS channel, liquidation fee 1.5%); TxFlow (maker 1.5 bp at VIP 0; public API "coming soon"; stock perps
may be reduce-only off-hours) — verify all.
```

---
# PART E: APPENDICES

## E1. Repository layout

See B2. Data directory (on the server, not in git): `data/{table}/venue=/market=/date=/`, `logs/`, `reports/`, `state/arclight.sqlite`.

## E2. Core interfaces (Python; adapt names, keep semantics)

```python
from __future__ import annotations
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Protocol, Sequence, AsyncIterator

class Venue(str, Enum):
    ARCUS = "arcus"
    LIGHTER_RH = "lighter_rh"

class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"

class TIF(str, Enum):
    GTT = "gtt"; IOC = "ioc"; FOK = "fok"; POST_ONLY = "post_only"   # Arcus ALO == POST_ONLY

@dataclass(frozen=True, slots=True)
class TickTier:
    tick: Decimal
    up_to_price: Decimal | None          # None = unbounded top band

@dataclass(frozen=True, slots=True)
class Market:
    venue: Venue
    base: str                            # canonical, e.g. "BTC", "SPY"
    venue_symbol: str                    # "BTC-USD" (Arcus) / "BTC" (Lighter)
    venue_market_id: int
    status: str                          # ONLINE/OFFLINE (Arcus) or active/... (Lighter)
    category: str                        # CRYPTO, EQUITIES, INDICES, COMMODITIES, FOREX
    tick_size: Decimal                   # Arcus top-level tickSize (signing divisor) / Lighter 10^-price_decimals
    tick_tiers: tuple[TickTier, ...]
    step_size: Decimal
    min_notional: Decimal                # $5 Arcus, $10 Lighter
    min_size: Decimal
    max_size: Decimal | None
    imf: Decimal
    mmf: Decimal
    offhours_imf: Decimal | None         # Arcus RWA
    close_out_mf: Decimal | None         # Lighter
    rth: tuple[int, int, str] | None     # (start_sec, end_sec, tz) or None for 24/7
    maker_fee: Decimal                   # fraction of notional (negative = rebate)
    taker_fee: Decimal
    oi_cap_usd: Decimal | None
    size_decimals: int | None = None     # Lighter
    price_decimals: int | None = None    # Lighter
    multiplier: Decimal = Decimal(1)     # Lighter

@dataclass(frozen=True, slots=True)
class OrderRequest:
    venue: Venue
    base: str
    side: Side
    price: Decimal
    size: Decimal
    tif: TIF
    reduce_only: bool = False
    client_id: str = ""
    tag: str = ""                        # strategy level tag, e.g. "grid_b3"
    reason: str = ""                     # decision-log reason

@dataclass(frozen=True, slots=True)
class OrderState:
    client_id: str
    venue_order_id: str | None
    status: str                          # PENDING_NEW, OPEN, PARTIALLY_FILLED, FILLED, CANCELED, REJECTED, EXPIRED
    filled_size: Decimal
    avg_fill_price: Decimal | None
    reject_reason: str | None
    ts_us: int

@dataclass(frozen=True, slots=True)
class Fill:
    venue: Venue
    base: str
    client_id: str
    side: Side
    price: Decimal
    size: Decimal
    fee: Decimal                         # positive = paid, negative = rebate
    is_maker: bool
    liquidation: bool
    ts_us: int
    trade_id: str

@dataclass(frozen=True, slots=True)
class Position:
    venue: Venue
    base: str
    size: Decimal                        # signed, base units
    entry_price: Decimal
    mark_price: Decimal
    unrealized_pnl: Decimal
    margin_mode: str                     # cross / isolated
    liq_price: Decimal | None

@dataclass(frozen=True, slots=True)
class RateBudget:
    venue: Venue
    order_remaining: int | None
    order_cap: int | None
    cancel_remaining: int | None
    cancel_cap: int | None
    tx_per_min_remaining: int | None     # Lighter
    next_available_ms: int

class VenueAdapter(Protocol):
    venue: Venue
    async def connect(self) -> None: ...
    async def close(self) -> None: ...
    async def markets(self) -> Sequence[Market]: ...
    async def place(self, orders: Sequence[OrderRequest]) -> Sequence[OrderState]: ...
    async def modify(self, client_id: str, price: Decimal, size: Decimal) -> OrderState: ...
    async def cancel(self, client_ids: Sequence[str]) -> None: ...
    async def cancel_all(self, base: str | None = None) -> None: ...
    async def set_leverage(self, base: str, leverage: int, isolated: bool = False) -> None: ...
    async def arm_dead_mans_switch(self, deadline_us: int) -> None: ...        # Arcus; no-op elsewhere
    async def positions(self) -> Sequence[Position]: ...
    async def open_orders(self) -> Sequence[OrderState]: ...
    async def balances(self) -> dict[str, Decimal]: ...
    def order_updates(self) -> AsyncIterator[OrderState]: ...
    def fills(self) -> AsyncIterator[Fill]: ...
    def budget(self) -> RateBudget: ...
    def health(self) -> dict[str, float]: ...                                  # feed ages, error rates

@dataclass(frozen=True, slots=True)
class DesiredOrder:
    side: Side
    price_ticks: int
    size_quantums: int
    tag: str
    post_only: bool = True
    reduce_only: bool = False

@dataclass(slots=True)
class StrategyOutput:
    desired: dict[tuple[Venue, str], list[DesiredOrder]] = field(default_factory=dict)
    hedge_intents: list[OrderRequest] = field(default_factory=list)   # IOC hedges (DN)
    reason: str = ""
    metrics: dict[str, float] = field(default_factory=dict)

class Strategy(Protocol):
    name: str
    def on_start(self, ctx: "StrategyContext") -> None: ...
    def on_tick(self, ctx: "StrategyContext") -> StrategyOutput: ...
    def on_fill(self, ctx: "StrategyContext", fill: Fill) -> None: ...
    def on_session_event(self, ctx: "StrategyContext", ev: "SessionEvent") -> None: ...
    def on_stop(self, ctx: "StrategyContext") -> StrategyOutput: ...
```

## E3. Parquet schemas (recorder and research)

All timestamps int64 µs UTC unless noted. Partition: `venue`, `market` (canonical base), `date`.

| Table | Columns |
| --- | --- |
| `book_deltas` | recv_ts_us, venue_ts_us, venue, market, seq (int64), global_seq (int64, Arcus), side (b/a), price (decimal string), size (decimal string; 0 = remove), is_snapshot (bool) |
| `book_snapshots` | recv_ts_us, venue_ts_us, venue, market, seq, bids (list<struct<price,size>>), asks (list<struct<price,size>>), n_levels |
| `bbo` | recv_ts_us, venue_ts_us, venue, market, seq, bid_px, bid_sz, ask_px, ask_sz |
| `trades` | recv_ts_us, venue_ts_us, venue, market, trade_id, price, size, taker_side, maker_address (Arcus), taker_address (Arcus), maker_order_id, seq, is_liquidation |
| `mark_oracle_index` | recv_ts_us, venue_ts_us, venue, market, mark, oracle (Arcus), index (Lighter), impact_bid, impact_ask (if exposed) |
| `funding_pred` | recv_ts_us, venue, market, predicted_rate_h (fraction), next_funding_ts_us, source |
| `funding_paid` | funding_ts_us, venue, market, rate_h (fraction, precise), rate_raw (as published), value_per_unit (Lighter), pay_price, pay_price_type (oracle/index), index_source |
| `market_attrs` | recv_ts_us, venue, market, is_outside_rth, settlement_price, upper_bound, lower_bound, next_upper, next_lower, upper_in_zone, lower_in_zone, upper_zone_entered_ts, lower_zone_entered_ts, bound_event, imf, offhours_imf, oi, oi_cap |
| `param_changes` | ts_us, venue, market, field, old_value, new_value |
| `latency_probe` | ts_us, venue, market, action (place/cancel/modify), send_ts_us, ack_ts_us, visible_ts_us, gone_ts_us, rtt_us |
| `clock_offset` | ts_us, ntp_offset_us, source |
| `recorder_health` | ts_us, venue, market, channel, last_msg_age_ms, msgs_per_min, resubscribes, gaps |
| `candles_oracle` (Arcus) / `candles_trade` (Lighter) | open_ts_us, venue, market, resolution, o, h, l, c, volume, notional_volume, trade_count, taker_buy_volume |
| `own_orders` / `own_fills` (live and paper) | per E2 models + session_id, strategy, mode, reason |

## E4. Session config templates

**Market making (Arcus, Autopilot)**

```yaml
session_id: arcus_btc_mm
venue: arcus
account_index: 1            # Arcus subaccount 0-9 (own rate pools)
market: BTC                 # canonical base; mapped to BTC-USD at start-up
mode: auto                  # auto | mid | grid | rgrid | dgrid | blend | signal
live_enabled: false         # triple lock part 2
capital_usd: 35
leverage_max: 5
bias: neutral               # neutral | long_skew | short_skew
bias_size_usd: 0            # B: peak target inventory for skewed runs
execution_style: normal     # aggressive | normal | passive
offset_bps: 0               # Tread-style: Mid-1 = -1, Grid +2 = 2
spacing_bps: auto           # grid delta; auto = DGrid formula
levels_per_side: auto
order_size_usd: auto        # >= 1.2 x venue minimum ($6 Arcus)
inventory_cap_usd: 30
skew_kappa: 1.0
reset_threshold_pct: 0.25
recentre_after_s: 120
recentre_inventory: skew_exit   # skew_exit | maker_unwind | hedge_other_venue
stop_loss_pct: 10           # of margin
take_profit_pct: null       # null = uncapped
participation_cap_pct: 25
requote: {min_ticks: 2, min_frac_of_half_spread: 0.25}
safety_pause: {move_sigma_1s: 6, spread_x_median: 3, depth_frac_min: 0.3, resume_s: 30}
session:
  duration: 8h
  repeat: 20
  windows_ist: []           # optional, e.g. ["06:30-12:30"] from the owner's IST slot research
  skip_events: [cpi, fomc, nfp, earnings]
off_hours: {spacing_mult: 2, size_mult: 0.25, allow_mid: false}   # Arcus RWA only
blend: {reference: ["lighter_rh:BTC", "pyth"], weights: [0.6, 0.4], stale_ms: 2000, dev_bps: 25}
signal: {rsi_len: 14, rsi_low: 25, rsi_high: 75, tp_bps: 15, sl_bps: 25, cooldown_s: 300, max_hold_min: 120}
autopilot:
  confirm_evals: 5
  min_dwell_min: 30
  er_trend: 0.5
  er_range: 0.3
  trend_z: 2.0
  oer_min: 1.5
  markout_stop_bps: -2
  target_fills_per_hour: 20
  k_delta: 1.0
  delta_min_bps: 5
  delta_max_bps: 100
```

**Delta-neutral (SPY, Arcus ↔ Lighter RH)**

```yaml
session_id: dn_spy
strategy: dn_carry          # dn_carry | dn_hedged_mm | points_overlay
market: SPY
live_enabled: false
legs:
  arcus: {account_index: 2, role: maker}      # ALO entries and exits
  lighter_rh: {role: hedge}                   # IOC hedge per Arcus fill
collateral_per_leg_usd: 25
leverage_per_leg: 3
entry_ev_bps: 5
exit_ev_bps: 0
horizons_h: [4, 8, 12, 24, 48, 72]
max_hold_h: 168
spread_forecast: persistence   # persistence | ar1 | gbm
hedge_slippage_cap_bps: 10
min_hedge_usd: 10
arcus_reprice_max: 5
arcus_inside_spread_ticks: 1
margin_warn_x_mm: 3.0
margin_crit_x_mm: 1.8
avoid: [earnings, ex_dividend]
```

**Hedged MM variant:** `strategy: dn_hedged_mm`, `half_spread_bps: 8`, `fair_weight_lighter: 0.7`, `hedge_slippage_cap_bps: 10`, `hedge_timeout_s: 3`, `lighter_down_kill_s: 10`.

## E5. Venue config skeletons

```yaml
# config/venues/arcus.yaml
venue: arcus
env: testnet                     # testnet | mainnet
rest: {mainnet: "https://api.arcus.xyz", testnet: "https://api.testnet.arcus.xyz"}
ws:   {mainnet: "wss://api.arcus.xyz/v1/ws", testnet: "wss://api.testnet.arcus.xyz/v1/ws"}
chain_id: {mainnet: 4663, testnet: 46630}
eip712_api_key_domain: {name: "Arcus API Key", version: "1"}
signing: {scheme1_ops: {place: 1, cancel: 2, modify: 3, tpsl: 4}, tif: {GTT: 0, FOK: 1, IOC: 2, ALO: 3}, side: {BUY: 0, SELL: 1}}
good_til_days: 35
ip_bucket: {capacity: 1500, refill_per_min: 1500, target_max_util: 0.5}
pools: {order_start: 20000, cancel_start: 40000, usd_per_unit: 0.10, drip_s: 10, cancel_all_cost: 1000}
batch: {place_max_free: 39, cancel_max: 100}
ws_limits: {connections: 50, subs_per_conn: 100, inflight_post: 50, lifetime_h: 24}
taker_speed_bump_ms: 50
dms: {refresh_s: 20, deadline_s: 60, min_lead_s: 5, max_lead_s: 300, max_fires_per_day: 10}
live_fields: [markets, feetiers, fundingRates, rateLimit, compliance]
```

```yaml
# config/venues/lighter_rh.yaml
venue: lighter_rh
rest: "https://api.rh.lighter.xyz"
ws: "wss://api.rh.lighter.xyz/stream"
signing_chain_id: 466324         # [VERIFY]
api_key_index: 3
tx_types: {change_pub_key: 8, transfer: 12, create_order: 14, cancel_order: 15, cancel_all: 16, update_leverage: 20, approve_integrator: 45}
order_type: {limit: 0, market: 1}
tif: {ioc: 0, gtt: 1, post_only: 2}   # [VERIFY] post_only against official SDK
tx_lifetime_ms: 599000
order_expiry_days: 28
auth_token: {lifetime_h: 7, refresh_before_min: 10}
limits_standard: {rest_weight_per_min: 60, sendtx_per_min: 60, pending_per_market: 10, pending_per_account: 50, active_per_market: 30, active_per_account: 250, ws_msgs_per_min: 200, ws_batch_max: 15, ws_ping_s: 60}
latency_ms: {maker: [0, 200], taker: 300, cancel: 300}   # maker [VERIFY]
min_quote_usd: 10
live_fields: [orderBooks, orderBookDetails, fundings]
```

## E6. Hand-off template (`handoffs/PHASE_N_HANDOFF.md`)

```markdown
# Phase N hand-off: <name>
Date: YYYY-MM-DD · Commit: <sha> · Agent/tool: <name>

## 1. What was built
- <component>: <one line> (path)

## 2. How to run it
<exact commands>

## 3. Acceptance tests
| Test | Command | Result | Evidence (file/log) |

## 4. Measured values and [VERIFY] results
| Item | Expected (Part A) | Observed | Source |

## 5. Deviations from the prompt pack (and why)

## 6. Known issues and risks

## 7. [QUESTION FOR OWNER]
1. …

## 8. Next phase prerequisites
- [ ] …
```

## E7. Checklists and reference tables

**Kill switches (implement all; unit-test each)**

| Trigger | Action | Resume |
| --- | --- | --- |
| Session loss ≥ margin × SL% (default 10%) | Cancel quotes; flatten maker-first, IOC after T | Next scheduled session |
| Daily loss > 3% of capital | Stop that venue for the UTC day; alert | Manual or next day |
| Drawdown > 10% of capital | Stop all strategies, flatten, alert CRIT | Manual only |
| Distance to liquidation < 4σ (1 h) | Reduce position by half; DN: transfer alert / proportional cut | Back above 6σ |
| Safety pause (6σ 1-s move, spread > 3× median, depth < 30% of median) | Cancel quotes; keep position | 30 s normal |
| Event window (CPI, FOMC, NFP ±30 min; earnings ±24 h; ex-dividend) | No new quotes; tighten exits | Window end |
| Arcus band in expansion zone, OI cap reached | Stop quoting that market | Cleared |
| Hedge leg missing > 5 s | Cancel maker quotes; flatten unhedged leg by taker | Both venues healthy 5 min |
| Bot heartbeat silent 60 s | DMS fires on Arcus; guardian cancel-all both venues | Manual after reconciliation |
| Arcus pool < 5% or Lighter 429/405 | Freeze requotes; cancels only | Budget recovered |
| DMS refresh fails twice | Safe mode | Manual |
| Unexpected SELF_TRADE or GEO_RESTRICTED | Stop venue; alert CRIT | Manual |

**Metrics (every run, and split by session: RTH, pre, post, overnight, weekend, crypto)**

Net PnL and its terms (spread capture, inventory MTM, funding per leg, fees, hedge cost, liquidation loss); volume; volume per $ capital per day; CPM; OI-hours ($·h); max drawdown (%), longest drawdown (h); max |inventory| ($); minimum distance to liquidation (σ); liquidation count; fill rate; post-only reject rate; average queue position at fill; markouts at 1/5/30/60/300 s (mean, median, by Arcus taker address); actions per filled dollar; mode time shares and switch counts; DN: hedge latency, hedge slippage (bps), residual exposure over time, hedge failures; points (owner-entered) per $1M volume and per $·h OI.

**Live-readiness checklist (end of P5, re-checked before each P6 stage)**

- [ ] Region check passes for both venues from the server IP
- [ ] Arcus keys valid > 7 days; rotation tested; wallet key not on server
- [ ] Lighter API key registered; integrator question resolved
- [ ] Triple lock verified; only approved session files have `live_enabled: true`
- [ ] DMS refresher and guardian tested on testnet (C1)
- [ ] Alerts arrive on the owner's phone (test message)
- [ ] Kill-switch thresholds match the session files
- [ ] Recorder running gap-free; disk > 30% free
- [ ] PAPER_REPORT.md approved; GO_NO_GO.md approved
- [ ] RUNBOOK.md read by owner
- [ ] Capital deposited per the plan: Arcus sub 1 $35 + $8 reserve; (stage 2) Arcus sub 2 $25; Lighter $25 + $7 reserve

**Pass/fail bar (live, per 4-week window)**

| Metric | Target |
| --- | --- |
| Net PnL after all costs | ≥ $0, or cost ≤ 1 bp of volume ($100 per $1M) |
| Liquidations | 0 |
| Max drawdown | ≤ 10% of allocated capital (≤ 8% for DN) |
| Distance to liquidation | never below 4σ (1 h) |
| Cross-venue hedge residual | ≤ 2× venue min notional for ≤ 5 s |
| Orphaned orders after a crash | 0 |

---

*End of prompt pack. If anything here conflicts with the live venue APIs, the live API wins; record the conflict in the phase hand-off and update Part A.*