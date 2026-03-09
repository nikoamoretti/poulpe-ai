# Next Steps for v1

These are the highest-value follow-ups after the current developer v0.

## 1. Real Codex process integration

Replace the dev simulator with a real Codex CLI/runtime path and add one true end-to-end integration test against the real binary.

Why it matters:
- the supervision layer is real, but the agent payload is still simulated by default

## 2. Path locks and conflict ownership

Persist and enforce real path locks across active worker sessions rather than only recording scope metadata and overlap detection.

Why it matters:
- v0 detects conflicts, but it does not yet provide strong ownership enforcement

## 3. Rich review UX

Add transcript drill-down, structured event timelines, and a full diff viewer in the frontend.

Why it matters:
- the review panel is functional, but it currently favors density over deep inspection

## 4. Merge queue and branch integration

Implement the post-approval flow from merge-ready review to an actual merge queue or branch handoff.

Why it matters:
- the current system intentionally stops before merging to `main`

## 5. Authentication and multi-user operator support

Add authn/authz, human actor identity, and audit trails for review and merge-ready actions.

Why it matters:
- human approval is persisted, but the system is still effectively single-operator dev tooling

## Additional worthwhile work

- add reconnect/resume semantics for long-running sessions
- move from local in-memory websocket broker to a cross-process event fan-out path
- add frontend tests around action forms and live-refresh behavior
- add a project creation flow in the UI
- add repo registration and validation helpers from the dashboard
