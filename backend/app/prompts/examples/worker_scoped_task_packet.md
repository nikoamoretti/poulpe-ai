# Example Worker Task Packet

Role: `worker`
Project: `local-agent-orchestrator`
Task Title: `Implement structured event extraction for worker output`

Scope:

- replace legacy event markers with `[[EVENT]] ... [[/EVENT]]`
- validate normalized event types
- persist parsed events separately from transcript chunks
- keep malformed blocks for debugging

Acceptance Criteria:

- valid event blocks are parsed and stored
- malformed blocks are preserved with validation errors
- websocket subscribers receive normalized structured events
- parser tests cover valid, partial, and malformed blocks

Constraints:

- do not remove raw transcript storage
- do not rely on freeform prose for state changes
- prefer additive changes over broad refactors

Required event types during execution:

- `start` when beginning implementation
- `progress` after parser/model wiring
- `tests_run` after running parser/runtime tests
- `blocked` or `question` if requirements are unclear
- `complete` when scope is finished
