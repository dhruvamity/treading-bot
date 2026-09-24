---
updatedAt: 2026-07-01T15:22:51.000Z
---

Fetch the complete documentation index at: https://apidocs.rh.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further. Append .md to any documentation page URL to get its markdown version.

# positionFunding

Get accounts position fundings

# OpenAPI definition

```json
{
  "openapi": "3.0.0",
  "info": {
    "title": "",
    "version": ""
  },
  "paths": {
    "/api/v1/positionFunding": {
      "get": {
        "summary": "positionFunding",
        "operationId": "positionFunding",
        "tags": [
          "account"
        ],
        "description": "Get accounts position fundings",
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
            "name": "account_index",
            "in": "query",
            "required": true,
            "schema": {
              "type": "integer",
              "format": "int64"
            }
          },
          {
            "name": "cursor",
            "in": "query",
            "required": false,
            "schema": {
              "type": "string"
            }
          },
          {
            "name": "limit",
            "in": "query",
            "required": true,
            "schema": {
              "type": "integer",
              "format": "int64",
              "minimum": 1,
              "maximum": 100
            }
          },
          {
            "name": "side",
            "in": "query",
            "required": false,
            "schema": {
              "type": "string",
              "enum": [
                "long",
                "short",
                "all"
              ],
              "default": "all"
            }
          },
          {
            "name": "start_timestamp",
            "in": "query",
            "required": false,
            "schema": {
              "type": "integer",
              "format": "int64"
            }
          },
          {
            "name": "end_timestamp",
            "in": "query",
            "required": false,
            "schema": {
              "type": "integer",
              "format": "int64"
            }
          },
          {
            "name": "market_ids",
            "in": "query",
            "required": false,
            "description": "comma-separated int64s, e.g. \"1,2\"",
            "schema": {
              "type": "array",
              "items": {
                "type": "integer",
                "format": "int16"
              }
            }
          }
        ],
        "responses": {
          "200": {
            "description": "A successful response.",
            "content": {
              "application/json": {
                "schema": {
                  "$ref": "#/components/schemas/PositionFundings"
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
      "PositionFunding": {
        "type": "object",
        "properties": {
          "timestamp": {
            "type": "integer",
            "format": "int64",
            "example": "1640995200"
          },
          "market_id": {
            "type": "integer",
            "format": "int16",
            "example": "1"
          },
          "funding_id": {
            "type": "integer",
            "format": "int64",
            "example": "1"
          },
          "change": {
            "type": "string",
            "example": "1"
          },
          "discount": {
            "type": "string",
            "example": "1"
          },
          "rate": {
            "type": "string",
            "example": "1"
          },
          "position_size": {
            "type": "string",
            "example": "1"
          },
          "position_side": {
            "type": "string",
            "example": "long",
            "enum": [
              "long",
              "short"
            ]
          }
        },
        "title": "PositionFunding",
        "required": [
          "timestamp",
          "market_id",
          "funding_id",
          "change",
          "rate",
          "position_size",
          "position_side",
          "discount"
        ]
      },
      "PositionFundings": {
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
          "position_fundings": {
            "type": "array",
            "items": {
              "$ref": "#/components/schemas/PositionFunding"
            }
          },
          "next_cursor": {
            "type": "string"
          }
        },
        "title": "PositionFundings",
        "required": [
          "code",
          "position_fundings"
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