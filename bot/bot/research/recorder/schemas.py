"""Parquet schemas (prompt pack Appendix E3). All timestamps int64 µs UTC; prices/sizes as decimal strings so no
precision is lost (research code casts to float64 or Decimal as needed)."""

from __future__ import annotations

import pyarrow as pa

LEVEL = pa.struct([("price", pa.string()), ("size", pa.string())])

SCHEMAS: dict[str, pa.Schema] = {
    "book_deltas": pa.schema([
        ("recv_ts_us", pa.int64()), ("venue_ts_us", pa.int64()), ("venue", pa.string()), ("market", pa.string()),
        ("seq", pa.int64()), ("global_seq", pa.int64()), ("side", pa.string()), ("price", pa.string()),
        ("size", pa.string()), ("is_snapshot", pa.bool_()),
    ]),
    "book_snapshots": pa.schema([
        ("recv_ts_us", pa.int64()), ("venue_ts_us", pa.int64()), ("venue", pa.string()), ("market", pa.string()),
        ("seq", pa.int64()), ("bids", pa.list_(LEVEL)), ("asks", pa.list_(LEVEL)), ("n_levels", pa.int32()),
    ]),
    "bbo": pa.schema([
        ("recv_ts_us", pa.int64()), ("venue_ts_us", pa.int64()), ("venue", pa.string()), ("market", pa.string()),
        ("seq", pa.int64()), ("bid_px", pa.string()), ("bid_sz", pa.string()), ("ask_px", pa.string()),
        ("ask_sz", pa.string()),
    ]),
    "trades": pa.schema([
        ("recv_ts_us", pa.int64()), ("venue_ts_us", pa.int64()), ("venue", pa.string()), ("market", pa.string()),
        ("trade_id", pa.string()), ("price", pa.string()), ("size", pa.string()), ("taker_side", pa.string()),
        ("maker_address", pa.string()), ("taker_address", pa.string()), ("maker_order_id", pa.string()),
        ("seq", pa.int64()), ("is_liquidation", pa.bool_()),
    ]),
    "mark_oracle_index": pa.schema([
        ("recv_ts_us", pa.int64()), ("venue_ts_us", pa.int64()), ("venue", pa.string()), ("market", pa.string()),
        ("mark", pa.string()), ("oracle", pa.string()), ("index", pa.string()), ("impact_bid", pa.string()),
        ("impact_ask", pa.string()),
    ]),
    "funding_pred": pa.schema([
        ("recv_ts_us", pa.int64()), ("venue", pa.string()), ("market", pa.string()),
        ("predicted_rate_h", pa.float64()), ("next_funding_ts_us", pa.int64()), ("source", pa.string()),
        ("premium", pa.float64()),
    ]),
    "funding_paid": pa.schema([
        ("funding_ts_us", pa.int64()), ("venue", pa.string()), ("market", pa.string()), ("rate_h", pa.float64()),
        ("rate_raw", pa.string()), ("value_per_unit", pa.string()), ("pay_price", pa.float64()),
        ("pay_price_type", pa.string()), ("index_source", pa.string()),
    ]),
    "market_attrs": pa.schema([
        ("recv_ts_us", pa.int64()), ("venue", pa.string()), ("market", pa.string()), ("is_outside_rth", pa.bool_()),
        ("settlement_price", pa.string()), ("upper_bound", pa.string()), ("lower_bound", pa.string()),
        ("next_upper", pa.string()), ("next_lower", pa.string()), ("upper_in_zone", pa.bool_()),
        ("lower_in_zone", pa.bool_()), ("upper_zone_entered_ts", pa.int64()), ("lower_zone_entered_ts", pa.int64()),
        ("bound_event", pa.string()), ("imf", pa.string()), ("offhours_imf", pa.string()), ("oi", pa.string()),
        ("oi_cap", pa.string()),
    ]),
    "param_changes": pa.schema([
        ("ts_us", pa.int64()), ("venue", pa.string()), ("market", pa.string()), ("field", pa.string()),
        ("old_value", pa.string()), ("new_value", pa.string()),
    ]),
    "latency_probe": pa.schema([
        ("ts_us", pa.int64()), ("venue", pa.string()), ("market", pa.string()), ("action", pa.string()),
        ("send_ts_us", pa.int64()), ("ack_ts_us", pa.int64()), ("visible_ts_us", pa.int64()),
        ("gone_ts_us", pa.int64()), ("rtt_us", pa.int64()),
    ]),
    "clock_offset": pa.schema([("ts_us", pa.int64()), ("ntp_offset_us", pa.int64()), ("source", pa.string())]),
    "recorder_health": pa.schema([
        ("ts_us", pa.int64()), ("venue", pa.string()), ("market", pa.string()), ("channel", pa.string()),
        ("last_msg_age_ms", pa.int64()), ("msgs_per_min", pa.int64()), ("resubscribes", pa.int64()),
        ("gaps", pa.int64()),
    ]),
    "candles_oracle": pa.schema([
        ("open_ts_us", pa.int64()), ("venue", pa.string()), ("market", pa.string()), ("resolution", pa.string()),
        ("o", pa.float64()), ("h", pa.float64()), ("l", pa.float64()), ("c", pa.float64()), ("volume", pa.float64()),
        ("notional_volume", pa.float64()), ("trade_count", pa.int64()), ("taker_buy_volume", pa.float64()),
    ]),
    "candles_trade": pa.schema([
        ("open_ts_us", pa.int64()), ("venue", pa.string()), ("market", pa.string()), ("resolution", pa.string()),
        ("o", pa.float64()), ("h", pa.float64()), ("l", pa.float64()), ("c", pa.float64()), ("volume", pa.float64()),
        ("notional_volume", pa.float64()), ("trade_count", pa.int64()), ("taker_buy_volume", pa.float64()),
    ]),
    "own_orders": pa.schema([
        ("ts_us", pa.int64()), ("venue", pa.string()), ("market", pa.string()), ("client_id", pa.string()),
        ("venue_order_id", pa.string()), ("status", pa.string()), ("side", pa.string()), ("price", pa.string()),
        ("size", pa.string()), ("filled_size", pa.string()), ("reject_reason", pa.string()), ("session_id", pa.string()),
        ("strategy", pa.string()), ("mode", pa.string()), ("reason", pa.string()),
    ]),
    "own_fills": pa.schema([
        ("ts_us", pa.int64()), ("venue", pa.string()), ("market", pa.string()), ("client_id", pa.string()),
        ("trade_id", pa.string()), ("side", pa.string()), ("price", pa.string()), ("size", pa.string()),
        ("fee", pa.string()), ("is_maker", pa.bool_()), ("liquidation", pa.bool_()), ("session_id", pa.string()),
        ("strategy", pa.string()), ("mode", pa.string()), ("reason", pa.string()),
    ]),
}

# Tables not partitioned by market (one global file set per venue/date).
GLOBAL_TABLES = {"clock_offset", "param_changes"}
