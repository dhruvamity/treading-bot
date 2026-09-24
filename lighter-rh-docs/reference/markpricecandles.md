---
updatedAt: 2026-07-01T15:22:51.000Z
---

Fetch the complete documentation index at: https://apidocs.rh.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further. Append .md to any documentation page URL to get its markdown version.

# markPriceCandles

Get mark price candles

# OpenAPI definition

```json
{
  "openapi": "3.0.0",
  "info": {
    "title": "",
    "version": ""
  },
  "paths": {
    "/api/v1/markPriceCandles": {
      "get": {
        "summary": "markPriceCandles",
        "operationId": "markPriceCandles",
        "tags": [
          "candlestick"
        ],
        "description": "Get mark price candles",
        "parameters": [
          {
            "name": "market_id",
            "in": "query",
            "required": true,
            "schema": {
              "type": "integer",
              "format": "int16"
            }
          },
          {
            "name": "resolution",
            "in": "query",
            "required": true,
            "schema": {
              "type": "string",
              "enum": [
                "1m",
                "5m",
                "15m",
                "30m",
                "1h",
                "4h",
                "12h",
                "1d"
              ]
            }
          },
          {
            "name": "start_timestamp",
            "in": "query",
            "required": true,
            "schema": {
              "type": "integer",
              "format": "int64",
              "minimum": 0,
              "maximum": 5000000000000
            }
          },
          {
            "name": "end_timestamp",
            "in": "query",
            "required": true,
            "schema": {
              "type": "integer",
              "format": "int64",
              "minimum": 0,
              "maximum": 5000000000000
            }
          },
          {
            "name": "count_back",
            "in": "query",
            "required": true,
            "schema": {
              "type": "integer",
              "format": "int64"
            }
          }
        ],
        "responses": {
          "200": {
            "description": "A successful response.",
            "content": {
              "application/json": {
                "schema": {
                  "$ref": "#/components/schemas/MarkPriceCandles"
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
      "MarkPriceCandles": {
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
          "r": {
            "type": "string",
            "example": "15m",
            "description": " resolution"
          },
          "c": {
            "type": "array",
            "items": {
              "$ref": "#/components/schemas/MarkPriceCandle"
            },
            "description": " candles"
          }
        },
        "title": "MarkPriceCandles",
        "required": [
          "code",
          "r",
          "c"
        ]
      },
      "MarkPriceCandle": {
        "type": "object",
        "properties": {
          "t": {
            "type": "integer",
            "format": "int64",
            "example": "1640995200",
            "description": " timestamp"
          },
          "o": {
            "type": "number",
            "format": "double",
            "example": "3024.66",
            "description": " open"
          },
          "h": {
            "type": "number",
            "format": "double",
            "example": "3034.66",
            "description": " high"
          },
          "l": {
            "type": "number",
            "format": "double",
            "example": "3014.66",
            "description": " low"
          },
          "c": {
            "type": "number",
            "format": "double",
            "example": "3024.66",
            "description": " close"
          },
          "sc": {
            "type": "integer",
            "format": "int64",
            "example": "42",
            "description": " sample_count"
          }
        },
        "title": "MarkPriceCandle",
        "required": [
          "t",
          "o",
          "h",
          "l",
          "c",
          "sc"
        ]
      }
    }
  }
}
```