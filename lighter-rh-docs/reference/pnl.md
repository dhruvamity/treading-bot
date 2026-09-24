---
updatedAt: 2026-07-01T15:22:51.000Z
---

Fetch the complete documentation index at: https://apidocs.rh.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further. Append .md to any documentation page URL to get its markdown version.

# pnl

Get account PnL chart

# OpenAPI definition

```json
{
  "openapi": "3.0.0",
  "info": {
    "title": "",
    "version": ""
  },
  "paths": {
    "/api/v1/pnl": {
      "get": {
        "summary": "pnl",
        "operationId": "pnl",
        "tags": [
          "account"
        ],
        "description": "Get account PnL chart",
        "parameters": [
          {
            "name": "authorization",
            "in": "header",
            "required": false,
            "schema": {
              "type": "string"
            }
          },
          {
            "name": "by",
            "in": "query",
            "required": true,
            "schema": {
              "type": "string",
              "enum": [
                "index"
              ]
            }
          },
          {
            "name": "value",
            "in": "query",
            "required": true,
            "schema": {
              "type": "string"
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
                "1h",
                "4h",
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
          },
          {
            "name": "ignore_transfers",
            "in": "query",
            "required": false,
            "schema": {
              "type": "boolean",
              "format": "boolean",
              "default": "false"
            }
          }
        ],
        "responses": {
          "200": {
            "description": "A successful response.",
            "content": {
              "application/json": {
                "schema": {
                  "$ref": "#/components/schemas/AccountPnL"
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
      "AccountPnL": {
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
            "example": "15m"
          },
          "pnl": {
            "type": "array",
            "items": {
              "$ref": "#/components/schemas/PnLEntry"
            }
          }
        },
        "title": "AccountPnL",
        "required": [
          "code",
          "resolution",
          "pnl"
        ]
      },
      "PnLEntry": {
        "type": "object",
        "properties": {
          "timestamp": {
            "type": "integer",
            "format": "int64",
            "example": "1640995200"
          },
          "trade_pnl": {
            "type": "number",
            "format": "double",
            "example": "12.0"
          },
          "inflow": {
            "type": "number",
            "format": "double",
            "example": "12.0"
          },
          "outflow": {
            "type": "number",
            "format": "double",
            "example": "12.0"
          },
          "pool_pnl": {
            "type": "number",
            "format": "double",
            "example": "12.0"
          },
          "pool_inflow": {
            "type": "number",
            "format": "double",
            "example": "12.0"
          },
          "pool_outflow": {
            "type": "number",
            "format": "double",
            "example": "12.0"
          },
          "pool_total_shares": {
            "type": "number",
            "format": "double",
            "example": "12.0"
          },
          "spot_inflow": {
            "type": "number",
            "format": "double",
            "example": "12.0"
          },
          "spot_outflow": {
            "type": "number",
            "format": "double",
            "example": "12.0"
          },
          "staked_lit": {
            "type": "number",
            "format": "double",
            "example": "12.0"
          },
          "staking_inflow": {
            "type": "number",
            "format": "double",
            "example": "12.0"
          },
          "staking_outflow": {
            "type": "number",
            "format": "double",
            "example": "12.0"
          },
          "staking_pnl": {
            "type": "number",
            "format": "double",
            "example": "12.0"
          },
          "trade_spot_pnl": {
            "type": "number",
            "format": "double",
            "example": "12.0"
          },
          "volume": {
            "type": "number",
            "format": "double",
            "example": "12.0"
          }
        },
        "title": "PnLEntry",
        "required": [
          "timestamp",
          "trade_pnl",
          "inflow",
          "outflow",
          "pool_pnl",
          "pool_inflow",
          "pool_outflow",
          "pool_total_shares",
          "spot_inflow",
          "spot_outflow",
          "staked_lit",
          "staking_inflow",
          "staking_outflow",
          "staking_pnl",
          "trade_spot_pnl",
          "volume"
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