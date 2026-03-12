from __future__ import annotations

import os
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app


def _create_project(client: TestClient, git_repo: Path) -> dict:
    response = client.post(
        "/api/v1/projects",
        json={"name": "Runtime Status Project", "repo_path": str(git_repo)},
    )
    assert response.status_code == 201
    return response.json()


def _create_task(client: TestClient, project_id: str) -> dict:
    response = client.post(
        "/api/v1/tasks",
        json={
            "project_id": project_id,
            "title": "Inspect runtime status",
            "description": "Verify runtime reporting and startup behavior.",
        },
    )
    assert response.status_code == 201
    return response.json()


def test_runtime_status_reports_simulated_auto_when_no_real_runtime(settings, git_repo: Path) -> None:
    custom_settings = settings.model_copy(
        update={
            "codex_runtime_command_template": str(git_repo / "missing-codex" / "{role}"),
            "claude_code_runtime_command_template": str(git_repo / "missing-claude" / "{role}"),
        }
    )

    with TestClient(create_app(custom_settings)) as client:
        response = client.get("/api/v1/runtime", params={"role": "worker"})
        assert response.status_code == 200
        payload = response.json()

    assert payload["selections"]["auto"]["simulated"] is True
    assert payload["selections"]["auto"]["can_start"] is True
    assert payload["selections"]["codex"]["disconnected"] is True
    assert payload["selections"]["codex"]["can_start"] is False
    assert payload["selections"]["claude_code"]["disconnected"] is True
    assert payload["supported_real_providers"] == []


def test_explicit_real_runtime_is_marked_disconnected_when_unavailable(settings, git_repo: Path) -> None:
    custom_settings = settings.model_copy(
        update={
            "codex_runtime_command_template": str(git_repo / "missing-codex" / "{role}"),
            "claude_code_runtime_command_template": str(git_repo / "missing-claude" / "{role}"),
        }
    )

    with TestClient(create_app(custom_settings)) as client:
        project = _create_project(client, git_repo)
        task = _create_task(client, project["id"])
        session_response = client.post(
            "/api/v1/sessions",
            json={
                "project_id": project["id"],
                "task_id": task["id"],
                "role": "worker",
                "runtime_preference": "codex",
            },
        )
        assert session_response.status_code == 201
        session = session_response.json()

        start_response = client.post(f"/api/v1/sessions/{session['id']}/start", json={})
        assert start_response.status_code == 400
        assert "Codex CLI is not installed or not on PATH" in start_response.json()["detail"]

    assert session["status"] == "blocked"
    assert session["blocked_reason"] == "runtime_disconnected"
    assert session["runtime"]["disconnected"] is True
    assert session["runtime"]["simulated"] is False
    assert session["runtime"]["can_start"] is False


def test_codex_runtime_requires_login(settings, git_repo: Path, tmp_path: Path, monkeypatch) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    codex_script = bin_dir / "codex"
    codex_script.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"login\" ] && [ \"$2\" = \"status\" ]; then\n"
        "  echo 'Not logged in' >&2\n"
        "  exit 1\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    codex_script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    custom_settings = settings.model_copy(
        update={
            "codex_runtime_command_template": "codex {role}",
            "claude_code_runtime_command_template": str(git_repo / "missing-claude" / "{role}"),
        }
    )

    with TestClient(create_app(custom_settings)) as client:
        response = client.get("/api/v1/runtime", params={"role": "worker"})
        assert response.status_code == 200
        payload = response.json()

    assert payload["selections"]["codex"]["available"] is False
    assert payload["selections"]["codex"]["disconnected"] is True
    assert "Run `codex login`" in payload["selections"]["codex"]["summary"]


def test_explicit_real_runtime_is_reported_available_when_binary_exists(
    settings,
    git_repo: Path,
    tmp_path: Path,
    monkeypatch,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    codex_script = bin_dir / "codex"
    codex_script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    codex_script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    custom_settings = settings.model_copy(
        update={
            "codex_runtime_command_template": "codex {role}",
            "claude_code_runtime_command_template": str(git_repo / "missing-claude" / "{role}"),
        }
    )

    with TestClient(create_app(custom_settings)) as client:
        runtime_response = client.get("/api/v1/runtime", params={"role": "worker"})
        assert runtime_response.status_code == 200
        runtime_payload = runtime_response.json()

        project = _create_project(client, git_repo)
        task = _create_task(client, project["id"])
        session_response = client.post(
            "/api/v1/sessions",
            json={
                "project_id": project["id"],
                "task_id": task["id"],
                "role": "worker",
                "runtime_preference": "codex",
            },
        )
        assert session_response.status_code == 201
        session = session_response.json()

    assert runtime_payload["selections"]["codex"]["available"] is True
    assert runtime_payload["selections"]["codex"]["disconnected"] is False
    assert session["status"] == "pending"
    assert session["runtime"]["available"] is True
    assert session["runtime"]["simulated"] is False
    assert session["runtime"]["resolved_provider"] == "codex"
