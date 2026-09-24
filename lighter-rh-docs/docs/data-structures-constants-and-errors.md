---
updatedAt: 2026-07-01T15:35:50.000Z
---

Fetch the complete documentation index at: https://apidocs.rh.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further. Append .md to any documentation page URL to get its markdown version.

# Data Structures, Constants and Errors

# Data and Event Structures

```go
type Order struct {
	OrderIndex                  int64  `json:"i"`
	ClientOrderIndex            int64  `json:"u"`
	OwnerAccountId              int64  `json:"a"`
	InitialBaseAmount           int64  `json:"is"`
	Price                       uint32 `json:"p"`
	RemainingBaseAmount         int64  `json:"rs"`
	IsAsk                       uint8  `json:"ia"`
	Type                        uint8  `json:"ot"`
	TimeInForce                 uint8  `json:"f"`
	ReduceOnly                  uint8  `json:"ro"`
	TriggerPrice                uint32 `json:"tp"`
	Expiry                      int64  `json:"e"`
	Status                      uint8  `json:"st"`
	TriggerStatus               uint8  `json:"ts"`
	ToTriggerOrderIndex0        int64  `json:"t0"`
	ToTriggerOrderIndex1        int64  `json:"t1"`
	ToCancelOrderIndex0         int64  `json:"c0"`
	IntegratorFeeCollectorIndex int64  `json:"ifci"`
	IntegratorTakerFee          uint32 `json:"itf"`
	IntegratorMakerFee          uint32 `json:"imf"`
}

type OrderExecution struct {
	MarketId   int16  `json:"m"`
	Trade      *Trade `json:"t"`
	MakerOrder *Order `json:"mo"`
	TakerOrder *Order `json:"to"`
	AppError string `json:"ae"`
}

type CancelOrder struct {
	AccountId        int64 `json:"a"`
	OrderIndex       int64 `json:"i"`
	ClientOrderIndex int64 `json:"u"`
	AppError string `json:"ae"`
}

type ModifyOrder struct {
	MarketId uint8  `json:"m"`
	OldOrder *Order `json:"oo"`
	NewOrder *Order `json:"no"`
	AppError string `json:"ae"`
}


type Trade struct {
	Price    uint32 `json:"p"`
	Size     int64  `json:"s"`
	TakerFee int32  `json:"tf"`
	MakerFee int32  `json:"mf"`
}

// Order Status
const (
  InProgressOrder  = iota // In register
  PendingOrder            // Pending to be triggered
  ActiveLimitOrder        // Active limit order
  FilledOrder // 3
  CanceledOrder // 4
  CanceledOrder_PostOnly // 5
  CanceledOrder_ReduceOnly // 6
  CanceledOrder_PositionNotAllowed // 7
  CanceledOrder_MarginNotAllowed // 8 
  CanceledOrder_TooMuchSlippage // 9 
  CanceledOrder_NotEnoughLiquidity // 10
  CanceledOrder_SelfTrade // 11
  CanceledOrder_Expired // 12
  CanceledOrder_OCO // 13
  CanceledOrder_Child // 14
  CanceledOrder_Liquidation // 15
  CanceledOrder_InvalidBalance // 16
)
```

<br />

# Constants

```go
TxTypeL2ChangePubKey     = 8
TxTypeL2CreateSubAccount = 9
TxTypeL2CreatePublicPool = 10
TxTypeL2UpdatePublicPool = 11
TxTypeL2Transfer         = 12
TxTypeL2Withdraw         = 13
TxTypeL2CreateOrder      = 14
TxTypeL2CancelOrder      = 15
TxTypeL2CancelAllOrders  = 16
TxTypeL2ModifyOrder      = 17
TxTypeL2MintShares       = 18
TxTypeL2BurnShares       = 19
TxTypeL2UpdateLeverage   = 20
TxTypeL2CreateGroupedOrders = 28
TxTypeL2UpdateMargin        = 29
TxTypeL1BurnShares          = 30
```

See all constants here: <https://github.com/elliottech/lighter-go/blob/37514ad5630052c162fa0745ac59ae47ff33d148/types/txtypes/constants.go>

# Transaction Status Mapping

```go
0: Failed
1: Pending
2: Executed
3: Pending - Final State
```

# Error Codes

