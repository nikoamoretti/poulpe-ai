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
VERIFICATION_HINTS = (
    "pytest",
    "tox",
    "nox",
    "vitest",
    "jest",
    "go test",
    "cargo test",
    "npm test",
    "pnpm test",
    "yarn test",
    "bun test",
    "npm run test",
    "pnpm run test",
    "yarn run test",
    "npm run lint",
    "pnpm run lint",
    "yarn run lint",
    "npm lint",
    "pnpm lint",
    "yarn lint",
    "ruff",
    "eslint",
    "mypy",
    "pyright",
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


def render_agent_message(text: str) -> None:
    if not text.strip():
        return
    print(text, flush=True)


def summarize_message(text: str) -> str | None:
    if not text.strip() or START_MARKER in text:
        return None
    collapsed = " ".join(text.split())
    if not collapsed:
        return None
    if len(collapsed) > MAX_SUMMARY_LENGTH:
        return collapsed[: MAX_SUMMARY_LENGTH - 3].rstrip() + "..."
    return collapsed


def is_verification_command(command: str) -> bool:
    lowered = command.lower()
    return any(hint in lowered for hint in VERIFICATION_HINTS)


def relative_paths(workspace_path: str, changes: list[dict[str, Any]]) -> list[str]:
    paths: list[str] = []
    for change in changes:
        raw_path = change.get("path")
        if not raw_path:
            continue
        try:
            path = os.path.relpath(str(raw_path), workspace_path)
        except ValueError:
            path = str(raw_path)
        if path not in paths:
            paths.append(path)
    return paths


def contains_structured_complete(text: str) -> bool:
    return bool(COMPLETE_BLOCK_RE.search(text))


def main() -> int:
    parser = argparse.ArgumentParser(description="Bridge codex exec JSONL into orchestrator transcript output.")
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--role", required=True)
    parser.add_argument("--workspace-path", required=True)
    parser.add_argument("--codex-command", required=True)
    parser.add_argument("--model")
    args = parser.parse_args()

    prompt = os.environ.get("ORCHESTRATOR_STARTUP_PROMPT", "").strip()
    if not prompt:
        print("[codex-worker] missing startup prompt", file=sys.stderr, flush=True)
        emit_event(
            "error",
            summary="Missing startup prompt for Codex execution.",
            payload={"error": "startup_prompt_missing", "retryable": False},
        )
        return 2

    try:
        base_command = json.loads(args.codex_command)
    except json.JSONDecodeError:
        base_command = None
    if not isinstance(base_command, list) or not base_command:
        print("[codex-worker] invalid codex command configuration", file=sys.stderr, flush=True)
        emit_event(
            "error",
            summary="Codex command is invalid.",
            payload={"error": "invalid_codex_command", "retryable": False},
        )
        return 2

    command = [
        *[str(part) for part in base_command],
        "exec",
        "--json",
        "--full-auto",
        "-C",
        args.workspace_path,
    ]
    if args.model:
        command.extend(["-m", args.model])
    command.append(prompt)

    print(
        f"[codex-worker] launching codex exec in {args.workspace_path}",
        flush=True,
    )
    emit_event(
        "start",
        summary="Real Codex execution started.",
        payload={
            "provider": "codex",
            "session_id": args.session_id,
            "workspace_path": args.workspace_path,
            "simulated": False,
        },
    )

    process = subprocess.Popen(
        command,
        cwd=args.workspace_path,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        env=os.environ.copy(),
    )

    changed_files: list[str] = []
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
            print(f"[codex-worker] {line}", file=sys.stderr, flush=True)

    stderr_thread = threading.Thread(target=forward_stderr, daemon=True)
    stderr_thread.start()

    for raw_line in process.stdout:
        line = raw_line.rstrip("\n")
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            print(line, flush=True)
            continue

        event_type = str(event.get("type") or "")
        if event_type == "item.completed":
            item = event.get("item", {})
            item_type = str(item.get("type") or "")
            if item_type == "agent_message":
                text = str(item.get("text") or "")
                if contains_structured_complete(text):
                    saw_structured_complete = True
                render_agent_message(text)
                summary = summarize_message(text)
                if summary is not None:
                    emit_event(
                        "progress",
                        summary=summary,
                        payload={"provider": "codex"},
                    )
                continue
            if item_type == "command_execution":
                command_text = str(item.get("command") or "")
                exit_code = int(item.get("exit_code") or 0)
                if is_verification_command(command_text):
                    emit_event(
                        "tests_run",
                        summary=f"Codex ran verification: {command_text}",
                        payload={
                            "provider": "codex",
                            "command": command_text,
                            "status": "passed" if exit_code == 0 else "failed",
                            "exit_code": exit_code,
                        },
                    )
                continue
            if item_type == "file_change":
                files = relative_paths(
                    args.workspace_path,
                    [change for change in item.get("changes", []) if isinstance(change, dict)],
                )
                if files:
                    for path in files:
                        if path not in changed_files:
                            changed_files.append(path)
                    emit_event(
                        "progress",
                        summary=f"Codex updated {len(files)} file(s).",
                        payload={"files": files, "provider": "codex"},
                    )
                continue

        if event_type == "turn.completed":
            usage = event.get("usage", {})
            emit_event(
                "heartbeat",
                summary="Codex completed a turn.",
                payload={"provider": "codex", "usage": usage},
            )
            continue

        if event_type == "error":
            remember_error(str(event.get("message") or "Codex execution failed."))
            print(f"[codex-worker] {last_error}", file=sys.stderr, flush=True)

    returncode = process.wait()
    stderr_thread.join(timeout=1)
    if returncode == 0:
        if not saw_structured_complete:
            emit_event(
                "complete",
                summary="Real Codex execution finished.",
                payload={"files": changed_files, "provider": "codex"},
            )
        return 0

    emit_event(
        "error",
        summary="Real Codex execution failed.",
        payload={
            "error": last_error or f"codex exec exited with code {returncode}",
            "provider": "codex",
            "files": changed_files,
            "retryable": False,
        },
    )
    return returncode


if __name__ == "__main__":
    raise SystemExit(main())
