# Portfolio Pivot Plan

## Target Product

The product should pivot from:

- one project -> many tasks -> many worker sessions -> manager/reviewer swarm

to:

- one portfolio/program manager session
- many projects in that portfolio
- one independent coding-agent session per project
- the program manager supervises those project sessions
- project sessions ask the manager for decisions, clarification, and review

The core unit becomes `project`, not `task`.

## Desired Runtime Model

### Sessions

- `portfolio_manager` session
  - one long-lived Claude Code or Codex session
  - sees the portfolio, project roster, statuses, questions, and completion claims
  - sends instructions back to project sessions
- `project_worker` session
  - exactly one active coding-agent session per project
  - owns the project workspace/worktree
  - works independently from other projects
  - emits `question`, `blocked`, `progress`, `complete`, `error`

### Review

- project session says it is done
- manager session reviews the project session's diff, transcript, and result
- manager either:
  - approves completion
  - asks follow-up questions
  - sends revision instructions back to the same project session

No automatic worker swarm should be the default path.

## Current Codebase: Keep / Replace / Delete

### Keep

These are good primitives and should stay:

- `backend/app/adapters/process_supervisor.py`
  - solid local process lifecycle primitive
- `backend/app/adapters/codex_local.py`
- `backend/app/adapters/claude_code_local.py`
  - keep the runtime bridge idea, but point it at project-level sessions
- `backend/app/runtime/codex_exec_worker.py`
- `backend/app/runtime/claude_code_exec_worker.py`
  - keep as runtime adapters
- `backend/app/services/session_supervisor.py`
  - keep the transcript/event persistence machinery
  - narrow its responsibility to starting/stopping sessions and recording output
- `backend/app/services/runtime_service.py`
  - keep provider selection and runtime probing
- `backend/app/services/worktree_manager.py`
- `backend/app/services/workspace_service.py`
  - keep workspace/worktree isolation
- `backend/app/adapters/repo_inspector.py`
- `backend/app/services/command_runner.py`
- `backend/app/services/event_service.py`
- `backend/app/api/routes/events.py`
- `backend/app/api/ws.py`
  - event streaming is still useful
- models:
  - `Session`
  - `Workspace`
  - `Event`
  - `TranscriptChunk`
  - `ParsedSessionEvent`

### Replace

These should be rewritten around portfolio/project supervision:

- `backend/app/models/project.py`
  - keep the table name if desired, but change semantics
  - add portfolio linkage and project objective fields
- `backend/app/services/project_service.py`
  - should become project CRUD inside a portfolio, not repo-only registration
- `backend/app/services/session_service.py`
  - should create:
    - portfolio manager sessions
    - one project execution session per project
  - should stop thinking in terms of task-attached worker sessions
- `backend/app/services/orchestration_service.py`
  - replace with `portfolio_manager_service.py`
  - responsibilities:
    - monitor all project sessions
    - forward questions to the manager
    - package review context for the manager
    - send manager replies back to project sessions
    - mark project completion
- `backend/app/services/review_service.py`
  - replace task review packets with project review checkpoints
- `backend/app/services/task_packet_service.py`
  - replace with packet builders for:
    - portfolio manager context
    - project execution context
    - project review context
- `frontend/components/dashboard-shell.tsx`
  - replace task pipeline UI with portfolio board + project threads
- `frontend/lib/types.ts`
- `frontend/lib/api.ts`
  - remove task-first assumptions

### Delete Or Deprecate

These are specific to the swarm/task-decomposition product and should be removed after migration:

- `backend/app/models/task.py`
- `backend/app/models/review.py`
- `backend/app/services/task_service.py`
- task assignment APIs
- task dependency logic
- task conflict logic
- auto-created worker swarm logic
- manager planning prompt:
  - `backend/app/prompts/manager.md`
- manager review packet built around task subtasks
- task-centric dashboard sections

Concretely, the following areas are the main deletion candidates:

- `backend/app/api/routes/tasks.py`
- large parts of `backend/app/services/orchestration_service.py`
- task-specific branches inside `backend/app/services/review_service.py`

## New Backend Shape

### Models

#### `Portfolio`

New model.

Suggested fields:

- `id`
- `name`
- `goal`
- `status`
- `manager_session_id`
- `metadata`
- `created_at`
- `updated_at`

#### `Project`

Refactor existing model.

Suggested fields:

- `id`
- `portfolio_id`
- `name`
- `slug`
- `repo_path`
- `default_branch`
- `objective`
- `status`
- `worker_session_id`
- `workspace_id`
- `completion_summary`
- `metadata`
- `created_at`
- `updated_at`

