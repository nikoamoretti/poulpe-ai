# TODO

## Phase 0: Scaffold

- [x] Create monorepo layout for backend, frontend, docs, and local dev
- [x] Define database schema and event envelope
- [x] Stub REST and WebSocket surfaces
- [x] Add placeholder prompt templates and dashboard shell

## Phase 1: Persistence and config

- [ ] Wire SQLAlchemy to Postgres and add session management
- [ ] Apply the initial migration through Alembic or a lightweight migration runner
- [ ] Persist projects, tasks, sessions, events, artifacts, reviews, and worktrees
- [ ] Add repository path validation and default branch detection

## Phase 2: Repo and worktree lifecycle

- [ ] Implement repo inspection adapter
- [ ] Implement deterministic branch naming
- [ ] Create and clean up git worktrees per worker session
- [ ] Track worktree dirty state and head commit updates

## Phase 3: Session supervision and event ingestion

- [ ] Launch manager / worker / reviewer processes as supervised local terminal sessions
- [ ] Stream stdout/stderr into event ingestion
- [ ] Parse `ORCHESTRATOR_EVENT` blocks from session output
- [ ] Add heartbeats, exit status tracking, and restart/error handling
- [ ] Broadcast live updates over Redis + WebSocket

## Phase 4: Review pipeline

- [ ] Collect git diff artifacts from worker branches
- [ ] Run lint and tests for completed worker tasks
- [ ] Spawn reviewer sessions with diff/test/lint context
- [ ] Persist reviewer findings and summary decisions
- [ ] Add human approval endpoint and merge-ready state transition

## Phase 5: Frontend dashboard

- [ ] Replace static dashboard cards with live project/task/session queries
- [ ] Add task graph and live session panes
- [ ] Add event timeline and filterable event feed
- [ ] Add review queue and human approval actions

## Phase 6: Hardening

- [ ] Add end-to-end tests against Docker Compose
- [ ] Add recovery paths for crashed worker sessions
- [ ] Add structured logging and operator diagnostics
- [ ] Add basic auth or local operator guardrails if v1 needs them
