# SPEC

## Product scope

Build v0 of a local-first multi-agent coding orchestrator:

- one project maps to one local git repo
- one manager session supervises worker and reviewer sessions
- each worker gets its own git branch and git worktree
- workers are supervised local terminal processes
- structured events are parsed from session output
- tasks, sessions, events, artifacts, and reviews persist in Postgres
- Redis is used for queueing and pubsub
- merge readiness always requires explicit human approval

Non-goals for v0:

- cloud multi-tenancy
- enterprise auth
- autonomous merges to `main`
- non-code tasks
- cross-repo orchestration

## Repository structure

```text
backend/
  app/
    api/
      routes/
      ws.py
    core/
    adapters/
    models/
    schemas/
    services/
    prompts/
  migrations/
  tests/
frontend/
  app/
  components/
  lib/
docker-compose.yml
Makefile
README.md
SPEC.md
TODO.md
```

## Main modules

### Backend services

- `ProjectService`: register a repo, inspect its default branch, and store project metadata.
- `TaskService`: create top-level tasks, child scoped tasks, status transitions, and task hierarchy.
- `SessionService`: spawn and stop manager / worker / reviewer sessions and track lifecycle state.
- `WorktreeService`: allocate branch names and worktree paths, create git worktrees, and clean them up.
- `EventService`: accept parsed event envelopes, persist them, and fan them out to WebSocket subscribers.
- `ReviewService`: collect diff/test/lint artifacts, dispatch reviewer sessions, and record human decisions.
- `OrchestrationService`: coordinate the full end-to-end flow and enforce policy boundaries.

### Backend adapters

- `RepoInspectorAdapter`: reads git metadata from the linked repo.
- `WorktreeManagerAdapter`: shells out to git for branch/worktree operations.
- `ProcessSupervisorAdapter`: launches and supervises local terminal processes.
- `EventParserAdapter`: extracts structured event blocks from stdout/stderr.
- `RedisBusAdapter`: pubsub and queue fanout for session/event updates.
- `PostgresAdapter`: DB access and transaction boundaries.

### Frontend modules

- `app/page.tsx`: dashboard entrypoint
- `components/dashboard-shell.tsx`: layout for live state, API surface, and phased status
- `components/status-pill.tsx`: consistent state labels
- `lib/api.ts`: backend access helpers
- `lib/types.ts`: shared frontend types for live snapshots

## Database schema

### `projects`

- `id UUID PK`
- `name TEXT`
- `slug TEXT UNIQUE`
- `repo_path TEXT`
- `default_branch TEXT`
- `status TEXT`
- `metadata JSONB`
- `created_at TIMESTAMPTZ`
- `updated_at TIMESTAMPTZ`

### `tasks`

- `id UUID PK`
- `project_id UUID FK -> projects.id`
- `parent_task_id UUID FK -> tasks.id NULL`
- `title TEXT`
- `description TEXT`
- `status TEXT`
- `priority INTEGER`
- `acceptance_criteria JSONB`
- `metadata JSONB`
- `created_at TIMESTAMPTZ`
- `updated_at TIMESTAMPTZ`

### `sessions`

- `id UUID PK`
- `project_id UUID FK -> projects.id`
- `task_id UUID FK -> tasks.id NULL`
- `supervisor_session_id UUID FK -> sessions.id NULL`
- `role TEXT` (`manager`, `worker`, `reviewer`)
- `status TEXT`
- `transport TEXT` (`local_process` in v0)
- `branch_name TEXT NULL`
- `worktree_path TEXT NULL`
- `command TEXT NULL`
- `metadata JSONB`
- `started_at TIMESTAMPTZ NULL`
- `ended_at TIMESTAMPTZ NULL`
- `last_heartbeat_at TIMESTAMPTZ NULL`
- `created_at TIMESTAMPTZ`
- `updated_at TIMESTAMPTZ`

### `worktrees`

- `id UUID PK`
- `project_id UUID FK -> projects.id`
- `session_id UUID FK -> sessions.id UNIQUE NULL`
- `branch_name TEXT`
- `base_branch TEXT`
- `base_commit TEXT`
- `head_commit TEXT NULL`
- `worktree_path TEXT`
- `status TEXT`
- `metadata JSONB`
- `created_at TIMESTAMPTZ`
- `updated_at TIMESTAMPTZ`

### `artifacts`

- `id UUID PK`
- `project_id UUID FK -> projects.id`
- `task_id UUID FK -> tasks.id NULL`
- `session_id UUID FK -> sessions.id NULL`
- `kind TEXT`
- `uri TEXT`
- `content_type TEXT`
- `size_bytes BIGINT NULL`
- `sha256 TEXT NULL`
- `metadata JSONB`
- `created_at TIMESTAMPTZ`

