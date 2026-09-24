---
updatedAt: 2026-07-01T15:22:51.000Z
---

Fetch the complete documentation index at: https://apidocs.rh.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further. Append .md to any documentation page URL to get its markdown version.

# status

Get status of zklighter

# OpenAPI definition

```json
{
  "openapi": "3.0.0",
  "info": {
    "title": "",
    "version": ""
  },
  "paths": {
    "/": {
      "get": {
        "summary": "status",
        "operationId": "status",
        "tags": [
          "root"
        ],
        "description": "Get status of zklighter",
        "responses": {
          "200": {
            "description": "A successful response.",
            "content": {
              "application/json": {
                "schema": {
                  "$ref": "#/components/schemas/Status"
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
      "Status": {
        "type": "object",
        "properties": {
          "status": {
            "type": "integer",
            "format": "int32",
            "example": "1"
          },
          "network_id": {
            "type": "integer",
            "format": "int32",
            "example": "1"
          },
          "timestamp": {
            "type": "integer",
            "format": "int64",
            "example": "1717777777"
          }
        },
        "title": "Status",
        "required": [
          "status",
          "network_id",
          "timestamp"
        ]
      }
    }
  }
}
```