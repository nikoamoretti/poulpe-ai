from __future__ import annotations

from pathlib import Path

from app.core.config import Settings
from app.core.enums import WorkspaceStatus
from app.services.command_runner import CommandRunner
from app.services.worktree_manager import WorktreeManager


def test_worktree_manager_creates_diffs_and_cleans_up(tmp_path: Path, git_repo: Path) -> None:
    settings = Settings(
        environment="test",
        debug=False,
        database_url=f"sqlite+pysqlite:///{tmp_path / 'test.db'}",
        redis_enabled=False,
        startup_check_connections=False,
        auto_create_schema=True,
        orchestrator_repos_root=tmp_path / ".orchestrator" / "repos",
        orchestrator_workspaces_root=tmp_path / ".orchestrator" / "workspaces",
    )
    settings.ensure_local_dirs()

    runner = CommandRunner()
    manager = WorktreeManager(settings, runner)
    workspace_path = settings.orchestrator_workspaces_root / "sample-project" / "task-1" / "session-1"

    state = manager.create_worktree(
        repo_path=str(git_repo),
        workspace_path=str(workspace_path),
        branch_name="orchestrator/worker/task-1/session-1",
        base_branch="main",
    )

    assert state.status == WorkspaceStatus.READY
    assert workspace_path.exists()
    assert (workspace_path / "README.md").exists()

    (workspace_path / "README.md").write_text("# Sample Repo\n\nchanged\n", encoding="utf-8")
    (workspace_path / "notes.txt").write_text("new file\n", encoding="utf-8")

    changed_files = manager.get_changed_files(
        repo_path=str(git_repo),
        workspace_path=str(workspace_path),
        base_ref=state.base_commit,
    )
    assert changed_files == ["README.md", "notes.txt"]

    diff = manager.get_diff(
        repo_path=str(git_repo),
        workspace_path=str(workspace_path),
        base_ref=state.base_commit,
    )
    assert "README.md" in diff
    assert "notes.txt" in diff
    assert "changed" in diff

    branch_name = manager.checkout_branch(
        workspace_path=str(workspace_path),
        branch_name="orchestrator/reviewer/task-1/session-1",
        create=True,
        start_point="HEAD",
    )
    assert branch_name == "orchestrator/reviewer/task-1/session-1"

    manager.remove_worktree(
        repo_path=str(git_repo),
        workspace_path=str(workspace_path),
        branch_name="orchestrator/reviewer/task-1/session-1",
        delete_branch=True,
    )
    assert not workspace_path.exists()