### `reviews`

- `id UUID PK`
- `project_id UUID FK -> projects.id`
- `task_id UUID FK -> tasks.id`
- `requester_session_id UUID FK -> sessions.id NULL`
- `reviewer_session_id UUID FK -> sessions.id NULL`
- `diff_artifact_id UUID FK -> artifacts.id NULL`
- `status TEXT`
- `summary TEXT NULL`
- `decision_note TEXT NULL`
- `lint_status TEXT NULL`
- `test_status TEXT NULL`
- `human_approved_by TEXT NULL`
- `human_approved_at TIMESTAMPTZ NULL`
- `metadata JSONB`
- `created_at TIMESTAMPTZ`
- `updated_at TIMESTAMPTZ`

### `events`

- `id UUID PK`
- `project_id UUID FK -> projects.id NULL`
- `task_id UUID FK -> tasks.id NULL`
- `session_id UUID FK -> sessions.id NULL`
- `sequence BIGINT`
- `category TEXT`
- `event_type TEXT`
- `level TEXT`
- `source TEXT`
- `correlation_id UUID NULL`
- `causation_id UUID NULL`
- `payload JSONB`
- `raw_output TEXT NULL`
- `occurred_at TIMESTAMPTZ`
- `created_at TIMESTAMPTZ`

## Internal event schema

All machine events use a versioned envelope:

```json
{
  "id": "uuid",
  "version": "v1",
  "sequence": 42,
  "category": "session",
  "event_type": "session.output",
  "level": "info",
  "source": {
    "kind": "session",
    "role": "worker",
    "id": "uuid"
  },
  "project_id": "uuid",
  "task_id": "uuid",
  "session_id": "uuid",
  "correlation_id": "uuid",
  "causation_id": "uuid",
  "occurred_at": "2026-03-08T00:00:00Z",
  "payload": {},
  "raw_output": "optional raw text chunk"
}
```

Structured event blocks embedded in session output use explicit delimiters:

```text
<<<ORCHESTRATOR_EVENT>>>
{"event_type":"task.progress","level":"info","summary":"Implemented API stub","payload":{"files":["backend/app/api/router.py"]}}
<<<END_ORCHESTRATOR_EVENT>>>
```

Recommended v0 event families:

- `project.created`
- `task.created`
- `task.assigned`
- `task.status_changed`
- `session.spawn_requested`
- `session.started`
- `session.output`
- `session.heartbeat`
- `session.ended`
- `worktree.provision_requested`
- `worktree.ready`
- `artifact.created`
- `review.requested`
- `review.completed`
- `review.human_approved`
- `merge.readiness_changed`

## API surface

### REST

- `GET /api/v1/health`
- `GET /api/v1/projects`
- `POST /api/v1/projects`
- `GET /api/v1/projects/{project_id}`
- `GET /api/v1/tasks`
- `POST /api/v1/tasks`
- `GET /api/v1/tasks/{task_id}`
- `GET /api/v1/sessions`
- `POST /api/v1/sessions`
- `GET /api/v1/sessions/{session_id}`
- `POST /api/v1/sessions/{session_id}/stop`
- `GET /api/v1/reviews`
- `POST /api/v1/reviews`
- `GET /api/v1/reviews/{review_id}`
- `POST /api/v1/reviews/{review_id}/decision`
- `GET /api/v1/events`

### WebSocket

- `WS /ws/projects/{project_id}/events`
- `WS /ws/sessions/{session_id}/output`

## Orchestration flow

1. Create a `project` that points at a local git repo.
2. Create a top-level `task`.
3. Spawn a manager `session` for that task.
4. Manager creates scoped child tasks and requests worker sessions.
5. `WorktreeService` allocates `orchestrator/<role>/<task>` branch names and worktree paths.
6. Worker sessions stream output; `EventParserAdapter` extracts structured event blocks.
7. `EventService` persists and broadcasts envelopes to the dashboard.
8. When a worker is done, collect diff + lint + test artifacts.
9. Spawn or notify a reviewer session.
10. Record reviewer outcome.
11. Require explicit human approval before the review is promoted to `merge_ready`.

## Extensibility notes

- The event envelope is intentionally generic so later background workers, audit logs, and analytics can subscribe without backend rewrites.
- Sessions and worktrees are modeled separately so cleanup and recovery can be reasoned about independently.
- Human approval is stored as review state, not inferred from agent output, to keep policy enforcement deterministic.
