---
updatedAt: 2026-07-15T17:37:23.000Z
---

Fetch the complete documentation index at: https://apidocs.rh.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further. Append .md to any documentation page URL to get its markdown version.

# accountOrders

Get account orders by client order indexes. Account index defaults to the authorization token's account. Supports the last 10K active orders (with no time limits), or the last 1K inactive orders (in the last 24 hours).

# OpenAPI definition

```json
{
  "openapi": "3.0.0",
  "info": {
    "title": "",
    "version": ""
  },
  "paths": {
    "/api/v1/accountOrders": {
      "get": {
        "summary": "accountOrders",
        "operationId": "accountOrders",
        "tags": [
          "order"
        ],
        "description": "Get account orders by client order indexes. Account index defaults to the authorization token's account. Supports the last 10K active orders (with no time limits), or the last 1K inactive orders (in the last 24 hours).",
        "parameters": [
          {
            "name": "authorization",
            "in": "header",
            "required": true,
            "schema": {
              "type": "string"
            }
          },
          {
            "name": "account_index",
            "in": "query",
            "required": false,
            "schema": {
              "type": "integer",
              "format": "int64",
              "default": "281474976710655"
            }
          },
          {
            "name": "client_order_indexes",
            "in": "query",
            "description": " comma-separated int64s, e.g. \"123,456\", with a limit of 20.",
            "required": true,
            "schema": {
              "type": "string"
            }
          }
        ],
        "responses": {
          "200": {
            "description": "A successful response.",
            "content": {
              "application/json": {
                "schema": {
                  "$ref": "#/components/schemas/Orders"
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
      "Order": {
        "type": "object",
        "properties": {
          "order_index": {
            "type": "integer",
            "format": "int64",
            "example": "1"
          },
          "client_order_index": {
            "type": "integer",
            "format": "int64",
            "example": "234"
          },
          "order_id": {
            "type": "string",
            "example": "1"
          },
          "client_order_id": {
            "type": "string",
            "example": "234"
          },
          "market_index": {
            "type": "integer",
            "format": "int16",
            "example": "1"
          },
          "owner_account_index": {
            "type": "integer",
            "format": "int64",
            "example": "1"
          },
          "initial_base_amount": {
            "type": "string",
            "example": "0.1"
          },
          "price": {
            "type": "string",
            "example": "3024.66"
          },
          "nonce": {
            "type": "integer",
            "format": "int64",
            "example": "722"
          },
          "remaining_base_amount": {
            "type": "string",
            "example": "0.1"
          },
          "is_ask": {
            "type": "boolean",
            "format": "boolean",
            "example": "true"
          },
          "base_size": {
            "type": "integer",
            "format": "int64",
            "example": "12354"
          },
          "base_price": {
            "type": "integer",
            "format": "int32",
            "example": "3024"
          },
          "filled_base_amount": {
            "type": "string",
            "example": "0.1"
          },
          "filled_quote_amount": {
            "type": "string",
            "example": "0.1"
          },
          "side": {
            "type": "string",
            "example": "buy",
            "default": "buy",
            "description": " TODO: remove this"
          },
          "type": {
            "type": "string",
            "example": "limit",
            "enum": [
              "limit",
              "market",
              "stop-loss",
              "stop-loss-limit",
              "take-profit",
              "take-profit-limit",
              "twap",
              "twap-sub",
              "liquidation"
            ]
          },
          "time_in_force": {
            "type": "string",
            "enum": [
              "good-till-time",
              "immediate-or-cancel",
              "post-only",
              "Unknown"
            ],
            "default": "good-till-time"
          },
          "reduce_only": {
            "type": "boolean",
            "format": "boolean",
            "example": "true"
          },
          "trigger_price": {
            "type": "string",
            "example": "3024.66"
          },
          "order_expiry": {
            "type": "integer",
            "format": "int64",
            "example": "1640995200"
          },
          "status": {
            "type": "string",
            "example": "open",
            "enum": [
              "in-progress",
              "pending",
              "open",
              "filled",
              "canceled",
              "canceled-post-only",
              "canceled-reduce-only",
              "canceled-position-not-allowed",
              "canceled-margin-not-allowed",
              "canceled-too-much-slippage",
              "canceled-not-enough-liquidity",
              "canceled-self-trade",
              "canceled-expired",
              "canceled-oco",
              "canceled-child",
              "canceled-liquidation",
              "canceled-invalid-balance"
            ]
          },
          "trigger_status": {
            "type": "string",
            "example": "twap",
            "enum": [
              "na",
              "ready",
              "mark-price",
              "twap",
              "parent-order"
            ]
          },
          "trigger_time": {
            "type": "integer",
            "format": "int64",
            "example": "1640995200"
          },
          "parent_order_index": {
            "type": "integer",
            "format": "int64",
            "example": "1"
          },
          "parent_order_id": {
            "type": "string",
            "example": "1"
          },
          "to_trigger_order_id_0": {
            "type": "string",
            "example": "1"
          },
          "to_trigger_order_id_1": {
            "type": "string",
            "example": "1"
          },
          "to_cancel_order_id_0": {
            "type": "string",
            "example": "1"
          },
          "block_height": {
            "type": "integer",
            "format": "int64",
            "example": "45434"
          },
          "timestamp": {
            "type": "integer",
            "format": "int64",
            "example": "1640995200"
          },
          "created_at": {
            "type": "integer",
            "format": "int64",
            "example": "1640995200"
          },
          "updated_at": {
            "type": "integer",
            "format": "int64",
            "example": "1640995200"
          },
          "transaction_time": {
            "type": "integer",
            "format": "int64"
          },
          "integrator_fee_collector_index": {
            "type": "string"
          },
          "integrator_maker_fee": {
            "type": "string"
          },
          "integrator_taker_fee": {
            "type": "string"
          },
          "order_flags": {
            "type": "integer",
            "format": "int64",
            "example": "1"
          },
          "order_version": {
            "type": "integer",
            "format": "int64",
            "example": "1"
          }
        },
        "title": "Order",
        "required": [
          "order_index",
          "client_order_index",
          "order_id",
          "client_order_id",
          "market_index",
          "owner_account_index",
          "initial_base_amount",
          "price",
          "nonce",
          "remaining_base_amount",
          "is_ask",
          "base_size",
          "base_price",
          "filled_base_amount",
          "filled_quote_amount",
          "side",
          "type",
          "time_in_force",
          "reduce_only",
          "trigger_price",
          "order_expiry",
          "status",
          "trigger_status",
          "trigger_time",
          "parent_order_index",
          "parent_order_id",
          "to_trigger_order_id_0",
          "to_trigger_order_id_1",
          "to_cancel_order_id_0",
          "integrator_fee_collector_index",
          "integrator_taker_fee",
          "integrator_maker_fee",
          "order_flags",
          "block_height",
          "timestamp",
          "created_at",
          "updated_at",
          "transaction_time",
          "order_version"
        ]
      },
      "Orders": {
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
          "next_cursor": {
            "type": "string"
          },
          "orders": {
            "type": "array",
            "items": {
              "$ref": "#/components/schemas/Order"
            }
          }
        },
        "title": "Orders",
        "required": [
          "code",
          "orders"
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