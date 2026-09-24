---
updatedAt: 2026-07-01T15:22:51.000Z
---

Fetch the complete documentation index at: https://apidocs.rh.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further. Append .md to any documentation page URL to get its markdown version.

# deposit_latest

Get most recent deposit for given l1 address

# OpenAPI definition

```json
{
  "openapi": "3.0.0",
  "info": {
    "title": "",
    "version": ""
  },
  "paths": {
    "/api/v1/deposit/latest": {
      "get": {
        "summary": "deposit_latest",
        "operationId": "deposit_latest",
        "tags": [
          "bridge"
        ],
        "description": "Get most recent deposit for given l1 address",
        "parameters": [
          {
            "name": "l1_address",
            "in": "query",
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
                  "$ref": "#/components/schemas/Deposit"
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
      "Deposit": {
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
          "source": {
            "type": "string",
            "example": "Arbitrum"
          },
          "source_chain_id": {
            "type": "string",
            "example": "42161"
          },
          "fast_bridge_tx_hash": {
            "type": "string",
            "example": "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
          },
          "batch_claim_tx_hash": {
            "type": "string",
            "example": "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
          },
          "cctp_burn_tx_hash": {
            "type": "string",
            "example": "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
          },
          "amount": {
            "type": "string"
          },
          "intent_address": {
            "type": "string"
          },
          "status": {
            "type": "string"
          },
          "step": {
            "type": "string"
          },
          "description": {
            "type": "string"
          },
          "created_at": {
            "type": "integer",
            "format": "int64"
          },
          "updated_at": {
            "type": "integer",
            "format": "int64"
          },
          "is_external_deposit": {
            "type": "boolean",
            "format": "boolean"
          },
          "is_next_bridge_fast": {
            "type": "boolean",
            "format": "boolean"
          }
        },
        "title": "Deposit",
        "required": [
          "code",
          "source",
          "source_chain_id",
          "fast_bridge_tx_hash",
          "batch_claim_tx_hash",
          "cctp_burn_tx_hash",
          "amount",
          "intent_address",
          "status",
          "step",
          "description",
          "created_at",
          "updated_at",
          "is_external_deposit",
          "is_next_bridge_fast"
        ]
      }
    }
  }
}
```