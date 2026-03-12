# Codex Integration

This repo now has one real worker execution path backed by the local Codex CLI.

## What is real today

- worker sessions can launch a real local Codex process
- each worker still gets its own git branch and linked git worktree
- the backend generates a task packet from the task, scope, and workspace state
- transcript chunks are persisted during execution
- structured events are extracted from the Codex bridge output
- the orchestrator can hand completed work off into the review pipeline

The main files are:
- `backend/app/adapters/codex_local.py`
- `backend/app/runtime/codex_exec_worker.py`
- `backend/app/services/task_packet_service.py`
- `backend/app/services/session_supervisor.py`

## Runtime contract

The real Codex path uses:

```text
codex exec --json --full-auto -C <workspace> "<generated task packet>"
```

The bridge process in `backend/app/runtime/codex_exec_worker.py` is responsible for:
- launching Codex in the isolated workspace
- forwarding Codex text into the transcript stream
- translating Codex JSONL items into structured event blocks
- surfacing verification runs and completion/error states

## What the backend sends to Codex

`TaskPacketService` builds a startup packet with:
- the worker prompt template
- project and task identity
- workspace path and branch information
- scope restrictions
- acceptance criteria
- explicit instructions to emit `[[EVENT]]` JSON blocks

That startup packet is persisted as a transcript input chunk so startup behavior is inspectable.

## Current limits

- live follow-up messaging is not supported for real Codex exec sessions yet
- reviewer and manager sessions still use the simulated runtime
- Claude Code is still future or experimental only
- merge to the main branch is still not implemented

## Future Claude adapter seam

The current architecture is ready for a future `ClaudeCodeAdapter`:
- `AgentAdapter` defines the runtime contract
- `SessionSupervisor` is runtime-agnostic once it has an adapter and startup packet
- `TaskPacketService` can be reused or split into provider-specific packet builders
- `codex_exec_worker.py` shows the pattern for turning provider-native output into transcript plus structured events
