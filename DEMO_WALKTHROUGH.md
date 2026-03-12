# Demo Walkthrough

This is the fastest way to show what v0 does to a new user.

## 1. Start the stack

```bash
docker compose up --build
```

Wait for:
- backend healthcheck to pass
- frontend to start on port `3000`
- demo seed logs from the backend

## 2. Open the app

Go to `http://localhost:3000`.

On a fresh database, the app opens with:
- one demo repo workspace
- three seeded tasks
- one approval-ready task
- readable activity updates already visible
- sample tasks for frontend, backend, and docs work

## 3. Explain the product in 20 seconds

Use the main screen only:
1. Enter one task.
2. Optionally choose scope.
3. Click `Start task`.
4. Watch the activity timeline.
5. Open `Review` and approve or request changes.

That is the whole v0 story.

## 4. Point out the seeded examples

The demo workspace already shows three realistic examples:
- Backend: `Tighten structured event handling`
- Frontend: `Make the review screen easier to scan`
- Docs: `Explain the approval flow clearly`

These map to the sample-task buttons on the main screen.

## 5. Best live demo flow

Use this order:

1. Show the `How it works` strip at the top of the screen.
2. Click the `Frontend polish` sample task.
3. Point out the Scope and Execution mode options.
4. Click `Start task`.
5. Switch to `Active` and watch the new task appear with readable activity.
6. Switch to `Review` and open the seeded approval item.
7. Show changed files, checks, reviewer notes, and click `Approve` or `Request changes`.

This demonstrates the full v0 flow without opening Advanced.

## 6. What the demo proves

The default flow is real in these ways:
- tasks are persisted
- agents are persisted
- worker sessions use isolated worktrees
- activity comes from real stored events
- approvals package diff and check data

What may still be simulated, depending on runtime selection:
- the worker runtime itself
- reviewer and manager agents

## 7. Inspect deeper only if asked

If someone wants the internal details, open `Advanced` and show:
- the active repo workspace
- internal task and agent records
- raw activity feed
- approval metadata

## 8. Useful sample tasks

Frontend:
- Make the review screen easier to scan.

Backend:
- Tighten structured event handling.

Docs:
- Explain the approval flow clearly.
