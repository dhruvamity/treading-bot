---
updatedAt: 2026-07-01T15:22:51.000Z
---

Fetch the complete documentation index at: https://apidocs.rh.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further. Append .md to any documentation page URL to get its markdown version.

# createIntentAddress

Create a bridge intent address for CCTP bridge

# OpenAPI definition

```json
{
  "openapi": "3.0.0",
  "info": {
    "title": "",
    "version": ""
  },
  "paths": {
    "/api/v1/createIntentAddress": {
      "post": {
        "summary": "createIntentAddress",
        "operationId": "createIntentAddress",
        "tags": [
          "bridge"
        ],
        "description": "Create a bridge intent address for CCTP bridge",
        "requestBody": {
          "required": true,
          "content": {
            "application/x-www-form-urlencoded": {
              "schema": {
                "$ref": "#/components/schemas/ReqCreateIntentAddress"
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
                  "$ref": "#/components/schemas/CreateIntentAddressResp"
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
      "CreateIntentAddressResp": {
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
          "intent_address": {
            "type": "string"
          }
        },
        "title": "CreateIntentAddressResp",
        "required": [
          "code",
          "intent_address"
        ]
      },
      "ReqCreateIntentAddress": {
        "type": "object",
        "properties": {
          "chain_id": {
            "type": "string"
          },
          "from_addr": {
            "type": "string"
          },
          "amount": {
            "type": "string"
          },
          "is_external_deposit": {
            "type": "boolean",
            "format": "boolean"
          }
        },
        "title": "ReqCreateIntentAddress",
        "required": [
          "chain_id",
          "from_addr",
          "amount"
        ]
      }
    }
  }
}
```