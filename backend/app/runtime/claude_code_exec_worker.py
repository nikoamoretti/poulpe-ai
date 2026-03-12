"""Bridge that launches ``claude -p`` with ``--output-format stream-json``
and translates the streaming JSON events into orchestrator ``[[EVENT]]``
blocks that the event-parser can pick up.

This mirrors ``codex_exec_worker.py`` but speaks the Claude Code CLI protocol.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
from typing import Any

START_MARKER = "[[EVENT]]"
END_MARKER = "[[/EVENT]]"
MAX_SUMMARY_LENGTH = 220

WRITE_TOOLS = frozenset({"Write", "Edit", "NotebookEdit", "MultiEdit"})
VERIFICATION_HINTS = (
    "pytest", "tox", "nox", "vitest", "jest", "go test", "cargo test",
    "npm test", "pnpm test", "yarn test", "bun test",
    "npm run test", "pnpm run test", "yarn run test",
    "npm run lint", "pnpm run lint", "yarn run lint",
    "npm lint", "pnpm lint", "yarn lint",
    "ruff", "eslint", "mypy", "pyright",
)
COMPLETE_BLOCK_RE = re.compile(
    r"\[\[EVENT\]\].*?\"type\"\s*:\s*\"complete\".*?\[\[/EVENT\]\]",
    re.DOTALL,
)


def emit_event(event_type: str, *, summary: str, payload: dict[str, Any] | None = None) -> None:
    rendered = {"type": event_type, "summary": summary, **(payload or {})}
    print(START_MARKER, flush=True)
    print(json.dumps(rendered), flush=True)
    print(END_MARKER, flush=True)


def truncate(text: str, max_len: int = MAX_SUMMARY_LENGTH) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) > max_len:
        return collapsed[: max_len - 3].rstrip() + "..."
    return collapsed


def is_verification_command(command: str) -> bool:
    lowered = command.lower()
    return any(hint in lowered for hint in VERIFICATION_HINTS)


def contains_structured_complete(text: str) -> bool:
    return bool(COMPLETE_BLOCK_RE.search(text))


def contains_structured_event(text: str) -> bool:
    return START_MARKER in text and END_MARKER in text


def main() -> int:
    parser = argparse.ArgumentParser(description="Bridge claude -p stream-json into orchestrator events.")
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--role", required=True)
    parser.add_argument("--workspace-path", required=True)
    parser.add_argument("--claude-command", required=True, help="JSON array of the claude binary + base args")
    parser.add_argument("--model")
    args = parser.parse_args()

    prompt = os.environ.get("ORCHESTRATOR_STARTUP_PROMPT", "").strip()
    if not prompt:
        print("[claude-worker] missing startup prompt", file=sys.stderr, flush=True)
        emit_event("error", summary="Missing startup prompt for Claude Code execution.",
                    payload={"error": "startup_prompt_missing", "retryable": False})
        return 2

    try:
        base_command = json.loads(args.claude_command)
    except json.JSONDecodeError:
        base_command = None
    if not isinstance(base_command, list) or not base_command:
        print("[claude-worker] invalid claude command configuration", file=sys.stderr, flush=True)
        emit_event("error", summary="Claude command is invalid.",
                    payload={"error": "invalid_claude_command", "retryable": False})
        return 2

    command = [
        *[str(part) for part in base_command],
        "-p", prompt,
        "--output-format", "stream-json",
        "--verbose",
        "--dangerously-skip-permissions",
        "--max-turns", "200",
    ]
    if args.model:
        command.extend(["--model", args.model])

    # Ensure CLAUDECODE env var is unset so nested check doesn't block us
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    env.pop("CLAUDE_CODE_ENTRYPOINT", None)

    print(f"[claude-worker] launching claude -p in {args.workspace_path}", flush=True)
    emit_event("start", summary="Real Claude Code execution started.", payload={
        "provider": "claude_code",
        "session_id": args.session_id,
        "workspace_path": args.workspace_path,
        "simulated": False,
    })

    process = subprocess.Popen(
        command,
        cwd=args.workspace_path,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        env=env,
    )

    changed_files: list[str] = []
    saw_result_event = False
    saw_structured_complete = False
    last_error: str | None = None
    error_lock = threading.Lock()
    assert process.stdout is not None
    assert process.stderr is not None

    def remember_error(message: str) -> None:
        nonlocal last_error
        with error_lock:
            last_error = message

    def forward_stderr() -> None:
        assert process.stderr is not None
        for raw_line in process.stderr:
            line = raw_line.rstrip("\n")
            if not line:
                continue
            remember_error(line)
            print(f"[claude-worker] {line}", file=sys.stderr, flush=True)

    stderr_thread = threading.Thread(target=forward_stderr, daemon=True)
    stderr_thread.start()

    for raw_line in process.stdout:
        line = raw_line.rstrip("\n")
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            # Plain text output from Claude — render it
            print(line, flush=True)
            continue

        event_type = str(event.get("type") or "")

        # ── system init ──
        if event_type == "system":
            subtype = event.get("subtype", "")
            if subtype == "init":
                emit_event("heartbeat", summary="Claude Code session initialised.",
                           payload={"provider": "claude_code"})
            continue

        # ── assistant message ──
        if event_type == "assistant":
            message = event.get("message", {})
            content_blocks = message.get("content", [])
            text_parts: list[str] = []
            tool_uses: list[dict[str, Any]] = []

            for block in content_blocks:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    text_parts.append(str(block.get("text", "")))
                elif block.get("type") == "tool_use":
                    tool_uses.append(block)

            # Emit text as progress
            full_text = "\n".join(text_parts).strip()
            if full_text:
                if contains_structured_complete(full_text):
                    saw_structured_complete = True
                print(full_text, flush=True)
                if not contains_structured_event(full_text):
                    emit_event("progress", summary=truncate(full_text),
                               payload={"provider": "claude_code"})

            # Track tool uses — file writes
            for tool in tool_uses:
                tool_name = str(tool.get("name", ""))
                tool_input = tool.get("input", {})
                if not isinstance(tool_input, dict):
                    continue
                if tool_name in WRITE_TOOLS:
                    file_path = str(tool_input.get("file_path", "") or tool_input.get("path", ""))
                    if file_path:
                        try:
                            rel = os.path.relpath(file_path, args.workspace_path)
                        except ValueError:
                            rel = file_path
                        if rel not in changed_files:
                            changed_files.append(rel)
                        emit_event("progress", summary=f"Claude Code edited {rel}",
                                   payload={"files": [rel], "provider": "claude_code"})
                elif tool_name == "Bash":
                    cmd = str(tool_input.get("command", ""))
                    if cmd and is_verification_command(cmd):
                        # We'll emit tests_run when we see the tool_result
                        pass
            continue

        # ── tool result ──
        if event_type == "tool_result":
            # Check for file changes from Bash tool results
            continue

        # ── content block delta (partial streaming) ──
        if event_type == "content_block_delta":
            continue

        # ── content block start/stop ──
        if event_type in ("content_block_start", "content_block_stop"):
            continue

        # ── final result ──
        if event_type == "result":
            saw_result_event = True
            subtype = event.get("subtype", "")
            is_error = event.get("is_error", False)
            cost = event.get("cost_usd")
            duration_ms = event.get("duration_ms")
            num_turns = event.get("num_turns")
            result_text = str(event.get("result", "")).strip()

            result_payload: dict[str, Any] = {
                "files": changed_files,
                "provider": "claude_code",
            }
            if cost is not None:
                result_payload["cost_usd"] = cost
            if duration_ms is not None:
                result_payload["duration_ms"] = duration_ms
            if num_turns is not None:
                result_payload["num_turns"] = num_turns

            if is_error or subtype == "error":
                remember_error(result_text or "Claude Code execution failed.")
                emit_event("error", summary=truncate(result_text or "Claude Code execution failed."),
                           payload={**result_payload, "error": result_text, "retryable": False})
            else:
                if result_text:
                    if contains_structured_complete(result_text):
                        saw_structured_complete = True
                    if not contains_structured_event(result_text):
                        print(result_text, flush=True)
                if not saw_structured_complete:
                    emit_event("complete", summary="Real Claude Code execution finished.",
                               payload=result_payload)
            continue

        # ── message_start / message_delta / message_stop ──
        if event_type in ("message_start", "message_delta", "message_stop"):
            # Usage info can come in message_delta
            if event_type == "message_delta":
                usage = event.get("usage")
                if usage:
                    emit_event("heartbeat", summary="Claude Code turn progress.",
                               payload={"provider": "claude_code", "usage": usage})
            continue

    returncode = process.wait()
    stderr_thread.join(timeout=1)

    if returncode == 0:
        # Fall back only if Claude exited cleanly without a structured or result completion event.
        if not saw_result_event and not saw_structured_complete:
            emit_event("complete", summary="Real Claude Code execution finished.",
                        payload={"files": changed_files, "provider": "claude_code"})
        return 0

    emit_event("error", summary="Real Claude Code execution failed.", payload={
        "error": last_error or f"claude exited with code {returncode}",
        "provider": "claude_code",
        "files": changed_files,
        "retryable": False,
    })
    return returncode


if __name__ == "__main__":
    raise SystemExit(main())
