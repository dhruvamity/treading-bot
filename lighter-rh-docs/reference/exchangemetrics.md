---
updatedAt: 2026-07-15T18:07:47.000Z
---

Fetch the complete documentation index at: https://apidocs.rh.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further. Append .md to any documentation page URL to get its markdown version.

# exchangeMetrics

Get exchange metrics. When filtering by market, use the market symbol as a value.

# OpenAPI definition

```json
{
  "openapi": "3.0.0",
  "info": {
    "title": "",
    "version": ""
  },
  "paths": {
    "/api/v1/exchangeMetrics": {
      "get": {
        "summary": "exchangeMetrics",
        "operationId": "exchangeMetrics",
        "tags": [
          "order"
        ],
        "description": "Get exchange metrics. When filtering by market, use the market symbol as a value.",
        "parameters": [
          {
            "name": "period",
            "in": "query",
            "required": true,
            "schema": {
              "type": "string",
              "enum": [
                "h",
                "d",
                "w",
                "m",
                "q",
                "y",
                "all"
              ]
            }
          },
          {
            "name": "kind",
            "in": "query",
            "required": true,
            "schema": {
              "type": "string",
              "enum": [
                "volume",
                "maker_fee",
                "taker_fee",
                "liquidation_fee",
                "trade_count",
                "liquidation_count",
                "liquidation_volume",
                "inflow",
                "outflow",
                "transfer_fee",
                "withdraw_fee",
                "open_interest",
                "account_count",
                "active_account_count",
                "tps",
                "buyback",
                "buyback_usdc"
              ]
            }
          },
          {
            "name": "filter",
            "in": "query",
            "required": false,
            "schema": {
              "type": "string",
              "enum": [
                "byMarket"
              ]
            }
          },
          {
            "name": "value",
            "in": "query",
            "required": false,
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
                  "$ref": "#/components/schemas/RespGetExchangeMetrics"
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
      },
      "RespGetExchangeMetrics": {
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
          "metrics": {
            "type": "array",
            "items": {
              "$ref": "#/components/schemas/ExchangeMetric"
            }
          }
        },
        "title": "RespGetExchangeMetrics",
        "required": [
          "code",
          "metrics"
        ]
      },
      "ExchangeMetric": {
        "type": "object",
        "properties": {
          "timestamp": {
            "type": "integer",
            "format": "int64",
            "example": "1640995200"
          },
          "data": {
            "type": "number",
            "format": "double",
            "example": "93566.25"
          }
        },
        "title": "ExchangeMetric",
        "required": [
          "timestamp",
          "data"
        ]
      }
    }
  }
}
```