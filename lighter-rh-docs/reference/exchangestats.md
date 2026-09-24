---
updatedAt: 2026-07-15T18:08:20.000Z
---

Fetch the complete documentation index at: https://apidocs.rh.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further. Append .md to any documentation page URL to get its markdown version.

# exchangeStats

Get exchange stats

# OpenAPI definition

```json
{
  "openapi": "3.0.0",
  "info": {
    "title": "",
    "version": ""
  },
  "paths": {
    "/api/v1/exchangeStats": {
      "get": {
        "summary": "exchangeStats",
        "operationId": "exchangeStats",
        "tags": [
          "order"
        ],
        "description": "Get exchange stats",
        "responses": {
          "200": {
            "description": "A successful response.",
            "content": {
              "application/json": {
                "schema": {
                  "$ref": "#/components/schemas/ExchangeStats"
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
      "ExchangeStats": {
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
          "total": {
            "type": "integer",
            "format": "int64",
            "example": "1"
          },
          "order_book_stats": {
            "type": "array",
            "example": "1",
            "items": {
              "$ref": "#/components/schemas/OrderBookStats"
            }
          },
          "daily_usd_volume": {
            "type": "number",
            "format": "double",
            "example": "93566.25"
          },
          "daily_trades_count": {
            "type": "integer",
            "format": "int64",
            "example": "68"
          }
        },
        "title": "ExchangeStats",
        "required": [
          "code",
          "total",
          "order_book_stats",
          "daily_usd_volume",
          "daily_trades_count"
        ]
      },
      "OrderBookStats": {
        "type": "object",
        "properties": {
          "symbol": {
            "type": "string",
            "example": "ETH"
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
          "daily_price_change": {
            "type": "number",
            "format": "double",
            "example": "3.66"
          }
        },
        "title": "OrderBookStats",
        "required": [
          "symbol",
          "last_trade_price",
          "daily_trades_count",
          "daily_base_token_volume",
          "daily_quote_token_volume",
          "daily_price_change"
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