---
updatedAt: 2026-07-01T15:46:06.000Z
---

Fetch the complete documentation index at: https://apidocs.rh.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further. Append .md to any documentation page URL to get its markdown version.

# Get Recent Batches

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
    "/batches": {
      "get": {
        "tags": [
          "Batches"
        ],
        "summary": "Get Recent Batches",
        "responses": {
          "200": {
            "description": "OK",
            "content": {
              "application/json": {
                "schema": {
                  "type": "array",
                  "items": {
                    "$ref": "#/components/schemas/response.RecentBatch"
                  }
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
      "response.RecentBatch": {
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
          "batch_size": {
            "type": "integer"
          },
          "batch_status": {
            "$ref": "#/components/schemas/response.BatchStatus"
          },
          "updated_at": {
            "type": "string"
          }
        },
        "required": [
          "batch_details",
          "batch_number",
          "batch_size",
          "batch_status",
          "updated_at"
        ]
      }
    }
  }
}
```