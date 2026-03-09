# Real Codex Integration Notes

The current v0 runtime is built so a real Codex CLI or terminal process can replace the dev simulator without changing the rest of the system.

## What is already real

These pieces are not mocked:
- PTY/subprocess lifecycle supervision in `backend/app/adapters/process_supervisor.py`
- session lifecycle persistence in `backend/app/services/session_supervisor.py`
- transcript chunk storage
- structured event parsing and persistence
- websocket fan-out for events
- git worktree provisioning
- review packaging and orchestrator state transitions

## What is still simulated

By default, `codex_local` launches:

```text
python -m app.dev.codex_session_simulator
```

instead of a real Codex binary.

That behavior lives in:
- `backend/app/adapters/codex_local.py`

## Primary swap point

The main integration seam is:
- `CodexLocalAdapter._build_command()` in `backend/app/adapters/codex_local.py`

Today it does:
- dev simulator command construction when `simulation_mode=True`
- shell-splitting of the provided real command when `simulation_mode=False`

To wire in a real Codex process, replace the real-command branch with the actual CLI invocation contract you want to support.

## Process contract already expected by the runtime

The adapter layer already supports:
- `start(session_config, callbacks)`
- `send(session_id, message)`
- `interrupt(session_id)`
- `stop(session_id)`
- `get_status(session_id)`

That contract is defined in:
- `backend/app/adapters/agent_adapter.py`

As long as the real Codex integration can honor that contract, the rest of the system does not need to change.

## Required work to plug in a real Codex process

1. Replace the simulator launch command in `codex_local.py` with the real Codex CLI command.
2. Decide how stdin messaging works for Codex:
   - plain stdin text
   - slash-command style protocol
   - JSON lines
3. Confirm interrupt semantics:
   - whether `SIGINT` is enough
   - whether a special stdin command is required
4. Confirm environment/bootstrap requirements:
   - auth tokens
   - config files
   - model selection
   - working-directory expectations
5. Ensure the real process emits the structured event block format:

```text
[[EVENT]]
{"type":"progress","summary":"implemented parser","progress":40}
[[/EVENT]]
```

6. Add one integration test that launches the real binary and exercises:
   - start
   - send
   - interrupt
   - stop
   - transcript persistence
   - structured event parsing

## Why the rest of the backend can stay unchanged

These layers are already adapter-agnostic:
- `SessionService` creates session records and launch plans
- `SessionSupervisor` persists runtime state and transcript/event records
- `OrchestratorService` reacts to normalized session and task state
- `ReviewService` consumes workspace diffs and check artifacts

That means the real Codex swap is mostly a runtime adapter job, not a full orchestration rewrite.

## Prompt contract expectations

The prompt templates in:
- `backend/app/prompts/worker.md`
- `backend/app/prompts/reviewer.md`
- `backend/app/prompts/manager.md`

already instruct sessions to emit structured event blocks. A real Codex process should receive those prompts unchanged or with only minimal transport-specific wrapping.
