---
updatedAt: 2026-07-01T15:46:06.000Z
---

Fetch the complete documentation index at: https://apidocs.rh.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further. Append .md to any documentation page URL to get its markdown version.

# Get Batch by Id

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
    "/batches/{batchId}": {
      "get": {
        "tags": [
          "Batches"
        ],
        "summary": "Get Batch by Id",
        "parameters": [
          {
            "description": "Batch ID",
            "name": "batchId",
            "in": "path",
            "required": true,
            "schema": {
              "type": "integer"
            }
          }
        ],
        "responses": {
          "200": {
            "description": "OK",
            "content": {
              "application/json": {
                "schema": {
                  "$ref": "#/components/schemas/response.Batch"
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
      "response.Batch": {
        "type": "object",
        "properties": {
          "batch_details": {
            "allOf": [
              {
                "$ref": "#/components/schemas/response.BatchDetails"
              }
            ],
            "nullable": true
          },
          "batch_number": {
            "type": "integer"
          },
          "blocks": {
            "description": "includes all blocks in the batch",
            "type": "array",
            "items": {
              "$ref": "#/components/schemas/response.BlockMeta"
            }
          },
          "status_changes": {
            "type": "array",
            "items": {
              "$ref": "#/components/schemas/response.BatchStatusChange"
            }
          },
          "updated_at": {
            "type": "string"
          }
        },
        "required": [
          "batch_details",
          "batch_number",
          "blocks",
          "status_changes",
          "updated_at"
        ]
      },
      "response.BatchDetails": {
        "type": "object",
        "properties": {
          "commit_tx_hash": {
            "type": "string",
            "nullable": true
          },
          "execute_tx_hash": {
            "type": "string",
            "nullable": true
          },
          "verify_tx_hash": {
            "type": "string",
            "nullable": true
          }
        },
        "required": [
          "commit_tx_hash",
          "execute_tx_hash",
          "verify_tx_hash"
        ]
      },
      "response.BatchStatus": {
        "type": "string",
        "enum": [
          "nothing_to_execute",
          "committed",
          "verified",
          "executed"
        ],
        "x-enum-varnames": [
          "BatchStatus_NothingToExecute",
          "BatchStatus_Committed",
          "BatchStatus_Verified",
          "BatchStatus_Executed"
        ]
      },
      "response.BatchStatusChange": {
        "type": "object",
        "properties": {
          "batch_status": {
            "$ref": "#/components/schemas/response.BatchStatus"
          },
          "hash": {
            "type": "string"
          },
          "updated_at": {
            "type": "string"
          }
        },
        "required": [
          "batch_status",
          "hash",
          "updated_at"
        ]
      },
      "response.BlockMeta": {
        "type": "object",
        "properties": {
          "block_number": {
            "type": "integer"
          },
          "total_transactions": {
            "type": "integer"
          },
          "updated_at": {
            "type": "string"
          }
        },
        "required": [
          "block_number",
          "total_transactions",
          "updated_at"
        ]
      }
    }
  }
}
```