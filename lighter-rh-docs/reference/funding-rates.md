---
updatedAt: 2026-07-01T15:22:51.000Z
---

Fetch the complete documentation index at: https://apidocs.rh.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further. Append .md to any documentation page URL to get its markdown version.

# funding-rates

Get funding rates across venues. For real-time funding rates, use the market_stats WebSocket channel.

# OpenAPI definition

```json
{
  "openapi": "3.0.0",
  "info": {
    "title": "",
    "version": ""
  },
  "paths": {
    "/api/v1/funding-rates": {
      "get": {
        "summary": "funding-rates",
        "operationId": "funding-rates",
        "tags": [
          "funding"
        ],
        "description": "Get funding rates across venues. For real-time funding rates, use the market_stats WebSocket channel.",
        "responses": {
          "200": {
            "description": "A successful response.",
            "content": {
              "application/json": {
                "schema": {
                  "$ref": "#/components/schemas/FundingRates"
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
      "FundingRate": {
        "type": "object",
        "properties": {
          "market_id": {
            "type": "integer",
            "format": "int16"
          },
          "exchange": {
            "type": "string",
            "enum": [
              "binance",
              "bybit",
              "hyperliquid",
              "lighter"
            ]
          },
          "symbol": {
            "type": "string"
          },
          "rate": {
            "type": "number",
            "format": "double"
          }
        },
        "title": "FundingRate",
        "required": [
          "market_id",
          "exchange",
          "symbol",
          "rate"
        ]
      },
      "FundingRates": {
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
          "funding_rates": {
            "type": "array",
            "items": {
              "$ref": "#/components/schemas/FundingRate"
            }
          }
        },
        "title": "FundingRates",
        "required": [
          "code",
          "funding_rates"
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