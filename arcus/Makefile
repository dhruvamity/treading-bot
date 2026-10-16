.PHONY: install lint type test test-network

PY := .venv/bin/python
UV ?= uv

# One environment for all three bots: Arcus (this folder), Lighter (../lighter) and the funding arbitrage
# (../arbitrage). Arcus is installed first and by itself, so a problem with another bot never keeps it from working.
install:
	$(UV) venv --allow-existing --python 3.12 .venv
	$(UV) pip install -e '.[dev]'
	@$(UV) pip install --python $(PY) -e '../lighter[dev]' -e '../arbitrage[dev]' \
		&& echo "installed: arcus, lighter, arbitrage" \
		|| echo "WARNING: lighter and arbitrage did not install; the Arcus bot works without them"
	@$(UV) pip install --python $(PY) -e '../daytrade[dev]' \
		&& echo "installed: daytrade (paper only)" \
		|| echo "WARNING: daytrade did not install; the three bots work without it"
	@$(UV) pip install --python $(PY) -e '../deltaneutral[dev]' \
		&& echo "installed: deltaneutral" \
		|| echo "WARNING: deltaneutral did not install; the other bots work without it"

lint:
	.venv/bin/ruff check arcus tests scripts deploy/scripts

type:
	.venv/bin/mypy

test:
	.venv/bin/pytest

test-network:
	.venv/bin/pytest -m network
