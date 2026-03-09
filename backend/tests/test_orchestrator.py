from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from app.models.session import Session as SessionModel


def _create_project(client, git_repo: Path, name: str = "Orchestrator Project") -> dict:
    response = client.post(
        "/api/v1/projects",
        json={"name": name, "repo_path": str(git_repo)},
    )
    assert response.status_code == 201
    return response.json()


def _create_task(client, project_id: str, title: str) -> dict:
    response = client.post(
        "/api/v1/tasks",
        json={
            "project_id": project_id,
            "title": title,
            "description": f"Task for {title}",
        },
    )
    assert response.status_code == 201
    return response.json()


def _create_worker_session(client, project_id: str, task_id: str) -> dict:
    response = client.post(
        "/api/v1/sessions",
        json={
            "project_id": project_id,
            "task_id": task_id,
            "role": "worker",
        },
    )
    assert response.status_code == 201
    return response.json()


def _wait_for_status(client, session_id: str, expected: str, timeout_seconds: float = 5.0) -> dict:
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


def test_orchestrator_assigns_and_queues_review_on_completion(client, git_repo: Path) -> None:
    project = _create_project(client, git_repo)
    task = _create_task(client, project["id"], "Complete a worker task")
    session = _create_worker_session(client, project["id"], task["id"])

    assign_response = client.post(
        f"/api/v1/tasks/{task['id']}/assign",
        json={
            "session_id": session["id"],
            "allowed_paths": ["backend/app"],
        },
    )
    assert assign_response.status_code == 200
    assert assign_response.json()["task"]["status"] == "in_progress"

    start_response = client.post(f"/api/v1/sessions/{session['id']}/start", json={})
    assert start_response.status_code == 200
    _wait_for_status(client, session["id"], "running")

    message_response = client.post(
        f"/api/v1/sessions/{session['id']}/messages",
        json={"message": "complete"},
    )
    assert message_response.status_code == 200
    _wait_for_status(client, session["id"], "completed")

    tick_response = client.post("/api/v1/orchestrator/tick", json={"project_id": project["id"]})
    assert tick_response.status_code == 200
    actions = tick_response.json()["projects"][0]["actions"]
    assert any(action["kind"] == "review_queued" for action in actions)

    task_response = client.get(f"/api/v1/tasks/{task['id']}")
    assert task_response.status_code == 200
    assert task_response.json()["status"] == "review"

    reviews_response = client.get("/api/v1/reviews", params={"project_id": project["id"]})
    assert reviews_response.status_code == 200
    assert len(reviews_response.json()) == 1


def test_orchestrator_manual_task_block_and_complete_routes(client, git_repo: Path) -> None:
    project = _create_project(client, git_repo, name="Manual Task State Project")
    task = _create_task(client, project["id"], "Manually managed task")

    block_response = client.post(
        f"/api/v1/tasks/{task['id']}/block",
        json={"reason": "waiting_on_human", "note": "Need approval before continuing."},
    )
    assert block_response.status_code == 200
    blocked_task = block_response.json()
    assert blocked_task["status"] == "blocked"
    assert blocked_task["metadata"]["orchestrator"]["blocked_reason"] == "waiting_on_human"

    complete_response = client.post(
        f"/api/v1/tasks/{task['id']}/complete",
        json={"summary": "Finished manually", "note": "No review required."},
    )
    assert complete_response.status_code == 200
    completed_task = complete_response.json()
    assert completed_task["status"] == "done"
    assert completed_task["metadata"]["orchestrator"]["completion_summary"] == "Finished manually"


