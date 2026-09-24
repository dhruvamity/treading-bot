---
updatedAt: 2026-07-01T15:22:51.000Z
---

Fetch the complete documentation index at: https://apidocs.rh.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further. Append .md to any documentation page URL to get its markdown version.

# orderBookDetails

Get order books metadata

# OpenAPI definition

```json
{
  "openapi": "3.0.0",
  "info": {
    "title": "",
    "version": ""
  },
  "paths": {
    "/api/v1/orderBookDetails": {
      "get": {
        "summary": "orderBookDetails",
        "operationId": "orderBookDetails",
        "tags": [
          "order"
        ],
        "description": "Get order books metadata",
        "parameters": [
          {
            "name": "market_id",
            "in": "query",
            "required": false,
            "schema": {
              "type": "integer",
              "format": "int16",
              "default": "255"
            }
          },
          {
            "name": "filter",
            "in": "query",
            "required": false,
            "schema": {
              "type": "string",
              "enum": [
                "all",
                "spot",
                "perp"
              ],
              "default": "all"
            },
            "description": "Filter order books by type"
          }
        ],
        "responses": {
          "200": {
            "description": "A successful response.",
            "content": {
              "application/json": {
                "schema": {
                  "$ref": "#/components/schemas/OrderBookDetails"
                }
              }
            }
          },
          "400": {
            "description": "Bad request",
            "content": {
              "application/json": {
                "schema": {
                  "$ref": "#/components/schemas/ResultCode"
                }
              }
            }
          }
        }
      }
    }
  },
  "servers": [
    {
      "url": "https://api.rh.lighter.xyz/"
    }
  ],
  "components": {
    "schemas": {
      "MarketConfig": {
        "type": "object",
        "properties": {
          "market_margin_mode": {
            "type": "integer",
            "format": "int32",
            "example": 0
          },
          "insurance_fund_account_index": {
            "type": "integer",
            "format": "int64",
            "example": 281474976710655
          },
          "liquidation_mode": {
            "type": "integer",
            "format": "int32",
            "example": 0
          },
          "force_reduce_only": {
            "type": "boolean",
            "format": "boolean",
            "example": false
          },
          "funding_fee_discounts_enabled": {
            "type": "boolean",
            "format": "boolean"
          },
          "trading_hours": {
            "type": "string",
            "example": ""
          },
          "hidden": {
            "type": "boolean"
          },
          "rfq_enabled": {
            "type": "boolean",
            "format": "boolean"
          }
        },
        "title": "MarketConfig",
        "required": [
          "market_margin_mode",
          "insurance_fund_account_index",
          "liquidation_mode",
          "force_reduce_only",
          "trading_hours",
          "hidden",
          "rfq_enabled"
        ]
      },
      "PerpsOrderBookDetail": {
        "type": "object",
        "properties": {
          "symbol": {
            "type": "string",
            "example": "ETH"
          },
          "market_id": {
            "type": "integer",
            "format": "int16",
            "example": 0
          },
          "market_type": {
            "type": "string",
            "example": "perp",
            "enum": [
              "perp",
              "spot"
            ]
          },
          "base_asset_id": {
            "type": "integer",
            "format": "int16",
            "example": 0
          },
          "quote_asset_id": {
            "type": "integer",
            "format": "int16",
            "example": 0
          },
          "status": {
            "type": "string",
            "example": "active",
            "enum": [
              "inactive",
              "active"
            ]
          },
          "taker_fee": {
            "type": "string",
            "example": "0.0001"
          },
          "maker_fee": {
            "type": "string",
            "example": "0.0000"
          },
          "liquidation_fee": {
            "type": "string",
            "example": "0.01"
          },
          "min_base_amount": {
            "type": "string",
            "example": "0.01"
          },
          "min_quote_amount": {
            "type": "string",
            "example": "0.1"
          },
          "supported_size_decimals": {
            "type": "integer",
            "format": "uint8",
            "example": "4"
          },
          "supported_price_decimals": {
            "type": "integer",
            "format": "uint8",
            "example": "4"
          },
          "supported_quote_decimals": {
            "type": "integer",
            "format": "uint8",
            "example": "4"
          },
          "order_quote_limit": {
            "type": "string",
            "example": "281474976.710655"
          },
          "size_decimals": {
            "type": "integer",
            "format": "uint8",
            "example": "4"
          },
          "price_decimals": {
            "type": "integer",
            "format": "uint8",
            "example": "4"
          },
          "quote_multiplier": {
            "type": "integer",
            "format": "int64",
            "example": "10000"
          },
          "default_initial_margin_fraction": {
            "type": "integer",
            "format": "uin16",
            "example": "100"
          },
          "min_initial_margin_fraction": {
            "type": "integer",
            "format": "uin16",
            "example": "100"
          },
          "maintenance_margin_fraction": {
            "type": "integer",
            "format": "uin16",
            "example": "50"
          },
          "closeout_margin_fraction": {
            "type": "integer",
            "format": "uin16",
            "example": "100"
          },
          "last_trade_price": {
            "type": "number",
            "format": "double",
            "example": "3024.66"
          },
          "daily_trades_count": {
            "type": "integer",
            "format": "int64",
            "example": "68"
          },
          "daily_base_token_volume": {
            "type": "number",
            "format": "double",
            "example": "235.25"
          },
          "daily_quote_token_volume": {
            "type": "number",
            "format": "double",
            "example": "93566.25"
          },
          "daily_price_low": {
            "type": "number",
            "format": "double",
            "example": "3014.66"
          },
          "daily_price_high": {
            "type": "number",
            "format": "double",
            "example": "3024.66"
          },
          "daily_price_change": {
            "type": "number",
            "format": "double",
            "example": "3.66"
          },
          "open_interest": {
            "type": "number",
            "format": "double",
            "example": "93.0"
          },
          "daily_chart": {
            "type": "object",
            "example": "{1640995200:3024.66}",
            "additionalProperties": {
              "type": "number",
              "format": "double"
            }
          },
          "market_config": {
            "$ref": "#/components/schemas/MarketConfig"
          },
          "strategy_index": {
            "type": "integer",
            "format": "uint8"
          },
          "is_maker_fee_enabled": {
            "type": "boolean",
            "example": true
          },
          "is_taker_fee_enabled": {
            "type": "boolean",
            "example": true
          },
          "funding_clamp_small": {
            "type": "string",
            "example": "0.005"
          },
          "funding_clamp_big": {
            "type": "string",
            "example": "0.4"
          },
          "base_interest_rate": {
            "type": "string",
            "example": "0.0001"
          },
          "created_at": {
            "type": "string",
            "example": "1640995200000"
          },
          "mark_price": {
            "type": "string",
            "example": "3024.66"
          },
          "index_price": {
            "type": "string",
            "example": "3024.66"
          },
          "multiplier": {
            "type": "string",
            "example": "1.000000000000000000"
          },
          "market_flags": {
            "type": "integer",
            "format": "int64"
          },
          "funding_premium_multiplier": {
            "type": "integer",
            "format": "uin16",
            "example": "100"
          }
        },
        "title": "PerpsOrderBookDetail",
        "required": [
          "symbol",
          "market_id",
          "market_type",
          "base_asset_id",
          "quote_asset_id",
          "status",
          "taker_fee",
          "is_taker_fee_enabled",
          "maker_fee",
          "is_maker_fee_enabled",
          "liquidation_fee",
          "min_base_amount",
          "min_quote_amount",
          "order_quote_limit",
          "supported_size_decimals",
          "supported_price_decimals",
          "supported_quote_decimals",
          "created_at",
          "multiplier",
          "size_decimals",
          "price_decimals",
          "quote_multiplier",
          "default_initial_margin_fraction",
          "min_initial_margin_fraction",
          "maintenance_margin_fraction",
          "closeout_margin_fraction",
          "mark_price",
          "index_price",
          "last_trade_price",
          "daily_trades_count",
          "daily_base_token_volume",
          "daily_quote_token_volume",
          "daily_price_low",
          "daily_price_high",
          "daily_price_change",
          "open_interest",
          "daily_chart",
          "market_config",
          "strategy_index",
          "market_flags",
          "funding_premium_multiplier",
          "funding_clamp_small",
          "funding_clamp_big",
          "base_interest_rate"
        ]
      },
      "OrderBookDetails": {
        "type": "object",
        "properties": {
          "code": {
            "type": "integer",
            "format": "int32",
            "example": "200"
          },
          "message": {
            "type": "string"
          },
          "order_book_details": {
            "type": "array",
            "items": {
              "$ref": "#/components/schemas/PerpsOrderBookDetail"
            }
          },
          "spot_order_book_details": {
            "type": "array",
            "items": {
              "$ref": "#/components/schemas/SpotOrderBookDetail"
            }
          }
        },
        "title": "OrderBookDetails",
        "required": [
          "code",
          "order_book_details",
          "spot_order_book_details"
        ]
      },
      "SpotOrderBookDetail": {
        "type": "object",
        "properties": {
          "symbol": {
            "type": "string",
            "example": "ETH/USDC"
          },
          "market_id": {
            "type": "integer",
            "format": "int16",
            "example": 2048
          },
          "market_type": {
            "type": "string",
            "example": "spot",
            "enum": [
              "perp",
              "spot"
            ]
          },
          "base_asset_id": {
            "type": "integer",
            "format": "int16",
            "example": 1
          },
          "quote_asset_id": {
            "type": "integer",
            "format": "int16",
            "example": 3
          },
          "status": {
            "type": "string",
            "example": "active",
            "enum": [
              "inactive",
              "active"
            ]
          },
          "taker_fee": {
            "type": "string",
            "example": "0.0000"
          },
          "maker_fee": {
            "type": "string",
            "example": "0.0000"
          },
          "liquidation_fee": {
            "type": "string",
            "example": "0.0000"
          },
          "min_base_amount": {
            "type": "string",
            "example": "0.0001"
          },
          "min_quote_amount": {
            "type": "string",
            "example": "0.000001"
          },
          "order_quote_limit": {
            "type": "string",
            "example": "2500000.000000"
          },
          "supported_size_decimals": {
            "type": "integer",
            "format": "uint8",
            "example": 4
          },
          "supported_price_decimals": {
            "type": "integer",
            "format": "uint8",
            "example": 2
          },
          "supported_quote_decimals": {
            "type": "integer",
            "format": "uint8",
            "example": 6
          },
          "size_decimals": {
            "type": "integer",
            "format": "uint8",
            "example": 4
          },
          "price_decimals": {
            "type": "integer",
            "format": "uint8",
            "example": 2
          },
          "last_trade_price": {
            "type": "number",
            "format": "double",
            "example": 2731.79
          },
          "daily_trades_count": {
            "type": "integer",
            "format": "int64",
            "example": 126993
          },
          "daily_base_token_volume": {
            "type": "number",
            "format": "double",
            "example": 1203.0962
          },
          "daily_quote_token_volume": {
            "type": "number",
            "format": "double",
            "example": 3516374.947553
          },
          "daily_price_low": {
            "type": "number",
            "format": "double",
            "example": 2717.47
          },
          "daily_price_high": {
            "type": "number",
            "format": "double",
            "example": 3044.21
          },
          "daily_price_change": {
            "type": "number",
            "format": "double",
            "example": -10.2389493724579
          },
          "daily_chart": {
            "type": "object",
            "example": "{1640995200:3024.66}",
            "additionalProperties": {
              "type": "number",
              "format": "double"
            }
          },
          "is_maker_fee_enabled": {
            "type": "boolean",
            "example": true
          },
          "is_taker_fee_enabled": {
            "type": "boolean",
            "example": true
          },
          "created_at": {
            "type": "string",
            "example": "1640995200000"
          },
          "multiplier": {
            "type": "string",
            "example": "1.000000000000000000"
          }
        },
        "title": "SpotOrderBookDetail",
        "required": [
          "symbol",
          "market_id",
          "market_type",
          "base_asset_id",
          "quote_asset_id",
          "status",
          "taker_fee",
          "is_taker_fee_enabled",
          "maker_fee",
          "is_maker_fee_enabled",
          "liquidation_fee",
          "min_base_amount",
          "min_quote_amount",
          "order_quote_limit",
          "supported_size_decimals",
          "supported_price_decimals",
          "supported_quote_decimals",
          "created_at",
          "multiplier",
          "size_decimals",
          "price_decimals",
          "last_trade_price",
          "daily_trades_count",
          "daily_base_token_volume",
          "daily_quote_token_volume",
          "daily_price_low",
          "daily_price_high",
          "daily_price_change",
          "daily_chart"
        ]
      },
      "ResultCode": {
        "type": "object",
        "properties": {
          "code": {
            "type": "integer",
            "format": "int32",
            "example": "200"
          },
          "message": {
            "type": "string"
          }
        },
        "title": "ResultCode",
        "required": [
          "code"
        ]
      }
    }
  }
}
```