# Implementation Audit

This audit reflects the current local repository state, including uncommitted frontend changes, plus a fresh verification run of `pytest -q` in `backend/` (`23 passed`) and `npm run build` in `frontend/` (passed).

## Current architecture

- FastAPI backend with SQLAlchemy models for projects, tasks, sessions, workspaces, events, transcript chunks, parsed session events, artifacts, and reviews.
- Local-first git integration: worker sessions get dedicated branches and linked git worktrees under `.orchestrator/workspaces/...`.
- PTY/subprocess supervision is real. Session runtime captures stdout/stderr, heartbeats, exit status, transcript chunks, and parsed structured event blocks.
- Structured events are parsed from transcript output and normalized into persisted event rows plus websocket fan-out.
- Deterministic orchestration is implemented as an explicit service tick, not an autonomous planner. It updates task state, detects conflicts, requests summaries, and auto-queues reviews on worker completion.
- Review packaging is real: diff, changed files, optional lint/tests, artifacts, and approval metadata are persisted.
- Next.js frontend is wired to the backend and now exposes a simplified mission-first flow, with raw orchestration controls under `Advanced`.

## Real functionality

- Project, task, session, workspace, event, and review APIs are implemented and exercised by tests.
- Git worktree provisioning is real and local. Worker session creation creates a workspace record and provisions a linked worktree immediately.
- Workspace inspection, diff collection, changed-file detection, and workspace-scoped command execution are real.
- Command execution includes cwd validation, destructive-command blocking by default, timeout handling, and structured results.
- Session supervision is real for local processes: start, send, interrupt, stop, heartbeat tracking, exit tracking, transcript persistence, structured event persistence, and websocket fan-out all work.
- The structured event protocol is real: `[[EVENT]] ... [[/EVENT]]` blocks are parsed, validated, stored separately from transcript chunks, and malformed blocks are preserved for debugging.
- Orchestrator conflict detection and state transitions are real: dependency blocking, idle summary requests, scope overlap checks, changed-file overlap checks, and automatic review queuing after worker completion.
- Review objects are real: diff/check artifacts are stored, reviewer approval and human merge-ready approval are persisted, and task state is updated accordingly.
- Demo seeding is real: startup can create a real local git repo, demo project, demo tasks, demo sessions, demo workspaces, and a sample review.
- Frontend/backend wiring is real for the current console flow. The dashboard fetches live API data and listens to project events over websocket.

## Mocked/stubbed functionality

- The default agent runtime is still simulated. `codex_local` launches `python -m app.dev.codex_session_simulator` unless simulation is explicitly disabled and a real command is provided.
- The simulator emits believable structured events, but it does not perform real coding work or modify the repo unless a human or test manually edits files in the workspace.
- Reviewer sessions are only packaged, not actually run as part of the review pipeline. Reviewer decisions are still made by direct API/UI actions.
- Manager sessions exist as records and can be started, but the real orchestration logic lives in `OrchestratorService`, not in a running manager agent.
- Path ownership / path locks exist only as metadata placeholders. Conflict detection exists, but there is no hard lock enforcement layer yet.

## Broken/incomplete flows

- The new default frontend flow is not a true end-to-end task completion flow yet. Clicking `Start task` creates the internal objects and starts a simulated worker, but that worker does not automatically edit files or reach completion from the mission text alone.
- The simple UI does not expose a direct “send instruction” control, so a simulated worker started from the main flow can remain running indefinitely without ever reaching review.
- Automatic review analysis is incomplete. Reviews are created and packaged, but no reviewer session is automatically launched to inspect the packet and produce notes.
- Automatic checks during review are incomplete. The review pipeline can run lint/tests when commands are supplied, but orchestrator-queued reviews do not currently attach default lint/test commands.
- Merge-ready is a persisted state only. There is no merge queue, branch handoff, or merge-to-main execution after approval.
- Orchestration is not running as a background daemon. It advances when the API tick endpoint is called, when tests call it, or when the open frontend triggers periodic ticks.

## Core happy path status

Status: partially working.

There is a real backend happy path for the control plane:

1. Create or seed a project that points at a local repo.
2. Create a task.
3. Create a worker session.
4. Provision a real git worktree.
5. Start a supervised local process.
6. Persist transcript output and structured events.
7. Trigger an orchestrator tick.
8. Queue a review after the worker reaches `completed`.
9. Approve or reject the review.
10. Mark the review merge-ready after explicit human approval.

What is missing from that happy path is real task execution. Today, repo changes in the working flow come from seeded demo data, manual file edits, or tests that edit the workspace directly. The default worker does not yet take a mission, modify code, run real checks, and reach review on its own.

## Top 5 blockers

1. No real worker execution path. The default worker is a simulator, so the product cannot yet deliver actual code changes from a user-entered task.
2. The default simplified UI does not naturally reach completion. It starts a simulated worker but does not provide a normal user path for that worker to finish and generate a review.
3. Reviewer automation is not integrated. Review packaging is real, but reviewer analysis is still manual state mutation through API/UI actions.
4. Orchestrator progress depends on explicit ticks rather than an always-on background loop, so automation pauses when nothing is calling the tick endpoint.
5. Post-approval merge flow does not exist. `merge_ready` is stored, but nothing operational happens after approval.

## Recommended next step

Implement one real worker execution path end-to-end:

- take the mission and optional scope from the current frontend flow
- turn that into a real worker launch/input contract
- have the worker operate inside its assigned git worktree
- emit structured events during real work
- reach `complete` with actual workspace changes

That single task unlocks the rest of the product, because it turns the current control plane from a simulation harness into a usable v0.
