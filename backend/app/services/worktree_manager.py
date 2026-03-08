from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from app.core.config import Settings
from app.core.enums import SessionRole, WorkspaceStatus


@dataclass(slots=True)
class WorkspacePlan:
    branch_name: str
    workspace_path: str
    base_branch: str
    status: WorkspaceStatus


class WorktreeManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

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
            Path(self.settings.orchestrator_workspaces_root)
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
