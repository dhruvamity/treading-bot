---
updatedAt: 2026-07-01T15:22:51.000Z
---

Fetch the complete documentation index at: https://apidocs.rh.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further. Append .md to any documentation page URL to get its markdown version.

# announcement

Get announcements

# OpenAPI definition

```json
{
  "openapi": "3.0.0",
  "info": {
    "title": "",
    "version": ""
  },
  "paths": {
    "/api/v1/announcement": {
      "get": {
        "summary": "announcement",
        "operationId": "announcement",
        "tags": [
          "announcement"
        ],
        "description": "Get announcements",
        "responses": {
          "200": {
            "description": "A successful response.",
            "content": {
              "application/json": {
                "schema": {
                  "$ref": "#/components/schemas/Announcements"
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
      "Announcement": {
        "type": "object",
        "properties": {
          "title": {
            "type": "string"
          },
          "content": {
            "type": "string"
          },
          "created_at": {
            "type": "integer",
            "format": "int64"
          },
          "expired_at": {
            "type": "integer",
            "format": "int64"
          }
        },
        "title": "Announcement",
        "required": [
          "title",
          "content",
          "created_at",
          "expired_at"
        ]
      },
      "Announcements": {
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
          "announcements": {
            "type": "array",
            "items": {
              "$ref": "#/components/schemas/Announcement"
            }
          }
        },
        "title": "Announcements",
        "required": [
          "code",
          "announcements"
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