from __future__ import annotations

import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from app.dev.seed import seed_demo_environment
from app.main import create_app


def test_demo_seed_is_idempotent(settings) -> None:
    first = seed_demo_environment(settings)
    second = seed_demo_environment(settings)

    assert first.seeded is True
    assert second.seeded is True
    assert first.project_id == second.project_id
    assert first.task_ids == second.task_ids
    assert first.session_ids == second.session_ids
    assert first.review_ids == second.review_ids

    repo_path = Path(first.repo_path)
    assert repo_path.exists()
    head_result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_path,
        check=True,
        capture_output=True,
        text=True,
    )
    assert head_result.stdout.strip()

    with TestClient(create_app(settings)) as client:
        project = client.get("/api/v1/projects").json()[0]
        tasks = client.get("/api/v1/tasks", params={"project_id": project["id"]}).json()
        sessions = client.get("/api/v1/sessions", params={"project_id": project["id"]}).json()
        reviews = client.get("/api/v1/reviews", params={"project_id": project["id"]}).json()

    assert len(tasks) == 3
    assert len(sessions) == 5
    assert len(reviews) == 1
    assert {task["status"] for task in tasks} == {"in_progress", "review", "blocked"}
    assert reviews[0]["diff"]["changed_files"] == ["frontend/components/ReviewPanel.tsx"]


def test_app_startup_can_auto_seed_demo_data(settings) -> None:
    seeded_settings = settings.model_copy(
        update={
            "seed_demo_data": True,
            "seed_demo_data_if_empty": True,
        }
    )

    with TestClient(create_app(seeded_settings)) as client:
        projects_response = client.get("/api/v1/projects")
        assert projects_response.status_code == 200
        projects = projects_response.json()
        assert len(projects) == 1

        project_id = projects[0]["id"]
        sessions_response = client.get("/api/v1/sessions", params={"project_id": project_id})
        reviews_response = client.get("/api/v1/reviews", params={"project_id": project_id})

    assert sessions_response.status_code == 200
    assert len(sessions_response.json()) == 5
    assert reviews_response.status_code == 200
    assert len(reviews_response.json()) == 1
