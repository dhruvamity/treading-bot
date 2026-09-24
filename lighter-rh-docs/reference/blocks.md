---
updatedAt: 2026-07-15T18:03:19.000Z
---

Fetch the complete documentation index at: https://apidocs.rh.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further. Append .md to any documentation page URL to get its markdown version.

# blocks

Get blocks

# OpenAPI definition

```json
{
  "openapi": "3.0.0",
  "info": {
    "title": "",
    "version": ""
  },
  "paths": {
    "/api/v1/blocks": {
      "get": {
        "summary": "blocks",
        "operationId": "blocks",
        "tags": [
          "block"
        ],
        "description": "Get blocks",
        "parameters": [
          {
            "name": "index",
            "in": "query",
            "required": false,
            "schema": {
              "type": "integer",
              "format": "int64"
            }
          },
          {
            "name": "limit",
            "in": "query",
            "required": true,
            "schema": {
              "type": "integer",
              "format": "int64",
              "minimum": 1,
              "maximum": 100
            }
          },
          {
            "name": "sort",
            "in": "query",
            "required": false,
            "schema": {
              "type": "string",
              "enum": [
                "asc",
                "desc"
              ],
              "default": "asc"
            }
          }
        ],
        "responses": {
          "200": {
            "description": "A successful response.",
            "content": {
              "application/json": {
                "schema": {
                  "$ref": "#/components/schemas/Blocks"
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
      "Block": {
        "type": "object",
        "properties": {
          "commitment": {
            "type": "string"
          },
          "height": {
            "type": "integer",
            "format": "int64"
          },
          "state_root": {
            "type": "string"
          },
          "priority_operations": {
            "type": "integer",
            "format": "int32"
          },
          "on_chain_l2_operations": {
            "type": "integer",
            "format": "int32"
          },
          "pending_on_chain_operations_pub_data": {
            "type": "string"
          },
          "committed_tx_hash": {
            "type": "string"
          },
          "committed_at": {
            "type": "integer",
            "format": "int64"
          },
          "verified_tx_hash": {
            "type": "string"
          },
          "verified_at": {
            "type": "integer",
            "format": "int64"
          },
          "txs": {
            "type": "array",
            "items": {
              "$ref": "#/components/schemas/Tx"
            }
          },
          "status": {
            "type": "integer",
            "format": "int64"
          },
          "size": {
            "type": "integer",
            "format": "uin16"
          }
        },
        "title": "Block",
        "required": [
          "commitment",
          "height",
          "state_root",
          "priority_operations",
          "on_chain_l2_operations",
          "pending_on_chain_operations_pub_data",
          "committed_tx_hash",
          "committed_at",
          "verified_tx_hash",
          "verified_at",
          "txs",
          "status",
          "size"
        ]
      },
      "Blocks": {
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
          "total": {
            "type": "integer",
            "format": "int64"
          },
          "blocks": {
            "type": "array",
            "items": {
              "$ref": "#/components/schemas/Block"
            }
          }
        },
        "title": "Blocks",
        "required": [
          "code",
          "total",
          "blocks"
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
      },
      "Tx": {
        "type": "object",
        "properties": {
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
            "format": "uint8"
          },
          "transaction_time": {
            "type": "integer",
            "format": "int64"
          }
        },
        "title": "Tx",
        "required": [
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
          "transaction_time"
        ]
      }
    }
  }
}
```