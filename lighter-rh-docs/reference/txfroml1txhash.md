---
updatedAt: 2026-07-01T15:22:51.000Z
---

Fetch the complete documentation index at: https://apidocs.rh.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further. Append .md to any documentation page URL to get its markdown version.

# txFromL1TxHash

Get L1 transaction by L1 transaction hash

# OpenAPI definition

```json
{
  "openapi": "3.0.0",
  "info": {
    "title": "",
    "version": ""
  },
  "paths": {
    "/api/v1/txFromL1TxHash": {
      "get": {
        "summary": "txFromL1TxHash",
        "operationId": "txFromL1TxHash",
        "tags": [
          "transaction"
        ],
        "description": "Get L1 transaction by L1 transaction hash",
        "parameters": [
          {
            "name": "hash",
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
                  "$ref": "#/components/schemas/EnrichedTx"
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
      "EnrichedTx": {
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
          "hash": {
            "type": "string",
            "example": "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
          },
          "type": {
            "type": "integer",
            "format": "uint8",
            "example": "1",
            "maximum": 64,
            "minimum": 1
          },
          "info": {
            "type": "string",
            "example": "{}"
          },
          "event_info": {
            "type": "string",
            "example": "{}"
          },
          "status": {
            "type": "integer",
            "format": "int64",
            "example": "1"
          },
          "transaction_index": {
            "type": "integer",
            "format": "int64",
            "example": "8761"
          },
          "l1_address": {
            "type": "string",
            "example": "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
          },
          "account_index": {
            "type": "integer",
            "format": "int64",
            "example": "1"
          },
          "nonce": {
            "type": "integer",
            "format": "int64",
            "example": "722"
          },
          "expire_at": {
            "type": "integer",
            "format": "int64",
            "example": "1640995200"
          },
          "block_height": {
            "type": "integer",
            "format": "int64",
            "example": "45434"
          },
          "queued_at": {
            "type": "integer",
            "format": "int64",
            "example": "1640995200"
          },
          "executed_at": {
            "type": "integer",
            "format": "int64",
            "example": "1640995200"
          },
          "sequence_index": {
            "type": "integer",
            "format": "int64",
            "example": "8761"
          },
          "parent_hash": {
            "type": "string",
            "example": "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
          },
          "api_key_index": {
            "type": "integer",
            "format": "uint8",
            "example": "0"
          },
          "transaction_time": {
            "type": "integer",
            "format": "int64",
            "example": "1257894000000000"
          },
          "committed_at": {
            "type": "integer",
            "format": "int64",
            "example": "1640995200"
          },
          "verified_at": {
            "type": "integer",
            "format": "int64",
            "example": "1640995200"
          }
        },
        "title": "EnrichedTx",
        "required": [
          "code",
          "hash",
          "type",
          "info",
          "event_info",
          "status",
          "transaction_index",
          "l1_address",
          "account_index",
          "nonce",
          "expire_at",
          "block_height",
          "queued_at",
          "executed_at",
          "sequence_index",
          "parent_hash",
          "api_key_index",
          "transaction_time",
          "committed_at",
          "verified_at"
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