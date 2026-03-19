# Poulpe Portfolio Console

**Live frontend:** https://frontend-yard-logix.vercel.app

> Auth (Clerk) and billing (Stripe) are integrated in code but require environment variables to be wired in before they activate. See [DEPLOYMENT.md](./DEPLOYMENT.md) for the step-by-step setup guide.

Poulpe is a local-first control plane for one program manager and several independent coding-agent projects.

The primary product model is:

- one `Portfolio`
- one portfolio manager session
- many `Projects` inside that portfolio
- one dedicated Claude Code or Codex worker session per project
- manager checkpoints for project questions, blockers, errors, and completion claims

The manager can supervise manually through the UI/API, or automatically through the portfolio automation loop.

## Current product direction

The portfolio path is now the default experience:

- create a portfolio
- add projects that point at local repos
- start one worker session per project
- let projects run independently in their own workspaces
- route project checkpoints to the portfolio manager
- approve or request changes at the project level

The older task-swarm orchestration APIs still exist for compatibility, but they are legacy surfaces now:

- `/api/v1/tasks`
- `/api/v1/reviews`
- `/api/v1/orchestrator`

## What is working

- FastAPI backend with session, project, portfolio, workspace, and checkpoint persistence
- Next.js portfolio board UI
- local git worktree management for project workers
- supervised local Codex and Claude Code process launching
- structured event parsing, transcript persistence, and websocket fan-out
- manager inbox checkpoints for `question`, `blocked`, `error`, and `completion`
- completion review artifacts with diff and verification context
- autonomous portfolio manager turns that can resolve checkpoints
- turn-based manager replies for real runtimes that cannot accept live follow-up stdin

## Quick start

1. Clone the repo and enter it:

```bash
git clone https://github.com/nikoamoretti/poulpe-ai.git
cd poulpe-ai
```

2. Optionally copy the env file:

```bash
cp .env.example .env
```

3. Start the stack:

```bash
docker compose up --build
```

4. Open the app:

- Frontend: `http://localhost:3000`
- Backend API: `http://localhost:8000`
- OpenAPI docs: `http://localhost:8000/docs`
- Health: `http://localhost:8000/api/v1/health`

## Portfolio workflow

1. Create a portfolio with a portfolio-level goal.
2. Add one or more projects, each with its own repo path and objective.
3. Start the portfolio manager session.
4. Start one worker session per project.
5. Let workers emit progress, questions, blockers, tests, and completion claims.
6. Review or automate the resulting manager checkpoints.

Useful portfolio endpoints:

- `POST /api/v1/portfolios`
- `GET /api/v1/portfolios`
- `POST /api/v1/portfolios/{portfolio_id}/manager/start`
- `GET /api/v1/portfolios/{portfolio_id}/inbox`
- `POST /api/v1/portfolios/{portfolio_id}/inbox/{checkpoint_id}/respond`
- `POST /api/v1/portfolios/{portfolio_id}/automation/tick`
- `POST /api/v1/projects`
- `POST /api/v1/projects/{project_id}/start`
- `POST /api/v1/projects/{project_id}/manager-instructions`

## Runtime model

- Simulated runtime is available for development and tests.
- Real Codex and Claude Code sessions are supported as one-shot turns.
- Because real CLI runtimes do not support live follow-up stdin cleanly, manager responses to real project workers are routed as new worker turns in the same project workspace.
- Portfolio manager automation follows the same turn model.

## Local development

### Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e ".[dev]"
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Frontend

```bash
cd frontend
npm install
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 npm run dev
```

## Useful environment variables

- `LOG_LEVEL=INFO`
- `LOG_REQUESTS=true`
- `DATABASE_URL=...`
- `REDIS_URL=...`
- `PORTFOLIO_AUTOMATION_ENABLED=true`
- `PORTFOLIO_AUTOMATION_INTERVAL_SECONDS=5`
- `CODEX_RUNTIME_COMMAND_TEMPLATE=codex`
- `CLAUDE_CODE_RUNTIME_COMMAND_TEMPLATE=claude`

## Legacy note

The repo still contains the original task/review/orchestrator implementation. It is intentionally left in place so existing data, tests, and integrations do not break during the portfolio migration. It should be treated as compatibility code, not the recommended product path.
