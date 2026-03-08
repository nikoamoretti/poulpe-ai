from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from app.core.errors import ConflictError, ValidationError
from app.core.config import Settings
from app.core.enums import SessionRole, WorkspaceStatus
from app.services.command_runner import CommandRunner


@dataclass(slots=True)
class WorkspacePlan:
    branch_name: str
    workspace_path: str
    base_branch: str
    status: WorkspaceStatus


@dataclass(slots=True)
class WorkspaceState:
    branch_name: str
    workspace_path: str
    base_branch: str
    base_commit: str
    head_commit: str
    status: WorkspaceStatus
    changed_files: list[str]


class WorktreeManager:
    def __init__(self, settings: Settings, command_runner: CommandRunner) -> None:
        self.settings = settings
        self.command_runner = command_runner

    def plan_workspace(
        self,
        *,
        project_slug: str,
        role: SessionRole,
        task_id: UUID,
        session_id: UUID,
        base_branch: str,
    ) -> WorkspacePlan:
        workspace_dir = (
            Path(self.settings.orchestrator_workspaces_root).expanduser().resolve()
            / project_slug
            / str(task_id)
            / str(session_id)
        )
        branch_name = f"orchestrator/{role.value}/{str(task_id)[:8]}/{str(session_id)[:8]}"
        return WorkspacePlan(
            branch_name=branch_name,
            workspace_path=str(workspace_dir),
            base_branch=base_branch,
            status=WorkspaceStatus.PLANNED,
        )

    def create_worktree(
        self,
        *,
        repo_path: str,
        workspace_path: str,
        branch_name: str,
        base_branch: str,
    ) -> WorkspaceState:
        repo_dir = self._resolve_repo_path(repo_path)
        workspace_dir = self._resolve_workspace_path(workspace_path)

        self._ensure_git_repo(repo_dir)
        self._validate_branch_name(repo_dir, branch_name)
        base_commit = self._resolve_git_ref(repo_dir, base_branch)

        if workspace_dir.exists():
            if any(workspace_dir.iterdir()):
                try:
                    existing_branch = self._current_branch(workspace_dir)
                except ValidationError:
                    existing_branch = None
                if existing_branch == branch_name:
                    return self.inspect_workspace(
                        repo_path=str(repo_dir),
                        workspace_path=str(workspace_dir),
                        base_branch=base_branch,
                        base_commit=base_commit,
                        expected_branch=branch_name,
                    )
                raise ValidationError(
                    f"Workspace path already exists and is not an empty orchestrator worktree: {workspace_dir}"
                )
        else:
            workspace_dir.parent.mkdir(parents=True, exist_ok=True)

        existing_worktree_path = self._branch_worktree(repo_dir, branch_name)
        if existing_worktree_path is not None and existing_worktree_path != workspace_dir:
            raise ConflictError(
                f"Branch {branch_name} is already attached to a different worktree: {existing_worktree_path}"
            )

        if existing_worktree_path is None:
            if self._branch_exists(repo_dir, branch_name):
                self.command_runner.run(
                    ["git", "worktree", "add", str(workspace_dir), branch_name],
                    cwd=repo_dir,
                )
            else:
                self.command_runner.run(
                    ["git", "worktree", "add", "-b", branch_name, str(workspace_dir), base_branch],
                    cwd=repo_dir,
                )

        return self.inspect_workspace(
            repo_path=str(repo_dir),
            workspace_path=str(workspace_dir),
            base_branch=base_branch,
            base_commit=base_commit,
            expected_branch=branch_name,
        )

    def remove_worktree(
        self,
        *,
        repo_path: str,
        workspace_path: str,
        branch_name: str | None = None,
        delete_branch: bool = False,
    ) -> None:
        repo_dir = self._resolve_repo_path(repo_path)
        workspace_dir = self._resolve_workspace_path(workspace_path)
        self._ensure_git_repo(repo_dir)

        if workspace_dir.exists():
            self.command_runner.run(
                ["git", "worktree", "remove", "--force", str(workspace_dir)],
                cwd=repo_dir,
                allow_destructive=True,
            )
        self.command_runner.run(
            ["git", "worktree", "prune"],
            cwd=repo_dir,
            allow_destructive=True,
        )

        if delete_branch and branch_name and self._branch_exists(repo_dir, branch_name):
            self.command_runner.run(
                ["git", "branch", "-D", branch_name],
                cwd=repo_dir,
                allow_destructive=True,
            )

    def get_diff(
        self,
        *,
        repo_path: str,
        workspace_path: str,
        base_ref: str,
    ) -> str:
        self._ensure_workspace_ready(repo_path=repo_path, workspace_path=workspace_path)
        self._resolve_git_ref(Path(workspace_path), base_ref)

        tracked = self.command_runner.run(
            ["git", "diff", "--no-ext-diff", "--binary", base_ref, "--"],
            cwd=workspace_path,
            check=False,
        ).stdout.rstrip()

        parts = [tracked] if tracked else []
        for untracked_file in self._untracked_files(workspace_path):
            diff_result = self.command_runner.run(
                ["git", "diff", "--no-ext-diff", "--binary", "--no-index", "--", "/dev/null", untracked_file],
                cwd=workspace_path,
                check=False,
            )
            rendered = diff_result.stdout.rstrip()
            if rendered:
                parts.append(rendered)
        return "\n\n".join(parts).strip()

    def get_changed_files(
        self,
        *,
        repo_path: str,
        workspace_path: str,
        base_ref: str,
    ) -> list[str]:
        self._ensure_workspace_ready(repo_path=repo_path, workspace_path=workspace_path)
        self._resolve_git_ref(Path(workspace_path), base_ref)

        tracked_output = self.command_runner.run(
            ["git", "diff", "--name-only", base_ref, "--"],
            cwd=workspace_path,
            check=False,
        ).stdout
        tracked_files = {line.strip() for line in tracked_output.splitlines() if line.strip()}
        tracked_files.update(self._untracked_files(workspace_path))
        return sorted(tracked_files)

    def checkout_branch(
        self,
        *,
        workspace_path: str,
        branch_name: str,
        create: bool = False,
        start_point: str | None = None,
    ) -> str:
        workspace_dir = self._resolve_workspace_path(workspace_path)
        self._ensure_git_repo(workspace_dir)
        self._validate_branch_name(workspace_dir, branch_name)

        command = ["git", "checkout"]
        if create:
            command.extend(["-b", branch_name])
            if start_point:
                command.append(start_point)
        else:
            command.append(branch_name)

        self.command_runner.run(command, cwd=workspace_dir)
        return self._current_branch(workspace_dir)

    def inspect_workspace(
        self,
        *,
        repo_path: str,
        workspace_path: str,
        base_branch: str,
        base_commit: str,
        expected_branch: str | None = None,
    ) -> WorkspaceState:
        repo_dir = self._resolve_repo_path(repo_path)
        workspace_dir = self._resolve_workspace_path(workspace_path)
        self._ensure_git_repo(repo_dir)
        self._ensure_git_repo(workspace_dir)

        branch_name = self._current_branch(workspace_dir)
        if expected_branch is not None and branch_name != expected_branch:
            raise ValidationError(
                f"Workspace branch mismatch. Expected {expected_branch}, found {branch_name} at {workspace_dir}"
            )

        head_commit = self._resolve_git_ref(workspace_dir, "HEAD")
        changed_files = self.get_changed_files(
            repo_path=str(repo_dir),
            workspace_path=str(workspace_dir),
            base_ref=base_commit,
        )
        status = WorkspaceStatus.DIRTY if changed_files else WorkspaceStatus.READY
        return WorkspaceState(
            branch_name=branch_name,
            workspace_path=str(workspace_dir),
            base_branch=base_branch,
            base_commit=base_commit,
            head_commit=head_commit,
            status=status,
            changed_files=changed_files,
        )

    def _resolve_repo_path(self, repo_path: str) -> Path:
        path = Path(repo_path).expanduser().resolve()
        if not path.exists() or not path.is_dir():
            raise ValidationError(f"Repository path does not exist: {path}")
        return path

    def _resolve_workspace_path(self, workspace_path: str) -> Path:
        root = Path(self.settings.orchestrator_workspaces_root).expanduser().resolve()
        path = Path(workspace_path).expanduser().resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ValidationError(
                f"Workspace path must live under {root}: {path}"
            ) from exc
        return path

    def _ensure_git_repo(self, repo_path: Path) -> None:
        result = self.command_runner.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=repo_path,
            check=False,
        )
        if result.returncode != 0:
            raise ValidationError(f"Path is not a git repository: {repo_path}")

    def _resolve_git_ref(self, cwd: Path, ref: str) -> str:
        result = self.command_runner.run(
            ["git", "rev-parse", "--verify", f"{ref}^{{commit}}"],
            cwd=cwd,
            check=False,
        )
        if result.returncode != 0:
            raise ValidationError(f"Git reference does not exist: {ref}")
        return result.stdout.strip()

    def _validate_branch_name(self, cwd: Path, branch_name: str) -> None:
        result = self.command_runner.run(
            ["git", "check-ref-format", "--branch", branch_name],
            cwd=cwd,
            check=False,
        )
        if result.returncode != 0:
            raise ValidationError(f"Invalid branch name: {branch_name}")

    def _branch_exists(self, repo_dir: Path, branch_name: str) -> bool:
        result = self.command_runner.run(
            ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch_name}"],
            cwd=repo_dir,
            check=False,
        )
        return result.returncode == 0

    def _branch_worktree(self, repo_dir: Path, branch_name: str) -> Path | None:
        output = self.command_runner.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=repo_dir,
            check=False,
        ).stdout

        current_path: Path | None = None
        current_branch: str | None = None
        for line in [*output.splitlines(), ""]:
            if not line.strip():
                if current_path is not None and current_branch == branch_name:
                    return current_path
                current_path = None
                current_branch = None
                continue
            if line.startswith("worktree "):
                current_path = Path(line.removeprefix("worktree ").strip()).expanduser().resolve()
            elif line.startswith("branch "):
                current_branch = line.removeprefix("branch ").strip().removeprefix("refs/heads/")
        return None

    def _current_branch(self, workspace_dir: Path) -> str:
        branch = self.command_runner.run(
            ["git", "branch", "--show-current"],
            cwd=workspace_dir,
            check=False,
        ).stdout.strip()
        if not branch:
            raise ValidationError(f"Workspace is not on a branch: {workspace_dir}")
        return branch

    def _untracked_files(self, workspace_path: str) -> list[str]:
        output = self.command_runner.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=workspace_path,
            check=False,
        ).stdout
        return [line.strip() for line in output.splitlines() if line.strip()]

    def _ensure_workspace_ready(self, *, repo_path: str, workspace_path: str) -> None:
        self._ensure_git_repo(self._resolve_repo_path(repo_path))
        self._ensure_git_repo(self._resolve_workspace_path(workspace_path))
