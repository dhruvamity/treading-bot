---
updatedAt: 2026-07-15T18:04:26.000Z
---

Fetch the complete documentation index at: https://apidocs.rh.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further. Append .md to any documentation page URL to get its markdown version.

# fundings

Get fundings. Maximum 750 fundings will be returned per call. `count_back` indicates the number of fundings you would like to be returned, starting from `start_timestamp`, if set to 0, all fundings will be returned.

# OpenAPI definition

```json
{
  "openapi": "3.0.0",
  "info": {
    "title": "",
    "version": ""
  },
  "paths": {
    "/api/v1/fundings": {
      "get": {
        "summary": "fundings",
        "operationId": "fundings",
        "tags": [
          "candlestick"
        ],
        "description": "Get fundings. Maximum 750 fundings will be returned per call. `count_back` indicates the number of fundings you would like to be returned, starting from `start_timestamp`, if set to 0, all fundings will be returned.",
        "parameters": [
          {
            "name": "market_id",
            "in": "query",
            "required": true,
            "schema": {
              "type": "integer",
              "format": "int16"
            }
          },
          {
            "name": "resolution",
            "in": "query",
            "required": true,
            "schema": {
              "type": "string",
              "enum": [
                "1h",
                "1d"
              ]
            }
          },
          {
            "name": "start_timestamp",
            "in": "query",
            "required": true,
            "schema": {
              "type": "integer",
              "format": "int64",
              "minimum": 0,
              "maximum": 5000000000000
            }
          },
          {
            "name": "end_timestamp",
            "in": "query",
            "required": true,
            "schema": {
              "type": "integer",
              "format": "int64",
              "minimum": 0,
              "maximum": 5000000000000
            }
          },
          {
            "name": "count_back",
            "in": "query",
            "required": true,
            "schema": {
              "type": "integer",
              "format": "int64"
            }
          }
        ],
        "responses": {
          "200": {
            "description": "A successful response.",
            "content": {
              "application/json": {
                "schema": {
                  "$ref": "#/components/schemas/Fundings"
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
      "Funding": {
        "type": "object",
        "properties": {
          "timestamp": {
            "type": "integer",
            "format": "int64",
            "example": "1640995200"
          },
          "value": {
            "type": "string",
            "example": "0.0001"
          },
          "rate": {
            "type": "string",
            "example": "0.0001"
          },
          "direction": {
            "type": "string",
            "example": "long"
          }
        },
        "title": "Funding",
        "required": [
          "timestamp",
          "value",
          "rate",
          "direction"
        ]
      },
      "Fundings": {
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
          "resolution": {
            "type": "string",
            "example": "1h"
          },
          "fundings": {
            "type": "array",
            "items": {
              "$ref": "#/components/schemas/Funding"
            }
          }
        },
        "title": "Fundings",
        "required": [
          "code",
          "resolution",
          "fundings"
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