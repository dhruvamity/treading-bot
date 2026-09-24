---
updatedAt: 2026-07-01T15:46:06.000Z
---

Fetch the complete documentation index at: https://apidocs.rh.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further. Append .md to any documentation page URL to get its markdown version.

# Get Account Assets

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
    "/accounts/{param}/assets": {
      "get": {
        "tags": [
          "Account"
        ],
        "summary": "Get Account Assets",
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
                  "$ref": "#/components/schemas/response.AccountAssetResponse"
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
      "response.AccountAssetResponse": {
        "type": "object",
        "properties": {
          "assets": {
            "type": "object",
            "additionalProperties": {
              "$ref": "#/components/schemas/response.EnrichedAccountAsset"
            }
          }
        },
        "required": [
          "assets"
        ]
      },
      "response.EnrichedAccountAsset": {
        "type": "object",
        "properties": {
          "asset_id": {
            "type": "integer"
          },
          "balance": {
            "type": "string"
          },
          "locked_balance": {
            "type": "string"
          },
          "symbol": {
            "type": "string"
          }
        },
        "required": [
          "asset_id",
          "balance",
          "locked_balance",
          "symbol"
        ]
      }
    }
  }
}
```