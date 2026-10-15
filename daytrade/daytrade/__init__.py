"""Three day-trading strategies (SPY, QQQ, gold): their rules, their backtest and a paper auto-trader.

Independent of the market-making bots: nothing here imports their strategies or their simulators.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
REPORTS = ROOT / "reports"
STATE = ROOT / "state"