#### `Session`

Keep, but add session-type semantics in metadata or a new enum:

- `portfolio_manager`
- `project_worker`

The existing `manager/worker/reviewer` enum can be:

- extended
- or simplified and interpreted differently

I would prefer a new enum rather than reusing the current one ambiguously.

#### `ManagerInstruction`

New model.

Tracks manager -> project messages:

- `id`
- `portfolio_id`
- `project_id`
- `manager_session_id`
- `project_session_id`
- `kind`
- `content`
- `status`
- `created_at`

#### `ProjectCheckpoint`

New model for review/completion checkpoints:

- `id`
- `project_id`
- `requested_by_session_id`
- `reviewed_by_session_id`
- `status`
- `summary`
- `metadata`
- `created_at`

## New Service Layer

### `PortfolioService`

Create/list/get portfolios.

### `ProjectService`

Refocus around projects inside a portfolio:

- create project
- attach repo
- start project execution
- pause/stop/retry project execution
- mark project complete

### `PortfolioManagerService`

This replaces the current task orchestrator.

Responsibilities:

- start the portfolio manager session
- build the manager context packet
- watch project session events
- detect:
  - project questions
  - project blocked states
  - project completion claims
- package those into manager-review messages
- send manager responses back to the right project session
- update project state

### `SessionService`

Narrow responsibilities:

- create/start/stop/send to sessions
- no task decomposition logic

### `ReviewService`

Shrink to project review checkpoints only.

## New API Surface

### New routes

- `POST /api/v1/portfolios`
- `GET /api/v1/portfolios`
- `GET /api/v1/portfolios/{portfolio_id}`
- `POST /api/v1/portfolios/{portfolio_id}/manager/start`
- `POST /api/v1/portfolios/{portfolio_id}/tick`

- `POST /api/v1/projects`
- `GET /api/v1/projects?portfolio_id=...`
- `GET /api/v1/projects/{project_id}`
- `POST /api/v1/projects/{project_id}/start`
- `POST /api/v1/projects/{project_id}/stop`
- `POST /api/v1/projects/{project_id}/retry`
- `GET /api/v1/projects/{project_id}/workspace`
- `GET /api/v1/projects/{project_id}/transcript`
- `GET /api/v1/projects/{project_id}/events`

- `POST /api/v1/projects/{project_id}/manager-instructions`
- `GET /api/v1/projects/{project_id}/checkpoints`

### Routes to retire

- `/tasks`
- task assignment endpoints
- task block/complete endpoints
- task-first review endpoints

## Frontend Pivot

### Replace Current Dashboard With

#### Portfolio Board

Left column:

- portfolio selector
- manager runtime status
- project list with:
  - status
  - provider
  - last activity
  - blocked/question badge

Center:

- selected project thread
- transcript snippets
- latest progress
- diff/checkpoint summary

Right column:

- manager inbox
  - project questions
  - blocked projects
  - completion reviews

### Remove

- task plan list
- subtask pipeline
- review queue centered on tasks

## Migration Strategy

### Phase 1: Stop Digging

- freeze new work on task swarm features
- stop extending `orchestration_service.py`
- stop adding task-specific UI

### Phase 2: Introduce Portfolio + Project Session Model

- add `Portfolio` model
- add `portfolio_id` to `Project`
- add `worker_session_id` to `Project`
- add a simple `PortfolioManagerService`
- keep old task tables alive temporarily

Goal:

- create a portfolio
- create projects under it
- start one session per project

### Phase 3: Manager Supervision Loop

- build manager packet for the portfolio session
- detect project `question`, `blocked`, `complete`
- route them into manager inbox/checkpoints
- allow manager responses to be sent back to projects

Goal:

- one manager session can supervise multiple independent project sessions

### Phase 4: UI Rewrite

- replace task pipeline with portfolio/project board
- expose manager inbox and project threads

### Phase 5: Delete Task Swarm Layer

- remove task routes
- remove task orchestrator logic
- remove task-based review flow
- remove manager planning prompts

## Practical Recommendation

Do not try to evolve the current swarm orchestrator into this shape incrementally inside the same service logic.

The cleanest path is:

1. keep the runtime/session/workspace primitives
2. build a new portfolio-manager orchestration path beside the old task path
3. switch the frontend to the new path
4. delete the old task swarm once the new flow works

That is lower risk than continuing to mutate the current task-centric state machine.
