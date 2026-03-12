# Dev Runtime Modes

The app can run worker sessions in four practical execution modes.

## Modes

### Real Codex

This is the first real backend.

Requirements:
- `codex` must be on `PATH`
- `codex login status` must succeed

Behavior:
- the backend launches a real Codex CLI process in the worker worktree
- the task packet is generated server-side
- transcript output is real process output
- structured events come from the Codex bridge plus any valid event blocks Codex emits

### Simulated dev runtime

This is still the fallback and test runtime.

Behavior:
- launches `python -m app.dev.codex_session_simulator`
- supports live follow-up messages
- emits predictable structured events for local development and tests

### Real Claude Code

Not implemented yet.

Behavior today:
- capability checks can report it as unavailable or disconnected
- there is no real execution adapter yet

### No runtime connected

This is the explicit disconnected state.

Behavior:
- session objects can still be created
- start requests fail with a clear runtime error
- the UI shows the worker as disconnected instead of pretending work is happening

## Resolution rules

- `Auto` prefers a real Codex runtime if it is available.
- If no real runtime is available, `Auto` falls back to the simulated dev runtime.
- Explicit `Codex` requires a ready Codex CLI and does not silently fall back.
- Explicit `Claude Code` is currently expected to remain unavailable.

## Exact Codex setup

Use these commands locally:

```bash
codex --version
codex login
codex login status
```

Optional env override:

```bash
export CODEX_RUNTIME_COMMAND_TEMPLATE="codex"
```

The app treats Codex as ready only when:
- the configured command exists
- `codex login status` exits successfully

## What is real vs simulated right now

Real:
- worker execution through Codex
- git worktree isolation
- transcript capture
- structured event extraction and persistence
- completion to review handoff

Simulated:
- reviewer runtime
- manager runtime
- any worker session started in explicit simulation mode or `Auto` fallback mode
