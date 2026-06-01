.PHONY: test test-fast test-cov test-module lint formatting

test:
	@echo "Running all tests..." && pytest

test-fast:
	@echo "Running tests (fail fast)..." && pytest -x --tb=short -q

test-cov:
	@echo "Running tests with coverage report (HTML)..." && pytest --cov=. --cov-report=html:htmlcov && echo "Report generated: htmlcov/index.html"

test-module:
	@echo "Running tests for module: $(MODULE)" && pytest tests/$(MODULE)/

lint:
	@echo "Running Ruff checks..." && ruff check .

formatting:
	@echo "Running Ruff..." && ruff check . --fix
	@echo "Running Ruff format..." && ruff format .
