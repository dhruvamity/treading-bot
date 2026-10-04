.PHONY: install lint type test test-network region-check paper doctor selftest telegram scout scan

PY := .venv/bin/python
UV ?= uv

# One environment for the whole bot: Arcus (this folder), Lighter (../lighter) and the funding arbitrage (../arb).
# The Arcus part is installed first and by itself, so a problem with another part never keeps it from working.
install:
	$(UV) venv --allow-existing --python 3.12 .venv
	$(UV) pip install -e '.[dev]'
	@$(UV) pip install --python $(PY) -e '../lighter[research]' -e ../arb \
		&& echo "installed: bot (Arcus), lbot (Lighter), arb (funding arbitrage)" \
		|| echo "WARNING: the Lighter and funding-arbitrage parts did not install; the Arcus bot works without them"

lint:
	.venv/bin/ruff check bot tests scripts deploy/scripts

type:
	.venv/bin/mypy

test:
	.venv/bin/pytest

test-network:
	.venv/bin/pytest -m network

region-check:
	$(PY) deploy/scripts/region_check.py

SESSION ?= arcus_btc_mm

paper:
	.venv/bin/bot run $(SESSION)

doctor:
	.venv/bin/bot doctor $(SESSION)

selftest:
	.venv/bin/bot selftest

telegram:
	.venv/bin/bot telegram

scout:
	.venv/bin/bot scout run

scan:
	.venv/bin/bot scout scan
