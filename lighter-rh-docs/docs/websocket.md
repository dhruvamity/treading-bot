---
updatedAt: 2026-08-24T17:59:41.000Z
---

Fetch the complete documentation index at: https://apidocs.rh.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further. Append .md to any documentation page URL to get its markdown version.

# WebSocket

# Connection

URL: `wss://api.rh.lighter.xyz/stream`

You can directly connect to the WebSocket server using wscat:

```shell
wscat -c 'wss://api.rh.lighter.xyz/stream'
```

If you're connecting from a restricted region, you can still access read-only data:

```shell
wscat -c 'wss://api.rh.lighter.xyz/stream?readonly=true'
```

# Keepalive Requirements

Clients are responsible for keeping the connection alive by sending at least one frame every **2 minutes**. This can be either:

* A **WebSocket ping frame**
* **Any application-level message**

If the server receives no frames from a client within the 2-minute window, it will close the connection.

Support for `permessage-deflate` compression is enabled.

Clients that fall behind on reading messages may be disconnected more aggressively than before.

# Send Tx

You can send transactions using the websocket as follows:

```json
{
    "type": "jsonapi/sendtx",
    "data": {
        "tx_type": INTEGER,
        "tx_info": ...
    }
}
```

The *tx\_type* options can be found in the [SignerClient](https://github.com/elliottech/lighter-python/blob/main/lighter/signer_client.py) file, while *tx\_info* can be generated using the sign methods in the SignerClient.

Example: [create\_modify\_cancel\_order\_ws.py](https://github.com/elliottech/lighter-python/blob/main/examples/orders/create_modify_cancel_order_ws.py)

# Send Batch Tx

You can send batch transactions to execute up to 15 transactions in a single message.

```json
{
    "type": "jsonapi/sendtxbatch",
    "data": {
        "tx_types": "[INTEGER]",
        "tx_infos": "[tx_info]"
    }
}
```

The *tx\_type* options can be found in the [SignerClient](https://github.com/elliottech/lighter-python/blob/main/lighter/signer_client.py) file, while *tx\_info* can be generated using the sign methods in the SignerClient.

Example: [send\_batch\_tx\_ws.py](https://github.com/elliottech/lighter-python/blob/main/examples/batch-orders/send_batch_tx_ws.py)

# Types

We first need to define some types that appear often in the JSONs.

## Transaction JSON

To decode `event_info`, you can refer to [Data Structures](https://apidocs.rh.lighter.xyz/docs/data-structures-constants-and-errors).

```json
Transaction = {
    "hash": STRING,
    "type": INTEGER,
    "info": STRING,                // json object as string, attributes depending on the tx type
    "event_info": STRING,          // json object as string, attributes depending on the tx type
    "status": INTEGER,
    "transaction_index": INTEGER,
    "l1_address": STRING,
    "account_index": INTEGER,
    "nonce": INTEGER,
    "expire_at": INTEGER,
    "block_height": INTEGER,
    "queued_at": INTEGER,
    "executed_at": INTEGER,
    "sequence_index": INTEGER,
    "parent_hash": STRING,
    "api_key_index": INTEGER,
    "transaction_time": INTEGER
}
```

Example:

```json
{
    "hash": "0xabc123456789def",
    "type": 15,
    "info": "{\"AccountIndex\":1,\"ApiKeyIndex\":2,\"MarketIndex\":3,\"Index\":404,\"ExpiredAt\":1700000000000,\"Nonce\":1234,\"Sig\":\"0xsigexample\"}",
    "event_info": "{\"a\":1,\"i\":404,\"u\":123,\"ae\":\"\"}",
    "status": 2,
    "transaction_index": 10,
    "l1_address": "0x123abc456def789",
    "account_index": 101,
    "nonce": 12345,
    "expire_at": 1700000000000,
    "block_height": 1500000,
    "queued_at": 1699999990000,
    "executed_at": 1700000000005,
    "sequence_index": 5678,
    "parent_hash": "0xparenthash123456",
    "api_key_index": 2,
    "transaction_time": 1700000000005000
}
```

Used in: [Account Tx](https://apidocs.rh.lighter.xyz/docs/websocket-reference#account-tx).

## Order JSON

```json
Order = {
    "order_index": INTEGER,
    "client_order_index": INTEGER,
    "order_id": STRING, // same as order_index but string
    "client_order_id": STRING, // same as client_order_index but string
    "market_index": INTEGER,
    "owner_account_index": INTEGER,
    "initial_base_amount": STRING,
    "price": STRING,
    "nonce": INTEGER,
    "remaining_base_amount": STRING,
    "is_ask": BOOLEAN,
    "base_size": INTEGER,
    "base_price": INTEGER,
    "filled_base_amount": STRING,
    "filled_quote_amount": STRING,
    "side": STRING,
    "type": "limit" | "market" | "stop-loss" | "stop-loss-limit" | "take-profit" | "take-profit-limit" | "twap" | "twap-sub" | "liquidation",
    "time_in_force": "good-till-time" | "immediate-or-cancel" | "post-only" | "Unknown",
    "reduce_only": BOOLEAN,
    "trigger_price": STRING,
    "order_expiry": INTEGER,
    "status": "in-progress" | "pending" | "open" | "filled" | "canceled" | "canceled-post-only" | "canceled-reduce-only" | "canceled-position-not-allowed" | "canceled-margin-not-allowed" | "canceled-too-much-slippage" | "canceled-not-enough-liquidity" | "canceled-self-trade" | "canceled-expired" | "canceled-oco" | "canceled-child" | "canceled-liquidation" | "canceled-invalid-balance",
    "trigger_status": "na" | "ready" | "mark-price" | "twap" | "parent-order",
    "trigger_time": INTEGER,
    "parent_order_index": INTEGER,
    "parent_order_id": STRING,
    "to_trigger_order_id_0": STRING,
    "to_trigger_order_id_1": STRING,
    "to_cancel_order_id_0": STRING,
    "integrator_fee_collector_index": STRING,
    "integrator_taker_fee": STRING,
    "integrator_maker_fee": STRING,
    "block_height": INTEGER,
    "timestamp": INTEGER,
    "created_at": INTEGER,
    "updated_at": INTEGER,
    "transaction_time": INTEGER
}
```

Used in: [Account Market](https://apidocs.rh.lighter.xyz/docs/websocket-reference#account-market), [Account All Orders](https://apidocs.rh.lighter.xyz/docs/websocket-reference#account-all-orders), [Account Orders](https://apidocs.rh.lighter.xyz/docs/websocket-reference#account-orders).

## Trade JSON

```json
Trade = {
    "trade_id": INTEGER,
    "trade_id_str": STRING,
    "tx_hash": STRING,
    "type": "trade" | "liquidation" | "deleverage" | "market-settlement",
    "market_id": INTEGER,
    "size": STRING,
    "price": STRING,
    "usd_amount": STRING,
    "ask_id": INTEGER,
    "ask_id_str": STRING,
    "bid_id": INTEGER,
    "bid_id_str": STRING,
    "ask_client_id": INTEGER,
    "ask_client_id_str": STRING,
    "bid_client_id": INTEGER,
    "bid_client_id_str": STRING,
    "ask_account_id": INTEGER,
    "bid_account_id": INTEGER,
    "is_maker_ask": BOOLEAN,
    "block_height": INTEGER,
    "timestamp": INTEGER,
    "taker_fee": INTEGER,                              // omitempty
    "taker_position_size_before": STRING,               // omitempty
    "taker_entry_quote_before": STRING,                 // omitempty
    "taker_initial_margin_fraction_before": INTEGER,    // omitempty
    "taker_position_sign_changed": BOOLEAN,             // omitempty
    "maker_fee": INTEGER,                               // omitempty
    "maker_position_size_before": STRING,               // omitempty
    "maker_entry_quote_before": STRING,                 // omitempty
    "maker_initial_margin_fraction_before": INTEGER,    // omitempty
    "maker_position_sign_changed": BOOLEAN,             // omitempty
    "transaction_time": INTEGER,
}
```

Example:

```json
{
    "trade_id":16164557907,
    "trade_id_str":"16164557907",
    "tx_hash":"019f2b9c9cc609196316a569541e135a739728e0837fcfb05c913534e305e503c01d5dcfeabeaf81",
    "type":"trade",
    "market_id":0,
    "size":"0.1336",
    "price":"2181.83",
    "usd_amount":"291.492488",
    "ask_id":281476612587355,
    "ask_id_str":"281476612587355",
    "bid_id":562948334068259,
    "bid_id_str":"562948334068259",
    "ask_client_id":363283,
    "ask_client_id_str":"363283",
    "bid_client_id":23004521241,
    "bid_client_id_str":"23004521241",
    "ask_account_id":57890,
    "bid_account_id":317068,
    "is_maker_ask":false,
    "block_height":198321831,
    "timestamp":1773854156654,
    "taker_position_size_before":"80.5467",
    "taker_entry_quote_before":"178806.039128",
    "taker_initial_margin_fraction_before":500,
    "taker_fee": 196, // omitted if zero
    "maker_fee":28,
    "maker_position_size_before":"-4.3180",
    "maker_entry_quote_before":"9419.085856",
    "maker_initial_margin_fraction_before":200,
    "transaction_time":1773854156686065
}
```

Used in: [Trade](https://apidocs.rh.lighter.xyz/docs/websocket-reference#trade), [Account All](https://apidocs.rh.lighter.xyz/docs/websocket-reference#account-all), [Account Market](https://apidocs.rh.lighter.xyz/docs/websocket-reference#account-market), [Account All Trades](https://apidocs.rh.lighter.xyz/docs/websocket-reference#account-all-trades).

## Position JSON

```json
Position = {
    "market_id": INTEGER,
    "symbol": STRING,
    "initial_margin_fraction": STRING,
    "open_order_count": INTEGER,
    "pending_order_count": INTEGER,
    "position_tied_order_count": INTEGER,
    "sign": INTEGER,
    "position": STRING,
    "avg_entry_price": STRING,
    "position_value": STRING,
    "unrealized_pnl": STRING,
    "realized_pnl": STRING,
    "liquidation_price": STRING,
    "total_funding_paid_out": STRING,      // omitempty
    "margin_mode": INTEGER,
    "allocated_margin": STRING,
    "total_discount": STRING               // omitempty
}
```

Example:

```json
{
    "market_id": 101,
    "symbol": "BTC-USD",
    "initial_margin_fraction": "0.1",
    "open_order_count": 2,
    "pending_order_count": 1,
    "position_tied_order_count": 3,
    "sign": 1,
    "position": "0.5",
    "avg_entry_price": "20000.00",
    "position_value": "10000.00",
    "unrealized_pnl": "500.00",
    "realized_pnl": "100.00",
    "liquidation_price": "3024.66",
    "total_funding_paid_out": "34.2",
    "margin_mode": 1,
    "allocated_margin": "46342",
}
```

Used in: [Account All](https://apidocs.rh.lighter.xyz/docs/websocket-reference#account-all), [Account Market](https://apidocs.rh.lighter.xyz/docs/websocket-reference#account-market), [Account All Positions](https://apidocs.rh.lighter.xyz/docs/websocket-reference#account-all-positions).

## PoolShares JSON

```json
PoolShares = {
    "public_pool_index": INTEGER,
    "shares_amount": INTEGER,
    "entry_usdc": STRING,
    "principal_amount": STRING,
    "entry_timestamp": INTEGER
}
```

Example:

```json
{
    "public_pool_index": 1,
    "shares_amount": 100,
    "entry_usdc": "1000.00",
    "principal_amount": "3000",
    "entry_timestamp": 3600000
}
```

Used in: [Account All](https://apidocs.rh.lighter.xyz/docs/websocket-reference#account-all), [Account All Positions](https://apidocs.rh.lighter.xyz/docs/websocket-reference#account-all-positions).

## Asset JSON

```json
Asset = {
    "symbol": STRING,
    "asset_id": INTEGER,
    "balance": STRING,
    "locked_balance": STRING
}
```

Example:

```json
Asset = {
    "symbol": "ETH",
    "asset_id": 1,
    "balance": "6691.4917",
    "locked_balance": "564.6135"
}
```

Used in: [Account All Assets](#account-all-assets), [Account Market](#account-market), [Account All](#account-all).

## PositionFunding JSON

```json
PositionFunding = {
    "timestamp": INTEGER,
    "market_id": INTEGER,
    "funding_id": INTEGER,
    "change": STRING,
    "rate": STRING,
    "position_size": STRING,
    "position_side": "long" | "short",
    "discount": STRING
}
```

```json
{
    "timestamp": 1773850000000,
    "market_id": 0,
    "funding_id": 2001,
    "change": "0.001",
    "rate": "0.0001",
    "position_size": "0.5",
    "position_side": "long",
    "discount": "0"
}
```

# Channels

To unsubscribe from a channel, use the following syntax:

```json
{
    "type": "unsubscribe",
    "channel": STRING
}
```

## Order Book

The order book channel sends the new ask and bid orders for the given market in batches, every 50ms. While the nonce is tied to Lighter's matching engine, the offset is tied to the API servers; hence, you can expect the offset to change drastically on reconnection if you're routed to a different server. Regardless, on each update the offset will increase, but it's not guaranteed to be continuous. Additionally, this channel sends a complete snapshot on subscription, but only state changes after that. To verify the continuity of the data, you can check that `begin_nonce` on the current update matches the `nonce` (i.e. `last_nonce`) of the previous update.

```json
{
    "type": "subscribe",
    "channel": "order_book/{MARKET_INDEX}"
}
```

**Example Subscription**

```json
{
    "type": "subscribe",
    "channel": "order_book/0"
}
```

**Response Structure**

```json
{
    "channel": "order_book:{MARKET_INDEX}",
    "last_updated_at": INTEGER,
    "offset": INTEGER,
    "order_book": {
        "code": INTEGER,
        "asks": [
            {
                "price": STRING,
                "size": STRING
            }
        ],
        "bids": [
            {
                "price": STRING,
                "size": STRING
            }
        ],
        "offset": INTEGER,
        "nonce": INTEGER,
        "last_updated_at": INTEGER,
        "begin_nonce": INTEGER,

    },
    "timestamp": INTEGER,
    "type": "update/order_book"
}
```

**Example Response**

```json
{
   "channel":"order_book:0",
   "last_updated_at":1774884082309144,
   "offset":1558300,
   "order_book":{
      "code":0,
      "asks":[
         {
            "price":"2064.54",
            "size":"0.3285"
         }
      ],
      "bids":[
         
      ],
      "offset":1558300,
      "nonce":9182390020,
      "last_updated_at":1774884082309144,
      "begin_nonce":9182389998
   },
   "timestamp":1774884082326,
   "type":"update/order_book"
}
```

## Best Bid and Offer (BBO)

Updates are triggered on every nonce update for a given market's order book.

```json
{
   "type":"subscribe",
   "channel":"ticker/{MARKET_INDEX}"
}
```

**Example subscription:**

```json
{
   "type":"subscribe",
   "channel":"ticker/0"
}
```

**Response Structure:**

```json
{
   "channel": "ticker:{MARKET_INDEX}",
   "last_updated_at": INTEGER,
   "nonce": INTEGER,
   "ticker":{
      "s": STRING,
      "a":{
         "price": STRING,
         "size": STRING
      },
      "b":{
         "price": STRING,
         "size": STRING
      },
      "last_updated_at":INTEGER
   },
   "timestamp": INTEGER,
   "type":"update/ticker"
}
```

**Example Response:**

```json
{
   "channel":"ticker:0",
   "last_updated_at":1774883844921166,
   "nonce":9182249734,
   "ticker":{
      "s":"ETH",
      "a":{
         "price":"2064.48",
         "size":"0.4950"
      },
      "b":{
         "price":"2064.30",
         "size":"1.0392"
      },
      "last_updated_at":1774883844921166
   },
   "timestamp":1774883844933,
   "type":"update/ticker"
}
```

## Market Stats

The market stats channel sends the market stat data for a given market. `current_funding_rate` is an estimation of the upcoming funding payment, while `funding_rate` represents the last funding payment that occurred (at `funding_timestamp`). To fetch stats for spot markets, use [spot\_market\_stats](https://apidocs.lighter.xyz/docs/websocket-reference#spot-market-stats).

```json
{
    "type": "subscribe",
    "channel": "market_stats/{MARKET_INDEX}"
}
```

or

```json
{
    "type": "subscribe",
    "channel": "market_stats/all"
}
```

**Example Subscription**

```json
{
    "type": "subscribe",
    "channel": "market_stats/0"
}
```

**Response Structure**

```json
{
    "channel": "market_stats:{MARKET_INDEX}",
    "market_stats": {
      "symbol": STRING,
      "market_id": INTEGER,
      "index_price": STRING,
      "mark_price": STRING,
      "mid_price": STRING,
      "best_ask_price": STRING,
      "best_bid_price": STRING,
      "open_interest": STRING,
      "open_interest_limit": STRING,
      "funding_clamp_small": STRING,
      "funding_clamp_big": STRING,
      "last_trade_price": STRING,
      "current_funding_rate": STRING,
      "funding_rate": STRING,
      "funding_timestamp": INTEGER,
      "daily_base_token_volume": FLOAT,
      "daily_quote_token_volume": FLOAT,
      "daily_price_low": FLOAT,
      "daily_price_high": FLOAT,
      "daily_price_change": FLOAT
    },
    "timestamp": INTEGER,
    "type": "update/market_stats"
}
```

**Example Response**

```json
{
   "channel":"market_stats:0",
   "market_stats":{
      "symbol":"ETH",
      "market_id":0,
      "index_price":"1621.50",
      "mark_price":"1620.50",
      "mid_price":"1620.37",
      "best_ask_price":"1620.45",
      "best_bid_price":"1620.28",
      "open_interest":"56951890.578450",
      "open_interest_limit":"72057594037927936.000000",
      "funding_clamp_small":"0.0500",
      "funding_clamp_big":"4.0000",
      "last_trade_price":"1620.55",
      "current_funding_rate":"0.0012",
      "funding_rate":"0.0006",
      "funding_timestamp":1781089200001,
      "daily_base_token_volume":248433.7721,
      "daily_quote_token_volume":408125266.637249,
      "daily_price_low":1605.15,
      "daily_price_high":1682.43,
      "daily_price_change":-3.297732696897375,
      "base_interest_rate":"0.0100"
   },
   "timestamp":1781090197120,
   "type":"update/market_stats"
}
```

## Trade

The trade channel sends the new trade data for the given market.

```json
{
    "type": "subscribe",
    "channel": "trade/{MARKET_INDEX}"
}
```

**Example Subscription**

```json
{
    "type": "subscribe",
    "channel": "trade/0"
}
```

**Response Structure**

```json
{
    "channel": "trade:{MARKET_INDEX}",
    "liquidation_trades": [Trade],
    "nonce":INTEGER,
    "trades": [Trade],
    "type": "update/trade"
}
```

**Example Response**

```json
{
   "channel":"trade:0",
   "liquidation_trades":[],
   "nonce":8630448841,
   "trades":[
      {
         "trade_id":16164557907,
         "trade_id_str":"16164557907",
         "tx_hash":"019f2b9c9cc609196316a569541e135a739728e0837fcfb05c913534e305e503c01d5dcfeabeaf81",
         "type":"trade",
         "market_id":0,
         "size":"0.1336",
         "price":"2181.83",
         "usd_amount":"291.492488",
         "ask_id":281476612587355,
         "ask_id_str":"281476612587355",
         "bid_id":562948334068259,
         "bid_id_str":"562948334068259",
         "ask_client_id":363283,
         "ask_client_id_str":"363283",
         "bid_client_id":23004521241,
         "bid_client_id_str":"23004521241",
         "ask_account_id":57890,
         "bid_account_id":317068,
         "is_maker_ask":false,
         "block_height":198321831,
         "timestamp":1773854156654,
         "taker_position_size_before":"80.5467",
         "taker_entry_quote_before":"178806.039128",
         "taker_initial_margin_fraction_before":500,
         "taker_fee": 196, // omitted if zero
         "maker_fee":28,
         "maker_position_size_before":"-4.3180",
         "maker_entry_quote_before":"9419.085856",
         "maker_initial_margin_fraction_before":200,
         "transaction_time":1773854156686065
      }
   ],
   "type":"update/trade"
}
```

## Candlesticks

Real-time candlestick (OHLCV) updates via WebSocket. Subscribe to any market and resolution to receive live candle data as trades occur. Supported resolutions: `1m`, `5m`, `15m`, `30m`, `1h`, `4h`, `12h`, `1d`. The candles\[] array allows up to 2 candles to be sent in a single update when a candle period closes (rolls over). If so, candles\[0] will be the oldest, just closed candle.

```json
{
	"type": "subscribe",
	"channel": "candle/{market_id}/{resolution}"
}
```

**Example Subscription**

```json
{
	"type": "subscribe",
	"channel": "candle/0/1m"
}
```

**Response Structure (Subscribe)**

```json
{
  "type": "subscribed/candle",
  "channel": "candle:{market_id}:{resolution}",
  "timestamp": INTEGER, 
  "candles": [
    {
      "t": INTEGER, // candle open timestamp
      "o": FLOAT, // open price
      "h": FLOAT, // high price
      "l": FLOAT, // low price
      "c": FLOAT, // close price
      "v": FLOAT, // base token volume
      "V": FLOAT, // quote token volume
      "i": INTEGER // last trade id
    }
  ]
}
```

**Response Structure (Updates)**

```json
{
  "type": "update/candle",
  "channel": "candle:{market_id}:{resolution}",
  "timestamp": INTEGER,
  "candles": [
    {
      "t": INTEGER,
      "o": FLOAT,
      "h": FLOAT,
      "l": FLOAT,
      "c": FLOAT,
      "v": FLOAT,
      "V": FLOAT,
      "i": INTEGER
    }
  ]
}
```

**Example Response (Subscribe)**

Returns the current live candle.

```json
{
  "type": "subscribed/candle",
  "channel": "candle:0:1m",
  "timestamp": 1778500801812,
  "candles": [
    {
      "t": 1778500800000,
      "o": 2334.89,
      "h": 2335.14,
      "l": 2334.73,
      "c": 2334.73,
      "v": 19.8259,
      "V": 46293.345028,
      "i": 19694821240
    }
  ]
}
```

**Example Response (Updates)**

Returns the latest candle state, whenever a trade occurs, with a 500ms batching.

```json
{
  "type": "update/candle",
  "channel": "candle:0:1m",
  "timestamp": 1778500803456,
  "candles": [
    {
      "t": 1778500800000,
      "o": 2334.89,
      "h": 2335.33,
      "l": 2334.73,
      "c": 2335.15,
      "v": 24.3878,
      "V": 56946.630946,
      "i": 19694823713
    }
  ]
}
```

## Mark Price Candlesticks

Real-time mark price candlestick (OHLCV) updates via WebSocket. Subscribe to any market and resolution to receive live candle data as trades occur. Supported resolutions: `1m`, `5m`, `15m`, `30m`, `1h`, `4h`, `12h`, `1d`. The candles\[] array allows up to 2 candles to be sent in a single update when a candle period closes (rolls over). If so, candles\[0] will be the oldest, just closed candle.

```json
{
	"type": "subscribe",
	"channel": "mark_price_candle/{market_id}/{resolution}"
}
```

**Example Subscription**

```json
{
	"type": "subscribe",
	"channel": "mark_price_candle/0/1m"
}
```

**Response Structure (Subscribe)**

```json
{
  "type": "subscribed/mark_price_candle",
  "channel": "mark_price_candle:{market_id}:{resolution}",
  "timestamp": INTEGER, 
  "candles": [
    {
      "t": INTEGER, // candle open timestamp
      "o": FLOAT, // open price
      "h": FLOAT, // high price
      "l": FLOAT, // low price
      "c": FLOAT, // close price
      "sc": INTEGER // mark price samples within candle
    }
  ]
}
```

**Response Structure (Updates)**

```json
{
  "type": "update/mark_price_candle",
  "channel": "mark_price_candle:{market_id}:{resolution}",
  "timestamp": INTEGER,
  "candles": [
    {
      "t": INTEGER,
      "o": FLOAT,
      "h": FLOAT,
      "l": FLOAT,
      "c": FLOAT,
      "sc": INTEGER
    }
  ]
}
```

**Example Response (Subscribe)**

Returns the current live candle.

```json
{
  "type": "subscribed/mark_price_candle",
  "channel": "mark_price_candle:0:1m",
  "timestamp": 1778500801812,
  "candles": [
    {
      "t": 1778500800000,
      "o": 2334.89,
      "h": 2335.14,
      "l": 2334.73,
      "c": 2334.73,
      "sc": 1
    }
  ]
}
```

**Example Response (Updates)**

Returns the latest candle state, whenever a trade occurs, with a 500ms batching.

```json
{
  "type": "update/mark_price_candle",
  "channel": "mark_price_candle:0:1m",
  "timestamp": 1778500803456,
  "candles": [
    {
      "t": 1778500800000,
      "o": 2334.89,
      "h": 2335.33,
      "l": 2334.73,
      "c": 2335.15,
      "sc": 1
    }
  ]
}
```

## Account All

The account all channel sends specific account market data for all markets. On every new subscription message sent, you will reconnect and receive a new snapshot.

```json
{
    "type": "subscribe",
    "channel": "account_all/{ACCOUNT_ID}"
}
```

**Example Subscription**

```json
{
    "type": "subscribe",
    "channel": "account_all/1"
}
```

**Response Structure**

```json
{
    "account": INTEGER,
    "assets": {
      "{ASSET_INDEX}": Asset
      },
    "channel": "account_all:{ACCOUNT_ID}",
    "daily_trades_count": INTEGER,
    "daily_volume": FLOAT,
    "weekly_trades_count": INTEGER,
    "weekly_volume": FLOAT,
    "monthly_trades_count": INTEGER,
    "monthly_volume": FLOAT,
    "total_trades_count": INTEGER,
    "total_volume": FLOAT,
    "funding_histories": [PositionFunding],
    "positions": {
        "{MARKET_INDEX}": Position
    },
    "shares": [PoolShares],
    "trades": {
        "{MARKET_INDEX}": [Trade]
    },
    "type": "update/account_all"
}
```

**Example Response**

```json
{
   "account": 10,
   "assets": {
      "1": {
         "symbol":"ETH",
         "asset_id":1,
         "balance":"1",
         "locked_balance":"0.00000000"
      }
   },
    "channel": "account_all:10",
    "daily_trades_count": 123,
    "daily_volume": 234,
    "weekly_trades_count": 345,
    "weekly_volume": 456,
    "monthly_trades_count": 567,
    "monthly_volume": 678,
    "total_trades_count": 891,
    "total_volume": 912,
    "funding_histories": {
        "1": {
            "timestamp": 1700000000,
            "market_id": 101,
            "funding_id": 2001,
            "change": "0.001",
            "rate": "0.0001",
            "position_size": "0.5",
            "position_side": "long"
        }
    },
    "positions": {
        "1": {
            "market_id": 101,
            "symbol": "BTC-USD",
            "initial_margin_fraction": "0.1",
            "open_order_count": 2,
            "pending_order_count": 1,
            "position_tied_order_count": 3,
            "sign": 1,
            "position": "0.5",
            "avg_entry_price": "20000.00",
            "position_value": "10000.00",
            "unrealized_pnl": "500.00",
            "realized_pnl": "100.00",
            "liquidation_price": "3024.66",
            "total_funding_paid_out": "34.2",
            "margin_mode": 1,
            "allocated_margin": "46342",
        }
    },
    "shares": {
        "public_pool_index": 1,
        "shares_amount": 100,
        "entry_usdc": "1000.00"
    },
    "trades": {
        "1": {
            "trade_id": 401,
            "tx_hash": "0xabc123456789",
            "type": "buy",
            "market_id": 101,
            "size": "0.5",
            "price": "20000.00",
            "usd_amount": "10000.00",
            "ask_id": 501,
            "bid_id": 502,
            "ask_account_id": 123456,
            "bid_account_id": 654321,
            "is_maker_ask": true,
            "block_height": 1500000,
            "timestamp": 1700000000,
            "taker_position_size_before":"1.14880",
            "taker_entry_quote_before":"136130.046511",
            "taker_initial_margin_fraction_before":500,
            "maker_position_size_before":"-0.02594",
            "maker_entry_quote_before":"3075.396750",
            "maker_initial_margin_fraction_before":400
        }
    },
    "type": "update/account_all"
}
```

## Account Market

The account market channel sends specific account market data for a market. If `{MARKET_ID}` is a perpetual market, `assets` will return `null`, if `{MARKET_ID}` is a spot market, `funding_history` will return `null`.

```json
{
    "type": "subscribe",
    "channel": "account_market/{MARKET_ID}/{ACCOUNT_ID}",
    "auth": "{AUTH_TOKEN}"
}
```

**Example Subscription**

```json
{
    "type": "subscribe",
    "channel": "account_market/0/40",
    "auth": "{AUTH_TOKEN}"
}
```

**Response Structure**

```json
{
    "account": INTEGER,
    "assets": [Asset],
    "channel": "account_market/{MARKET_ID}/{ACCOUNT_ID}",
    "funding_history": {
        "timestamp": INTEGER,
        "market_id": INTEGER,
        "funding_id": INTEGER,
        "change": STRING,
        "rate": STRING,
        "position_size": STRING,
        "position_side": STRING
        },
    "orders": [Order],
    "position": [Position],
    "trades": [Trade],
    "type": "update/account_market"
}
```

## Account Stats

The account stats channel sends account stats data for the specific account.

```json
{
    "type": "subscribe",
    "channel": "user_stats/{ACCOUNT_ID}"
}
```

**Example Subscription**

```json
{
    "type": "subscribe",
    "channel": "user_stats/1234"
}
```

**Response Structure**

```json
{
    "channel": "user_stats:{ACCOUNT_ID}",
    "stats": {
        "collateral": STRING,
        "portfolio_value": STRING,
        "leverage": STRING,
        "available_balance": STRING,      
        "margin_usage": STRING,
        "buying_power": STRING,
        "account_trading_mode": INTEGER,
        "cross_stats":{
            "collateral": STRING,
            "portfolio_value": STRING,
            "leverage": STRING,
            "available_balance": STRING,
            "margin_usage": STRING,
            "buying_power": STRING
        },
        "total_stats":{
            "collateral": STRING,
            "portfolio_value": STRING,
            "leverage": STRING,
            "available_balance": STRING,
            "margin_usage": STRING,
            "buying_power": STRING
        }
    },
    "timestamp": INTEGER,
    "type": "update/user_stats"
}
```

**Example Response**

```json
{
    "channel": "user_stats:10",
    "stats": {
        "collateral": "5000.00",
        "portfolio_value": "15000.00",
        "leverage": "3.0",
        "available_balance": "2000.00",
        "margin_usage": "0.80",
        "buying_power": "4000.00",
        "account_trading_mode": 1,
        "cross_stats":{
           "collateral":"0.000000",
           "portfolio_value":"0.000000",
           "leverage":"0.00",
           "available_balance":"0.000000",
           "margin_usage":"0.00",
           "buying_power":"0"
        },
        "total_stats":{
           "collateral":"0.000000",
           "portfolio_value":"0.000000",
           "leverage":"0.00",
           "available_balance":"0.000000",
           "margin_usage":"0.00",
           "buying_power":"0"
        }
    },
    "timestamp": 1773158679717,
    "type": "update/user_stats"
}
```

## Account Tx

This channel sends transactions related to a specific account.

```json
{
    "type": "subscribe",
    "channel": "account_tx/{ACCOUNT_ID}",
    "auth": "{AUTH_TOKEN}"
}
```

**Response Structure**

```json
{
    "channel": "account_tx:{ACCOUNT_ID}",
    "txs": [Account_tx],
    "type": "update/account_tx"
}
```

## Account All Orders

The account all orders channel sends data about all the orders of an account.

```json
{
    "type": "subscribe",
    "channel": "account_all_orders/{ACCOUNT_ID}",
    "auth": "{AUTH_TOKEN}"
}
```

**Response Structure**

```json
{
    "channel": "account_all_orders:{ACCOUNT_ID}",
    "orders": {
        "{MARKET_INDEX}": [Order]
    },
    "type": "update/account_all_orders"
}
```

## Height

Blockchain height updates

```json
{
    "type": "subscribe",
    "channel": "height"
}
```

**Response Structure**

```json
{
    "channel": "height",
    "height": INTEGER,
    "timestamp": INTEGER,
    "type": "update/height"
}
```

## Pool data

Provides data about pool activities: trades, orders, positions, shares and funding histories.

```json
{
    "type": "subscribe",
    "channel": "pool_data/{ACCOUNT_ID}",
    "auth": "{AUTH_TOKEN}"
}
```

**Response Structure**

```json
{
    "channel": "pool_data:{ACCOUNT_ID}",
    "account": INTEGER,
    "trades": {
        "{MARKET_INDEX}": [Trade]
    },
    "orders": {
        "{MARKET_INDEX}": [Order]
    },
    "positions": {
        "{MARKET_INDEX}": [Position]
    },
    "shares": [PoolShares],
    "funding_histories": {
        "{MARKET_INDEX}": [PositionFunding]
    },
    "type": "subscribed/pool_data"
}
```

## Pool info

Provides information about pools.

```json
{
    "type": "subscribe",
    "channel": "pool_info/{ACCOUNT_ID}",
    "auth": "{AUTH_TOKEN}"
}
```

**Response Structure**

```json
{
    "channel": "pool_info:{ACCOUNT_ID}",
    "pool_info": {
        "status": INTEGER,
        "operator_fee": STRING,
        "min_operator_share_rate": STRING,
        "total_shares": INTEGER,
        "operator_shares": INTEGER,
        "annual_percentage_yield": FLOAT,
        "sharpe_ratio": FLOAT,
        "daily_returns": [
            {
                "timestamp": INTEGER,
                "daily_return": FLOAT
            }
        ],
        "share_prices": [
            {
                "timestamp": INTEGER,
                "share_price": FLOAT
            }
        ],
        "strategies": [
            {
                "collateral": STRING
            }
        ]
    },
    "type": "subscribed/pool_info"
}
```

## Notification

Provides notifications received by an account. Notifications can be of three kinds: liquidation, deleverage, or announcement. Each kind has a different content structure.

```json
{
    "type": "subscribe",
    "channel": "notification/{ACCOUNT_ID}",
    "auth": "{AUTH_TOKEN}"
}
```

**Response Structure**

```json
{
    "channel": "notification:{ACCOUNT_ID}",
    "notifs": [
        {
            "id": STRING,
            "created_at": STRING,
            "updated_at": STRING,
            "kind": STRING,
            "account_index": INTEGER,
            "content": NotificationContent,
            "ack": BOOLEAN,
            "acked_at": STRING
        }
    ],
    "type": "subscribed/notification"
}
```

**Liquidation Notification Content**

```json
{
    "id": STRING,
    "is_ask": BOOL,
    "usdc_amount": STRING,
    "size": STRING,
    "market_index": INTEGER,
    "price": STRING,
    "timestamp": INTEGER,
    "avg_price": STRING
}
```

**Deleverage Notification Content**

```json
{
    "id": STRING,
    "usdc_amount": STRING,
    "size": STRING,
    "market_index": INTEGER,
    "settlement_price": STRING,
    "timestamp": INTEGER
}
```

**Announcement Notification Content**

```json
{
    "title": STRING,
    "content": STRING, 
    "created_at": INTEGER
}
```

**Example response**

```json
{
    "channel": "notification:12345",
    "notifs": [
        {
            "id": "notif_123",
            "created_at": "2024-01-15T10:30:00Z",
            "updated_at": "2024-01-15T10:30:00Z",
            "kind": "liquidation",
            "account_index": 12345,
            "content": {
                "id": "notif_123",
                "is_ask": false,
                "usdc_amount": "1500.50",
                "size": "0.500000",
                "market_index": 1,
                "price": "3000.00",
                "timestamp": 1705312200,
                "avg_price": "3000.00"
            },
            "ack": false,
            "acked_at": null
        },
        {
            "id": "notif_124",
            "created_at": "2024-01-15T11:00:00Z",
            "updated_at": "2024-01-15T11:00:00Z",
            "kind": "deleverage",
            "account_index": 12345,
            "content": {
                "id": "notif_124",
                "usdc_amount": "500.25",
                "size": "0.200000",
                "market_index": 1,
                "settlement_price": "2501.25",
                "timestamp": 1705314000
            },
            "ack": false,
            "acked_at": null
        }
    ],
    "type": "update/notification"
}
```

## Account Orders

The account orders channel sends data about the orders of an account on a certain market.

```json
{
    "type": "subscribe",
    "channel": "account_orders/{MARKET_INDEX}/{ACCOUNT_ID}",
    "auth": "{AUTH_TOKEN}"
}
```

**Response Structure**

```json
{
    "account": {ACCOUNT_INDEX}, 
    "channel": "account_orders:{MARKET_INDEX}",
    "nonce": INTEGER,
    "orders": {
        "{MARKET_INDEX}": [Order] // the only present market index will be the one provided
    },
    "type": "update/account_orders"
}
```

## Account All Trades

The account all trades channel sends data about all the trades of an account.

```json
{
    "type": "subscribe",
    "channel": "account_all_trades/{ACCOUNT_ID}"
}
```

**Response Structure**

```json
{
    "channel": "account_all_trades:{ACCOUNT_ID}",
    "trades": [],
    "total_volume": FLOAT,
    "monthly_volume": FLOAT,
    "weekly_volume": FLOAT,
    "daily_volume": FLOAT,
    "type": "subscribed/account_all_trades"
}
```

```json
{
    "channel": "account_all_trades:{ACCOUNT_ID}",
    "trades": {
        "{MARKET_INDEX}": [Trade]
    },
    "type": "update/account_all_trades"
}
```

## Account All Positions

The account all positions channel sends data about all the positions of an account.

```json
{
    "type": "subscribe",
    "channel": "account_all_positions/{ACCOUNT_ID}"
}
```

**Response Structure**

```json
{
    "channel": "account_all_positions:{ACCOUNT_ID}",
    "positions": {
        "{MARKET_INDEX}": Position
    },
    "shares": [PoolShares],
    "type": "subscribed/account_all_positions",
}
```

```json
{
    "channel": "account_all_positions:{ACCOUNT_ID}",
    "positions": {
        "{MARKET_INDEX}": Position
    },
    "shares": [PoolShares],
    "last_funding_round": {            // optional, only present when funding occurs
        "{MARKET_INDEX}": STRING
    },
    "last_funding_discount": {         // optional, only present when funding occurs
        "{MARKET_INDEX}": STRING
    },
    "type": "update/account_all_positions",
}
```

## Spot Market Stats

The spot market stats channel sends the market stat data for the given spot market.

```json
{
    "type": "subscribe",
    "channel": "spot_market_stats/{MARKET_INDEX}"
}
```

or

```json
{
    "type": "subscribe",
    "channel": "spot_market_stats/all"
}
```

**Example Subscription**

```json
{
    "type": "subscribe",
    "channel": "spot_market_stats/2048"
}
```

**Response Structure** **using&#x20;**`all`

```json
{
    "channel": "spot_market_stats:all",
    "spot_market_stats": {
      "{MARKET_INDEX}": {
          "symbol": STRING,
          "market_id": INTEGER,
          "index_price": STRING,
          "mid_price": STRING,
          "last_trade_price": STRING,
          "daily_base_token_volume": FLOAT,
          "daily_quote_token_volume": FLOAT,
          "daily_price_low": FLOAT,
          "daily_price_high": FLOAT,
          "daily_price_change": FLOAT
      }
    },
    "timestamp": INTEGER,
    "type": "update/spot_market_stats"
}
```

**Example Response** **using&#x20;**`all`

```json
{
   "channel":"spot_market_stats:all",
   "spot_market_stats":{
      "2048":{
         "symbol":"ETH/USDC",
         "market_id":2048,
         "index_price":"2188.994707",
         "mid_price":"2188.90",
         "last_trade_price":"2190.25",
         "daily_base_token_volume":681.5806,
         "daily_quote_token_volume":1535781.121577,
         "daily_price_low":2166.63,
         "daily_price_high":2347.78,
         "daily_price_change":-5.917096219931271
      }
   },
   "timestamp":1773860622360,
   "type":"update/spot_market_stats"
}
```

**Response Structure** **using&#x20;**`MARKET_INDEX`

```json
{
    "channel": "spot_market_stats:{MARKET_INDEX}",
    "spot_market_stats": {
      "symbol": STRING,
        "market_id": INTEGER,
        "index_price": STRING,
        "mid_price": STRING,
        "last_trade_price": STRING,
        "daily_base_token_volume": FLOAT,
        "daily_quote_token_volume": FLOAT,
        "daily_price_low": FLOAT,
        "daily_price_high": FLOAT,
        "daily_price_change": FLOAT
    },
    "timestamp": INTEGER,
    "type": "update/spot_market_stats"
}
```

**Example Response** **using&#x20;**`MARKET_INDEX`

```json
{
   "channel":"spot_market_stats:2048",
   "spot_market_stats":{
      "symbol":"ETH/USDC",
      "market_id":2048,
      "index_price":"2183.030179",
      "mid_price":"2182.60",
      "last_trade_price":"2179.81",
      "daily_base_token_volume":670.444,
      "daily_quote_token_volume":1511428.664237,
      "daily_price_low":2166.63,
      "daily_price_high":2347.78,
      "daily_price_change":-6.365549828178694
   },
   "timestamp":1773860440362,
   "type":"update/spot_market_stats"
}
```

## Account All Assets

The account all assets channel sends specific account market data for all spot markets for a specific account. `balance` is in coin terms, not USDC (unless asset\_index=3, in which case they coincide).

```json
{
    "type": "subscribe",
    "channel": "account_all_assets/{ACCOUNT_ID}",
    "auth": "{AUTH_TOKEN}"
}
```

**Example Subscription**

```json
{
    "type": "subscribe",
    "channel": "account_all_assets/1234",
    "auth": "{AUTH_TOKEN}"
}
```

**Response Structure**

```json
{
    "assets": {
         "{ASSET_INDEX}": Asset,
         "{ASSET_INDEX}": Asset
    },
    "channel": "account_all_assets:{ACCOUNT_ID}",
    "timestamp": INTEGER,
    "type": "update/account_all_assets"
}
```

**Example Response**

```json
{
    "assets": {
        "1": {
            "symbol": "ETH",
            "asset_id": 1,
            "balance": "7.1072",
            "locked_balance": "0.0000"
        },
        "3": {
            "symbol": "USDC",
            "asset_id": 3,
            "balance": "6343.581906",
            "locked_balance": "297.000000"
        }
    },
    "channel": "account_all_assets:1234",
    "timestamp": 1773158679717,
    "type": "update/account_all_assets"
}
```

## Average Entry Prices

Updates on the `account_spot_avg_entry_prices` channel are triggered whenever an event occurs for a given spot asset. Each event is accounted for as a buy or sell executed at the index price. Events include trades, deposits, withdrawals, transfers, and staking. The `last_trade_id` field confirms the validity of the data returned up to that `trade_id`.

```json
{
    "type": "subscribe",
    "channel": "account_spot_avg_entry_prices/{ACCOUNT_ID}",
    "auth": "{AUTH_TOKEN}"
}
```

**Example Subscription**

```json
{
    "type": "subscribe",
    "channel": "account_spot_avg_entry_prices/1234",
    "auth": "{AUTH_TOKEN}"
}
```

**Response Structure**

```json
{
   "avg_entry_prices":{
      "{ASSET_INDEX}":{
         "asset_id":INTEGER,
         "avg_entry_price":STRING,
         "asset_size":STRING,
         "last_trade_id":INTEGER
      }
   },
   "channel":"account_spot_avg_entry_prices:{account_index}",
   "timestamp": INTEGER,
   "type":"subscribed/account_spot_avg_entry_prices"
}
```

**Example Response**

```json
{
   "avg_entry_prices":{
      "1":{
         "asset_id":1,
         "avg_entry_price":"1850.45",
         "asset_size":"0.01234567",
         "last_trade_id":13472591098
      }
   },
   "channel":"account_spot_avg_entry_prices:1234",
   "timestamp": 1773158679717,
   "type":"subscribed/account_spot_avg_entry_prices"
}
```

## Request for Quote (RFQ)

This channel allows you to receive all currently open RFQs, followed by live updates as new ones arrive. RFQ responses are hidden if you're not the requester, so you will only see the market and its size. API endpoints to create, respond, update, get, and list RFQs are available [here](https://apidocs.lighter.xyz/reference/rfq_create). Currently, this program is whitelist-only.

```json
{
	"type": "subscribe", 
	"channel": "rfq", 
	"auth": "{AUTH_TOKEN}"
}
```

**Response Structure**

```json
{
   "channel":"rfq",
   "rfqs":[
      {
         "id":INTEGER, // needed to respond
         "account_index":INTEGER,
         "market_index":INTEGER,
         "direction":INTEGER, // redacted to -1
         "base_amount":STRING,
         "quote_amount":STRING,
         "status":STRING,
         "metadata":{},
         "responses":[],
         "created_at":INTEGER,
         "updated_at":INTEGER
      }
   ],
   "type":"update/rfq"
}
```

**Example Structure**

```json
{
   "channel":"rfq",
   "rfqs":[
      {
         "id":1234,
         "account_index":704885,
         "market_index":1,
         "direction":-1,
         "base_amount":"100",
         "quote_amount":"",
         "status":"opened",
         "metadata":{
            
         },
         "responses":[
            
         ],
         "created_at":1780077339117,
         "updated_at":1780077339117
      }
   ],
   "type":"update/rfq"
}
```

## Live points&#x20;

This channel returns the total live points accrued to an account index. Updates are triggered as the total changes. You can confirm you have the latest value by fetching [livePoints/total](https://apidocs.rh.lighter.xyz/reference/livepoints_total) via REST.

```json
{ 
	"type": "subscribe", 
	"channel": "live_points/{ACCOUNT_ID}", 
	"auth": "{AUTH_TOKEN}" 
}
```

**Example Subscription:**

```json
{ 
	"type": "subscribe", 
	"channel": "live_points/1234", 
	"auth": "{AUTH_TOKEN}" 
}
```

**Response Structure**

```json
{ 
	"type": "subscribed/live_points", 
	"channel": "live_points/{ACCOUNT_ID}", 
	"total_live_points": INTEGER 
}
```

**Example Response**

```json
{ 
	"type": "subscribed/live_points", 
	"channel": "live_points/{ACCOUNT_ID}", 
	"total_live_points": 1234.56 
}
```

```json
{ 
	"type": "update/live_points", 
	"channel": "live_points/{ACCOUNT_ID}", 
	"total_live_points": 1234.56 
}
```