def test_orchestrator_requests_summary_for_idle_sessions(client, git_repo: Path) -> None:
    project = _create_project(client, git_repo, name="Idle Session Project")
    task = _create_task(client, project["id"], "Prompt a silent worker")
    session = _create_worker_session(client, project["id"], task["id"])

    assign_response = client.post(
        f"/api/v1/tasks/{task['id']}/assign",
        json={
            "session_id": session["id"],
            "allowed_paths": ["backend/app"],
        },
    )
    assert assign_response.status_code == 200

    start_response = client.post(f"/api/v1/sessions/{session['id']}/start", json={})
    assert start_response.status_code == 200
    _wait_for_status(client, session["id"], "running")

    with client.app.state.container.database.session() as db:
        session_record = db.get(SessionModel, UUID(session["id"]))
        assert session_record is not None
        session_record.last_heartbeat_at = datetime.now(UTC) - timedelta(minutes=10)
        db.commit()

    tick_response = client.post("/api/v1/orchestrator/tick", json={"project_id": project["id"]})
    assert tick_response.status_code == 200
    actions = tick_response.json()["projects"][0]["actions"]
    assert any(action["kind"] == "summary_requested" for action in actions)

    transcript_response = client.get(f"/api/v1/sessions/{session['id']}/transcript")
    assert transcript_response.status_code == 200
    transcript = transcript_response.json()
    assert any(
        chunk["stream"] == "stdin" and "Emit a [[EVENT]] heartbeat" in chunk["content"]
        for chunk in transcript
    )

    stop_response = client.post(f"/api/v1/sessions/{session['id']}/stop")
    assert stop_response.status_code == 200
    _wait_for_status(client, session["id"], "stopped")


def test_orchestrator_detects_scope_conflicts_on_assignment(client, git_repo: Path) -> None:
    project = _create_project(client, git_repo, name="Scope Conflict Project")
    first_task = _create_task(client, project["id"], "First task")
    second_task = _create_task(client, project["id"], "Second task")
    first_session = _create_worker_session(client, project["id"], first_task["id"])
    second_session = _create_worker_session(client, project["id"], second_task["id"])

    first_assign_response = client.post(
        f"/api/v1/tasks/{first_task['id']}/assign",
        json={"session_id": first_session["id"], "allowed_paths": ["backend/app"]},
    )
    assert first_assign_response.status_code == 200

    second_assign_response = client.post(
        f"/api/v1/tasks/{second_task['id']}/assign",
        json={"session_id": second_session["id"], "allowed_paths": ["backend/app/services"]},
    )
    assert second_assign_response.status_code == 409


def test_orchestrator_detects_overlapping_changed_files(client, git_repo: Path) -> None:
    project = _create_project(client, git_repo, name="Changed Files Conflict Project")
    first_task = _create_task(client, project["id"], "First worker change")
    second_task = _create_task(client, project["id"], "Second worker change")
    first_session = _create_worker_session(client, project["id"], first_task["id"])
    second_session = _create_worker_session(client, project["id"], second_task["id"])

    first_assign_response = client.post(
        f"/api/v1/tasks/{first_task['id']}/assign",
        json={"session_id": first_session["id"], "allowed_paths": ["docs"]},
    )
    assert first_assign_response.status_code == 200

    second_assign_response = client.post(
        f"/api/v1/tasks/{second_task['id']}/assign",
        json={"session_id": second_session["id"], "allowed_paths": ["src"]},
    )
    assert second_assign_response.status_code == 200

    first_workspace = client.get(f"/api/v1/sessions/{first_session['id']}/workspace").json()
    second_workspace = client.get(f"/api/v1/sessions/{second_session['id']}/workspace").json()
    (Path(first_workspace["workspace_path"]) / "README.md").write_text("# Sample Repo\n\nfirst\n", encoding="utf-8")
    (Path(second_workspace["workspace_path"]) / "README.md").write_text("# Sample Repo\n\nsecond\n", encoding="utf-8")

    tick_response = client.post("/api/v1/orchestrator/tick", json={"project_id": project["id"]})
    assert tick_response.status_code == 200
    actions = tick_response.json()["projects"][0]["actions"]
    conflict_actions = [action for action in actions if action["kind"] == "changed_files_conflict"]
    assert len(conflict_actions) == 2

    first_task_response = client.get(f"/api/v1/tasks/{first_task['id']}")
    second_task_response = client.get(f"/api/v1/tasks/{second_task['id']}")
    assert first_task_response.json()["status"] == "blocked"
    assert second_task_response.json()["status"] == "blocked"
