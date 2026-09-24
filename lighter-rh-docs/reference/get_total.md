---
updatedAt: 2026-07-01T15:46:06.000Z
---

Fetch the complete documentation index at: https://apidocs.rh.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further. Append .md to any documentation page URL to get its markdown version.

# List Total Values

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
    "/total": {
      "get": {
        "tags": [
          "Total"
        ],
        "summary": "List Total Values",
        "responses": {
          "200": {
            "description": "OK",
            "content": {
              "application/json": {
                "schema": {
                  "$ref": "#/components/schemas/response.TotalValues"
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
      "response.TotalValues": {
        "type": "object",
        "properties": {
          "accounts": {
            "type": "integer"
          },
          "batches": {
            "type": "integer"
          },
          "blocks": {
            "type": "integer"
          },
          "txs": {
            "type": "integer"
          }
        },
        "required": [
          "accounts",
          "batches",
          "blocks",
          "txs"
        ]
      }
    }
  }
}
```