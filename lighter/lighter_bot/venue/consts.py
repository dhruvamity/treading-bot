"""Lighter's numbers (apidocs.rh.lighter.xyz: signing-transactions, data-structures-constants-and-errors, and the
official signer's constants.go)."""

from __future__ import annotations

# transaction types (sendTx tx_type)
TX_UPDATE_LEVERAGE = 20
TX_CREATE_ORDER = 14
TX_CANCEL_ORDER = 15
TX_CANCEL_ALL = 16
TX_MODIFY_ORDER = 17

# order types and time in force
ORDER_LIMIT = 0
ORDER_MARKET = 1
ORDER_STOP_LOSS = 2                        # trigger orders: wait for trigger_price, then an IOC order at `price`
ORDER_TAKE_PROFIT = 4
TIF_IOC = 0
TIF_GTT = 1
TIF_POST_ONLY = 2

# cancel-all time in force: SCHEDULED is Lighter's own dead man's switch (cancel everything at `time` unless moved)
CANCEL_ALL_NOW = 0
CANCEL_ALL_SCHEDULED = 1
CANCEL_ALL_ABORT = 2
CANCEL_ALL_MIN_MS = 5 * 60 * 1000          # a scheduled cancel-all at least 5 minutes out (MinOrderCancelAllPeriod)

ORDER_EXPIRY_DEFAULT = -1                  # the signer turns -1 into 28 days
# What clears a dead bot's orders (measured on the venue, 2026-10-09: the scheduled cancel-all does not fire by itself,
# an order's expiry does). Every resting order of the bot expires this long after it is placed: 5 minutes is the
# shortest Lighter takes, plus 30 s for the clocks. An expiry cannot be moved, so a live order is replaced
# QUOTE_RENEW_S before it. Paper and the backtest replace on the same clock, so they lose the same queue places.
QUOTE_EXPIRY_S = 330.0
QUOTE_RENEW_S = 120.0
# What closes a dead bot's position: a stop-loss order resting on Lighter (lighter_bot/trade/live.py: protect),
# triggered at this multiple of the bot's own position stop, so that the bot's own exit comes first while it lives.
VENUE_STOP_X = 2.0
IOC_EXPIRY = 0
CROSS_MARGIN = 0
NIL_MARKET = 255                           # cancel-all over every market
MAX_BATCH = 50                             # transactions in one REST sendTxBatch
MAX_SKIP_NONCE = 2**47 - 1
MAX_CLIENT_ORDER_INDEX = 2**48 - 1
RESERVED_KEY_SLOTS = (0, 1, 2, 3, 157)     # the web and mobile apps' API key slots

# order statuses (Order JSON "status"), and which end an order
OPEN_STATUSES = ("in-progress", "pending", "open")
DONE_STATUSES = ("filled", "canceled", "canceled-post-only", "canceled-reduce-only", "canceled-position-not-allowed",
                 "canceled-margin-not-allowed", "canceled-too-much-slippage", "canceled-not-enough-liquidity",
                 "canceled-self-trade", "canceled-expired", "canceled-oco", "canceled-child", "canceled-liquidation",
                 "canceled-invalid-balance")

# API error codes the bot acts on
ERR_INVALID_NONCE = 21104
ERR_NOT_ENOUGH_MARGIN = 21739
ERR_TOO_MANY_REQUESTS = 23000
ERR_BELOW_INITIAL_MARGIN = 21508
ERR_FAT_FINGER = 21733                    # price too far from the mark; a crossing post-only order instead ends as
ERR_TOO_FAR_FROM_MARK = 21734             # status "canceled-post-only"
