---
updatedAt: 2026-07-15T18:01:34.000Z
---

Fetch the complete documentation index at: https://apidocs.rh.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further. Append .md to any documentation page URL to get its markdown version.

# tokens_create

Create an API token for read-only access

# OpenAPI definition

```json
{
  "openapi": "3.0.0",
  "info": {
    "title": "",
    "version": ""
  },
  "paths": {
    "/api/v1/tokens/create": {
      "post": {
        "summary": "tokens_create",
        "operationId": "tokens_create",
        "tags": [
          "account"
        ],
        "description": "Create an API token for read-only access",
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
                "$ref": "#/components/schemas/ReqPostApiToken"
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
                  "$ref": "#/components/schemas/RespPostApiToken"
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
      "ReqPostApiToken": {
        "type": "object",
        "properties": {
          "name": {
            "type": "string"
          },
          "account_index": {
            "type": "integer",
            "format": "int64"
          },
          "expiry": {
            "type": "integer",
            "format": "int64"
          },
          "sub_account_access": {
            "type": "boolean",
            "format": "boolean"
          },
          "scopes": {
            "type": "string",
            "example": "read.*",
            "default": "read.*"
          }
        },
        "title": "ReqPostApiToken",
        "required": [
          "name",
          "account_index",
          "expiry",
          "sub_account_access"
        ]
      },
      "RespPostApiToken": {
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
          "api_token": {
            "type": "string"
          },
          "name": {
            "type": "string"
          },
          "account_index": {
            "type": "integer",
            "format": "int64"
          },
          "expiry": {
            "type": "integer",
            "format": "int64"
          },
          "sub_account_access": {
            "type": "boolean",
            "format": "boolean"
          },
          "revoked": {
            "type": "boolean",
            "format": "boolean"
          },
          "scopes": {
            "type": "string"
          }
        },
        "title": "RespPostApiToken",
        "required": [
          "code",
          "token_id",
          "api_token",
          "name",
          "account_index",
          "expiry",
          "sub_account_access",
          "revoked",
          "scopes"
        ]
      }
    }
  }
}
```