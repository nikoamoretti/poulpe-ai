# Example Manager Task Packet

Role: `manager`
Project: `local-agent-orchestrator`
Goal: `Ship a reliable structured event protocol for supervised worker sessions`

Subtasks:

1. Parser and schema contract
2. Parsed-event DB persistence
3. Runtime state mapping and websocket fan-out
4. Prompt template updates
5. Parser and integration tests

Success Conditions:

- worker state is machine-readable
- malformed blocks are inspectable
- structured events are queryable and live-streamed

Required outputs:

- `start` when planning begins
- `progress` for each subtask handoff or milestone
- `question` if operator input is required
- `complete` when all subtasks are handed off or verified
