from __future__ import annotations

from pathlib import Path


def _create_project(client, git_repo: Path, name: str = "Review Project") -> dict:
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


def _create_session(client, project_id: str, *, role: str, task_id: str | None = None) -> dict:
    payload = {"project_id": project_id, "role": role}
    if task_id is not None:
        payload["task_id"] = task_id
    response = client.post("/api/v1/sessions", json=payload)
    assert response.status_code == 201
    return response.json()


def test_review_creation_collects_diff_checks_and_packet(client, git_repo: Path) -> None:
    project = _create_project(client, git_repo)
    task = _create_task(client, project["id"], "Review this worker output")
    worker_session = _create_session(client, project["id"], role="worker", task_id=task["id"])
    reviewer_session = _create_session(client, project["id"], role="reviewer")

    workspace_path = Path(worker_session["workspace_path"])
    (workspace_path / "README.md").write_text("# Sample Repo\n\nreview me\n", encoding="utf-8")

    review_response = client.post(
        "/api/v1/reviews",
        json={
            "project_id": project["id"],
            "task_id": task["id"],
            "reviewer_session_id": reviewer_session["id"],
            "lint_command": "git diff --stat",
            "test_command": "git status --short",
            "summary": "Queue a reviewer handoff.",
        },
    )
    assert review_response.status_code == 201
    review = review_response.json()

    assert review["status"] == "running"
    assert review["diff"]["changed_files"] == ["README.md"]
    assert review["lint"]["status"] == "passed"
    assert review["tests"]["status"] == "passed"
    assert review["review_packet"]["task"]["id"] == task["id"]
    assert review["review_packet"]["worker_session"]["id"] == worker_session["id"]
    assert review["prompt_template_path"].endswith("prompts/reviewer.md")
    assert review["approval"]["merge_ready"] is False


def test_review_requires_reviewer_approval_before_merge_ready(client, git_repo: Path) -> None:
    project = _create_project(client, git_repo, name="Approval Gate Project")
    task = _create_task(client, project["id"], "Approve before merge-ready")
    worker_session = _create_session(client, project["id"], role="worker", task_id=task["id"])
    workspace_path = Path(worker_session["workspace_path"])
    (workspace_path / "README.md").write_text("# Sample Repo\n\napproval\n", encoding="utf-8")

    review_response = client.post(
        "/api/v1/reviews",
        json={
            "project_id": project["id"],
            "task_id": task["id"],
        },
    )
    assert review_response.status_code == 201
    review = review_response.json()

    premature_merge_ready = client.post(
        f"/api/v1/reviews/{review['id']}/merge-ready",
        json={"approved_by": "human@example.com"},
    )
    assert premature_merge_ready.status_code == 400

    approve_response = client.post(
        f"/api/v1/reviews/{review['id']}/approve",
        json={"note": "Looks good from reviewer side."},
    )
    assert approve_response.status_code == 200
    approved_review = approve_response.json()
    assert approved_review["status"] == "approved"

    merge_ready_response = client.post(
        f"/api/v1/reviews/{review['id']}/merge-ready",
        json={"approved_by": "human@example.com", "note": "Approved for merge queue."},
    )
    assert merge_ready_response.status_code == 200
    merge_ready_review = merge_ready_response.json()
    assert merge_ready_review["approval"]["merge_ready"] is True
    assert merge_ready_review["approval"]["human_approved_by"] == "human@example.com"

    task_response = client.get(f"/api/v1/tasks/{task['id']}")
    assert task_response.status_code == 200
    assert task_response.json()["status"] == "done"


def test_review_reject_can_mark_needs_changes(client, git_repo: Path) -> None:
    project = _create_project(client, git_repo, name="Needs Changes Project")
    task = _create_task(client, project["id"], "Reject this review")
    worker_session = _create_session(client, project["id"], role="worker", task_id=task["id"])
    workspace_path = Path(worker_session["workspace_path"])
    (workspace_path / "README.md").write_text("# Sample Repo\n\nneeds changes\n", encoding="utf-8")

    review_response = client.post(
        "/api/v1/reviews",
        json={
            "project_id": project["id"],
            "task_id": task["id"],
        },
    )
    assert review_response.status_code == 201
    review = review_response.json()

    reject_response = client.post(
        f"/api/v1/reviews/{review['id']}/reject",
        json={"note": "Coverage is missing for the new path.", "status": "needs_changes"},
    )
    assert reject_response.status_code == 200
    rejected_review = reject_response.json()
    assert rejected_review["status"] == "needs_changes"
    assert rejected_review["reviewer_notes"] == "Coverage is missing for the new path."
