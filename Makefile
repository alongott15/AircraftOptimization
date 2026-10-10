# Fleet Usability Optimizer — developer commands (see CLAUDE.md)
.PHONY: setup migrate migration user password seed train demo dev dev-backend dev-frontend test test-backend test-frontend e2e lint gen-api

BACKEND := cd backend &&
FRONTEND := cd frontend &&

setup:
	python3 scripts/init_env.py
	$(BACKEND) uv sync
	$(FRONTEND) pnpm install
	$(FRONTEND) pnpm exec playwright install chromium

## App-store schema (PostgreSQL in production via APP_STORE_URL; the POC seed stamps head automatically)
migrate:
	$(BACKEND) uv run alembic upgrade head

## New migration after changing app-store ORM models: make migration m="describe change"
migration:
	$(BACKEND) uv run alembic revision --autogenerate -m "$(m)"

## Create a local login (POC, APP_AUTH_MODE=local) — prompts for username and a hidden password: make user [role=ADMIN]
user:
	$(BACKEND) uv run python -m app.manage create-user --role $(or $(role),ADMIN)

## New password for a local login: make password
password:
	$(BACKEND) uv run python -m app.manage set-password

## Generate data/poc.sqlite anchored to "now" (demo scenarios are relative to the anchor)
seed:
	$(BACKEND) uv run python -m app.seed.generator --anchor now --seed 42 --profile extended

train:
	$(BACKEND) uv run python -m app.ml.train --all

## Fresh demo data + trained models
demo: seed train

dev:
	$(MAKE) -j2 dev-backend dev-frontend

dev-backend:
	$(BACKEND) uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

dev-frontend:
	$(FRONTEND) pnpm dev --host 127.0.0.1 --port 5173

test: test-backend test-frontend

test-backend:
	$(BACKEND) uv run pytest

test-frontend:
	$(FRONTEND) pnpm test

## Playwright E2E — starts its own backend (frozen clock, seeded test DB) and frontend
e2e:
	$(FRONTEND) pnpm e2e

lint:
	$(BACKEND) uv run ruff check app tests
	$(BACKEND) uv run mypy app
	$(BACKEND) uv run lint-imports
	$(FRONTEND) pnpm lint
	$(FRONTEND) pnpm typecheck

## Regenerate the typed API client after backend DTO changes
gen-api:
	$(BACKEND) uv run python -c "import json; from app.config import Settings; from app.main import create_app; print(json.dumps(create_app(Settings(log_level='WARNING')).openapi(), ensure_ascii=False, indent=1))" > ../frontend/openapi.json
	$(FRONTEND) pnpm gen:api
