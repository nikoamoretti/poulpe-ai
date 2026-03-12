from __future__ import annotations

import logging
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
from app.schemas.session import (
    SessionCreate,
    SessionMessageRequest,
    SessionRead,
    SessionStartRequest,
)
from app.schemas.structured_event import ParsedSessionEventRead
from app.schemas.transcript import TranscriptChunkRead
from app.services.event_service import EventService
from app.services.runtime_service import RuntimeService
from app.services.session_supervisor import SessionSupervisor
from app.services.worktree_manager import WorktreeManager
from app.services.workspace_service import WorkspaceService

logger = logging.getLogger(__name__)


class SessionService:
    def __init__(
        self,
        db: Session,
        event_service: EventService,
        session_supervisor: SessionSupervisor,
        worktree_manager: WorktreeManager,
        repo_inspector: RepoInspectorAdapter,
        runtime_service: RuntimeService,
        workspace_service: WorkspaceService,
    ) -> None:
        self.db = db
        self.event_service = event_service
        self.session_supervisor = session_supervisor
        self.worktree_manager = worktree_manager
        self.repo_inspector = repo_inspector
        self.runtime_service = runtime_service
        self.workspace_service = workspace_service

    def list_sessions(self, project_id: UUID | None = None) -> list[SessionRead]:
        self.db.expire_all()
        stmt = select(SessionModel).order_by(SessionModel.created_at.desc())
        if project_id is not None:
            stmt = stmt.where(SessionModel.project_id == project_id)
        records = self.db.scalars(stmt).all()
        return [self._to_read(record) for record in records]

    def get_session(self, session_id: UUID) -> SessionRead:
        self.session_supervisor.refresh_session_runtime(session_id)
        self.db.expire_all()
        session = self.db.get(SessionModel, session_id)
        if session is None:
            raise NotFoundError(f"Session not found: {session_id}")
        return self._to_read(session)

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
            adapter_kind=payload.adapter_kind,
            runtime_preference=payload.runtime_preference
            or str(payload.metadata.get("preferred_engine", "") or "")
            or None,
            allow_simulation_fallback=payload.allow_simulation_fallback,
            simulation_mode=payload.simulation_mode,
        )
        metadata = dict(payload.metadata)
        if payload.model is not None:
            metadata["model"] = payload.model
        metadata["simulation_mode"] = launch_plan.simulation_mode
        metadata["launch_notes"] = launch_plan.notes
        metadata["runtime"] = launch_plan.runtime.model_dump(mode="json")

        session = SessionModel(
            project_id=payload.project_id,
            task_id=payload.task_id,
            supervisor_session_id=payload.supervisor_session_id,
            role=payload.role,
            status=launch_plan.initial_status,
            transport=launch_plan.transport,
            adapter_kind=launch_plan.adapter_kind,
            command=launch_plan.command,
            blocked_reason=launch_plan.blocked_reason,
            metadata_json=metadata,
            runtime_metadata_json={},
        )
        self.db.add(session)
        self.db.flush()

        if payload.role == SessionRole.WORKER and task is not None:
            repo_info = self.repo_inspector.inspect(project.repo_path, project.default_branch)
            workspace_plan = self.worktree_manager.plan_workspace(
                project_slug=project.slug,
                project_id=project.id,
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

        # Manager/reviewer sessions use the project repo as their working directory
        if payload.role in (SessionRole.MANAGER, SessionRole.REVIEWER) and not session.workspace_path:
            session.workspace_path = project.repo_path

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
                    "runtime_provider": launch_plan.runtime.resolved_provider,
                    "runtime_simulated": launch_plan.runtime.simulated,
                    "runtime_disconnected": launch_plan.runtime.disconnected,
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
        logger.info(
            "created %s session %s for project=%s task=%s adapter=%s runtime=%s simulated=%s disconnected=%s",
            session.role.value,
            session.id,
            session.project_id,
            session.task_id,
            session.adapter_kind,
            launch_plan.runtime.resolved_provider,
            launch_plan.runtime.simulated,
            launch_plan.runtime.disconnected,
        )
        return self._to_read(session)

    def start_session(self, session_id: UUID, payload: SessionStartRequest) -> SessionRead:
        self.session_supervisor.start_session(session_id, initial_message=payload.initial_message)
        session = self.get_session(session_id)
        logger.info(
            "start requested for session %s runtime=%s simulated=%s disconnected=%s",
            session_id,
            session.runtime.resolved_provider,
            session.runtime.simulated,
            session.runtime.disconnected,
        )
        return session

    def send_instruction(self, session_id: UUID, payload: SessionMessageRequest) -> ApiMessage:
        self.session_supervisor.send(session_id, payload.message)
        self.db.expire_all()
        session = self.db.get(SessionModel, session_id)
        if session is None:
            raise NotFoundError(f"Session not found: {session_id}")
        return ApiMessage(
            detail=f"Instruction delivered to session {session_id} ({session.status.value}).",
            generated_at=datetime.now(UTC),
        )

    def interrupt_session(self, session_id: UUID) -> ApiMessage:
        self.session_supervisor.interrupt(session_id)
        self.db.expire_all()
        session = self.db.get(SessionModel, session_id)
        if session is None:
            raise NotFoundError(f"Session not found: {session_id}")
        return ApiMessage(
            detail=f"Interrupt sent to session {session_id} ({session.status.value}).",
            generated_at=datetime.now(UTC),
        )

    def list_transcript(self, session_id: UUID, limit: int = 200) -> list[TranscriptChunkRead]:
        return self.session_supervisor.list_transcript(session_id, limit=limit)

    def list_structured_events(
        self,
        session_id: UUID,
        limit: int = 200,
    ) -> list[ParsedSessionEventRead]:
        return self.session_supervisor.list_structured_events(session_id, limit=limit)

    def stop_session(self, session_id: UUID) -> ApiMessage:
        self.session_supervisor.stop(session_id)
        return ApiMessage(
            detail=f"Stop requested for session {session_id}.",
            generated_at=datetime.now(UTC),
        )

    def _to_read(self, session: SessionModel) -> SessionRead:
        payload = SessionRead.model_validate(session).model_dump(mode="python")
        payload["runtime"] = self.runtime_service.runtime_from_metadata(session.metadata_json)
        return SessionRead.model_validate(payload)
