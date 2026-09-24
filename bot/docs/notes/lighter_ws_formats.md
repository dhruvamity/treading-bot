# Lighter RH WebSocket formats (observed live)

Source: `wss://api.rh.lighter.xyz/stream?readonly=true`, captured 2026-09-23 with `scripts/ws_capture.py`. There are
703 frames over 20 s in `tests/fixtures/live/lighter_ws_frames.json`. Tests replay these frames:
`test_replay_live_lighter_frames_no_gap`, `test_lighter_ws_dispatches_live_frames`.

## Envelope

- First frame: `{"type": "connected", "session_id": ...}`.
- Subscribe: `{"type": "subscribe", "channel": "order_book/1"}`. The subscribe request uses `/`, but replies and
  updates name the channel with `:`, as in `"channel": "order_book:1"`.
- Types: `subscribed/<channel>` for the first message, then `update/<channel>`.
- Ping: send `{"type": "ping"}` every 60 s. The server drops the connection after 2 min of silence.

## order_book:{market_id}

```json
{"type": "update/order_book", "channel": "order_book:1", "offset": 3429146, "timestamp": ..., "last_updated_at": ...,
 "order_book": {"code": 0, "asks": [{"price": "86522.7", "size": "0.00200"}], "bids": [...],
                "offset": 3429146, "nonce": 2263973040, "begin_nonce": 2263972971, "last_updated_at": ...}}
```

- `subscribed/order_book` is a **full snapshot**. On BTC it had 631 asks and 1,889 bids. It carries `nonce`, with
  `begin_nonce` = 0.
- `update/order_book` is a **delta**: absolute sizes per price, where size `"0"` deletes the level.
- **Continuity rule:** apply a delta only if `begin_nonce == last_nonce`, then set `last_nonce = nonce`. If `nonce`
  is at or below `last_nonce`, the delta is stale: drop it. Any other `begin_nonce` is a gap: resubscribe for a fresh
  snapshot. See `core/book.py: LighterBookSync`.
- `offset` is **not** a sequence. It skipped (3429147 → 3429149) while the nonce chain stayed continuous.

## trade:{market_id}

`{"type": "update/trade", "channel": "trade:1", "nonce": ..., "trades": [...], "liquidation_trades": [...]}`.

- Each trade carries `trade_id`, `price`, `size`, `usd_amount`, `is_maker_ask`, `timestamp` (ms), `transaction_time`
  (µs), `tx_hash`, `block_height`, and both sides' `*_account_id`, `*_client_id` and `*_position_size_before`.
- Taker side: if `is_maker_ask` is true, the taker bought.
- Liquidations come in a separate list with the same schema. Both lists are recorded, and liquidations are tagged.

## market_stats:{market_id}

Fields:

- `index_price`, `mark_price`, `mid_price`, `best_bid_price`, `best_ask_price`, `last_trade_price`;
- `open_interest` (quote), `open_interest_limit`;
- `current_funding_rate`, `funding_rate`, `funding_timestamp`, `funding_clamp_small`, `funding_clamp_big`,
  `base_interest_rate`, `premium`;
- daily volume, high and low.

The funding and premium fields are **percent**. Divide by 100, as the runner and recorder do.

## ticker:{market_id}

`{"ticker": {"s": "BTC", "a": {"price", "size"}, "b": {"price", "size"}, "last_updated_at"}, "nonce": ...}`: the BBO
only. The recorder stores it as Lighter's `bbo` table. The trading engine reads the top of book from the synced
order book.

## REST notes

- `fundings.rate` is a rounded **percent** (`"0.0004"` means 4e-6). The precise hourly rate is `value / price`.
  Check: SPY value 0.0030498 at a mark-candle open of 762.38 gives 4.00e-6/h. The prompt pack's check (≈ 3.94e-6 at
  index ≈ 773.3) reconstructs the same way. All 2,120 hourly rows per market were rebuilt from the mark-candle open.
- `sendTx` is `application/x-www-form-urlencoded` (`tx_type`, `tx_info`, `price_protection`), not JSON.
