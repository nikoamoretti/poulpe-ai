from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.repo_inspector import RepoInspectorAdapter
from app.core.enums import EventCategory, EventLevel, SessionRole, SessionStatus, WorkspaceStatus
from app.core.errors import AppError, InfrastructureError, NotFoundError, ValidationError
from app.models.project import Project
from app.models.session import Session as SessionModel
from app.models.task import Task
from app.models.workspace import Workspace
from app.schemas.event import EventCreate, EventSourceRef
from app.schemas.workspace import (
    WorkspaceCommandRead,
    WorkspaceCommandRequest,
    WorkspaceDiffRead,
    WorkspaceStatusRead,
)
from app.services.command_runner import CommandRunner
from app.services.event_service import EventService
from app.services.worktree_manager import WorktreeManager

logger = logging.getLogger(__name__)


class WorkspaceService:
    def __init__(
        self,
        db: Session,
        event_service: EventService,
        worktree_manager: WorktreeManager,
        repo_inspector: RepoInspectorAdapter,
        command_runner: CommandRunner,
    ) -> None:
        self.db = db
        self.event_service = event_service
        self.worktree_manager = worktree_manager
        self.repo_inspector = repo_inspector
        self.command_runner = command_runner

    def get_workspace(self, workspace_id: UUID) -> WorkspaceStatusRead:
        workspace = self._get_workspace(workspace_id)
        project = self._get_project(workspace.project_id)
        return self._sync_workspace(workspace, project)

    def get_session_workspace(self, session_id: UUID) -> WorkspaceStatusRead:
        session = self._get_session(session_id)
        workspace = self._get_workspace_for_session(session.id)
        project = self._get_project(workspace.project_id)
        return self._sync_workspace(workspace, project)

    def provision_session_workspace(self, session_id: UUID) -> WorkspaceStatusRead:
        session = self._get_session(session_id)
        if session.role != SessionRole.WORKER or session.task_id is None:
            raise ValidationError("Only worker sessions with a task_id can provision a workspace.")

        project = self._get_project(session.project_id)
        task = self._get_task(session.task_id)
        repo_info = self.repo_inspector.inspect(project.repo_path, project.default_branch)
        workspace = self._ensure_workspace_record(session=session, task=task, project=project, repo_commit=repo_info.current_commit)

        workspace_path = Path(workspace.workspace_path)
        if workspace.status in {WorkspaceStatus.READY, WorkspaceStatus.DIRTY} and workspace_path.exists():
            return self._sync_workspace(workspace, project)

        metadata = dict(workspace.metadata_json)
        metadata.setdefault(
            "ownership",
            {
                "session_id": str(session.id),
                "path_lock_owner": str(session.id),
                "path_locks": [],
            },
        )
        metadata["repo_path"] = project.repo_path
        metadata["project_slug"] = project.slug
        metadata["provision_requested_at"] = datetime.now(UTC).isoformat()

        workspace.status = WorkspaceStatus.PROVISIONING
        workspace.metadata_json = metadata
        self.db.commit()
        self.db.refresh(workspace)

        self.event_service.record_event(
            EventCreate(
                category=EventCategory.WORKSPACE,
                event_type="worktree.provision_requested",
                level=EventLevel.INFO,
                source=EventSourceRef(kind="service", role=session.role, id="workspace-service"),
                project_id=project.id,
                task_id=session.task_id,
                session_id=session.id,
                payload={
                    "workspace_id": str(workspace.id),
                    "branch_name": workspace.branch_name,
                    "workspace_path": workspace.workspace_path,
                },
            )
        )

        try:
            snapshot = self.worktree_manager.create_worktree(
                repo_path=project.repo_path,
                workspace_path=workspace.workspace_path,
                branch_name=workspace.branch_name,
                base_branch=workspace.base_branch,
            )
        except Exception as exc:
            return self._handle_provision_failure(
                exc=exc,
                workspace=workspace,
                session=session,
                project=project,
            )

        workspace.base_commit = snapshot.base_commit
        workspace.head_commit = snapshot.head_commit
        workspace.status = snapshot.status
        session.branch_name = snapshot.branch_name
        session.workspace_path = snapshot.workspace_path

        metadata["last_provisioned_at"] = datetime.now(UTC).isoformat()
        metadata.pop("last_error", None)
        workspace.metadata_json = metadata
        self.db.commit()
        self.db.refresh(workspace)
        self.db.refresh(session)

        self.event_service.record_event(
            EventCreate(
                category=EventCategory.WORKSPACE,
                event_type="worktree.ready",
                level=EventLevel.INFO,
                source=EventSourceRef(kind="service", role=session.role, id="workspace-service"),
                project_id=project.id,
                task_id=session.task_id,
                session_id=session.id,
                payload={
                    "workspace_id": str(workspace.id),
                    "branch_name": workspace.branch_name,
                    "workspace_path": workspace.workspace_path,
                    "base_commit": workspace.base_commit,
                    "head_commit": workspace.head_commit,
                },
            )
        )
        logger.info(
            "workspace ready for session=%s branch=%s path=%s",
            session.id,
            workspace.branch_name,
            workspace.workspace_path,
        )
        return self._workspace_status_read(workspace, snapshot.changed_files)

    def get_diff(self, workspace_id: UUID) -> WorkspaceDiffRead:
        workspace = self._get_workspace(workspace_id)
        project = self._get_project(workspace.project_id)
        snapshot = self._sync_workspace(workspace, project)
        self._ensure_workspace_is_ready(workspace)

        diff = self.worktree_manager.get_diff(
            repo_path=project.repo_path,
            workspace_path=workspace.workspace_path,
            base_ref=workspace.base_commit,
        )
        return WorkspaceDiffRead(
            workspace_id=workspace.id,
            session_id=workspace.session_id,
            branch_name=workspace.branch_name,
            base_branch=workspace.base_branch,
            base_commit=workspace.base_commit,
            head_commit=workspace.head_commit,
            workspace_path=workspace.workspace_path,
            status=workspace.status,
            changed_files=snapshot.changed_files,
            diff=diff,
        )

    def run_command(self, workspace_id: UUID, payload: WorkspaceCommandRequest, *, kind: str = "command") -> WorkspaceCommandRead:
        workspace = self._get_workspace(workspace_id)
        project = self._get_project(workspace.project_id)
        self._sync_workspace(workspace, project)
        self._ensure_workspace_is_ready(workspace)

        result = self.command_runner.run_shell(
            payload.command,
            cwd=workspace.workspace_path,
            env=payload.env,
            timeout=payload.timeout_seconds,
            check=False,
        )
        snapshot = self._sync_workspace(workspace, project)

        metadata = dict(workspace.metadata_json)
        metadata[f"last_{kind}"] = {
            "command": payload.command,
            "returncode": result.returncode,
            "timed_out": result.timed_out,
            "duration_ms": result.duration_ms,
            "executed_at": datetime.now(UTC).isoformat(),
        }
        workspace.metadata_json = metadata
        self.db.commit()
        self.db.refresh(workspace)

        self.event_service.record_event(
            EventCreate(
                category=EventCategory.WORKSPACE,
                event_type=f"workspace.{kind}.completed",
                level=EventLevel.INFO if result.returncode == 0 else EventLevel.WARN,
                source=EventSourceRef(kind="service", id="workspace-service"),
                project_id=project.id,
                task_id=self._get_session(workspace.session_id).task_id if workspace.session_id else None,
                session_id=workspace.session_id,
                payload={
                    "workspace_id": str(workspace.id),
                    "command": payload.command,
                    "returncode": result.returncode,
                    "timed_out": result.timed_out,
                },
            )
        )
        logger.info(
            "workspace %s ran %s command=%r returncode=%s timed_out=%s",
            workspace.id,
            kind,
            payload.command,
            result.returncode,
            result.timed_out,
        )

        return WorkspaceCommandRead(
            workspace_id=workspace.id,
            session_id=workspace.session_id,
            kind=kind,
            command=payload.command,
            cwd=result.cwd or workspace.workspace_path,
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            timed_out=result.timed_out,
            duration_ms=result.duration_ms,
            status=workspace.status,
            changed_files=snapshot.changed_files,
            executed_at=datetime.now(UTC),
        )

    def run_lint(self, workspace_id: UUID, payload: WorkspaceCommandRequest) -> WorkspaceCommandRead:
        return self.run_command(workspace_id, payload, kind="lint")

    def run_tests(self, workspace_id: UUID, payload: WorkspaceCommandRequest) -> WorkspaceCommandRead:
        return self.run_command(workspace_id, payload, kind="tests")

    def _ensure_workspace_record(
        self,
        *,
        session: SessionModel,
        task: Task,
        project: Project,
        repo_commit: str | None,
    ) -> Workspace:
        existing = self.db.scalar(select(Workspace).where(Workspace.session_id == session.id))
        if existing is not None:
            return existing

        plan = self.worktree_manager.plan_workspace(
            project_slug=project.slug,
            role=session.role,
            task_id=task.id,
            session_id=session.id,
            base_branch=project.default_branch,
        )
        workspace = Workspace(
            project_id=project.id,
            session_id=session.id,
            branch_name=plan.branch_name,
            base_branch=plan.base_branch,
            base_commit=repo_commit or "",
            head_commit=repo_commit,
            workspace_path=plan.workspace_path,
            status=plan.status,
            metadata_json={},
        )
        self.db.add(workspace)
        session.branch_name = plan.branch_name
        session.workspace_path = plan.workspace_path
        self.db.commit()
        self.db.refresh(workspace)
        self.db.refresh(session)
        return workspace

    def _sync_workspace(self, workspace: Workspace, project: Project) -> WorkspaceStatusRead:
        workspace_path = Path(workspace.workspace_path)
        if workspace.status == WorkspaceStatus.PLANNED and not workspace_path.exists():
            return self._workspace_status_read(workspace, [])

        if not workspace_path.exists():
            metadata = dict(workspace.metadata_json)
            metadata["last_error"] = f"Workspace path is missing on disk: {workspace.workspace_path}"
            workspace.metadata_json = metadata
            workspace.status = WorkspaceStatus.FAILED
            self.db.commit()
            self.db.refresh(workspace)
            return self._workspace_status_read(workspace, [])

        snapshot = self.worktree_manager.inspect_workspace(
            repo_path=project.repo_path,
            workspace_path=workspace.workspace_path,
            base_branch=workspace.base_branch,
            base_commit=workspace.base_commit,
            expected_branch=workspace.branch_name,
        )
        metadata = dict(workspace.metadata_json)
        metadata["last_synced_at"] = datetime.now(UTC).isoformat()
        workspace.head_commit = snapshot.head_commit
        workspace.status = snapshot.status
        workspace.metadata_json = metadata
        self.db.commit()
        self.db.refresh(workspace)
        return self._workspace_status_read(workspace, snapshot.changed_files)

    def _handle_provision_failure(
        self,
        *,
        exc: Exception,
        workspace: Workspace,
        session: SessionModel,
        project: Project,
    ) -> WorkspaceStatusRead:
        detail = exc.detail if isinstance(exc, AppError) else str(exc)
        metadata = dict(workspace.metadata_json)
        metadata["last_error"] = detail
        workspace.status = WorkspaceStatus.FAILED
        workspace.metadata_json = metadata
        session.status = SessionStatus.FAILED
        self.db.commit()
        self.db.refresh(workspace)
        self.db.refresh(session)

        self.event_service.record_event(
            EventCreate(
                category=EventCategory.WORKSPACE,
                event_type="worktree.failed",
                level=EventLevel.ERROR,
                source=EventSourceRef(kind="service", role=session.role, id="workspace-service"),
                project_id=project.id,
                task_id=session.task_id,
                session_id=session.id,
                payload={
                    "workspace_id": str(workspace.id),
                    "branch_name": workspace.branch_name,
                    "workspace_path": workspace.workspace_path,
                    "error": detail,
                },
            )
        )

        if isinstance(exc, AppError):
            raise exc
        raise InfrastructureError(f"Failed to provision workspace {workspace.id}: {detail}") from exc

    def _workspace_status_read(self, workspace: Workspace, changed_files: list[str]) -> WorkspaceStatusRead:
        return WorkspaceStatusRead(
            id=workspace.id,
            project_id=workspace.project_id,
            session_id=workspace.session_id,
            branch_name=workspace.branch_name,
            base_branch=workspace.base_branch,
            base_commit=workspace.base_commit,
            head_commit=workspace.head_commit,
            workspace_path=workspace.workspace_path,
            status=workspace.status,
            changed_files=changed_files,
            metadata=workspace.metadata_json,
            created_at=workspace.created_at,
            updated_at=workspace.updated_at,
        )

    def _ensure_workspace_is_ready(self, workspace: Workspace) -> None:
        if workspace.status not in {WorkspaceStatus.READY, WorkspaceStatus.DIRTY}:
            raise ValidationError(
                f"Workspace {workspace.id} is not provisioned. "
                "Create or repair it before requesting diff or command execution."
            )

    def _get_project(self, project_id: UUID) -> Project:
        project = self.db.get(Project, project_id)
        if project is None:
            raise NotFoundError(f"Project not found: {project_id}")
        return project

    def _get_session(self, session_id: UUID | None) -> SessionModel:
        if session_id is None:
            raise NotFoundError("Workspace is not attached to a session.")
        session = self.db.get(SessionModel, session_id)
        if session is None:
            raise NotFoundError(f"Session not found: {session_id}")
        return session

    def _get_task(self, task_id: UUID) -> Task:
        task = self.db.get(Task, task_id)
        if task is None:
            raise NotFoundError(f"Task not found: {task_id}")
        return task

    def _get_workspace(self, workspace_id: UUID) -> Workspace:
        workspace = self.db.get(Workspace, workspace_id)
        if workspace is None:
            raise NotFoundError(f"Workspace not found: {workspace_id}")
        return workspace

    def _get_workspace_for_session(self, session_id: UUID) -> Workspace:
        workspace = self.db.scalar(select(Workspace).where(Workspace.session_id == session_id))
        if workspace is None:
            raise NotFoundError(f"Workspace not found for session: {session_id}")
        return workspace
