.PHONY: help lint lint-fix format typecheck test test-fast test-arch check clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------------------
# Linting
# ---------------------------------------------------------------------------

lint: ## Run ruff linter (report only, no changes)
	python3 -m ruff check .

lint-fix: ## Auto-fix safe issues (imports are NEVER removed)
	python3 -m ruff check --fix .

format: ## Run black formatter
	python3 -m black .

format-check: ## Check formatting without modifying files
	python3 -m black --check .

# ---------------------------------------------------------------------------
# Type checking
# ---------------------------------------------------------------------------

typecheck: ## Run mypy on finance_kernel
	python3 -m mypy finance_kernel/

typecheck-all: ## Run mypy on all source packages
	python3 -m mypy finance_kernel/ finance_engines/ finance_modules/ finance_services/ finance_config/

# ---------------------------------------------------------------------------
# Testing
# ---------------------------------------------------------------------------

test: ## Run full test suite
	python3 -m pytest tests/ -v --tb=short

test-fast: ## Run tests excluding slow locks and benchmarks
	python3 -m pytest tests/ -v --tb=short -m "not slow_locks and not benchmark"

test-arch: ## Run architecture boundary tests only
	python3 -m pytest tests/architecture/ -v --tb=short

test-cov: ## Run tests with coverage report
	python3 -m pytest tests/ -v --tb=short --cov=finance_kernel --cov-report=term-missing

# ---------------------------------------------------------------------------
# Combined checks
# ---------------------------------------------------------------------------

check: lint format-check typecheck test-arch ## Run all static checks (lint + format + types + arch)
	@echo "\n All static checks passed."

ci: check test ## Full CI pipeline (static checks + all tests)
	@echo "\n CI pipeline passed."
