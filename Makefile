SHELL := /bin/bash
.DEFAULT_GOAL := help

BACKEND_DIR := backend
FRONTEND_DIR := frontend
COMPOSE := docker compose

.PHONY: help bootstrap install-backend install-frontend dev up down reset ps logs logs-backend logs-frontend logs-db api web migrate seed test test-backend test-frontend

help:
	@printf "\nPoulpe AI developer commands\n\n"
	@printf "  make up             Build and start the full Docker Compose stack in the background\n"
	@printf "  make dev            Build and start the full Docker Compose stack in the foreground\n"
	@printf "  make down           Stop the Docker Compose stack\n"
	@printf "  make reset          Stop the stack, remove volumes, and clear local demo state\n"
	@printf "  make logs           Tail all service logs\n"
	@printf "  make logs-backend   Tail backend logs only\n"
	@printf "  make logs-frontend  Tail frontend logs only\n"
	@printf "  make seed           Rerun the demo seed against the current backend container\n"
	@printf "  make test           Run the backend test suite\n"
	@printf "  make test-frontend  Run a production Next.js build\n"
	@printf "  make bootstrap      Install backend and frontend dependencies for non-Docker local dev\n"
	@printf "  make api            Run the backend locally from backend/.venv\n"
	@printf "  make web            Run the frontend locally on port 3000\n\n"

bootstrap: install-backend install-frontend

install-backend:
	cd $(BACKEND_DIR) && python3 -m venv .venv && . .venv/bin/activate && pip install --upgrade pip && pip install -e ".[dev]"

install-frontend:
	cd $(FRONTEND_DIR) && npm install

dev:
	$(COMPOSE) up --build

up:
	$(COMPOSE) up -d --build

down:
	$(COMPOSE) down --remove-orphans

reset:
	$(COMPOSE) down -v --remove-orphans
	rm -rf .orchestrator

ps:
	$(COMPOSE) ps

logs:
	$(COMPOSE) logs -f --tail=200

logs-backend:
	$(COMPOSE) logs -f --tail=200 backend

logs-frontend:
	$(COMPOSE) logs -f --tail=200 frontend

logs-db:
	$(COMPOSE) logs -f --tail=200 postgres redis

api:
	cd $(BACKEND_DIR) && . .venv/bin/activate && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

web:
	cd $(FRONTEND_DIR) && npm run dev

migrate:
	cd $(BACKEND_DIR) && . .venv/bin/activate && alembic upgrade head

seed:
	$(COMPOSE) exec backend python -m app.dev.seed

test: test-backend

test-backend:
	cd $(BACKEND_DIR) && pytest -q

test-frontend:
	cd $(FRONTEND_DIR) && npm run build
