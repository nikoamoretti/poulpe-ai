from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from dataclasses import dataclass

from app.adapters.event_parser import END_MARKER, START_MARKER

MANAGER_TURN_PREFIX = "PORTFOLIO_MANAGER_TURN_JSON: "


@dataclass
class SimulatorState:
    interrupted: bool = False
    stop_requested: bool = False


def emit_event(event_type: str, *, summary: str | None = None, payload: dict | None = None) -> None:
    event_payload = {
        "type": event_type,
        "summary": summary,
        **(payload or {}),
    }
    print(START_MARKER, flush=True)
    print(json.dumps(event_payload), flush=True)
    print(END_MARKER, flush=True)


def _manager_turn_payload(message: str) -> dict | None:
    if not message.startswith(MANAGER_TURN_PREFIX):
        return None
    raw_payload = message.removeprefix(MANAGER_TURN_PREFIX).strip()
    if not raw_payload:
        return None
    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Simulate a Codex-style local terminal session.")
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--role", required=True)
    parser.add_argument("--workspace-path")
    args = parser.parse_args()
    session_kind = os.environ.get("ORCHESTRATOR_SESSION_KIND", "").strip()
    is_manager_turn = args.role == "manager" and session_kind == "portfolio_manager_turn"

    state = SimulatorState()

    def handle_sigint(_: int, __) -> None:
        state.interrupted = True
        print("[codex-sim] interrupt received", flush=True)
        emit_event(
            "blocked",
            summary="Interrupted and waiting for the next operator instruction.",
            payload={"reason": "operator_interrupt"},
        )

    def handle_sigterm(_: int, __) -> None:
        state.stop_requested = True
        print("[codex-sim] stop requested", file=sys.stderr, flush=True)
        raise SystemExit(0)

    signal.signal(signal.SIGINT, handle_sigint)
    signal.signal(signal.SIGTERM, handle_sigterm)

    print(
        f"[codex-sim] boot session={args.session_id} role={args.role} workspace={args.workspace_path or ''}",
        flush=True,
    )
    emit_event(
        "start",
        summary="Codex local simulator booted.",
        payload={"phase": "boot", "details": {"simulated": True}},
    )

    for line in sys.stdin:
        message = line.strip()
        if not message:
            continue

        if is_manager_turn:
            payload = _manager_turn_payload(message)
            if payload is None:
                if message.startswith(MANAGER_TURN_PREFIX):
                    emit_event(
                        "error",
                        summary="Manager turn packet could not be parsed.",
                        payload={"error": "manager_turn_packet_invalid", "retryable": False},
                    )
                    return 1
                continue

            checkpoint_kind = str(payload.get("checkpoint_kind") or "")
            diff_file_count = int(payload.get("diff_file_count") or 0)
            has_review_context_error = bool(payload.get("has_review_context_error"))
            checkpoint_id = str(payload.get("checkpoint_id") or "")

            if checkpoint_kind == "completion":
                if has_review_context_error or diff_file_count <= 0:
                    result = "request_changes"
                    response_message = (
                        "Review context was incomplete or no diff was attached. "
                        "Continue the project, produce a concrete change set, and report completion again."
                    )
                else:
                    result = "approve"
                    response_message = "Approved for the current project milestone."
            else:
                result = "answer"
                response_message = (
                    "Continue with the stated project objective, keep the current API shape stable, "
                    "and ask again only if a new decision is required."
                )

            emit_event(
                "progress",
                summary="Prepared a manager decision for the checkpoint.",
                payload={"checkpoint_id": checkpoint_id, "result": result},
            )
            emit_event(
                "complete",
                summary="Manager decision ready.",
                payload={
                    "checkpoint_id": checkpoint_id,
                    "result": result,
                    "response_message": response_message,
                    "details": {"mode": "simulated_manager_turn"},
                },
            )
            print("[codex-sim] manager turn completed", flush=True)
            time.sleep(0.1)
            return 0

        if message in {"\x03", "__INTERRUPT__"}:
            state.interrupted = True
            print("[codex-sim] interrupt message received", flush=True)
            emit_event(
                "blocked",
                summary="Interrupted and waiting for the next operator instruction.",
                payload={"reason": "operator_interrupt"},
            )
            continue

        print(f"[codex-sim] received: {message}", flush=True)
        lowered = message.lower()

        emit_event(
            "progress",
            summary=f"Received operator instruction: {message}",
            payload={"next_step": "Apply the requested change or report a blocker."},
        )

        if "resume" in lowered:
            state.interrupted = False
            emit_event(
                "progress",
                summary="Resumed after operator input.",
                payload={"next_step": "Continue implementation."},
            )

        if "block" in lowered:
            emit_event(
                "blocked",
                summary="Waiting for additional operator guidance.",
                payload={"reason": "needs_guidance", "needs": ["clarified acceptance criteria"]},
            )

        if "question" in lowered:
            emit_event(
                "question",
                summary="Need clarification before continuing.",
                payload={
                    "question": "Should the worker update the API schema as part of this task?",
                    "choices": ["yes", "no"],
                },
            )

        if "test" in lowered or "lint" in lowered:
            emit_event(
                "tests_run",
                summary="Ran a verification command.",
                payload={
                    "command": "pytest -q",
                    "status": "passed",
                    "exit_code": 0,
                    "passed": 4,
                    "failed": 0,
                },
            )
            print("[codex-sim] acknowledged task command", flush=True)

        if "malformed" in lowered:
            print(START_MARKER, flush=True)
            print('{"type":"progress","summary":"broken"', flush=True)
            print(END_MARKER, flush=True)

        if "fail" in lowered:
            print("[codex-sim] failing as requested", file=sys.stderr, flush=True)
            emit_event(
                "error",
                summary="Failure requested by operator.",
                payload={"error": "Operator requested failure.", "retryable": False},
            )
            return 1

        if "complete" in lowered or "done" in lowered:
            emit_event(
                "complete",
                summary="Completion requested by operator.",
                payload={"result": "Requested work marked complete.", "files": ["README.md"]},
            )
            print("[codex-sim] completed", flush=True)
            return 0

    if state.stop_requested:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
