---
updatedAt: 2026-07-15T18:01:26.000Z
---

Fetch the complete documentation index at: https://apidocs.rh.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further. Append .md to any documentation page URL to get its markdown version.

# apikeys

Get account api key. Set `api_key_index` to 255 to retrieve all api keys associated with the account.

# OpenAPI definition

```json
{
  "openapi": "3.0.0",
  "info": {
    "title": "",
    "version": ""
  },
  "paths": {
    "/api/v1/apikeys": {
      "get": {
        "summary": "apikeys",
        "operationId": "apikeys",
        "tags": [
          "account"
        ],
        "description": "Get account api key. Set `api_key_index` to 255 to retrieve all api keys associated with the account.",
        "parameters": [
          {
            "name": "account_index",
            "in": "query",
            "required": true,
            "schema": {
              "type": "integer",
              "format": "int64"
            }
          },
          {
            "name": "api_key_index",
            "in": "query",
            "required": false,
            "schema": {
              "type": "integer",
              "format": "uint8",
              "default": "255"
            }
          }
        ],
        "responses": {
          "200": {
            "description": "A successful response.",
            "content": {
              "application/json": {
                "schema": {
                  "$ref": "#/components/schemas/AccountApiKeys"
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
      "AccountApiKeys": {
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
          "api_keys": {
            "type": "array",
            "items": {
              "$ref": "#/components/schemas/ApiKey"
            }
          }
        },
        "title": "AccountApiKeys",
        "required": [
          "code",
          "api_keys"
        ]
      },
      "ApiKey": {
        "type": "object",
        "properties": {
          "account_index": {
            "type": "integer",
            "format": "int64",
            "example": "3"
          },
          "api_key_index": {
            "type": "integer",
            "format": "uint8",
            "example": "0"
          },
          "nonce": {
            "type": "integer",
            "format": "int64",
            "example": "722"
          },
          "public_key": {
            "type": "string"
          },
          "transaction_time": {
            "type": "integer",
            "format": "int64"
          }
        },
        "title": "ApiKey",
        "required": [
          "account_index",
          "api_key_index",
          "nonce",
          "public_key",
          "transaction_time"
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