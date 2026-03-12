# Poulpe Project Brief

## Purpose

Poulpe is a local-first control plane for running one program manager across several independent coding-agent projects.

The intended product is:

- one `Portfolio`
- one portfolio manager session
- many `Projects` inside that portfolio
- one dedicated Claude Code or Codex worker session per project
- manager review and answer checkpoints when projects ask questions, get blocked, error, or claim completion

The main idea is not a worker swarm. It is a program-manager model:

- each project works independently
- the manager supervises those projects
- the human can step in through the UI or API when needed

## Product Direction

This repo originally centered on a task-swarm orchestrator:

- one project
- many tasks
- many worker sessions
- task review and dependency management

That is no longer the primary direction.

The product has been deliberately pivoted toward:

- portfolio-first orchestration
- project as the core execution unit
- one worker session per project
- one manager session supervising the portfolio
- project-level review rather than task-level swarm review

Legacy task/review/orchestrator code still exists for compatibility, but it should be treated as old product surface, not the target product.

## What We Worked On

### 1. Portfolio Architecture Pivot

We added the portfolio model and portfolio-first backend path so the app matches the intended product:

- `Portfolio` records and portfolio APIs
- one manager session per portfolio
- one worker session per project
- project checkpoints for:
  - `question`
  - `blocked`
  - `error`
  - `completion`

This changed the center of the app from task orchestration to portfolio supervision.

### 2. Manager Supervision Flow

We built the manager-side supervision loop:

- projects can raise checkpoints
- the manager can answer, approve, dismiss, or request changes
- completion checkpoints include review context such as diff summaries and verification artifacts
- manager responses are routed back into project execution

We also added automation so the manager can resolve checkpoints without manual clicking when appropriate.

### 3. Real Runtime Handling

We hardened the real Codex and Claude Code runtime paths:

- fixed Claude CLI startup issues
- normalized real structured events such as `question` and `tests_run`
- removed duplicate completion events from runtime bridges
- switched real manager replies to turn-based worker restarts instead of assuming live stdin chat

This was necessary to make the software usable with real local agent CLIs rather than only simulations.

### 4. Project Creation Flow

We made project creation easier:

- projects can now auto-create a local git repo
- the UI defaults to repo auto-creation
- a project can be created from just a name
- the backend fills in a default objective when one is not provided

This supports the intended workflow where a new project can be started quickly without manually preparing a repo first.

### 5. Frontend Rework

We reworked the frontend from a noisy status dump into a more guided portfolio console:

- stronger portfolio-first layout
- guided workflow in the left rail
- cleaner project cards
- separate `Workspace`, `Inbox`, and `Activity` views
- clearer "next recommended action" messaging

The goal of the frontend work has been to make the app understandable as an operator console, not just technically functional.

## Current Software Shape

Today the repo supports this model:

1. Create a portfolio.
2. Add one or more projects.
3. Start the portfolio manager session.
4. Start one worker session per project.
5. Let projects run independently.
6. Resolve questions, blockers, and completion checkpoints through the manager.

This is the product the repo is now optimized for.

## Current Strengths

- local-first session orchestration
- isolated workspaces and git worktrees
- portfolio manager supervision model
- project-level checkpoints and review context
- real Codex and Claude Code runtime support
- UI and API both usable for day-to-day control

## Known Rough Edges

The software is usable, but it is not the finished product yet.

The main rough edges are:

- legacy task-swarm code is still in the repo
- frontend usability still needs refinement and simplification
- operator flows need more polish and consistency
- the manager policy can still be made smarter and more predictable
- production hardening and end-to-end live-runtime testing should keep improving

## How To Think About This Repo

If you are new to the codebase, the right mental model is:

- this is a portfolio manager for coding-agent projects
- not a task decomposition engine
- not a worker swarm coordinator

The most important files to understand first are:

- `README.md`
- `PORTFOLIO_PIVOT_PLAN.md`
- `backend/app/services/portfolio_service.py`
- `backend/app/services/portfolio_automation_service.py`
- `backend/app/services/project_service.py`
- `frontend/components/dashboard-shell.tsx`

## Short Summary

Poulpe is meant to help one manager supervise several independent coding-agent projects at the same time.

The main work in this repo has been turning an older task-swarm orchestrator into that portfolio-manager product, making the real runtimes usable, simplifying project creation, and reshaping the frontend so the operator can actually understand what to do.
