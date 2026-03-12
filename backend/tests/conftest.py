from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    repo_path = tmp_path / "sample-repo"
    repo_path.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_path, check=True, capture_output=True, text=True)
    (repo_path / "README.md").write_text("# Sample Repo\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo_path, check=True, capture_output=True, text=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test User",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-m",
            "Initial commit",
        ],
        cwd=repo_path,
        check=True,
        capture_output=True,
        text=True,
    )
    return repo_path


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        environment="test",
        debug=False,
        database_url=f"sqlite+pysqlite:///{tmp_path / 'test.db'}",
        redis_enabled=False,
        startup_check_connections=False,
        auto_create_schema=True,
        codex_runtime_command_template=str(tmp_path / ".missing-runtime" / "codex-{role}"),
        claude_code_runtime_command_template=str(tmp_path / ".missing-runtime" / "claude-{role}"),
        portfolio_automation_enabled=False,
        orchestrator_repos_root=tmp_path / ".orchestrator" / "repos",
        orchestrator_workspaces_root=tmp_path / ".orchestrator" / "workspaces",
    )


@pytest.fixture
def client(settings: Settings) -> TestClient:
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client
