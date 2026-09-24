# Venue facts corrected against the live docs and APIs (2026-09-23)

Where the prompt pack (Part A / E5) and the venues disagree, the code follows the venue. Each row gives the source.
`config/venues/*.yaml` carries the corrected values. [LIVE] values are fetched hourly by `core/liveparams.py`, and any
change is logged to `data/param_changes_jsonl/`.

## Lighter RH

| Item | Prompt pack | Observed | Source |
|---|---|---|---|
| Testnet | "no known testnet" | Exists: `api.rh-testnet.lighter.xyz`, signing chain id **300** | apidocs get-started, lighter-sdk `signer_client` |
| Mainnet signing chain id | 466324 [VERIFY] | **466324** confirmed | apidocs get-started, lighter-sdk |
| API key slot | 3 (delta-farmer) | Slots **0 to 3 and 157 are reserved** by Lighter's apps; The bot uses **4** | apidocs api-keys |
| Signer | port of delta-farmer crypto [VERIFY] | Official **lighter-sdk 1.1.4** native signer via ctypes. The delta-farmer port is not needed. | PyPI lighter-sdk; `venues/lighter_rh/signer/` |
| Integrator approval (tx 45) | maybe required | **Not needed** for own-account trading. It lets a partner charge fees and is rejected on standard accounts. | apidocs partner-attribution |
| Key registration | – | The account must hold collateral first, otherwise error **21126** ("Account with zero collateral can't change PublicKey") | apidocs data-structures-constants-and-errors |
| Order limits (standard) | pending 10/market, 50/account; active 30/market, 250/account | pending **16/market, 500/account**; active **1000/market, 1500/account**. The bot keeps its own tighter design caps of 10 pending and 30 active per market. | apidocs rate-limits |
| REST budget | 60 weighted/min | 60/min, with per-endpoint weights (sendTx 6 … trades 600). `sendTx` over **WS** does not count toward the 200 msgs/min WS limit. | apidocs rate-limits |
| Maker/cancel latency (standard) | maker 0 to 200 ms [VERIFY], cancel 300 ms | documented **200 ms** for standard maker and cancel. Taker 300 ms. Measured by the B3 probe once the owner runs it. | apidocs account-types (Standard table) |
| Order expiry | 28 days | 5 min to 30 days (ms). The bot uses 28 days. | apidocs |
| `fundings.rate` | – | **Percent and rounded** (0.0004 = 4e-6). The precise rate is `value / price`. | live `/api/v1/fundings`; `docs/notes/lighter_ws_formats.md` |
| Order book WS | format [VERIFY] | Snapshot, then deltas chained by `begin_nonce`. `offset` is not a sequence. | live capture; `docs/notes/lighter_ws_formats.md` |
| Minimum order | "$10" | Also a **min base**: BTC 0.0002 (≈ $17 at $86k) | live `/api/v1/orderBookDetails` |
| Cancel-all TIF | – | 0 immediate, 1 **scheduled (Lighter-side dead man's switch)**, 2 abort | apidocs, lighter-sdk |
| Deposit contract | – | `0x94bAB9693Ba2f6358507eFfcbd372b0660AFfF9d` | apidocs deposits-transfers-and-withdrawals |

## Arcus

| Item | Prompt pack | Observed | Source |
|---|---|---|---|
| Minimum order | "$5 opening" | Also a **min size**. BTC 0.0001 (≈ $8.6 at $86k) is binding, so the bot's 1.2× order is about **$10.3**. | live `/v1/markets` |
| `goodTilTime` | 35 days default | **Required ≥ about 1 month on every order, including IOC and FOK.** The bot always sends 35 days. | docs.arcus.xyz (REST trading) |
| ALO | maker TIF | ALO **skips the 50 ms taker speed bump**. Takers are delayed by 50 ms. | docs.arcus.xyz |
| IP budget | – | **1500 weight/min per IP**. The bot targets ≤ 50% utilisation. | docs rate-limits |
| Action pools | – | order pool 20k, cancel pool 40k, growing by **1 unit per $0.10 of fills**; cancel-all costs 1000 | docs rate-limits; `/v1/rateLimit` |
| Batches | – | `batchPlaceOrders` ≤ 39 free, `batchCancel` ≤ 100 | docs rate-limits |
| scheduleCancel | 5 s to 5 min | confirmed; **max 10 fires/day**. The bot refreshes every 20 s with a 60 s deadline. | docs schedule-cancel |
| EIP-712 CreateApiKey | domain with chainId | `{"name": "Arcus API Key", "version": "1", "chainId": 4663 or 46630}`, **no verifyingContract** | docs authentication |
| WS per-market id | – | the `id` field is the **market display name** (`BTC-USD`). `l2OrderbookUpdates` deltas are spliced by level. | live capture; `tests/fixtures/live/arcus_ws_frames.json` |
| WS limits | – | 50 connections, 100 subs/connection, 1000 subs/IP, 50 in-flight posts, 24 h lifetime | docs websocket |
| Signing | scheme 1 / 2 | As in the pack; verified by `tests/unit/test_arcus_signing.py` (layout, identity rules, signature verification) | docs authentication |
| Modify identity | [VERIFY] | **Still open.** `scripts/testnet_cycle.py` tries the newest rule (exactly one of `id` / `c`) first and reports the result. | needs B2 on testnet |
| Liquidation fee, partial vs full | [VERIFY] | **Still open.** Not documented, and cannot be measured without a liquidation. The simulator models a full close at mark when equity ≤ maintenance margin, with the fee as a parameter (default 1%). | `research/sim/margin.py` |

## Market-structure observations (not errors, but they change the plan)

- **Arcus BTC:** one taker address made about 78% of taker notional in the recorded sample. Quotes face one dominant
  counterparty. Tracked as `arcus_top_taker_share` in diagnostics.
- **Funding:** Arcus funding runs higher than Lighter's on every RWA market during RTH, so Arcus longs pay more (for example NVDA: 18.9%/yr against
  5.6%/yr). Off-hours and weekends are close to equal. See `reports/carry/2026-09-23/CARRY_STUDY.md`.
