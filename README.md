# Local Agent Orchestrator v0

Local Agent Orchestrator is a local-first control plane for supervised coding-agent work. A project points at a local git repo, worker sessions get their own git branches and linked worktrees, structured events are extracted from agent output, and completed work flows into a review pipeline before anything becomes merge-ready.

This repository is now usable as a developer v0:
- FastAPI backend with Postgres + Redis
- Next.js operator console
- local git worktree management
- PTY/subprocess-based session supervision
- structured event parsing and websocket fan-out
- deterministic orchestration loop
- review packaging with human approval gate
- automatic demo seeding for local development

## Quick start

1. Clone the repo and enter it:

```bash
git clone https://github.com/nikoamoretti/local-agent-orchestrator.git
cd local-agent-orchestrator
```

2. Optionally copy the env file if you want to override defaults:

```bash
cp .env.example .env
```

3. Start the full stack:

```bash
docker compose up --build
```

4. Open the app:
- Frontend: `http://localhost:3000`
- Backend API: `http://localhost:8000`
- OpenAPI docs: `http://localhost:8000/docs`
- Health: `http://localhost:8000/api/v1/health`

On a fresh database, the backend automatically seeds a demo repo plus sample project/task/session/review data.

## What gets seeded

On first boot, the backend creates:
- a real local git repo at `.orchestrator/repos/demo-local-agent-repo`
- one demo project attached to that repo
- three demo tasks
- five demo sessions: manager, reviewer, and three workers
- one pending review package with diff, lint, and test artifacts

The demo worktrees live under:

```text
.orchestrator/workspaces/<project-slug>/<task-id>/<session-id>
```

## Make targets

```bash
make up             # compose up -d --build
make dev            # compose up --build in the foreground
make down           # stop the stack
make reset          # remove volumes and local demo state
make logs           # tail all logs
make logs-backend   # tail backend logs
make logs-frontend  # tail frontend logs
make seed           # rerun demo seeding inside the backend container
make test           # run backend tests
make test-frontend  # run a production Next.js build
make bootstrap      # install backend + frontend deps for local non-Docker dev
make api            # run backend locally from backend/.venv
make web            # run frontend locally
```

## Local non-Docker setup

### Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e ".[dev]"
export DATABASE_URL=postgresql+psycopg://orchestrator:orchestrator@localhost:5432/orchestrator
export REDIS_URL=redis://localhost:6379/0
alembic upgrade head
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Frontend

```bash
cd frontend
npm install
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 npm run dev
```

If you want local auto-seeding outside Docker, set:

```bash
export SEED_DEMO_DATA=true
export SEED_DEMO_DATA_IF_EMPTY=true
```

## End-to-end architecture

The runtime path is:

1. A `project` points at a local git repository.
2. A `task` is assigned to a worker `session`.
3. A worker session gets a dedicated git branch + linked git worktree.
4. The session runtime launches a supervised PTY subprocess.
5. Raw transcript chunks are stored separately from parsed structured events.
6. Structured events fan out to websocket subscribers and feed the deterministic orchestrator loop.
7. When worker output is ready, a `review` packages the diff, changed files, and optional lint/test results.
8. Human approval is required before a review can be marked merge-ready.

## Frontend operator console

The dashboard at `http://localhost:3000` includes:
- Projects panel
- Tasks panel
- Sessions panel
- Live project event feed
- Review detail panel
- Basic operator actions for creating tasks, creating sessions, assigning tasks, starting/stopping sessions, and approving/rejecting reviews

## Logging and developer ergonomics

The backend now logs:
- app startup and shutdown
- HTTP requests with status and latency
- demo seeding decisions
- session creation and start requests
- workspace provisioning and command execution
- review creation and decisions

Useful environment variables:
- `LOG_LEVEL=INFO`
- `LOG_REQUESTS=true`
- `SEED_DEMO_DATA=true`
- `SEED_DEMO_DATA_IF_EMPTY=true`
- `NEXT_PUBLIC_API_BASE_URL=http://localhost:8000`

## Real Codex integration

The current runtime is intentionally pluggable. The simulated path is useful for local development, and the real Codex integration points are documented in [CODEX_INTEGRATION.md](./CODEX_INTEGRATION.md).

Short version:
- `backend/app/adapters/codex_local.py` is the adapter swap point
- `backend/app/adapters/process_supervisor.py` already handles PTY lifecycle
- `backend/app/services/session_supervisor.py` already persists transcript chunks and structured events
- prompt files in `backend/app/prompts/` already enforce the event protocol

## More docs

- [DEMO_WALKTHROUGH.md](./DEMO_WALKTHROUGH.md): local operator flow using the seeded demo
- [CODEX_INTEGRATION.md](./CODEX_INTEGRATION.md): exactly where to plug in a real Codex process
- [NEXT_STEPS.md](./NEXT_STEPS.md): highest-value v1 improvements
- [SPEC.md](./SPEC.md): original system spec
