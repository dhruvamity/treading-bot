---
updatedAt: 2026-07-01T15:22:51.000Z
---

Fetch the complete documentation index at: https://apidocs.rh.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further. Append .md to any documentation page URL to get its markdown version.

# l1Metadata

Get L1 metadata

# OpenAPI definition

```json
{
  "openapi": "3.0.0",
  "info": {
    "title": "",
    "version": ""
  },
  "paths": {
    "/api/v1/l1Metadata": {
      "get": {
        "summary": "l1Metadata",
        "operationId": "l1Metadata",
        "tags": [
          "account"
        ],
        "description": "Get L1 metadata",
        "parameters": [
          {
            "name": "authorization",
            "in": "header",
            "required": true,
            "schema": {
              "type": "string"
            }
          },
          {
            "name": "l1_address",
            "in": "query",
            "required": true,
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
                  "$ref": "#/components/schemas/L1Metadata"
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
      "L1Metadata": {
        "type": "object",
        "properties": {
          "l1_address": {
            "type": "string"
          },
          "can_invite": {
            "type": "boolean",
            "format": "boolean"
          },
          "referral_points_percentage": {
            "type": "string"
          },
          "referral_program_kickback": {
            "type": "string",
            "example": "10.0"
          },
          "referral_program_kickback_history": {
            "type": "array",
            "items": {
              "$ref": "#/components/schemas/ReferralProgramKickback"
            }
          }
        },
        "title": "L1Metadata",
        "required": [
          "l1_address",
          "can_invite",
          "referral_points_percentage",
          "referral_program_kickback",
          "referral_program_kickback_history"
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
      "ReferralProgramKickback": {
        "type": "object",
        "properties": {
          "date": {
            "type": "integer",
            "format": "int64",
            "example": "1785110400000"
          },
          "percentage": {
            "type": "string",
            "example": "10.0"
          }
        },
        "title": "ReferralProgramKickback",
        "required": [
          "date",
          "percentage"
        ]
      }
    }
  }
}
```