from __future__ import annotations

from pathlib import Path


def test_project_task_session_review_flow(client, git_repo: Path) -> None:
    project_response = client.post(
        "/api/v1/projects",
        json={
            "name": "Rail Orchestrator",
            "repo_path": str(git_repo),
        },
    )
    assert project_response.status_code == 201
    project = project_response.json()

    projects_response = client.get("/api/v1/projects")
    assert projects_response.status_code == 200
    assert len(projects_response.json()) == 1

    task_response = client.post(
        "/api/v1/tasks",
        json={
            "project_id": project["id"],
            "title": "Implement worker planning",
            "description": "Create the first scoped task.",
            "acceptance_criteria": ["Persist task", "Emit task event"],
        },
    )
    assert task_response.status_code == 201
    task = task_response.json()

    session_response = client.post(
        "/api/v1/sessions",
        json={
            "project_id": project["id"],
            "task_id": task["id"],
            "role": "worker",
            "command_override": "codex worker --dry-run",
        },
    )
    assert session_response.status_code == 201
    session = session_response.json()
    assert session["status"] == "pending"
    assert session["workspace_path"].endswith(session["id"])
    assert session["branch_name"].startswith("orchestrator/worker/")
    assert Path(session["workspace_path"]).exists()

    workspace_response = client.get(f"/api/v1/sessions/{session['id']}/workspace")
    assert workspace_response.status_code == 200
    workspace = workspace_response.json()
    assert workspace["status"] == "ready"
    assert workspace["changed_files"] == []
    assert workspace["workspace_path"] == session["workspace_path"]

    reprovision_response = client.post(f"/api/v1/sessions/{session['id']}/workspace")
    assert reprovision_response.status_code == 200
    assert reprovision_response.json()["id"] == workspace["id"]

    workspace_readme = Path(workspace["workspace_path"]) / "README.md"
    workspace_readme.write_text("# Sample Repo\n\nworkspace change\n", encoding="utf-8")

    diff_response = client.get(f"/api/v1/workspaces/{workspace['id']}/diff")
    assert diff_response.status_code == 200
    diff_payload = diff_response.json()
    assert diff_payload["status"] == "dirty"
    assert diff_payload["changed_files"] == ["README.md"]
    assert "workspace change" in diff_payload["diff"]

    lint_response = client.post(
        f"/api/v1/workspaces/{workspace['id']}/lint",
        json={"command": "git diff --stat"},
    )
    assert lint_response.status_code == 200
    lint_result = lint_response.json()
    assert lint_result["kind"] == "lint"
    assert lint_result["returncode"] == 0
    assert "README.md" in lint_result["stdout"]

    tests_response = client.post(
        f"/api/v1/workspaces/{workspace['id']}/tests",
        json={"command": "git status --short"},
    )
    assert tests_response.status_code == 200
    tests_result = tests_response.json()
    assert tests_result["kind"] == "tests"
    assert tests_result["returncode"] == 0
    assert "README.md" in tests_result["stdout"]

    sessions_response = client.get("/api/v1/sessions", params={"project_id": project["id"]})
    assert sessions_response.status_code == 200
    assert len(sessions_response.json()) == 1

    review_response = client.post(
        "/api/v1/reviews",
        json={
            "project_id": project["id"],
            "task_id": task["id"],
            "requester_session_id": session["id"],
            "summary": "Ready for reviewer handoff.",
        },
    )
    assert review_response.status_code == 201

    reviews_response = client.get("/api/v1/reviews", params={"project_id": project["id"]})
    assert reviews_response.status_code == 200
    assert len(reviews_response.json()) == 1

    events_response = client.get("/api/v1/events", params={"project_id": project["id"]})
    assert events_response.status_code == 200
    event_types = {event["event_type"] for event in events_response.json()}
    assert "project.created" in event_types
    assert "task.created" in event_types
    assert "session.created" in event_types
    assert "worktree.provision_requested" in event_types
    assert "worktree.ready" in event_types
    assert "review.requested" in event_types


def test_project_event_websocket_stream(client, git_repo: Path) -> None:
    project_response = client.post(
        "/api/v1/projects",
        json={
            "name": "Streaming Project",
            "repo_path": str(git_repo),
        },
    )
    project = project_response.json()

    with client.websocket_connect(f"/ws/projects/{project['id']}/events") as websocket:
        task_response = client.post(
            "/api/v1/tasks",
            json={
                "project_id": project["id"],
                "title": "Stream one task event",
                "description": "Exercise live project updates.",
            },
        )
        assert task_response.status_code == 201
        message = websocket.receive_json()
        assert message["event_type"] == "task.created"
        assert message["project_id"] == project["id"]
