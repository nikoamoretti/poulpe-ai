# Example Reviewer Task Packet

Role: `reviewer`
Project: `local-agent-orchestrator`
Task Title: `Review structured event protocol implementation`

Review Inputs:

- workspace diff for the worker branch
- parser and runtime test output
- structured event rows and websocket behavior

Focus Areas:

- malformed block handling
- state regressions from parsed events
- schema validation gaps
- websocket fan-out correctness

Required outputs:

- `start` at review kickoff
- `progress` for each concrete finding
- `tests_run` if you run extra verification
- `complete` with review outcome
