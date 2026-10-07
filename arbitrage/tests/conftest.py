"""Every test runs offline and can never start a real process."""

import os

os.environ["ARB_NO_SPAWN"] = "1"
