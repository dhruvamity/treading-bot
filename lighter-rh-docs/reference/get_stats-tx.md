---
updatedAt: 2026-07-01T15:46:06.000Z
---

Fetch the complete documentation index at: https://apidocs.rh.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further. Append .md to any documentation page URL to get its markdown version.

# Get Tx Stats

# OpenAPI definition

```json
{
  "openapi": "3.0.0",
  "info": {
    "description": "Explorer Api for ZkLighter",
    "title": "Lighter Explorer API",
    "contact": {},
    "version": "1.0"
  },
  "paths": {
    "/stats/tx": {
      "get": {
        "tags": [
          "Stats"
        ],
        "summary": "Get Tx Stats",
        "parameters": [
          {
            "description": "Aggregation period (e.g., 1h)",
            "name": "aggregation_period",
            "in": "query",
            "required": true,
            "schema": {
              "type": "string"
            }
          }
        ],
        "responses": {
          "200": {
            "description": "OK",
            "content": {
              "application/json": {
                "schema": {
                  "$ref": "#/components/schemas/response.StatsResponse"
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
      "url": "https://explorerapi.rh.lighter.xyz/api",
      "description": "Production server"
    }
  ],
  "components": {
    "schemas": {
      "response.ChartData": {
        "type": "object",
        "properties": {
          "data": {
            "type": "array",
            "items": {
              "type": "array",
              "items": {
                "type": "integer"
              }
            }
          },
          "types": {
            "type": "array",
            "items": {
              "type": "string"
            }
          }
        },
        "required": [
          "data",
          "types"
        ]
      },
      "response.StatsData": {
        "type": "object",
        "properties": {
          "chart": {
            "$ref": "#/components/schemas/response.ChartData"
          }
        },
        "required": [
          "chart"
        ]
      },
      "response.StatsResponse": {
        "type": "object",
        "properties": {
          "data": {
            "$ref": "#/components/schemas/response.StatsData"
          }
        },
        "required": [
          "data"
        ]
      }
    }
  }
}
```