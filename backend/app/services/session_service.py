from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.repo_inspector import RepoInspectorAdapter
from app.core.enums import EventCategory, EventLevel, SessionRole, SessionStatus, WorkspaceStatus
from app.core.errors import NotFoundError, ValidationError
from app.models.project import Project
from app.models.session import Session as SessionModel
from app.models.task import Task
from app.models.workspace import Workspace
from app.schemas.common import ApiMessage
from app.schemas.event import EventCreate, EventSourceRef
from app.schemas.session import SessionCreate, SessionRead
from app.services.event_service import EventService
from app.services.session_supervisor import SessionSupervisor
from app.services.worktree_manager import WorktreeManager
from app.services.workspace_service import WorkspaceService


class SessionService:
    def __init__(
        self,
        db: Session,
        event_service: EventService,
        session_supervisor: SessionSupervisor,
        worktree_manager: WorktreeManager,
        repo_inspector: RepoInspectorAdapter,
        workspace_service: WorkspaceService,
    ) -> None:
        self.db = db
        self.event_service = event_service
        self.session_supervisor = session_supervisor
        self.worktree_manager = worktree_manager
        self.repo_inspector = repo_inspector
        self.workspace_service = workspace_service

    def list_sessions(self, project_id: UUID | None = None) -> list[SessionRead]:
        stmt = select(SessionModel).order_by(SessionModel.created_at.desc())
        if project_id is not None:
            stmt = stmt.where(SessionModel.project_id == project_id)
        records = self.db.scalars(stmt).all()
        return [SessionRead.model_validate(record) for record in records]

    def get_session(self, session_id: UUID) -> SessionRead:
        session = self.db.get(SessionModel, session_id)
        if session is None:
            raise NotFoundError(f"Session not found: {session_id}")
        return SessionRead.model_validate(session)

    def create_session(self, payload: SessionCreate) -> SessionRead:
        project = self.db.get(Project, payload.project_id)
        if project is None:
            raise NotFoundError(f"Project not found: {payload.project_id}")

        task: Task | None = None
        if payload.task_id is not None:
            task = self.db.get(Task, payload.task_id)
            if task is None:
                raise NotFoundError(f"Task not found: {payload.task_id}")
            if task.project_id != payload.project_id:
                raise ValidationError("Task must belong to the same project as the session.")

        if payload.role == SessionRole.WORKER and task is None:
            raise ValidationError("Worker sessions require a task_id.")

        launch_plan = self.session_supervisor.plan_session(
            role=payload.role,
            command_override=payload.command_override,
        )
        metadata = dict(payload.metadata)
        if payload.model is not None:
            metadata["model"] = payload.model
        metadata["launch_notes"] = launch_plan.notes

        session = SessionModel(
            project_id=payload.project_id,
            task_id=payload.task_id,
            supervisor_session_id=payload.supervisor_session_id,
            role=payload.role,
            status=launch_plan.initial_status,
            transport=launch_plan.transport,
            command=launch_plan.command,
            metadata_json=metadata,
        )
        self.db.add(session)
        self.db.flush()

        if payload.role == SessionRole.WORKER and task is not None:
            repo_info = self.repo_inspector.inspect(project.repo_path, project.default_branch)
            workspace_plan = self.worktree_manager.plan_workspace(
                project_slug=project.slug,
                role=payload.role,
                task_id=task.id,
                session_id=session.id,
                base_branch=project.default_branch,
            )
            workspace = Workspace(
                project_id=project.id,
                session_id=session.id,
                branch_name=workspace_plan.branch_name,
                base_branch=workspace_plan.base_branch,
                base_commit=repo_info.current_commit or "",
                head_commit=repo_info.current_commit,
                workspace_path=workspace_plan.workspace_path,
                status=workspace_plan.status,
                metadata_json={
                    "ownership": {
                        "session_id": str(session.id),
                        "path_lock_owner": str(session.id),
                        "path_locks": [],
                    }
                },
            )
            self.db.add(workspace)
            session.branch_name = workspace.branch_name
            session.workspace_path = workspace.workspace_path

        self.db.commit()
        self.db.refresh(session)

        self.event_service.record_event(
            EventCreate(
                category=EventCategory.SESSION,
                event_type="session.created",
                level=EventLevel.INFO,
                source=EventSourceRef(kind="api", role=payload.role, id="sessions.create"),
                project_id=session.project_id,
                task_id=session.task_id,
                session_id=session.id,
                payload={
                    "role": session.role.value,
                    "status": session.status.value,
                    "transport": session.transport.value,
                },
            )
        )
        if session.workspace_path:
            self.event_service.record_event(
                EventCreate(
                    category=EventCategory.WORKSPACE,
                    event_type="workspace.planned",
                    level=EventLevel.INFO,
                    source=EventSourceRef(kind="service", role=session.role, id="worktree-manager"),
                    project_id=session.project_id,
                    task_id=session.task_id,
                    session_id=session.id,
                    payload={
                        "branch_name": session.branch_name,
                        "workspace_path": session.workspace_path,
                    },
                )
            )
        if payload.role == SessionRole.WORKER:
            self.workspace_service.provision_session_workspace(session.id)
            self.db.refresh(session)
        return SessionRead.model_validate(session)

    def stop_session(self, session_id: UUID) -> ApiMessage:
        session = self.db.get(SessionModel, session_id)
        if session is None:
            raise NotFoundError(f"Session not found: {session_id}")

        session.status = self.session_supervisor.stop_session(session.status)
        session.ended_at = session.ended_at or datetime.now(UTC)
        self.db.commit()
        self.db.refresh(session)

        self.event_service.record_event(
            EventCreate(
                category=EventCategory.SESSION,
                event_type="session.stopped",
                level=EventLevel.INFO,
                source=EventSourceRef(kind="api", role=session.role, id="sessions.stop"),
                project_id=session.project_id,
                task_id=session.task_id,
                session_id=session.id,
                payload={"status": SessionStatus.STOPPED.value},
            )
        )
        return ApiMessage(
            detail=f"Session {session_id} marked as stopped.",
            generated_at=datetime.now(UTC),
        )
