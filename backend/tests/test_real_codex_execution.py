from __future__ import annotations

import os
import time
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app


def _wait_for_status(client: TestClient, session_id: str, expected: str, timeout_seconds: float = 8.0) -> dict:
    deadline = time.time() + timeout_seconds
    last_payload: dict | None = None
    while time.time() < deadline:
        response = client.get(f"/api/v1/sessions/{session_id}")
        assert response.status_code == 200
        payload = response.json()
        last_payload = payload
        if payload["status"] == expected:
            return payload
        time.sleep(0.1)
    raise AssertionError(f"Session {session_id} did not reach {expected}. Last payload: {last_payload}")


def _wait_for_structured_type(
    client: TestClient,
    session_id: str,
    expected: str,
    timeout_seconds: float = 5.0,
) -> list[dict]:
    deadline = time.time() + timeout_seconds
    last_payload: list[dict] = []
    while time.time() < deadline:
        response = client.get(f"/api/v1/sessions/{session_id}/structured-events")
        assert response.status_code == 200
        payload = response.json()
        last_payload = payload
        if any(event["status"] == "valid" and event["event_type"] == expected for event in payload):
            return payload
        time.sleep(0.1)
    raise AssertionError(
        f"Session {session_id} did not emit structured event {expected}. Last payload: {last_payload}"
    )


def test_real_codex_worker_executes_and_hands_off_to_review(
    settings,
    git_repo: Path,
    tmp_path: Path,
    monkeypatch,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    codex_script = bin_dir / "codex"
    codex_script.write_text(
        """#!/usr/bin/env python3
import json
import pathlib
import sys


def main() -> int:
    args = sys.argv[1:]
    if args[:2] == ["login", "status"]:
        print("Logged in using test runtime")
        return 0

    if not args or args[0] != "exec":
        print("unsupported invocation", file=sys.stderr)
        return 1

    workspace = "."
    if "-C" in args:
        workspace = args[args.index("-C") + 1]

    prompt = args[-1]
    workspace_path = pathlib.Path(workspace)
    if "Task title: Build Codex path" not in prompt:
        print("missing task title in startup packet", file=sys.stderr)
        return 2
    if "- README.md" not in prompt:
        print("missing scope in startup packet", file=sys.stderr)
        return 2

    (workspace_path / ".startup-prompt.txt").write_text(prompt, encoding="utf-8")
    target = workspace_path / "REAL_CODEX.txt"
    target.write_text("real codex wrote this\\n", encoding="utf-8")

    events = [
        {"type": "thread.started", "thread_id": "fake-thread"},
        {"type": "turn.started"},
        {
            "type": "item.completed",
            "item": {
                "id": "item_0",
                "type": "agent_message",
                "text": "Inspecting the workspace and preparing the requested change.",
            },
        },
        {
            "type": "item.completed",
            "item": {
                "id": "item_1",
                "type": "command_execution",
                "command": "/bin/zsh -lc pytest -q",
                "aggregated_output": "1 passed\\n",
                "exit_code": 0,
                "status": "completed",
            },
        },
        {
            "type": "item.completed",
            "item": {
                "id": "item_2",
                "type": "file_change",
                "changes": [{"path": str(target), "kind": "update"}],
                "status": "completed",
            },
        },
        {
            "type": "item.completed",
            "item": {
                "id": "item_3",
                "type": "agent_message",
                "text": "Finished the requested workspace update.",
            },
        },
        {"type": "turn.completed", "usage": {"input_tokens": 12, "output_tokens": 34}},
    ]
    for event in events:
        print(json.dumps(event), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
""",
        encoding="utf-8",
    )
    codex_script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    custom_settings = settings.model_copy(
        update={
            "codex_runtime_command_template": "codex {role}",
            "claude_code_runtime_command_template": str(tmp_path / "missing-claude" / "{role}"),
        }
    )

    with TestClient(create_app(custom_settings)) as client:
        project = client.post(
            "/api/v1/projects",
            json={"name": "Real Codex Project", "repo_path": str(git_repo)},
        ).json()
        task = client.post(
            "/api/v1/tasks",
            json={
                "project_id": project["id"],
                "title": "Build Codex path",
                "description": "Create a REAL_CODEX.txt marker in the isolated workspace.",
                "acceptance_criteria": ["Create REAL_CODEX.txt in the workspace."],
            },
        ).json()

        session_response = client.post(
            "/api/v1/sessions",
            json={
                "project_id": project["id"],
                "task_id": task["id"],
                "role": "worker",
                "runtime_preference": "codex",
                "metadata": {"preferred_engine": "codex"},
            },
        )
        assert session_response.status_code == 201
        session = session_response.json()
        assert session["runtime"]["resolved_provider"] == "codex"
        assert session["runtime"]["simulated"] is False

        assign_response = client.post(
            f"/api/v1/tasks/{task['id']}/assign",
            json={"session_id": session["id"], "allowed_paths": ["README.md"]},
        )
        assert assign_response.status_code == 200

        start_response = client.post(f"/api/v1/sessions/{session['id']}/start", json={})
        assert start_response.status_code == 200
        assert start_response.json()["status"] in {"starting", "running"}

        completed = _wait_for_status(client, session["id"], "completed")
        workspace_path = Path(completed["workspace_path"])
        assert (workspace_path / "REAL_CODEX.txt").exists()
        assert (workspace_path / ".startup-prompt.txt").exists()

        transcript = client.get(f"/api/v1/sessions/{session['id']}/transcript").json()
        assert any(
            chunk["stream"] == "stdin"
            and "Task title: Build Codex path" in chunk["content"]
            and "Execution requirements:" in chunk["content"]
            for chunk in transcript
        )
        assert any(
            chunk["stream"] == "stdout"
            and "Inspecting the workspace" in chunk["content"]
            for chunk in transcript
        )

        structured_events = _wait_for_structured_type(client, session["id"], "complete")
        valid_types = [event["event_type"] for event in structured_events if event["status"] == "valid"]
        assert "start" in valid_types
        assert "progress" in valid_types
        assert "tests_run" in valid_types
        assert "complete" in valid_types

        tick_response = client.post("/api/v1/orchestrator/tick", json={"project_id": project["id"]})
        assert tick_response.status_code == 200
        actions = tick_response.json()["projects"][0]["actions"]
        assert any(action["kind"] == "review_queued" for action in actions)

        task_payload = client.get("/api/v1/tasks", params={"project_id": project["id"]}).json()[0]
        assert task_payload["status"] == "review"

        reviews = client.get("/api/v1/reviews", params={"project_id": project["id"]}).json()
        assert len(reviews) == 1
        assert reviews[0]["task_id"] == task["id"]
