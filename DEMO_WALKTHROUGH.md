# Demo Walkthrough

This walkthrough assumes a fresh clone and the default Docker Compose flow.

## 1. Start the stack

```bash
docker compose up --build
```

Wait for:
- backend healthcheck to pass
- frontend dev server to start on port `3000`
- demo seed log lines from the backend

## 2. Open the operator console

Go to `http://localhost:3000`.

On a fresh database you should see:
- one demo project
- three tasks
- five sessions
- one review
- a live project event feed

## 3. Inspect the seeded demo state

The seeded data is meant to show three common states at once:
- `in_progress`: active worker task assigned to a worker session
- `blocked`: dependency-gated task waiting on the active task
- `review`: task with a packaged review containing diff + checks

The review panel should already show:
- diff summary
- changed file list
- lint result
- test result
- approval controls

## 4. Start a worker session

In the Sessions panel:
1. Find a worker session in `pending`
2. Click `Start`
3. Watch the Live event feed update

What is real:
- the session is launched as a supervised PTY subprocess
- transcript chunks are persisted
- structured events are parsed from session output

What is simulated by default:
- the actual Codex process payload, via the local dev simulator

## 5. Create a new task

In the Actions panel:
1. Enter a task title and description
2. Click `Create task`

The new task appears immediately in the Tasks panel and emits project events.

## 6. Create a session for that task

Still in the Actions panel:
1. Choose `worker` as the role
2. Pick the task you just created
3. Optionally enter a command override
4. Click `Create session`

For worker sessions, the backend also provisions a git branch and linked worktree.

## 7. Assign the task

In the Assign task form:
1. Select the task in the Tasks list
2. Choose the worker session
3. Add allowed paths such as:

```text
backend/app/services
frontend/components
```

4. Click `Assign task`

The orchestrator records ownership and scope in task/session metadata.

## 8. Create a review package

In the Create review form:
1. Pick a task
2. Pick the worker session that owns it
3. Optionally pick a reviewer session
4. Optionally keep the default commands:

```text
git diff --stat
git status --short
```

5. Click `Create review`

The review panel will refresh with:
- diff summary
- changed files
- lint artifact
- test artifact
- approval state

## 9. Approve or reject the review

In the Review detail panel:
- use `Approve`, `Needs changes`, or `Reject`
- enter a human approver and click `Mark merge-ready` after reviewer approval

`merge-ready` is gated. Human approval is required before that state can be set.

## 10. Inspect the backend directly

Useful endpoints:
- `http://localhost:8000/docs`
- `http://localhost:8000/api/v1/projects`
- `http://localhost:8000/api/v1/tasks?project_id=<project-id>`
- `http://localhost:8000/api/v1/sessions?project_id=<project-id>`
- `http://localhost:8000/api/v1/reviews?project_id=<project-id>`
- `ws://localhost:8000/ws/projects/<project-id>/events`

## Resetting the demo

To wipe containers, database volumes, and local generated repos/worktrees:

```bash
make reset
```