```go
	// Account
	AppErrAccountNotFound                 = NewBusinessError(21100, "account not found")
	AppErrAccountNonceNotFound            = NewBusinessError(21101, "account nonce not found")
	AppErrInvalidAccountIndex             = NewBusinessError(21102, "invalid account index")
	AppErrInvalidAccountL1Address         = NewBusinessError(21103, "invalid account l1 address")
	AppErrInvalidNonce                    = NewBusinessError(21104, "invalid nonce")
	AppErrNonIncreasingNonce              = NewBusinessError(21105, "batch transaction nonce is not increasing")
	AppErrAccountInvalidToAccount         = NewBusinessError(21106, "invalid ToAccount")
	AppErrInvalidAccount                  = NewBusinessError(21107, "invalid account")
	AppErrInvalidPublicKey                = NewBusinessError(21108, "invalid PublicKey,please run changePubKey")
	AppErrApiKeyNotFound                  = NewBusinessError(21109, "api key not found")
	AppErrInvalidApiKeyIndex              = NewBusinessError(21110, "invalid api key index")
	AppErrPreLiquidation                  = NewBusinessError(21111, "account is in pre-liquidation and the transaction doesn't increase the account health")
	AppErrAccountIsInLiquidation          = NewBusinessError(21112, "account is in liquidation")
	AppErrInvalidInitialMarginFraction    = NewBusinessError(21113, "invalid initial margin fraction")
	AppErrFaultyLiquidation               = NewBusinessError(21114, "account value is over maintenance margin, can't activate liquidation")
	AppErrWithdrawalAmountTooLow          = NewBusinessError(21116, "withdrawal amount is too small")
	AppErrWithdrawalAmountTooHigh         = NewBusinessError(21117, "withdrawal amount is too high")
	AppErrTransferAmountTooLow            = NewBusinessError(21118, "transfer amount is too small")
	AppErrTransferAmountTooHigh           = NewBusinessError(21119, "transfer amount is too high")
	AppErrInvalidSignature                = NewBusinessError(21120, "invalid signature")
	AppErrBatchTxMultipleOwner            = NewBusinessError(21121, "all transactions in the batch must have use same account and apikey")
	AppErrDeadMansSwitchShouldBeTriggered = NewBusinessError(21122, "dead man's switch should be triggered")
	AppErrInvalidAccountType              = NewBusinessError(21123, "invalid account type")
  AppErrInvalidRouteType                = NewBusinessError(21124, "invalid route type")

	// 21124 deprecated
	AppErrWitnessNotFound                  = NewBusinessError(21125, "witness not found")
	AppErrAccountHasZeroCollateral         = NewBusinessError(21126, "Account with zero collateral can't change PublicKey")
	AppErrWithdrawalFromPublicPool         = NewBusinessError(21127, "withdrawal from public pool is not allowed")
	AppErrInvalidMasterAccountIndex        = NewBusinessError(21128, "invalid master account index")
	AppErrTooManySubAccounts               = NewBusinessError(21129, "too many sub accounts")
	AppErrTooManyPublicPools               = NewBusinessError(21130, "too many public pools")
	AppErrInvalidL1Address                 = NewBusinessError(21131, "invalid l1 address")
	AppErrMarginModeChangeOnActivePosition = NewBusinessError(21132, "margin mode change on a market with position or open order is not allowed")
	AppErrInvalidRiskChange                = NewBusinessError(21133, "invalid risk change")
	AppErrInvalidFee                       = NewBusinessError(21134, "invalid fee")
	AppErrAssetAlreadyExist                = NewBusinessError(21135, "asset already exist for given index")
	AppErrPublicKeyUpdateSdk               = NewBusinessError(21136, "invalid PublicKey, update the sdk to the latest version")
	AppErrRestrictedWithdrawal             = NewBusinessError(21137, "Withdrawals are restricted for this asset")
	AppErrMaxPendingUnlocksExceeded        = NewBusinessError(21138, "maximum pending unlocks per account exceeded")
	AppErrNoPendingUnlocks                 = NewBusinessError(21139, "no pending unlocks for the account") 
	AppErrPendingUnlockStillWaiting        = NewBusinessError(21140, "pending unlock is still in waiting period")
	AppErrPendingUnlockInvalidAsset        = NewBusinessError(21141, "only LIT asset can be used for pending unlock")

	// Public Pool
	AppErrInvalidPublicPoolIndex               = NewBusinessError(21200, "invalid public pool index")
	AppErrInvalidOperatorFee                   = NewBusinessError(21201, "invalid operator fee")
	AppErrInvalidPublicPoolStatus              = NewBusinessError(21202, "invalid public pool status")
	AppErrPublicPoolIsFrozen                   = NewBusinessError(21203, "public pool is frozen, only burning is allowed")
	AppErrPoolInitialTotalSharesInvalid        = NewBusinessError(21204, "invalid pool initial usdc amount")
	AppErrInvalidMinOperatorShareRate          = NewBusinessError(21205, "invalid min operator share rate")
	AppErrTooManyInvestedPublicPools           = NewBusinessError(21206, "too many invested public pools")
	AppErrInsufficientAvailableShares          = NewBusinessError(21207, "insufficient available shares")
	AppErrOwnerDropsBelowMinimumOwnership      = NewBusinessError(21208, "owner drops below minimum ownership")
	AppErrInvalidMintShareAmount               = NewBusinessError(21209, "invalid mint share amount")
	AppErrInvalidBurnShareAmount               = NewBusinessError(21210, "invalid burn share amount")
	AppErrShareUSDCAmountTooHigh               = NewBusinessError(21211, "burnt share usdc amount is too high")
	AppErrEntryUSDCAmountTooHigh               = NewBusinessError(21212, "entry usdc amount is too high")
	AppErrInvalidUpdatePublicPoolStatus        = NewBusinessError(21213, "invalid update public pool status")
	AppErrInvalidPoolPositionToTransfer        = NewBusinessError(21214, "invalid pool position to transfer")
	AppErrInvalidMasterAccount                 = NewBusinessError(21215, "only master account can update public pool")
	AppErrPoolInLiquidation                    = NewBusinessError(21216, "public pool is in liquidation")
	AppErrPoolReachedMaximumInvestedPools      = NewBusinessError(21217, "public pool reached maximum invested pools")
	AppErrPoolCreationDisabled                 = NewBusinessError(21218, "pool creation is disabled")
	AppErrMaxLLPPercentageExceeded             = NewBusinessError(21219, "max llp percentage exceeded")
	AppErrPublicPoolHasOpenPositions           = NewBusinessError(21220, "public pool has open positions")
	AppErrPublicPoolHasActiveOrders            = NewBusinessError(21221, "public pool has active orders")
	AppErrPoolHasNoShares                      = NewBusinessError(21222, "public pool has no shares")
	AppErrMaxLLPAmountExceeded                 = NewBusinessError(21223, "max llp amount exceeded")
	AppErrStakingPoolIsFrozen                  = NewBusinessError(21224, "staking pool is frozen, only burning is allowed")
	AppErrInvalidStakingPoolIndex              = NewBusinessError(21225, "invalid staking pool index")
	AppErrEntryStakedAmountTooHigh             = NewBusinessError(21226, "entry staked amount is too high")
	AppErrShareLITAmountTooHigh                = NewBusinessError(21227, "burnt share lit amount is too high")
	AppErrInsufficientAvailableStakingShares   = NewBusinessError(21228, "insufficient available staking shares")
	AppErrInvalidUnstakeAmount                 = NewBusinessError(21229, "invalid unstake amount")
	AppErrStakingPoolAlreadyInitialized        = NewBusinessError(21230, "staking pool is already initialized")
	AppErrMintShareAmountExceedsStakedLIT      = NewBusinessError(21231, "mint share amount exceeds staked LIT value")
	AppErrInvalidStakeAssetAmount              = NewBusinessError(21232, "invalid stake asset amount")
	AppErrInvalidUnstakeAssetAmount            = NewBusinessError(21233, "invalid unstake asset amount")
	AppErrLiquidityPoolCooldownPeriodNotPassed = NewBusinessError(21234, "liquidity pool cooldown period has not passed yet")
	AppErrOperatorSharesCantBeForced           = NewBusinessError(21235, "operator shares can't be force burned")
	AppErrStakingPoolDoesNotExist              = NewBusinessError(21236, "staking pool does not exist")
	AppErrForceBurnSharesExceedsStakedLIT      = NewBusinessError(21237, "force burn shares amount exceeds staked LIT value")

	// Collateral
	AppErrInvalidAssetAmount           = NewBusinessError(21300, "invalid asset amount")
	AppErrNotEnoughCollateral          = NewBusinessError(21301, "not enough collateral")
	AppErrInvalidReceiverAssetAmount   = NewBusinessError(21302, "invalid receiver asset amount")
	AppErrInvalidFeeAccountAssetAmount = NewBusinessError(21303, "invalid fee account asset amount")
	AppErrNotEnoughAssetBalance        = NewBusinessError(21304, "not enough asset balance")
	AppErrNotEnoughAssetBalanceForFee  = NewBusinessError(21305, "not enough asset balance for fee")

	// Block
	AppErrBlockNotFound      = NewBusinessError(21400, "block not found")
  AppErrInvalidBlockHeight = NewBusinessError(21401, "invalid block height")

  // Tx
	AppErrTxNotFound                         = NewBusinessError(21500, "transaction not found")
	AppErrInvalidTxInfo                      = NewBusinessError(21501, "invalid tx info")
	AppErrMarshalTxFailed                    = NewBusinessError(21502, "marshal tx failed")
	AppErrMarshalEventsFailed                = NewBusinessError(21503, "marshal event failed")
	AppErrFailToL1Signature                  = NewBusinessError(21504, "fail to l1 signature")
	AppErrUnsupportedTxType                  = NewBusinessError(21505, "unsupported tx type")
	AppErrTooManyTxs                         = NewBusinessError(21506, "too many pending txs. Please try again later")
	AppErrAccountBelowMaintenanceMargin      = NewBusinessError(21507, "account is below maintenance margin, can't execute transaction")
	AppErrAccountBelowInitialMargin          = NewBusinessError(21508, "account is below initial margin, can't execute transaction")
	AppErrInvalidTxTypeForAccount            = NewBusinessError(21511, "invalid tx type for account")
	AppErrInvalidL1RequestId                 = NewBusinessError(21512, "invalid l1 request id")
	AppErrTxInfoTxTypeLengthMismatch         = NewBusinessError(21513, "TxInfos and TxTypes should have the same length")
	AppErrMaxBatchTx                         = NewBusinessError(21514, "maximum 50 transactions allowed per batch")
	AppErrCannotCreateTxOnAsset              = NewBusinessError(21515, "transaction is not allowed")
	AppErrTreasuryOutgoingTransferNotAllowed = NewBusinessError(21516, "outgoing transfers from treasury account are not allowed")

	// OrderBook
	AppErrInactiveCancel             = NewBusinessError(21600, "given order is not an active limit order")
	AppErrOrderBookFull              = NewBusinessError(21601, "order book is full")
	AppErrInvalidMarketIndex         = NewBusinessError(21602, "invalid market index")
	AppErrInvalidMinAmountsForMarket = NewBusinessError(21603, "invalid min amounts for market")
	// 21604 deprecated
	AppErrInvalidMarketStatus           = NewBusinessError(21605, "invalid market status")
	AppErrMarketAlreadyExist            = NewBusinessError(21606, "market already exist for given index")
	AppErrInvalidMarketFees             = NewBusinessError(21607, "invalid market fees")
	AppErrInvalidQuoteMultiplier        = NewBusinessError(21608, "invalid quote multiplier")
	AppErrInvalidInterestRate           = NewBusinessError(21611, "invalid interest rate")
	AppErrInvalidOpenInterest           = NewBusinessError(21612, "invalid open interest")
	AppErrInvalidMarginMode             = NewBusinessError(21613, "invalid margin mode")
	AppErrNoPositionFound               = NewBusinessError(21614, "no position found")
	AppErrInvalidUpdateMarginDirection  = NewBusinessError(21615, "invalid update margin direction")
	AppErrAssetNotExist                 = NewBusinessError(21616, "asset does not exists for given index")
	AppErrInvalidCreateMarketParameters = NewBusinessError(21617, "invalid create market parameters")
	AppErrInvalidOrderTypeForMarket     = NewBusinessError(21618, "invalid order type for market")
	AppErrInvalidMarketType             = NewBusinessError(21619, "invalid market type")
	AppErrInvalidFundingClamp           = NewBusinessError(21620, "invalid funding clamp")
	AppErrInvalidPerpsMarketIndex       = NewBusinessError(21621, "invalid perps market index")
	AppErrInvalidOrderQuoteLimit        = NewBusinessError(21622, "invalid order quote limit")
	AppErrInvalidOpenInterestLimit      = NewBusinessError(21623, "invalid open interest limit")
	AppErrInvalidMinBaseAmount          = NewBusinessError(21624, "invalid min base amount")
	AppErrInvalidMinQuoteAmount         = NewBusinessError(21625, "invalid min quote amount")
	AppErrInvalidMarginFraction         = NewBusinessError(21626, "invalid margin fraction")

	// Order
	AppErrInvalidOrderIndex                        = NewBusinessError(21700, "invalid order index")
	AppErrInvalidBaseAmount                        = NewBusinessError(21701, "invalid base amount")
	AppErrInvalidPrice                             = NewBusinessError(21702, "invalid price")
	AppErrInvalidIsAsk                             = NewBusinessError(21703, "invalid isAsk")
	AppErrInvalidOrderType                         = NewBusinessError(21704, "invalid OrderType")
	AppErrInvalidOrderTimeInForce                  = NewBusinessError(21705, "invalid OrderTimeInForce")
	AppErrInvalidOrderAmount                       = NewBusinessError(21706, "invalid order base or quote amount")
	AppErrInvalidOrderOwner                        = NewBusinessError(21707, "account is not owner of the order")
	AppErrEmptyOrder                               = NewBusinessError(21708, "order is empty")
	AppErrInactiveOrder                            = NewBusinessError(21709, "order is inactive")
	AppErrUnsupportedOrderType                     = NewBusinessError(21710, "unsupported order type")
	AppErrInvalidOrderExpiry                       = NewBusinessError(21711, "invalid expiry")
	AppErrAccountHasAQueuedCancelAllOrdersRequest  = NewBusinessError(21712, "account has a queued cancel all orders request")
	AppErrInvalidCancelAllTimeInForce              = NewBusinessError(21713, "invalid cancel all time in force")
	AppErrInvalidCancelAllTime                     = NewBusinessError(21714, "invalid cancel all time")
	AppErrInctiveOrder                             = NewBusinessError(21715, "given order is not an active order")
	AppErrOrderNotExpired                          = NewBusinessError(21716, "order is not expired")
	AppErrMaxOrdersPerAccount                      = NewBusinessError(21717, "maximum active limit order count reached")
	AppErrMaxOrdersPerAccountPerMarket             = NewBusinessError(21718, "maximum active limit order count per market reached")
	AppErrMaxPendingOrdersPerAccount               = NewBusinessError(21719, "maximum pending order count reached")
	AppErrMaxPendingOrdersPerAccountPerMarket      = NewBusinessError(21720, "maximum pending order count per market reached")
	AppErrMaxTWAPOrdersInExchange                  = NewBusinessError(21721, "maximum twap order count reached")
	AppErrMaxConditionalOrdersInExchange           = NewBusinessError(21722, "maximum conditional order count reached")
	AppErrInvalidAccountHealth                     = NewBusinessError(21723, "invalid account health")
	AppErrInvalidLiquidationSize                   = NewBusinessError(21724, "invalid liquidation size")
	AppErrInvalidLiquidationPrice                  = NewBusinessError(21725, "invalid liquidation price")
	AppErrInsuranceFundCannotBePartiallyLiquidated = NewBusinessError(21726, "insurance fund cannot be partially liquidated")
	AppErrInvalidClientOrderIndex                  = NewBusinessError(21727, "invalid client order index")
	AppErrClientOrderIndexExists                   = NewBusinessError(21728, "client order index already exists")
	AppErrInvalidOrderTriggerPrice                 = NewBusinessError(21729, "invalid order trigger price")
	AppOrderStatusIsNotPending                     = NewBusinessError(21730, "order status is not pending")
	AppPendingOrderCanNotBeTriggered               = NewBusinessError(21731, "order can not be triggered")
	AppReduceOnlyIncreasesPosition                 = NewBusinessError(21732, "reduce only increases position")
	AppErrFatFingerPrice                           = NewBusinessError(21733, "order price flagged as an accidental price")
	AppErrPriceTooFarFromMarkPrice                 = NewBusinessError(21734, "limit order price is too far from the mark price")
	AppErrPriceTooFarFromTrigger                   = NewBusinessError(21735, "SL/TP order price is too far from the trigger price")
	AppErrInvalidOrderTriggerStatus                = NewBusinessError(21736, "invalid order trigger status")
	AppErrInvalidOrderStatus                       = NewBusinessError(21737, "invalid order status")
	AppErrInvalidReduceOnlyDirection               = NewBusinessError(21738, "invalid reduce only direction")
	AppErrNotEnoughOrderMargin                     = NewBusinessError(21739, "not enough margin to create the order")
	AppErrInvalidReduceOnlyMode                    = NewBusinessError(21740, "invalid reduce only mode")
	AppErrInvalidGroupingType                      = NewBusinessError(21741, "invalid grouping type")
	AppErrInvalidOrderGroupSize                    = NewBusinessError(21742, "invalid order group size")
	AppErrInvalidOrderInfo                         = NewBusinessError(21743, "invalid order info")
	AppErrInvalidAccountTypeForSpotMarket          = NewBusinessError(21744, "pools are not allowed to trade in spot markets")
	AppErrInvalidMarketTypeForL1Order              = NewBusinessError(21745, "only perps markets are allowed for L1 orders")

	// Asset
	AppErrInvalidAssetIndex          = NewBusinessError(21801, "invalid asset index")
	AppErrInvalidAssetMarginMode     = NewBusinessError(21802, "invalid asset margin mode")
	AppErrAssetAlreadyExists         = NewBusinessError(21803, "asset already exists for given index")
	AppErrAssetDoesNotExists         = NewBusinessError(21804, "asset does not exist for given index")
	AppErrOnlyUSDCTransferSupported  = NewBusinessError(21805, "only usdc transfer supported")
	AppErrInvalidExtensionMultiplier = NewBusinessError(21806, "invalid extension multiplier")
	AppErrInvalidMinTransferAmount   = NewBusinessError(21807, "invalid min transfer amount")
	AppErrInvalidMinWithdrawalAmount = NewBusinessError(21808, "invalid min withdrawal amount")
	AppErrAssetNotFound              = NewBusinessError(21809, "asset not found")

	// Deleverage
	AppErrDeleverageAgainstItself                 = NewBusinessError(21901, "deleverage against itself")
	AppErrDeleverageDoesNotMatchLiquidationStatus = NewBusinessError(21902, "deleverage does not match liquidation status")
	AppErrDeleverageWithOpenOrders                = NewBusinessError(21903, "deleverage with open orders")
	AppErrInvalidDeleverageSize                   = NewBusinessError(21904, "invalid deleverage size")
	AppErrInvalidDeleveragePrice                  = NewBusinessError(21905, "invalid deleverage price")
	AppErrInvalidDeleverageSide                   = NewBusinessError(21906, "invalid deleverage side")

	// Candlestick
	AppErrInvalidTimestamps     = NewBusinessError(22400, "invalid timestamps: end_timestamp must be greater than start_timestamp")
	AppErrInvalidTimestamp      = NewBusinessError(22401, "invalid timestamp: timestamp must be greater than 0 and less than year 2286")
	AppErrInvalidResolution     = NewBusinessError(22402, "invalid resolution: resolution unsupported")
	AppErrTimeRangeExceedsLimit = NewBusinessError(22403, "time range exceeds maximum allowed range for the specified resolution")

	// RateLimit
	AppErrTooManyRequest           = NewBusinessError(23000, "Too Many Requests!")
	AppErrTooManySubscriptions     = NewBusinessError(23001, "Too Many Subscriptions!")
	AppErrTooManyDifferentAccounts = NewBusinessError(23002, "Too Many Different Accounts!")
	AppErrTooManyConnections       = NewBusinessError(23003, "Too Many Connections!")
	AppErrTooManyL2Withdrawals     = NewBusinessError(23004, "Too Many L2 Withdrawal Requests!")

	// General Errors
	AppErrNotFound = NewBusinessError(29404, "not found")
	AppErrInternal = NewBusinessError(29500, "internal server error")
	AppTimeout     = NewBusinessError(29501, "process timeout")

	// Websocket
	AppErrWebsocketInvalidJson        = NewBusinessError(30000, "Invalid Json")
	AppErrWebsocketInvalidType        = NewBusinessError(30001, "Invalid Type")
	AppErrWebsocketNotSubscribed      = NewBusinessError(30002, "Not Subscribed to ")
	AppErrWebsocketAlreadySubscribed  = NewBusinessError(30003, "Already Subscribed to ")
	AppErrWebsocketFetchFailed        = NewBusinessError(30004, "Failed to fetch ")
	AppErrWebsocketInvalidChannel     = NewBusinessError(30005, "Invalid Channel")
	AppErrWebsocketNotSupported       = NewBusinessError(30006, "Operation isn't supported ")
	AppErrWebsocketInvalidData        = NewBusinessError(30007, "Invalid Data")
	AppErrWebsocketInvalidAccountType = NewBusinessError(30008, "Invalid account type")
	AppErrWebsocketRateLimit          = NewBusinessError(30009, "Too Many Websocket Messages!")
	AppErrWebsocketTooManyInflight    = NewBusinessError(30010, "Too Many Inflight Messages!")
	AppErrWebsocketFailedToConnect    = NewBusinessError(30011, "Failed to connect")
	AppErrWebsocketFailedToSubscribe  = NewBusinessError(30012, "Failed to subscribe")

	// Referral
	AppErrReferralAlreadyExists      = NewBusinessError(41001, "Referral code already exists")
	AppErrReferralExpired            = NewBusinessError(41002, "Referral code is expired")
	AppErrReferralAlreadyUsed        = NewBusinessError(41003, "Referral code already used")
	AppErrReferralUserHasAccess      = NewBusinessError(41004, "User can already access")
	AppErrReferralUserCantInvite     = NewBusinessError(41005, "User can't invite")
	AppErrReferralInvalid            = NewBusinessError(41006, "Invalid referral code")
	AppErrReferralRequired           = NewBusinessError(41007, "Referral code is required")
	AppErrReferralKickbackUpdate     = NewBusinessError(41008, "Kickback can only be updated once per week")
	AppErrReferralCodeAlreadyUpdated = NewBusinessError(41009, "Referral code has already been updated once, cannot update again")
	AppErrReferralCodeInvalid        = NewBusinessError(41010, "Referral code must be 4-14 characters and contain only uppercase letters (A-Z) and numbers (0-9)")
	AppErrReferralOnlyMainAccount    = NewBusinessError(41011, "only main account index is valid, please use the main account")
	AppErrReferralCannotUseOwnCode   = NewBusinessError(41012, "cannot use your own referral code")

	// Read Only API Token
	AppErrAPITokenExpiryBeforeMinAllowed   = NewBusinessError(61001, "api token expiry is before minimum allowed expiry of 1 day")
	AppErrAPITokenExpiryAfterMaxAllowed    = NewBusinessError(61002, "api token expiry is after maximum allowed expiry of 10 years")
	AppErrAPITokenMaxTokensExceeded        = NewBusinessError(61003, "maximum number of 10 api tokens for the account exceeded")
	AppErrAPITokenCustomScopesNotSupported = NewBusinessError(61004, "custom scopes for api tokens are not supported yet")
	AppErrAPITokenNotFound                 = NewBusinessError(61005, "api token not found or does not belong to this account")
	AppErrAPITokenAlreadyRevoked           = NewBusinessError(61006, "api token has already been revoked")
	AppErrAPITokenNameEmpty                = NewBusinessError(61007, "api token name cannot be empty")
	AppErrAPITokenNameTooLong              = NewBusinessError(61008, "api token name cannot exceed 50 characters")
	AppErrAPITokenNameInvalidChars         = NewBusinessError(61009, "api token name can only contain letters, numbers, spaces, hyphens, underscores, and periods")

	// Tier Change
	AppErrTierChangeInProgress       = NewBusinessError(62001, "tier change already in progress")
	AppErrTierChangeInvalidTier      = NewBusinessError(62002, "invalid tier")
	AppErrTierChangeSameTier         = NewBusinessError(62003, "account already part of requested tier")
	AppErrTierChangeHasOpenPositions = NewBusinessError(62004, "account has open positions")
	AppErrTierChangeHasOpenOrders    = NewBusinessError(62005, "account has open orders")
	AppErrTierChangeTooManyRequest   = NewBusinessError(62006, "too frequent tier change request")
```

<br />