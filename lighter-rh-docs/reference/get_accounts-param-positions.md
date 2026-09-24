---
updatedAt: 2026-07-01T15:46:06.000Z
---

Fetch the complete documentation index at: https://apidocs.rh.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further. Append .md to any documentation page URL to get its markdown version.

# Get Account by Address

# OpenAPI definition

```json
{
  "openapi": "3.0.0",
  "info": {
    "description": "Explorer Api for ZkLighter",
    "title": "Lighter Explorer API",
    "contact": {},
    "version": "1.0"
  },
  "paths": {
    "/accounts/{param}/positions": {
      "get": {
        "tags": [
          "Account"
        ],
        "summary": "Get Account by Address",
        "parameters": [
          {
            "description": "L1 Address or AccountIndex",
            "name": "param",
            "in": "path",
            "required": true,
            "schema": {
              "type": "string"
            }
          }
        ],
        "responses": {
          "200": {
            "description": "OK",
            "content": {
              "application/json": {
                "schema": {
                  "$ref": "#/components/schemas/response.AccountPositionResponse"
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
      "url": "https://explorerapi.rh.lighter.xyz/api",
      "description": "Production server"
    }
  ],
  "components": {
    "schemas": {
      "response.AccountPositionResponse": {
        "type": "object",
        "properties": {
          "positions": {
            "type": "object",
            "additionalProperties": {
              "$ref": "#/components/schemas/response.EnrichedAccountPosition"
            }
          }
        },
        "required": [
          "positions"
        ]
      },
      "response.EnrichedAccountPosition": {
        "type": "object",
        "properties": {
          "entry_price": {
            "type": "string"
          },
          "market_index": {
            "type": "integer"
          },
          "pnl": {
            "type": "string"
          },
          "side": {
            "$ref": "#/components/schemas/response.PositionSide"
          },
          "size": {
            "type": "string"
          }
        },
        "required": [
          "entry_price",
          "market_index",
          "pnl",
          "side",
          "size"
        ]
      },
      "response.PositionSide": {
        "type": "string",
        "enum": [
          "short",
          "long"
        ],
        "x-enum-varnames": [
          "SHORT",
          "LONG"
        ]
      }
    }
  }
}
```