SHELL := /bin/bash
BACKEND_DIR := backend
FRONTEND_DIR := frontend

.PHONY: install-backend install-frontend dev up down logs api web test

install-backend:
	cd $(BACKEND_DIR) && python3 -m venv .venv && . .venv/bin/activate && pip install --upgrade pip && pip install -e ".[dev]"

install-frontend:
	cd $(FRONTEND_DIR) && npm install

dev:
	docker compose up --build

up:
	docker compose up -d --build

down:
	docker compose down --remove-orphans

logs:
	docker compose logs -f --tail=200

api:
	cd $(BACKEND_DIR) && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

web:
	cd $(FRONTEND_DIR) && npm run dev

migrate:
	cd $(BACKEND_DIR) && alembic upgrade head

seed:
	cd $(BACKEND_DIR) && python -m app.dev.seed

test:
	cd $(BACKEND_DIR) && pytest
