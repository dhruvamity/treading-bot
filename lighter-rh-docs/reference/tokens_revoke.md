---
updatedAt: 2026-07-15T18:01:39.000Z
---

Fetch the complete documentation index at: https://apidocs.rh.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further. Append .md to any documentation page URL to get its markdown version.

# tokens_revoke

Revoke read only auth token for an account

# OpenAPI definition

```json
{
  "openapi": "3.0.0",
  "info": {
    "title": "",
    "version": ""
  },
  "paths": {
    "/api/v1/tokens/revoke": {
      "post": {
        "summary": "tokens_revoke",
        "operationId": "tokens_revoke",
        "tags": [
          "account"
        ],
        "description": "Revoke read only auth token for an account",
        "parameters": [
          {
            "name": "authorization",
            "in": "header",
            "required": false,
            "schema": {
              "type": "string"
            }
          }
        ],
        "requestBody": {
          "required": true,
          "content": {
            "application/x-www-form-urlencoded": {
              "schema": {
                "$ref": "#/components/schemas/ReqRevokeApiToken"
              }
            }
          }
        },
        "responses": {
          "200": {
            "description": "A successful response.",
            "content": {
              "application/json": {
                "schema": {
                  "$ref": "#/components/schemas/RespRevokeApiToken"
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
      "ReqRevokeApiToken": {
        "type": "object",
        "properties": {
          "token_id": {
            "type": "integer",
            "format": "int64"
          },
          "account_index": {
            "type": "integer",
            "format": "int64"
          }
        },
        "title": "ReqRevokeApiToken",
        "required": [
          "token_id",
          "account_index"
        ]
      },
      "RespRevokeApiToken": {
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
          "token_id": {
            "type": "integer",
            "format": "int64"
          },
          "revoked": {
            "type": "boolean",
            "format": "boolean"
          }
        },
        "title": "RespRevokeApiToken",
        "required": [
          "code",
          "token_id",
          "revoked"
        ]
      }
    }
  }
}
```