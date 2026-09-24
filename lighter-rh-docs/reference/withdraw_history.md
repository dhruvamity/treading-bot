---
updatedAt: 2026-07-01T15:22:51.000Z
---

Fetch the complete documentation index at: https://apidocs.rh.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further. Append .md to any documentation page URL to get its markdown version.

# withdraw_history

Get withdraw history. Secure withdrawals are only set to `claimable` when ready. You should only expect the `completed` status on fast withdrawals via Arbitrum.

# OpenAPI definition

```json
{
  "openapi": "3.0.0",
  "info": {
    "title": "",
    "version": ""
  },
  "paths": {
    "/api/v1/withdraw/history": {
      "get": {
        "summary": "withdraw_history",
        "operationId": "withdraw_history",
        "tags": [
          "transaction"
        ],
        "description": "Get withdraw history. Secure withdrawals are only set to `claimable` when ready. You should only expect the `completed` status on fast withdrawals via Arbitrum.",
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
            "required": true,
            "schema": {
              "type": "integer",
              "format": "int64"
            }
          },
          {
            "name": "cursor",
            "in": "query",
            "required": false,
            "schema": {
              "type": "string"
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
                "pending",
                "claimable"
              ]
            }
          }
        ],
        "responses": {
          "200": {
            "description": "A successful response.",
            "content": {
              "application/json": {
                "schema": {
                  "$ref": "#/components/schemas/WithdrawHistory"
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
      "WithdrawHistory": {
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
          "withdraws": {
            "type": "array",
            "items": {
              "$ref": "#/components/schemas/WithdrawHistoryItem"
            }
          },
          "cursor": {
            "type": "string"
          }
        },
        "title": "WithdrawHistory",
        "required": [
          "code",
          "withdraws",
          "cursor"
        ]
      },
      "WithdrawHistoryItem": {
        "type": "object",
        "properties": {
          "id": {
            "type": "string"
          },
          "amount": {
            "type": "string",
            "example": "0.1"
          },
          "timestamp": {
            "type": "integer",
            "format": "int64",
            "example": "1640995200"
          },
          "status": {
            "type": "string",
            "enum": [
              "failed",
              "pending",
              "claimable",
              "refunded",
              "completed"
            ]
          },
          "type": {
            "type": "string",
            "enum": [
              "secure",
              "fast"
            ]
          },
          "l1_tx_hash": {
            "type": "string",
            "example": "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
          },
          "asset_id": {
            "type": "integer",
            "format": "int16"
          }
        },
        "title": "WithdrawHistoryItem",
        "required": [
          "id",
          "amount",
          "timestamp",
          "status",
          "type",
          "l1_tx_hash",
          "asset_id"
        ]
      }
    }
  }
}
```