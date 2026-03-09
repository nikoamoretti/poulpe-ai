from __future__ import annotations

import time
from pathlib import Path


def _create_worker_session(client, git_repo: Path) -> dict:
    project = client.post(
        "/api/v1/projects",
        json={"name": "Structured Events Project", "repo_path": str(git_repo)},
    ).json()
    task = client.post(
        "/api/v1/tasks",
        json={
            "project_id": project["id"],
            "title": "Exercise the event protocol",
            "description": "Persist and stream structured agent events.",
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
    return {"project": project, "task": task, "session": session_response.json()}


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


def _receive_event(websocket, event_type: str, limit: int = 30) -> dict:
    for _ in range(limit):
        message = websocket.receive_json()
        if message["event_type"] == event_type:
            return message
    raise AssertionError(f"Did not receive websocket event {event_type!r}")


def test_structured_events_are_persisted_and_streamed(client, git_repo: Path) -> None:
    created = _create_worker_session(client, git_repo)
    session = created["session"]

    with client.websocket_connect(f"/ws/sessions/{session['id']}/events") as websocket:
        start_response = client.post(f"/api/v1/sessions/{session['id']}/start", json={})
        assert start_response.status_code == 200

        start_event = _receive_event(websocket, "session.start")
        assert start_event["session_id"] == session["id"]
        assert start_event["payload"]["type"] == "start"

        message_response = client.post(
            f"/api/v1/sessions/{session['id']}/messages",
            json={"message": "run test malformed complete"},
        )
        assert message_response.status_code == 200

        tests_event = _receive_event(websocket, "session.tests_run")
        assert tests_event["payload"]["command"] == "pytest -q"
        malformed_event = _receive_event(websocket, "session.event_malformed")
        assert malformed_event["payload"]["validation_error"] is not None

    completed = _wait_for_status(client, session["id"], "completed")
    assert completed["exit_code"] == 0

    structured_response = client.get(f"/api/v1/sessions/{session['id']}/structured-events")
    assert structured_response.status_code == 200
    structured_events = structured_response.json()

    valid_types = [event["event_type"] for event in structured_events if event["status"] == "valid"]
    assert "start" in valid_types
    assert "progress" in valid_types
    assert "tests_run" in valid_types
    assert "complete" in valid_types

    malformed = next(event for event in structured_events if event["status"] == "malformed")
    assert malformed["raw_block"].startswith("[[EVENT]]")
    assert malformed["validation_error"] is not None

    events_response = client.get("/api/v1/events", params={"session_id": session["id"]})
    assert events_response.status_code == 200
    event_types = {event["event_type"] for event in events_response.json()}
    assert "session.start" in event_types
    assert "session.tests_run" in event_types
    assert "session.complete" in event_types
    assert "session.event_malformed" in event_types
