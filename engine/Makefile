# l2arb developer & CI entrypoints.
# Every gate the Ralph loop must pass before a commit is reachable from here.
# Keep this file the single source of truth for "how do I check the code".

.DEFAULT_GOAL := help
SHELL := /usr/bin/env bash

# Coverage floor. Ratchets upward over time; never lower it (see CLAUDE.md).
COV_FAIL_UNDER ?= 85

.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

.PHONY: setup
setup: ## Create venv and install core deps + dev toolchain
	uv sync
	uv run pre-commit install

.PHONY: setup-all
setup-all: ## Also install the analytics extra (Phase 9 backtesting/research)
	uv sync --all-extras
	uv run pre-commit install

.PHONY: fmt
fmt: ## Auto-format
	uv run ruff format .
	uv run ruff check --fix .

.PHONY: lint
lint: ## Lint (syntax/style tier) — no changes
	uv run ruff format --check .
	uv run ruff check .

.PHONY: types
types: ## Static types (logic/interface tier)
	uv run mypy src tests scripts

.PHONY: pairing
pairing: ## Enforce: every source module has a test module
	uv run python scripts/check_test_pairing.py

.PHONY: unit
unit: ## Fast unit tests (pure logic, no I/O)
	uv run pytest -m "unit" -q

.PHONY: test
test: ## Full suite except chain/db/slow, with coverage gate
	uv run pytest -m "not chain and not db and not slow" \
		--cov=l2arb --cov-branch --cov-report=term-missing \
		--cov-fail-under=$(COV_FAIL_UNDER)

.PHONY: integration
integration: ## Boundary tests (RPC fork, redis, db). Needs services up.
	uv run pytest -m "integration or db or chain" -q

.PHONY: bench
bench: ## Latency/throughput regression gates
	uv run pytest -m "benchmark" --benchmark-only --benchmark-autosave

.PHONY: verify
verify: ## On-chain data-integrity checks vs independent oracle
	uv run pytest -m "verify" -q

.PHONY: audit
audit: ## Security + dependency audit (security tier)
	uv run pip-audit
	uv run bandit -q -r src

.PHONY: check
check: lint types pairing test ## The pre-commit gate: everything fast + green

.PHONY: ci
ci: lint types pairing test audit ## The full CI gate

.PHONY: loop
loop: ## Run the Ralph build loop (see ralph/loop.sh)
	bash ralph/loop.sh

.PHONY: services-up
services-up: ## Start local Postgres/Timescale + Redis for integration tests
	docker compose -f docker/compose.dev.yml up -d

.PHONY: services-down
services-down:
	docker compose -f docker/compose.dev.yml down -v
