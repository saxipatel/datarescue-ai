# Everything runs in containers; the host only needs Docker (Compose v2.24+) and make.
# COMPOSE_PROJECT_NAME (default: datarescue) lets a second copy run with its own volumes.
PROJECT := $(or $(COMPOSE_PROJECT_NAME),datarescue)
COMPOSE := docker compose
TEST    := $(COMPOSE) --profile test run --rm -T tests
NODE    := docker run --rm -v "$(CURDIR)/frontend:/app" -w /app node:22-alpine
DEV     := docker run --rm -v "$(CURDIR):/repo" -w /repo datarescue-backend:dev
PW      := docker run --rm --network $(PROJECT)_frontend -e E2E_BASE_URL=http://frontend \
             -v "$(CURDIR)/frontend:/work" -w /work mcr.microsoft.com/playwright:v1.64.0-noble

.DEFAULT_GOAL := help
.PHONY: help setup up down reset seed migrate test test-backend test-frontend e2e demo demo-chaos \
        logs lint fmt fixtures benchmark dbt streaming-up orchestration-up ps

help: ## List targets
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-18s %s\n", $$1, $$2}'

.env:
	cp .env.example .env

setup: .env ## Copy env file, build images, migrate and seed (waits for Postgres)
	$(COMPOSE) build
	$(COMPOSE) --profile test build tests
	$(COMPOSE) up -d postgres redis
	$(COMPOSE) run --rm migrate

up: .env ## Start the stack (frontend :5173, API :8000)
	$(COMPOSE) up -d --build
	@echo "UI  http://localhost:5173   API docs http://localhost:8000/docs"

down: ## Stop the stack (keeps data)
	$(COMPOSE) --profile optional-streaming --profile optional-orchestration --profile analytics down

reset: ## Delete ALL demo data (volumes) and start fresh; asks for confirmation
	@read -p "This deletes the Postgres, Redis and data volumes. Type 'reset' to continue: " ans; \
	  [ "$$ans" = "reset" ] || { echo "aborted"; exit 1; }
	$(COMPOSE) --profile optional-streaming --profile optional-orchestration --profile analytics down -v
	$(COMPOSE) up -d --build

migrate: ## Apply database migrations
	$(COMPOSE) run --rm migrate alembic upgrade head

seed: ## Load reference data and ingest the seed batches (idempotent)
	$(COMPOSE) run --rm migrate python scripts/seed.py

test: test-backend test-frontend ## Backend tests + frontend lint/typecheck/build

test-backend: ## pytest (unit + integration) against Postgres in Docker
	$(TEST) python -m pytest -q

test-frontend: ## ESLint, TypeScript and production build
	$(NODE) sh -c "npm ci --no-audit --no-fund --loglevel=error && npm run lint && npm run typecheck && npm run build"

e2e: ## Playwright UI smoke tests against the running stack (`make up` first)
	$(PW) sh -c "npm ci --no-audit --no-fund --loglevel=error && npx playwright test"

demo: ## Scripted failure -> diagnosis -> approval -> recovery walkthrough via the API
	$(COMPOSE) run --rm --no-deps -T api python scripts/demo_scenario.py --api http://api:8000

demo-chaos: ## Demo plus a crash-and-retry recovery (needs CHAOS_ENABLED=true)
	$(COMPOSE) run --rm --no-deps -T api python scripts/demo_scenario.py --api http://api:8000 --chaos

logs: ## Follow API, worker and scheduler logs
	$(COMPOSE) logs -f api worker scheduler

lint: ## Ruff, formatting check, mypy, ESLint and tsc
	$(DEV) sh -c "ruff check backend scripts optional && ruff format --check backend scripts optional"
	$(TEST) mypy app
	$(NODE) sh -c "npm ci --no-audit --no-fund --loglevel=error && npm run lint && npm run typecheck"

fmt: ## Apply Ruff formatting and autofixes
	$(DEV) sh -c "ruff check --fix backend scripts optional && ruff format backend scripts optional"

fixtures: ## Regenerate labeled fixture datasets into data/fixtures and data/sample
	$(COMPOSE) --profile test run --rm -T -v "$(CURDIR)/data:/app/data" tests python scripts/generate_data.py

benchmark: ## Measure 1k/10k/100k records (separate database); results in data/benchmarks
	$(TEST) env BENCH_OUT=/repo-data/benchmarks python scripts/benchmark.py --records 1000 10000 100000 --seed 42

dbt: ## Build and test the dbt staging view and marts
	$(COMPOSE) --profile analytics run --rm dbt build

streaming-up: ## Optional: Redpanda + Kafka consumer (publish with optional.kafka.producer)
	$(COMPOSE) --profile optional-streaming up -d --build redpanda stream-consumer

orchestration-up: ## Optional: Airflow standalone on :8080 with the datarescue_pipeline DAG
	$(COMPOSE) --profile optional-orchestration up -d airflow

ps: ## Service status
	$(COMPOSE) ps
