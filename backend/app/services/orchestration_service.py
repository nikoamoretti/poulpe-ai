from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.core.enums import SessionRole
from app.services.session_supervisor import SessionLaunchPlan, SessionSupervisor
from app.services.worktree_manager import WorkspacePlan, WorktreeManager


@dataclass(slots=True)
class WorkerSessionBlueprint:
    project_id: UUID
    task_id: UUID
    role: SessionRole
    launch_plan: SessionLaunchPlan
    workspace_plan: WorkspacePlan | None


class OrchestratorService:
    def __init__(
        self,
        worktree_manager: WorktreeManager,
        session_supervisor: SessionSupervisor,
    ) -> None:
        self.worktree_manager = worktree_manager
        self.session_supervisor = session_supervisor

    def describe_flow(self) -> list[str]:
        return [
            "register project",
            "create top-level task",
            "create manager session",
            "create worker sessions with planned workspaces",
            "ingest structured events",
            "request reviewer handoff",
            "await human approval before merge-ready",
        ]
