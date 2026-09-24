---
updatedAt: 2026-07-01T15:46:06.000Z
---

Fetch the complete documentation index at: https://apidocs.rh.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further. Append .md to any documentation page URL to get its markdown version.

# Get Market Latest Logs. To query perpetual markets, use the symbol alone. For spot markets, use `symbol-quote` e.g. `eth-usdc`

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
    "/markets/{symbol}/logs": {
      "get": {
        "tags": [
          "Markets"
        ],
        "summary": "Get Market Latest Logs. To query perpetual markets, use the symbol alone. For spot markets, use `symbol-quote` e.g. `eth-usdc`",
        "parameters": [
          {
            "description": "Symbol",
            "name": "symbol",
            "in": "path",
            "required": true,
            "schema": {
              "type": "string"
            }
          },
          {
            "description": "Limit",
            "name": "limit",
            "in": "query",
            "schema": {
              "type": "integer",
              "maximum": 100
            }
          },
          {
            "description": "Offset",
            "name": "offset",
            "in": "query",
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
                  "type": "array",
                  "items": {
                    "$ref": "#/components/schemas/response.MarketTx"
                  }
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
      "response.BurnedSharesPubData": {
        "type": "object",
        "properties": {
          "account_index": {
            "type": "string"
          },
          "operator_fee_share_amount": {
            "type": "string"
          },
          "public_pool_index": {
            "type": "integer"
          },
          "shares_to_burn": {
            "type": "string"
          },
          "shares_to_burn_usdc": {
            "type": "string"
          }
        },
        "required": [
          "account_index",
          "operator_fee_share_amount",
          "public_pool_index",
          "shares_to_burn",
          "shares_to_burn_usdc"
        ]
      },
      "response.DeleveragePubData": {
        "type": "object",
        "properties": {
          "bankrupt_account_index": {
            "type": "string"
          },
          "deleverager_account_index": {
            "type": "string"
          },
          "is_taker_ask": {
            "type": "integer"
          },
          "market_index": {
            "type": "integer"
          },
          "quote": {
            "type": "string"
          },
          "size": {
            "type": "string"
          }
        },
        "required": [
          "bankrupt_account_index",
          "deleverager_account_index",
          "is_taker_ask",
          "market_index",
          "quote",
          "size"
        ]
      },
      "response.DeleveragePubDataWithFunding": {
        "type": "object",
        "properties": {
          "bankrupt_account_index": {
            "type": "string"
          },
          "deleverager_account_index": {
            "type": "string"
          },
          "funding_rate_prefix_sum": {
            "description": "FundingPubData",
            "type": "integer"
          },
          "is_taker_ask": {
            "type": "integer"
          },
          "market_index": {
            "type": "integer"
          },
          "quote": {
            "type": "string"
          },
          "size": {
            "type": "string"
          }
        },
        "required": [
          "bankrupt_account_index",
          "deleverager_account_index",
          "funding_rate_prefix_sum",
          "is_taker_ask",
          "market_index",
          "quote",
          "size"
        ]
      },
      "response.ExecutedPendingUnlockPubData": {
        "type": "object",
        "properties": {
          "account_index": {
            "type": "string"
          },
          "amount": {
            "type": "string"
          },
          "asset_symbol": {
            "type": "string"
          }
        },
        "required": [
          "account_index",
          "amount",
          "asset_symbol"
        ]
      },
      "response.ExitPositionPubData": {
        "type": "object",
        "properties": {
          "account_index": {
            "type": "string"
          },
          "market_index": {
            "type": "integer"
          },
          "settlement_price": {
            "type": "string"
          }
        },
        "required": [
          "account_index",
          "market_index",
          "settlement_price"
        ]
      },
      "response.ExitPositionPubDataWithFunding": {
        "type": "object",
        "properties": {
          "account_index": {
            "type": "string"
          },
          "funding_rate_prefix_sum": {
            "description": "FundingPubData",
            "type": "integer"
          },
          "market_index": {
            "type": "integer"
          },
          "settlement_price": {
            "type": "string"
          }
        },
        "required": [
          "account_index",
          "funding_rate_prefix_sum",
          "market_index",
          "settlement_price"
        ]
      },
      "response.L1DepositPubData": {
        "type": "object",
        "properties": {
          "account_index": {
            "type": "string"
          },
          "l1_address": {
            "type": "string"
          },
          "usdc_amount": {
            "type": "string"
          }
        },
        "required": [
          "account_index",
          "l1_address",
          "usdc_amount"
        ]
      },
      "response.L1DepositPubDataV2": {
        "type": "object",
        "properties": {
          "accepted_amount": {
            "type": "string"
          },
          "account_index": {
            "type": "string"
          },
          "asset_index": {
            "type": "string"
          },
          "l1_address": {
            "type": "string"
          },
          "route_type": {
            "type": "string"
          }
        },
        "required": [
          "accepted_amount",
          "account_index",
          "asset_index",
          "l1_address",
          "route_type"
        ]
      },
      "response.L2CreateSubAccountPubData": {
        "type": "object",
        "properties": {
          "account_index": {
            "type": "string"
          },
          "subAccount_index": {
            "type": "string"
          }
        },
        "required": [
          "account_index",
          "subAccount_index"
        ]
      },
      "response.L2MintSharesPubData": {
        "type": "object",
        "properties": {
          "account_index": {
            "type": "string"
          },
          "public_pool_index": {
            "type": "integer"
          },
          "share_amount": {
            "type": "string"
          },
          "usdc_amount": {
            "type": "string"
          }
        },
        "required": [
          "account_index",
          "public_pool_index",
          "share_amount",
          "usdc_amount"
        ]
      },
      "response.L2StakeAssetsPubData": {
        "type": "object",
        "properties": {
          "account_index": {
            "type": "string"
          },
          "lit_amount": {
            "type": "string"
          },
          "share_amount": {
            "type": "string"
          },
          "staking_pool_index": {
            "type": "integer"
          }
        },
        "required": [
          "account_index",
          "lit_amount",
          "share_amount",
          "staking_pool_index"
        ]
      },
      "response.L2TransferPubData": {
        "type": "object",
        "properties": {
          "from_account_index": {
            "type": "string"
          },
          "to_account_index": {
            "type": "string"
          },
          "usdc_amount": {
            "type": "string"
          }
        },
        "required": [
          "from_account_index",
          "to_account_index",
          "usdc_amount"
        ]
      },
      "response.L2TransferPubDataV2": {
        "type": "object",
        "properties": {
          "amount": {
            "type": "string"
          },
          "asset_index": {
            "type": "string"
          },
          "fee_account_index": {
            "type": "string"
          },
          "from_account_index": {
            "type": "string"
          },
          "from_route_type": {
            "type": "string"
          },
          "to_account_index": {
            "type": "string"
          },
          "to_route_type": {
            "type": "string"
          },
          "usdc_fee": {
            "type": "string"
          }
        },
        "required": [
          "amount",
          "asset_index",
          "fee_account_index",
          "from_account_index",
          "from_route_type",
          "to_account_index",
          "to_route_type",
          "usdc_fee"
        ]
      },
      "response.L2UpdateAccountConfigPubData": {
        "type": "object",
        "properties": {
          "account_index": {
            "type": "integer"
          },
          "account_trading_mode": {
            "type": "integer"
          },
          "asset_amounts": {
            "type": "array",
            "items": {
              "$ref": "#/components/schemas/response.UnifiedAsset"
            }
          }
        },
        "required": [
          "account_index",
          "account_trading_mode",
          "asset_amounts"
        ]
      },
      "response.L2UpdateLeveragePubData": {
        "type": "object",
        "properties": {
          "account_index": {
            "type": "string"
          },
          "initial_margin_fraction": {
            "type": "integer"
          },
          "margin_mode": {
            "type": "integer"
          },
          "market_index": {
            "type": "integer"
          }
        },
        "required": [
          "account_index",
          "initial_margin_fraction",
          "margin_mode",
          "market_index"
        ]
      },
      "response.L2UpdateMarginPubData": {
        "type": "object",
        "properties": {
          "account_index": {
            "type": "string"
          },
          "direction": {
            "type": "integer"
          },
          "market_index": {
            "type": "integer"
          },
          "usdc_amount": {
            "type": "string"
          }
        },
        "required": [
          "account_index",
          "direction",
          "market_index",
          "usdc_amount"
        ]
      },
      "response.MarketTx": {
        "type": "object",
        "properties": {
          "hash": {
            "type": "string"
          },
          "pubdata": {
            "$ref": "#/components/schemas/response.PubData"
          },
          "pubdata_type": {
            "$ref": "#/components/schemas/response.PubDataType"
          },
          "time": {
            "type": "string"
          },
          "tx_type": {
            "$ref": "#/components/schemas/response.TxType"
          }
        },
        "required": [
          "hash",
          "pubdata",
          "pubdata_type",
          "time",
          "tx_type"
        ]
      },
      "response.PubData": {
        "type": "object",
        "properties": {
          "burned_shares_pubdata": {
            "allOf": [
              {
                "$ref": "#/components/schemas/response.BurnedSharesPubData"
              }
            ],
            "nullable": true
          },
          "deleverage_pubdata": {
            "allOf": [
              {
                "$ref": "#/components/schemas/response.DeleveragePubData"
              }
            ],
            "nullable": true
          },
          "deleverage_pubdata_with_funding": {
            "allOf": [
              {
                "$ref": "#/components/schemas/response.DeleveragePubDataWithFunding"
              }
            ],
            "nullable": true
          },
          "executed_pending_unlock_pubdata": {
            "allOf": [
              {
                "$ref": "#/components/schemas/response.ExecutedPendingUnlockPubData"
              }
            ],
            "nullable": true
          },
          "exit_position_pubdata": {
            "allOf": [
              {
                "$ref": "#/components/schemas/response.ExitPositionPubData"
              }
            ],
            "nullable": true
          },
          "exit_position_pubdata_with_funding": {
            "allOf": [
              {
                "$ref": "#/components/schemas/response.ExitPositionPubDataWithFunding"
              }
            ],
            "nullable": true
          },
          "l1_create_market_pubdata": {
            "allOf": [
              {
                "$ref": "#/components/schemas/types.L1CreateMarketPubData"
              }
            ],
            "nullable": true
          },
          "l1_create_market_pubdata_v2": {
            "allOf": [
              {
                "$ref": "#/components/schemas/types.L1CreateMarketPubDataV2"
              }
            ],
            "nullable": true
          },
          "l1_create_spot_market_pubdata": {
            "allOf": [
              {
                "$ref": "#/components/schemas/types.L1CreateSpotMarketPubData"
              }
            ],
            "nullable": true
          },
          "l1_deposit_pubdata": {
            "allOf": [
              {
                "$ref": "#/components/schemas/response.L1DepositPubData"
              }
            ],
            "nullable": true
          },
          "l1_deposit_pubdata_v2": {
            "allOf": [
              {
                "$ref": "#/components/schemas/response.L1DepositPubDataV2"
              }
            ],
            "nullable": true
          },
          "l1_register_asset_pubdata": {
            "allOf": [
              {
                "$ref": "#/components/schemas/types.L1RegisterAssetPubData"
              }
            ],
            "nullable": true
          },
          "l1_set_system_config_pubdata": {
            "allOf": [
              {
                "$ref": "#/components/schemas/types.L1SetSystemConfigPubData"
              }
            ],
            "nullable": true
          },
          "l1_update_asset_pubdata": {
            "allOf": [
              {
                "$ref": "#/components/schemas/types.L1UpdateAssetPubData"
              }
            ],
            "nullable": true
          },
          "l1_update_market_pubdata": {
            "allOf": [
              {
                "$ref": "#/components/schemas/types.L1UpdateMarketPubData"
              }
            ],
            "nullable": true
          },
          "l1_update_spot_market_pubdata": {
            "allOf": [
              {
                "$ref": "#/components/schemas/types.L1UpdateSpotMarketPubData"
              }
            ],
            "nullable": true
          },
          "l2_create_public_pool_pubdata": {
            "allOf": [
              {
                "$ref": "#/components/schemas/types.L2CreatePublicPoolPubData"
              }
            ],
            "nullable": true
          },
          "l2_create_staking_pool_pubdata": {
            "allOf": [
              {
                "$ref": "#/components/schemas/types.L2CreateStakingPoolPubData"
              }
            ],
            "nullable": true
          },
          "l2_create_sub_account_pubdata": {
            "allOf": [
              {
                "$ref": "#/components/schemas/response.L2CreateSubAccountPubData"
              }
            ],
            "nullable": true
          },
          "l2_mint_shares_pubdata": {
            "allOf": [
              {
                "$ref": "#/components/schemas/response.L2MintSharesPubData"
              }
            ],
            "nullable": true
          },
          "l2_stake_assets_pubdata": {
            "allOf": [
              {
                "$ref": "#/components/schemas/response.L2StakeAssetsPubData"
              }
            ],
            "nullable": true
          },
          "l2_transfer_pubdata": {
            "allOf": [
              {
                "$ref": "#/components/schemas/response.L2TransferPubData"
              }
            ],
            "nullable": true
          },
          "l2_transfer_pubdata_v2": {
            "allOf": [
              {
                "$ref": "#/components/schemas/response.L2TransferPubDataV2"
              }
            ],
            "nullable": true
          },
          "l2_update_account_config_pubdata": {
            "allOf": [
              {
                "$ref": "#/components/schemas/response.L2UpdateAccountConfigPubData"
              }
            ],
            "nullable": true
          },
          "l2_update_leverage_pubdata": {
            "allOf": [
              {
                "$ref": "#/components/schemas/response.L2UpdateLeveragePubData"
              }
            ],
            "nullable": true
          },
          "l2_update_margin_pubdata": {
            "allOf": [
              {
                "$ref": "#/components/schemas/response.L2UpdateMarginPubData"
              }
            ],
            "nullable": true
          },
          "l2_update_public_pool_pubdata": {
            "allOf": [
              {
                "$ref": "#/components/schemas/types.L2UpdatePublicPoolPubData"
              }
            ],
            "nullable": true
          },
          "trade_pubdata": {
            "allOf": [
              {
                "$ref": "#/components/schemas/response.TradePubData"
              }
            ],
            "nullable": true
          },
          "trade_pubdata_with_funding": {
            "allOf": [
              {
                "$ref": "#/components/schemas/response.TradePubDataWithFunding"
              }
            ],
            "nullable": true
          },
          "unstake_assets_pubdata": {
            "allOf": [
              {
                "$ref": "#/components/schemas/response.UnstakeAssetsPubData"
              }
            ],
            "nullable": true
          },
          "withdraw_pubdata": {
            "allOf": [
              {
                "$ref": "#/components/schemas/response.WithdrawPubData"
              }
            ],
            "nullable": true
          },
          "withdraw_pubdata_v2": {
            "allOf": [
              {
                "$ref": "#/components/schemas/response.WithdrawPubDataV2"
              }
            ],
            "nullable": true
          }
        },
        "required": [
          "burned_shares_pubdata",
          "deleverage_pubdata",
          "deleverage_pubdata_with_funding",
          "executed_pending_unlock_pubdata",
          "exit_position_pubdata",
          "exit_position_pubdata_with_funding",
          "l1_create_market_pubdata",
          "l1_create_market_pubdata_v2",
          "l1_create_spot_market_pubdata",
          "l1_deposit_pubdata",
          "l1_deposit_pubdata_v2",
          "l1_register_asset_pubdata",
          "l1_set_system_config_pubdata",
          "l1_update_asset_pubdata",
          "l1_update_market_pubdata",
          "l1_update_spot_market_pubdata",
          "l2_create_public_pool_pubdata",
          "l2_create_staking_pool_pubdata",
          "l2_create_sub_account_pubdata",
          "l2_mint_shares_pubdata",
          "l2_stake_assets_pubdata",
          "l2_transfer_pubdata",
          "l2_transfer_pubdata_v2",
          "l2_update_account_config_pubdata",
          "l2_update_leverage_pubdata",
          "l2_update_margin_pubdata",
          "l2_update_public_pool_pubdata",
          "trade_pubdata",
          "trade_pubdata_with_funding",
          "unstake_assets_pubdata",
          "withdraw_pubdata",
          "withdraw_pubdata_v2"
        ]
      },
      "response.PubDataType": {
        "type": "string",
        "enum": [
          "Empty",
          "L1Deposit",
          "L1CreateMarket",
          "L1UpdateMarket",
          "L2CreateSubAccount",
          "L2CreatePublicPool",
          "L2UpdatePublicPool",
          "L2MintShares",
          "L2UpdateLeverage",
          "L2UpdateMargin",
          "L2Transfer",
          "Withdraw",
          "Trade",
          "TradeWithFunding",
          "LiquidationTrade",
          "LiquidationTradeWithFunding",
          "BurnedShares",
          "ExitPosition",
          "ExitPositionWithFunding",
          "Deleverage",
          "DeleverageWithFunding",
          "L1RegisterAsset",
          "L1UpdateAsset",
          "L1CreateSpotMarket",
          "L1UpdateSpotMarket",
          "WithdrawV2",
          "L1CreateMarketV2",
          "L1DepositV2",
          "L1UpdateMarketV2",
          "L2TransferV2",
          "L2CreateStakingPool",
          "L2UpdateStakingPool",
          "L2StakeAssets",
          "UnstakeAssets",
          "L1SetSystemConfig",
          "ExecutedPendingUnlock",
          "L2UpdateAccountConfig",
          "L2UpdateMarketConfig"
        ],
        "x-enum-varnames": [
          "PubDataType_Empty",
          "PubDataType_L1deposit",
          "PubDataType_L1createMarket",
          "PubDataType_L1updateMarket",
          "PubDataType_L2createSubAccount",
          "PubDataType_L2createPublicPool",
          "PubDataType_L2updatePublicPool",
          "PubDataType_L2mintShares",
          "PubDataType_L2updateLeverage",
          "PubDataType_L2updateMargin",
          "PubDataType_L2transfer",
          "PubDataType_Withdraw",
          "PubDataType_Trade",
          "PubDataType_TradeWithFunding",
          "PubDataType_LiquidationTrade",
          "PubDataType_LiquidationTradeWithFunding",
          "PubDataType_BurnedShares",
          "PubDataType_ExitPosition",
          "PubDataType_ExitPositionWithFunding",
          "PubDataType_Deleverage",
          "PubDataType_DeleverageWithFunding",
          "PubDataType_L1registerAsset",
          "PubDataType_L1updateAsset",
          "PubDataType_L1createSpotMarket",
          "PubDataType_L1updateSpotMarket",
          "PubDataType_WithdrawV2",
          "PubDataType_L1createMarketV2",
          "PubDataType_L1depositV2",
          "PubDataType_L1updateMarketV2",
          "PubDataType_L2transferV2",
          "PubDataType_L2createStakingPool",
          "PubDataType_L2updateStakingPool",
          "PubDataType_L2stakeAssets",
          "PubDataType_UnstakeAssets",
          "PubDataType_L1setSystemConfig",
          "PubDataType_ExecutedPendingUnlock",
          "PubDataType_L2updateAccountConfig",
          "PubDataType_L2updateMarketConfig"
        ]
      },
      "response.TradePubData": {
        "type": "object",
        "properties": {
          "fee_account_index": {
            "type": "string"
          },
          "is_taker_ask": {
            "type": "integer"
          },
          "maker_account_index": {
            "type": "string"
          },
          "maker_fee": {
            "type": "integer"
          },
          "market_index": {
            "type": "integer"
          },
          "price": {
            "type": "string"
          },
          "size": {
            "type": "string"
          },
          "taker_account_index": {
            "type": "string"
          },
          "taker_fee": {
            "type": "integer"
          },
          "trade_type": {
            "type": "integer"
          }
        },
        "required": [
          "fee_account_index",
          "is_taker_ask",
          "maker_account_index",
          "maker_fee",
          "market_index",
          "price",
          "size",
          "taker_account_index",
          "taker_fee",
          "trade_type"
        ]
      },
      "response.TradePubDataWithFunding": {
        "type": "object",
        "properties": {
          "fee_account_index": {
            "type": "string"
          },
          "funding_rate_prefix_sum": {
            "type": "integer"
          },
          "is_taker_ask": {
            "type": "integer"
          },
          "maker_account_index": {
            "type": "string"
          },
          "maker_fee": {
            "type": "integer"
          },
          "market_index": {
            "type": "integer"
          },
          "price": {
            "type": "string"
          },
          "size": {
            "type": "string"
          },
          "taker_account_index": {
            "type": "string"
          },
          "taker_fee": {
            "type": "integer"
          },
          "trade_type": {
            "type": "integer"
          }
        },
        "required": [
          "fee_account_index",
          "funding_rate_prefix_sum",
          "is_taker_ask",
          "maker_account_index",
          "maker_fee",
          "market_index",
          "price",
          "size",
          "taker_account_index",
          "taker_fee",
          "trade_type"
        ]
      },
      "response.TxType": {
        "type": "string",
        "enum": [
          "Empty",
          "L1Deposit",
          "L1ChangePubKey",
          "L1CreateMarket",
          "L1UpdateMarket",
          "L1CancelAllOrders",
          "L1Withdraw",
          "L1CreateOrder",
          "L2ChangePubKey",
          "L2CreateSubAccount",
          "L2CreatePublicPool",
          "L2UpdatePublicPool",
          "L2Transfer",
          "L2Withdraw",
          "L2CreateOrder",
          "L2CancelOrder",
          "L2CancelAllOrders",
          "L2ModifyOrder",
          "L2MintShares",
          "L2BurnShares",
          "L2UpdateLeverage",
          "InternalClaimOrder",
          "InternalCancelOrder",
          "InternalDeleverage",
          "InternalExitPosition",
          "InternalCancelAllOrders",
          "InternalLiquidatePosition",
          "InternalCreateOrder",
          "L2CreateGroupedOrders",
          "L2UpdateMargin",
          "L1BurnShares",
          "L1RegisterAsset",
          "L1UpdateAsset",
          "L2CreateStakingPool",
          "L2StakeAssets",
          "L2UnstakeAssets",
          "L1UnstakeAssets",
          "L1SetSystemConfig",
          "InternalPendingUnlock",
          "L2ForceBurnShares",
          "L2UpdateAccountConfig",
          "L2StrategyTransfer",
          "L2UpdateMarketConfig",
          "InternalTransfer",
          "L2ApproveIntegrator"
        ],
        "x-enum-varnames": [
          "TxType_Empty",
          "TxType_L1deposit",
          "TxType_L1changePubKey",
          "TxType_L1createMarket",
          "TxType_L1updateMarket",
          "TxType_L1cancelAllOrders",
          "TxType_L1withdraw",
          "TxType_L1createOrder",
          "TxType_L2changePubKey",
          "TxType_L2createSubAccount",
          "TxType_L2createPublicPool",
          "TxType_L2updatePublicPool",
          "TxType_L2transfer",
          "TxType_L2withdraw",
          "TxType_L2createOrder",
          "TxType_L2cancelOrder",
          "TxType_L2cancelAllOrders",
          "TxType_L2modifyOrder",
          "TxType_L2mintShares",
          "TxType_L2burnShares",
          "TxType_L2updateLeverage",
          "TxType_InternalClaimOrder",
          "TxType_InternalCancelOrder",
          "TxType_InternalDeleverage",
          "TxType_InternalExitPosition",
          "TxType_InternalCancelAllOrders",
          "TxType_InternalLiquidatePosition",
          "TxType_InternalCreateOrder",
          "TxType_L2createGroupedOrders",
          "TxType_L2updateMargin",
          "TxType_L1burnShares",
          "TxType_L1registerAsset",
          "TxType_L1updateAsset",
          "TxType_L2createStakingPool",
          "TxType_L2stakeAssets",
          "TxType_L2unstakeAssets",
          "TxType_L1unstakeAssets",
          "TxType_L1setSystemConfig",
          "TxType_InternalPendingUnlock",
          "TxType_L2forceBurnShares",
          "TxType_L2updateAccountConfig",
          "TxType_L2strategyTransfer",
          "TxType_L2updateMarketConfig",
          "TxType_InternalTransfer",
          "TxType_L2approveIntegrator"
        ]
      },
      "response.UnifiedAsset": {
        "type": "object",
        "properties": {
          "asset_index": {
            "type": "integer"
          },
          "moved_asset_amount": {
            "type": "string"
          }
        },
        "required": [
          "asset_index",
          "moved_asset_amount"
        ]
      },
      "response.UnstakeAssetsPubData": {
        "type": "object",
        "properties": {
          "account_index": {
            "type": "string"
          },
          "lit_amount": {
            "type": "string"
          },
          "share_amount": {
            "type": "string"
          },
          "staking_pool_index": {
            "type": "integer"
          }
        },
        "required": [
          "account_index",
          "lit_amount",
          "share_amount",
          "staking_pool_index"
        ]
      },
      "response.WithdrawPubData": {
        "type": "object",
        "properties": {
          "from_account_index": {
            "type": "string"
          },
          "usdc_amount": {
            "type": "string"
          }
        },
        "required": [
          "from_account_index",
          "usdc_amount"
        ]
      },
      "response.WithdrawPubDataV2": {
        "type": "object",
        "properties": {
          "amount": {
            "type": "string"
          },
          "asset_index": {
            "type": "string"
          },
          "from_account_index": {
            "type": "string"
          },
          "route_type": {
            "type": "string"
          }
        },
        "required": [
          "amount",
          "asset_index",
          "from_account_index",
          "route_type"
        ]
      },
      "types.L1CreateMarketPubData": {
        "type": "object",
        "properties": {
          "close_out_margin_fraction": {
            "type": "integer"
          },
          "default_initial_margin_fraction": {
            "type": "integer"
          },
          "interest_rate": {
            "type": "integer"
          },
          "liquidation_fee": {
            "type": "integer"
          },
          "maintenance_margin_fraction": {
            "type": "integer"
          },
          "maker_fee": {
            "type": "integer"
          },
          "market_index": {
            "type": "integer"
          },
          "min_base_amount": {
            "type": "integer"
          },
          "min_initial_margin_fraction": {
            "type": "integer"
          },
          "min_quote_amount": {
            "type": "integer"
          },
          "quote_multiplier": {
            "type": "integer"
          },
          "taker_fee": {
            "type": "integer"
          }
        },
        "required": [
          "close_out_margin_fraction",
          "default_initial_margin_fraction",
          "interest_rate",
          "liquidation_fee",
          "maintenance_margin_fraction",
          "maker_fee",
          "market_index",
          "min_base_amount",
          "min_initial_margin_fraction",
          "min_quote_amount",
          "quote_multiplier",
          "taker_fee"
        ]
      },
      "types.L1CreateMarketPubDataV2": {
        "type": "object",
        "properties": {
          "close_out_margin_fraction": {
            "type": "integer"
          },
          "default_initial_margin_fraction": {
            "type": "integer"
          },
          "funding_clamp_big": {
            "type": "integer"
          },
          "funding_clamp_small": {
            "type": "integer"
          },
          "interest_rate": {
            "type": "integer"
          },
          "liquidation_fee": {
            "type": "integer"
          },
          "maintenance_margin_fraction": {
            "type": "integer"
          },
          "maker_fee": {
            "type": "integer"
          },
          "market_index": {
            "type": "integer"
          },
          "min_base_amount": {
            "type": "integer"
          },
          "min_initial_margin_fraction": {
            "type": "integer"
          },
          "min_quote_amount": {
            "type": "integer"
          },
          "open_interest_limit": {
            "type": "integer"
          },
          "order_quote_limit": {
            "type": "integer"
          },
          "quote_multiplier": {
            "type": "integer"
          },
          "taker_fee": {
            "type": "integer"
          }
        },
        "required": [
          "close_out_margin_fraction",
          "default_initial_margin_fraction",
          "funding_clamp_big",
          "funding_clamp_small",
          "interest_rate",
          "liquidation_fee",
          "maintenance_margin_fraction",
          "maker_fee",
          "market_index",
          "min_base_amount",
          "min_initial_margin_fraction",
          "min_quote_amount",
          "open_interest_limit",
          "order_quote_limit",
          "quote_multiplier",
          "taker_fee"
        ]
      },
      "types.L1CreateSpotMarketPubData": {
        "type": "object",
        "properties": {
          "base_asset_id": {
            "type": "integer"
          },
          "maker_fee": {
            "type": "integer"
          },
          "market_index": {
            "type": "integer"
          },
          "min_base_amount": {
            "type": "integer"
          },
          "min_quote_amount": {
            "type": "integer"
          },
          "order_quote_limit": {
            "type": "integer"
          },
          "quote_asset_id": {
            "type": "integer"
          },
          "quote_extension_multiplier": {
            "type": "integer"
          },
          "size_extension_multiplier": {
            "type": "integer"
          },
          "taker_fee": {
            "type": "integer"
          }
        },
        "required": [
          "base_asset_id",
          "maker_fee",
          "market_index",
          "min_base_amount",
          "min_quote_amount",
          "order_quote_limit",
          "quote_asset_id",
          "quote_extension_multiplier",
          "size_extension_multiplier",
          "taker_fee"
        ]
      },
      "types.L1RegisterAssetPubData": {
        "type": "object",
        "properties": {
          "asset_index": {
            "type": "integer"
          },
          "extension_multiplier": {
            "type": "integer"
          },
          "margin_mode": {
            "type": "integer"
          },
          "min_transfer_amount": {
            "type": "integer"
          },
          "min_withdrawal_amount": {
            "type": "integer"
          }
        },
        "required": [
          "asset_index",
          "extension_multiplier",
          "margin_mode",
          "min_transfer_amount",
          "min_withdrawal_amount"
        ]
      },
      "types.L1SetSystemConfigPubData": {
        "type": "object",
        "properties": {
          "liquidity_pool_cooldown_period": {
            "type": "integer"
          },
          "liquidity_pool_index": {
            "type": "integer"
          },
          "max_integrator_perps_maker_fee": {
            "type": "integer"
          },
          "max_integrator_perps_taker_fee": {
            "type": "integer"
          },
          "max_integrator_spot_maker_fee": {
            "type": "integer"
          },
          "max_integrator_spot_taker_fee": {
            "type": "integer"
          },
          "staking_pool_index": {
            "type": "integer"
          },
          "staking_pool_lockup_period": {
            "type": "integer"
          }
        },
        "required": [
          "liquidity_pool_cooldown_period",
          "liquidity_pool_index",
          "max_integrator_perps_maker_fee",
          "max_integrator_perps_taker_fee",
          "max_integrator_spot_maker_fee",
          "max_integrator_spot_taker_fee",
          "staking_pool_index",
          "staking_pool_lockup_period"
        ]
      },
      "types.L1UpdateAssetPubData": {
        "type": "object",
        "properties": {
          "asset_index": {
            "type": "integer"
          },
          "margin_mode": {
            "type": "integer"
          },
          "min_transfer_amount": {
            "type": "integer"
          },
          "min_withdrawal_amount": {
            "type": "integer"
          }
        },
        "required": [
          "asset_index",
          "margin_mode",
          "min_transfer_amount",
          "min_withdrawal_amount"
        ]
      },
      "types.L1UpdateMarketPubData": {
        "type": "object",
        "properties": {
          "close_out_margin_fraction": {
            "type": "integer"
          },
          "default_initial_margin_fraction": {
            "type": "integer"
          },
          "interest_rate": {
            "type": "integer"
          },
          "liquidation_fee": {
            "type": "integer"
          },
          "maintenance_margin_fraction": {
            "type": "integer"
          },
          "maker_fee": {
            "type": "integer"
          },
          "market_index": {
            "type": "integer"
          },
          "min_base_amount": {
            "type": "integer"
          },
          "min_initial_margin_fraction": {
            "type": "integer"
          },
          "min_quote_amount": {
            "type": "integer"
          },
          "status": {
            "type": "integer"
          },
          "taker_fee": {
            "type": "integer"
          }
        },
        "required": [
          "close_out_margin_fraction",
          "default_initial_margin_fraction",
          "interest_rate",
          "liquidation_fee",
          "maintenance_margin_fraction",
          "maker_fee",
          "market_index",
          "min_base_amount",
          "min_initial_margin_fraction",
          "min_quote_amount",
          "status",
          "taker_fee"
        ]
      },
      "types.L1UpdateSpotMarketPubData": {
        "type": "object",
        "properties": {
          "maker_fee": {
            "type": "integer"
          },
          "market_index": {
            "type": "integer"
          },
          "min_base_amount": {
            "type": "integer"
          },
          "min_quote_amount": {
            "type": "integer"
          },
          "order_quote_limit": {
            "type": "integer"
          },
          "status": {
            "type": "integer"
          },
          "taker_fee": {
            "type": "integer"
          }
        },
        "required": [
          "maker_fee",
          "market_index",
          "min_base_amount",
          "min_quote_amount",
          "order_quote_limit",
          "status",
          "taker_fee"
        ]
      },
      "types.L2CreatePublicPoolPubData": {
        "type": "object",
        "properties": {
          "account_index": {
            "type": "integer"
          },
          "initial_total_shares": {
            "type": "integer"
          },
          "min_operator_share_rate": {
            "type": "integer"
          },
          "operator_fee": {
            "type": "integer"
          },
          "public_pool_index": {
            "type": "integer"
          }
        },
        "required": [
          "account_index",
          "initial_total_shares",
          "min_operator_share_rate",
          "operator_fee",
          "public_pool_index"
        ]
      },
      "types.L2CreateStakingPoolPubData": {
        "type": "object",
        "properties": {
          "account_index": {
            "type": "integer"
          },
          "initial_total_shares": {
            "type": "integer"
          },
          "min_operator_share_rate": {
            "type": "integer"
          },
          "staking_pool_index": {
            "type": "integer"
          }
        },
        "required": [
          "account_index",
          "initial_total_shares",
          "min_operator_share_rate",
          "staking_pool_index"
        ]
      },
      "types.L2UpdatePublicPoolPubData": {
        "type": "object",
        "properties": {
          "min_operator_share_rate": {
            "type": "integer"
          },
          "operator_fee": {
            "type": "integer"
          },
          "public_pool_index": {
            "type": "integer"
          },
          "status": {
            "type": "integer"
          }
        },
        "required": [
          "min_operator_share_rate",
          "operator_fee",
          "public_pool_index",
          "status"
        ]
      }
    }
  }
}
```