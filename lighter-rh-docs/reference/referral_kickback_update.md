---
updatedAt: 2026-07-01T15:22:51.000Z
---

Fetch the complete documentation index at: https://apidocs.rh.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further. Append .md to any documentation page URL to get its markdown version.

# referral_kickback_update

Update kickback percentage for referral rewards (allowed once per day)

# OpenAPI definition

```json
{
  "openapi": "3.0.0",
  "info": {
    "title": "",
    "version": ""
  },
  "paths": {
    "/api/v1/referral/kickback/update": {
      "post": {
        "summary": "referral_kickback_update",
        "operationId": "referral_kickback_update",
        "tags": [
          "referral"
        ],
        "description": "Update kickback percentage for referral rewards (allowed once per day)",
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
                "$ref": "#/components/schemas/ReqUpdateKickback"
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
                  "$ref": "#/components/schemas/RespUpdateKickback"
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
      "ReqUpdateKickback": {
        "type": "object",
        "properties": {
          "account_index": {
            "type": "integer",
            "format": "int64"
          },
          "kickback_percentage": {
            "type": "number",
            "format": "double",
            "maximum": 100
          }
        },
        "title": "ReqUpdateKickback",
        "required": [
          "account_index",
          "kickback_percentage"
        ]
      },
      "RespUpdateKickback": {
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
          "success": {
            "type": "boolean",
            "format": "boolean",
            "example": "true"
          }
        },
        "title": "RespUpdateKickback",
        "required": [
          "code",
          "success"
        ]
      }
    }
  }
}
```