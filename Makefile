# Every part of the bot, from the repository root: one environment (arcus/.venv) holds them all, one credentials file
# (arcus/.env) serves them all. `make install` once; it prints the line that puts the commands on your PATH.
.PHONY: install env test lint type test-network

UV ?= uv
VENV := $(CURDIR)/arcus/.venv
BIN := $(VENV)/bin
# Arcus first and on its own, so that a problem with another part never keeps it from working. Lighter before the
# arbitrage, and the arbitrage before the delta-neutral bot: each trades through the one before it.
PARTS := lighter arbitrage daytrade deltaneutral

install:
	$(UV) venv --allow-existing --python 3.12 $(VENV)
	$(UV) pip install --python $(BIN)/python -e './arcus[dev]'
	@for p in $(PARTS); do \
		$(UV) pip install --python $(BIN)/python -e "./$$p[dev]" && echo "installed: $$p" \
			|| echo "WARNING: $$p did not install; the other parts work without it"; \
	done
	@$(MAKE) --no-print-directory env
	@echo
	@echo "Installed: tbot, arcus, lighter, arbitrage, daytrade, deltaneutral (in arcus/.venv/bin)."
	@echo "Put them on your PATH once, then type them from any folder:"
	@echo "  echo 'export PATH=\"$(BIN):\$$PATH\"' >> ~/.$$(basename $${SHELL:-bash})rc && exec $${SHELL:-bash}"
	@echo "Then fill in arcus/.env (only what you use) and run:  arcus doctor  and  tbot up"

env:
	@if [ ! -f arcus/.env ]; then cp arcus/.env.example arcus/.env && echo "created arcus/.env from the template"; fi
	@chmod 600 arcus/.env

test:
	cd arcus && $(BIN)/pytest
	cd lighter && $(BIN)/pytest
	cd arbitrage && $(BIN)/pytest
	cd daytrade && $(BIN)/pytest
	cd deltaneutral && $(BIN)/pytest

lint:
	$(BIN)/ruff check arcus/arcus arcus/tests arcus/scripts arcus/deploy/scripts
	$(BIN)/ruff check lighter/lighter_bot lighter/tests lighter/scripts
	$(BIN)/ruff check arbitrage/arbitrage arbitrage/tests
	$(BIN)/ruff check daytrade/daytrade daytrade/tests
	$(BIN)/ruff check deltaneutral/deltaneutral deltaneutral/tests

type:
	cd arcus && $(BIN)/mypy

test-network:
	cd arcus && $(BIN)/pytest -m network
