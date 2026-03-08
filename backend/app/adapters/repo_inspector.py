from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.core.errors import ValidationError
from app.services.command_runner import CommandRunner


@dataclass(slots=True)
class RepoInfo:
    repo_path: str
    default_branch: str
    is_git_repository: bool
    current_commit: str | None = None


class RepoInspectorAdapter:
    def __init__(self, command_runner: CommandRunner) -> None:
        self.command_runner = command_runner

    def inspect(self, repo_path: str, fallback_branch: str = "main") -> RepoInfo:
        path = Path(repo_path).expanduser().resolve()
        if not path.exists() or not path.is_dir():
            raise ValidationError(f"Repository path does not exist: {path}")

        git_check = self.command_runner.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=path,
            check=False,
        )
        if git_check.returncode != 0:
            raise ValidationError(f"Repository path is not a git repository: {path}")

        branch_result = self.command_runner.run(
            ["git", "branch", "--show-current"],
            cwd=path,
            check=False,
        )
        commit_result = self.command_runner.run(
            ["git", "rev-parse", "HEAD"],
            cwd=path,
            check=False,
        )

        branch_name = branch_result.stdout.strip() or fallback_branch
        current_commit = commit_result.stdout.strip() or None

        return RepoInfo(
            repo_path=str(path),
            default_branch=branch_name,
            is_git_repository=True,
            current_commit=current_commit,
        )
