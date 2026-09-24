---
updatedAt: 2026-07-21T18:38:59.000Z
---

Fetch the complete documentation index at: https://apidocs.rh.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further. Append .md to any documentation page URL to get its markdown version.

# marketPriceCharts

Get last 24h hourly prices for all markets (mark price for perps, index price for spot)

# OpenAPI definition

```json
{
  "openapi": "3.0.0",
  "info": {
    "title": "",
    "version": ""
  },
  "paths": {
    "/api/v1/marketPriceCharts": {
      "get": {
        "summary": "marketPriceCharts",
        "operationId": "marketPriceCharts",
        "tags": [
          "candlestick"
        ],
        "description": "Get last 24h hourly prices for all markets (mark price for perps, index price for spot)",
        "parameters": [
          {
            "name": "market_ids",
            "in": "query",
            "required": false,
            "schema": {
              "type": "array",
              "items": {
                "type": "integer",
                "format": "int16"
              }
            }
          }
        ],
        "responses": {
          "200": {
            "description": "A successful response.",
            "content": {
              "application/json": {
                "schema": {
                  "$ref": "#/components/schemas/MarketPriceCharts"
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
      "MarketPriceCharts": {
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
          "resolution": {
            "type": "string",
            "example": "1h"
          },
          "price_charts": {
            "type": "array",
            "items": {
              "$ref": "#/components/schemas/MarketPriceChart"
            }
          }
        },
        "title": "MarketPriceCharts",
        "required": [
          "code",
          "resolution",
          "price_charts"
        ]
      },
      "MarketPriceChart": {
        "type": "object",
        "properties": {
          "market_id": {
            "type": "integer",
            "format": "int16"
          },
          "prices": {
            "type": "array",
            "items": {
              "type": "string"
            }
          }
        },
        "title": "MarketPriceChart",
        "required": [
          "market_id",
          "prices"
        ]
      }
    }
  }
}
```