# Arcus facts corrected against the live docs and APIs (2026-09-23)

Where the original design notes and the venue disagree, the code follows the venue. Each row gives the source.
`config/venues/arcus.yaml` carries the corrected values. [LIVE] values are fetched hourly by `core/liveparams.py`, and any
change is logged to `data/param_changes_jsonl/`.

## Arcus

| Item | Design notes said | Observed | Source |
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
| Modify identity | [VERIFY] | The docs disagree (modify page: exactly one of `id`/`c`; authentication page: `id` always, `c` echoed). Live 2026-09-25: every modify by clientId alone was refused, 14 by orderId were not. The bot only modifies by orderId, and only with `use_modify: true` after `bot selftest --allow-funded` passes. | docs/incidents/2026-09-25-live-requotes-refused.md |
| Liquidation fee | [VERIFY] | **Still open.** Not documented, and cannot be measured without a liquidation. The test simulator (`tests/sim/margin.py`) models a full close at mark with a 1% fee. | – |

## Market-structure observations (not errors, but they change the plan)

- **Arcus BTC:** one taker address made about 78% of taker notional in the recorded sample. Quotes face one dominant
  counterparty.
