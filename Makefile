# Run from the repository root. One environment (arcus/.venv) serves all three bots.
.PHONY: install test lint

install:
	$(MAKE) -C arcus install

test:
	cd arcus && .venv/bin/pytest
	cd lighter && ../arcus/.venv/bin/pytest
	cd arbitrage && ../arcus/.venv/bin/pytest

lint:
	cd arcus && .venv/bin/ruff check arcus tests scripts deploy/scripts
	cd lighter && ../arcus/.venv/bin/ruff check lighter_bot tests scripts
	cd arbitrage && ../arcus/.venv/bin/ruff check arbitrage tests
