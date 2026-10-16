"""Every test runs with the bot's process starter refused: nothing here may start a real bot."""

import os

os.environ["DN_NO_SPAWN"] = "1"
