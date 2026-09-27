.PHONY: install test lint

UV ?= uv

install:
	$(UV) venv --allow-existing --python 3.12 .venv
	$(UV) pip install --python .venv/bin/python -e '.[dev,research]'

test:
	.venv/bin/pytest

lint:
	.venv/bin/ruff check lbot tests
