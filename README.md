# Local Agent Orchestrator v0

Local Agent Orchestrator is a local-first multi-agent coding orchestrator. A single manager session supervises worker and reviewer sessions, each running as supervised local terminal processes in isolated git worktrees. v0 is deliberately narrow: one repo per project, code tasks only, persistent state in Postgres, Redis for queue/pubsub, and human approval before anything is marked merge-ready.

This repository is scaffolded for clarity, not completeness. The backend and frontend are wired with stable module boundaries, database and event contracts are defined, and the main API surface exists as typed stubs.

## Stack

- Backend: FastAPI, SQLAlchemy models, Postgres, Redis
- Frontend: Next.js App Router with TypeScript
- Local dev: Docker Compose
- Orchestration model: manager, worker, reviewer sessions with per-worker git branch + git worktree

## Repository layout

```text
.
├── backend
│   ├── app
│   │   ├── adapters
│   │   ├── api
│   │   ├── core
│   │   ├── models
│   │   ├── prompts
│   │   ├── schemas
│   │   └── services
│   ├── migrations
│   └── tests
├── frontend
│   ├── app
│   ├── components
│   └── lib
├── docker-compose.yml
├── Makefile
├── README.md
├── SPEC.md
└── TODO.md
```

## Quick start

1. Copy `.env.example` to `.env` if you want to override defaults.
2. Start the stack with `make dev` or `docker compose up --build`.
3. Open the frontend at `http://localhost:3000`.
4. Open the backend health endpoint at `http://localhost:8000/api/v1/health`.

## Manual local setup

### Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e ".[dev]"
uvicorn app.main:app --reload
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

## Architecture overview

- `ProjectService` owns repo registration and project metadata.
- `TaskService` owns top-level tasks and scoped child tasks.
- `SessionService` owns manager / worker / reviewer lifecycle and process state.
- `WorktreeService` owns branch naming, worktree allocation, cleanup, and repo isolation.
- `EventService` owns the internal event envelope and event persistence.
- `ReviewService` owns diff/test/lint handoff, reviewer outcomes, and human approval state.
- `OrchestrationService` coordinates the full task -> worker -> review -> merge-ready flow.

See [SPEC.md](./SPEC.md) for the data model, event schema, API surface, and lifecycle details.

## Current scaffold status

- Implemented: directory structure, typed API stubs, SQLAlchemy models, SQL migration scaffold, prompt templates, Docker Compose, and a dashboard shell.
- Deferred: real database wiring, Redis pubsub, process supervision, git worktree creation, diff/test/lint execution, review automation, and merge approval workflows.
