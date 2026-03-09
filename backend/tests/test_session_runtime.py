from __future__ import annotations

import time
from pathlib import Path


def _create_worker_session(client, git_repo: Path) -> dict:
    project = client.post(
        "/api/v1/projects",
        json={"name": "Runtime Project", "repo_path": str(git_repo)},
    ).json()
    task = client.post(
        "/api/v1/tasks",
        json={
            "project_id": project["id"],
            "title": "Run a session",
            "description": "Exercise runtime supervision.",
        },
    ).json()
    session_response = client.post(
        "/api/v1/sessions",
        json={
            "project_id": project["id"],
            "task_id": task["id"],
            "role": "worker",
        },
    )
    assert session_response.status_code == 201
    session = session_response.json()
    return {"project": project, "task": task, "session": session}


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


def _wait_for_event_type(client, session_id: str, expected: str, timeout_seconds: float = 5.0) -> list[dict]:
    deadline = time.time() + timeout_seconds
    last_events: list[dict] = []
    while time.time() < deadline:
        response = client.get("/api/v1/events", params={"session_id": session_id})
        assert response.status_code == 200
        events = response.json()
        last_events = events
        if any(event["event_type"] == expected for event in events):
            return events
        time.sleep(0.1)
    raise AssertionError(f"Session {session_id} did not emit {expected}. Last events: {last_events}")


def test_session_runtime_start_block_resume_complete(client, git_repo: Path) -> None:
    created = _create_worker_session(client, git_repo)
    session = created["session"]

    start_response = client.post(f"/api/v1/sessions/{session['id']}/start", json={})
    assert start_response.status_code == 200
    assert start_response.json()["status"] in {"starting", "running"}

    running = _wait_for_status(client, session["id"], "running")
    assert running["pid"] is not None
    assert running["last_heartbeat_at"] is not None
    assert running["adapter_kind"] == "codex_local"

    message_response = client.post(
        f"/api/v1/sessions/{session['id']}/messages",
        json={"message": "please block on this task"},
    )
    assert message_response.status_code == 200

    blocked = _wait_for_status(client, session["id"], "blocked")
    assert blocked["blocked_reason"] == "needs_guidance"

    complete_response = client.post(
        f"/api/v1/sessions/{session['id']}/messages",
        json={"message": "resume and complete"},
    )
    assert complete_response.status_code == 200

    completed = _wait_for_status(client, session["id"], "completed")
    assert completed["exit_code"] == 0
    assert completed["ended_at"] is not None

    transcript_response = client.get(f"/api/v1/sessions/{session['id']}/transcript")
    assert transcript_response.status_code == 200
    transcript = transcript_response.json()
    assert any(chunk["stream"] == "stdin" and chunk["content"] == "please block on this task" for chunk in transcript)
    assert any(chunk["stream"] == "stdout" and "received: please block on this task" in chunk["content"] for chunk in transcript)
    assert any(chunk["stream"] == "system" and "Starting session" in chunk["content"] for chunk in transcript)

    structured_events_response = client.get(f"/api/v1/sessions/{session['id']}/structured-events")
    assert structured_events_response.status_code == 200
    structured_events = structured_events_response.json()
    structured_types = [event["event_type"] for event in structured_events if event["status"] == "valid"]
    assert "start" in structured_types
    assert "blocked" in structured_types
    assert "complete" in structured_types

    events = _wait_for_event_type(client, session["id"], "session.completed")
    event_types = {event["event_type"] for event in events}
    assert "session.starting" in event_types
    assert "session.started" in event_types
    assert "session.start" in event_types
    assert "session.output" in event_types
    assert "session.instruction_sent" in event_types
    assert "session.blocked" in event_types
    assert "session.complete" in event_types
    assert "session.completed" in event_types


def test_session_runtime_interrupt_and_stop(client, git_repo: Path) -> None:
    created = _create_worker_session(client, git_repo)
    session = created["session"]

    start_response = client.post(f"/api/v1/sessions/{session['id']}/start", json={})
    assert start_response.status_code == 200
    _wait_for_status(client, session["id"], "running")

    interrupt_response = client.post(f"/api/v1/sessions/{session['id']}/interrupt")
    assert interrupt_response.status_code == 200
    blocked = _wait_for_status(client, session["id"], "blocked")
    assert blocked["blocked_reason"] == "operator_interrupt"

    stop_response = client.post(f"/api/v1/sessions/{session['id']}/stop")
    assert stop_response.status_code == 200
    stopped = _wait_for_status(client, session["id"], "stopped")
    assert stopped["ended_at"] is not None

    transcript_response = client.get(f"/api/v1/sessions/{session['id']}/transcript")
    assert transcript_response.status_code == 200
    transcript = transcript_response.json()
    assert any(chunk["stream"] == "system" and "interrupt" in chunk["content"].lower() for chunk in transcript)
    assert any(chunk["stream"] == "system" and "stop requested" in chunk["content"].lower() for chunk in transcript)

    events = _wait_for_event_type(client, session["id"], "session.stopped")
    event_types = {event["event_type"] for event in events}
    assert "session.interrupt_requested" in event_types
    assert "session.stop_requested" in event_types
    assert "session.blocked" in event_types
    assert "session.stopped" in event_types
