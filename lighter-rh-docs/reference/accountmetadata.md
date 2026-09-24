---
updatedAt: 2026-07-01T15:22:51.000Z
---

Fetch the complete documentation index at: https://apidocs.rh.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further. Append .md to any documentation page URL to get its markdown version.

# accountMetadata

Get account metadatas

# OpenAPI definition

```json
{
  "openapi": "3.0.0",
  "info": {
    "title": "",
    "version": ""
  },
  "paths": {
    "/api/v1/accountMetadata": {
      "get": {
        "summary": "accountMetadata",
        "operationId": "accountMetadata",
        "tags": [
          "account"
        ],
        "description": "Get account metadatas",
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
                "index",
                "l1_address"
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
            "name": "cursor",
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
                  "$ref": "#/components/schemas/AccountMetadatas"
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
      "AccountMetadata": {
        "type": "object",
        "properties": {
          "account_index": {
            "type": "integer",
            "format": "int64"
          },
          "name": {
            "type": "string"
          },
          "description": {
            "type": "string"
          },
          "can_invite": {
            "type": "boolean",
            "format": "boolean",
            "description": " Remove After FE uses L1 meta endpoint"
          },
          "referral_points_percentage": {
            "type": "string",
            "description": " Remove After FE uses L1 meta endpoint"
          },
          "created_at": {
            "type": "integer",
            "format": "int64"
          },
          "can_rfq": {
            "type": "boolean",
            "format": "boolean"
          },
          "can_rfq_market_ids": {
            "type": "array",
            "items": {
              "type": "string"
            }
          },
          "metadata": {
            "$ref": "#/components/schemas/SubAccountMetadata"
          },
          "agent_enabled": {
            "type": "boolean",
            "format": "boolean"
          }
        },
        "title": "AccountMetadata",
        "required": [
          "account_index",
          "name",
          "description",
          "can_invite",
          "referral_points_percentage",
          "can_rfq",
          "can_rfq_market_ids",
          "agent_enabled",
          "created_at",
          "metadata"
        ]
      },
      "AccountMetadatas": {
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
          "account_metadatas": {
            "type": "array",
            "items": {
              "$ref": "#/components/schemas/AccountMetadata"
            }
          },
          "next_cursor": {
            "type": "string"
          }
        },
        "title": "AccountMetadatas",
        "required": [
          "code",
          "account_metadatas"
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
      "SubAccountMetadata": {
        "type": "object",
        "properties": {
          "color": {
            "type": "string"
          }
        },
        "title": "SubAccountMetadata",
        "required": [
          "color"
        ]
      }
    }
  }
}
```