---
updatedAt: 2026-07-15T17:59:38.000Z
---

Fetch the complete documentation index at: https://apidocs.rh.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further. Append .md to any documentation page URL to get its markdown version.

# leaseOptions

Returns available lease duration/rate tiers, sorted by duration descending.

# OpenAPI definition

```json
{
  "openapi": "3.0.0",
  "info": {
    "title": "",
    "version": ""
  },
  "paths": {
    "/api/v1/leaseOptions": {
      "get": {
        "summary": "leaseOptions",
        "operationId": "leaseOptions",
        "tags": [
          "account"
        ],
        "description": "Returns available lease duration/rate tiers, sorted by duration descending.",
        "responses": {
          "200": {
            "description": "A successful response.",
            "content": {
              "application/json": {
                "schema": {
                  "$ref": "#/components/schemas/RespGetLeaseOptions"
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
      "LeaseOptionEntry": {
        "type": "object",
        "properties": {
          "duration_days": {
            "type": "integer",
            "description": "Lease duration in days",
            "format": "int32"
          },
          "annual_rate": {
            "type": "number",
            "format": "double",
            "description": "Annual rate as a percentage (e.g. 25.0 means 25%)"
          }
        },
        "required": [
          "duration_days",
          "annual_rate"
        ]
      },
      "RespGetLeaseOptions": {
        "type": "object",
        "properties": {
          "code": {
            "type": "integer",
            "format": "int32"
          },
          "message": {
            "type": "string"
          },
          "options": {
            "type": "array",
            "items": {
              "$ref": "#/components/schemas/LeaseOptionEntry"
            }
          },
          "lit_incentives_account_index": {
            "type": "integer",
            "format": "int64",
            "description": "Account index that receives the leasing fee"
          }
        },
        "required": [
          "code",
          "options",
          "lit_incentives_account_index"
        ]
      }
    }
  }
}
```