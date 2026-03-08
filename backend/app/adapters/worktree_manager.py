from dataclasses import dataclass


@dataclass(slots=True)
class PlannedWorktree:
    branch_name: str
    worktree_path: str
    base_branch: str


class WorktreeManagerAdapter:
    """Create and clean up git worktrees for worker sessions."""

    def create(self, plan: PlannedWorktree) -> None:
        raise NotImplementedError("Git worktree creation is not implemented yet.")

    def cleanup(self, worktree_path: str) -> None:
        raise NotImplementedError("Git worktree cleanup is not implemented yet.")

