.PHONY: install lint type test test-network region-check paper doctor selftest telegram scout scan

PY := .venv/bin/python
UV ?= uv

install:
	$(UV) venv --python 3.12 .venv
	$(UV) pip install -e '.[dev]'

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
