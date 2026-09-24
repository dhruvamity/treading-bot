---
updatedAt: 2026-09-09T11:34:39.000Z
---

Fetch the complete documentation index at: https://apidocs.rh.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further. Append .md to any documentation page URL to get its markdown version.

# pnlLeaderboard

Get pnl leaderboard

# OpenAPI definition

```json
{
  "openapi": "3.0.0",
  "info": {
    "title": "",
    "version": ""
  },
  "paths": {
    "/api/v1/pnlLeaderboard": {
      "get": {
        "summary": "pnlLeaderboard",
        "operationId": "pnlLeaderboard",
        "tags": [
          "account"
        ],
        "description": "Get pnl leaderboard",
        "parameters": [
          {
            "name": "time_window",
            "in": "query",
            "required": true,
            "schema": {
              "type": "string",
              "enum": [
                "24h",
                "7d",
                "30d",
                "all"
              ],
              "default": "all"
            }
          },
          {
            "name": "sort_by",
            "in": "query",
            "required": true,
            "schema": {
              "type": "string",
              "enum": [
                "pnl",
                "roi",
                "volume",
                "account_value"
              ],
              "default": "pnl"
            }
          },
          {
            "name": "sort_dir",
            "in": "query",
            "required": true,
            "schema": {
              "type": "string",
              "enum": [
                "asc",
                "desc"
              ],
              "default": "desc"
            }
          },
          {
            "name": "limit",
            "in": "query",
            "required": true,
            "schema": {
              "type": "integer",
              "format": "uint8",
              "default": "10",
              "minimum": 1,
              "maximum": 100
            }
          },
          {
            "name": "offset",
            "in": "query",
            "required": true,
            "schema": {
              "type": "integer",
              "format": "int32",
              "default": "0",
              "minimum": 0,
              "maximum": 100000
            }
          },
          {
            "name": "search",
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
                  "$ref": "#/components/schemas/PnlLeaderboard"
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
      "PnlLeaderboard": {
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
          "entries": {
            "type": "array",
            "items": {
              "$ref": "#/components/schemas/PnlLeaderboardEntry"
            }
          },
          "total": {
            "type": "integer",
            "format": "int64",
            "example": "41543"
          },
          "updated_at": {
            "type": "integer",
            "format": "int64",
            "example": "1754992800"
          }
        },
        "title": "PnlLeaderboard",
        "required": [
          "code",
          "entries",
          "total",
          "updated_at"
        ]
      },
      "PnlLeaderboardEntry": {
        "type": "object",
        "properties": {
          "rank": {
            "type": "integer",
            "format": "int64",
            "example": "1"
          },
          "l1_address": {
            "type": "string",
            "example": "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
          },
          "account_value": {
            "type": "number",
            "format": "double",
            "example": "225006123.46"
          },
          "pnl": {
            "type": "number",
            "format": "double",
            "example": "445047878.27"
          },
          "roi": {
            "type": "number",
            "format": "double",
            "example": "26412.5721"
          },
          "volume": {
            "type": "number",
            "format": "double",
            "example": "15999.34"
          }
        },
        "title": "PnlLeaderboardEntry",
        "required": [
          "rank",
          "l1_address",
          "account_value",
          "pnl",
          "roi",
          "volume"
        ]
      }
    }
  }
}
```