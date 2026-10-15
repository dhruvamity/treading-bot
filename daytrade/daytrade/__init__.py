"""Three day-trading strategies (SPY, QQQ, gold): their rules, their backtest and a paper auto-trader.

Independent of the market-making bots: nothing here imports their strategies or their simulators.
"""

import datetime as dt
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
REPORTS = ROOT / "reports"
STATE = ROOT / "state"

# The owner's rule (15 Oct 2026): a backtest uses at most the last two years. Results count only trades entered from
# WINDOW_START; SPLIT divides them into the first year (where versions are ranked) and the last twelve months (where
# the ranking is judged). Indicators may read up to WARMUP_DAYS before WINDOW_START (a 200-day average needs about
# ten months), but no trade is entered there.
WINDOW_START = int(dt.datetime(2024, 10, 15, tzinfo=dt.UTC).timestamp())
SPLIT = int(dt.datetime(2025, 10, 15, tzinfo=dt.UTC).timestamp())
WARMUP_DAYS = 300
WARMUP_START = WINDOW_START - WARMUP_DAYS * 86_400
PERIODS = (("two years", WINDOW_START, 2**40), ("first year", WINDOW_START, SPLIT), ("last 12m", SPLIT, 2**40))
