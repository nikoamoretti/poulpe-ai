from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.enums import (
    EventCategory,
    EventLevel,
    ProjectCheckpointAction,
    ProjectCheckpointKind,
    ProjectCheckpointResolution,
    ProjectCheckpointStatus,
    ProjectStatus,
    SessionRole,
    SessionStatus,
)
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.text import slugify
from app.models.artifact import Artifact
from app.models.portfolio import Portfolio
from app.models.project import Project
from app.models.project_checkpoint import ProjectCheckpoint
from app.models.session import Session as SessionModel
from app.schemas.event import EventCreate, EventSourceRef
from app.schemas.project_checkpoint import (
    ProjectCheckpointArtifactRead,
    ProjectCheckpointRead,
    ProjectCheckpointRespondRequest,
)
from app.schemas.portfolio import PortfolioCreate, PortfolioManagerStartRequest, PortfolioRead
from app.schemas.session import SessionRead
from app.services.event_service import EventService
from app.services.project_service import ProjectService
from app.services.runtime_service import RuntimeService
from app.services.session_supervisor import SessionSupervisor


class PortfolioService:
    def __init__(
        self,
        db: Session,
        event_service: EventService,
        settings: Settings,
        runtime_service: RuntimeService,
        session_supervisor: SessionSupervisor,
        project_service: ProjectService,
    ) -> None:
        self.db = db
        self.event_service = event_service
        self.settings = settings
        self.runtime_service = runtime_service
        self.session_supervisor = session_supervisor
        self.project_service = project_service

    def list_portfolios(self) -> list[PortfolioRead]:
        records = self.db.scalars(select(Portfolio).order_by(Portfolio.created_at.desc())).all()
        return [PortfolioRead.model_validate(record) for record in records]

    def get_portfolio(self, portfolio_id: UUID) -> PortfolioRead:
        portfolio = self.db.get(Portfolio, portfolio_id)
        if portfolio is None:
            raise NotFoundError(f"Portfolio not found: {portfolio_id}")
        return PortfolioRead.model_validate(portfolio)

    def create_portfolio(self, payload: PortfolioCreate) -> PortfolioRead:
        portfolio = Portfolio(
            name=payload.name,
            slug=self._build_unique_slug(payload.name),
            goal=payload.goal.strip(),
            status=ProjectStatus.ACTIVE,
            metadata_json=payload.metadata,
        )
        self.db.add(portfolio)
        self.db.commit()
        self.db.refresh(portfolio)

        self.event_service.record_event(
            EventCreate(
                category=EventCategory.SYSTEM,
                event_type="portfolio.created",
                level=EventLevel.INFO,
                source=EventSourceRef(kind="api", id="portfolios.create"),
                payload={
                    "portfolio_id": str(portfolio.id),
                    "name": portfolio.name,
                    "goal": portfolio.goal,
                },
            )
        )
        return PortfolioRead.model_validate(portfolio)

    def start_manager_session(
        self,
        portfolio_id: UUID,
        payload: PortfolioManagerStartRequest,
    ) -> SessionRead:
        portfolio = self.db.get(Portfolio, portfolio_id)
        if portfolio is None:
            raise NotFoundError(f"Portfolio not found: {portfolio_id}")

        session = self._current_manager_session(portfolio)
        if session is None or session.status in {
            SessionStatus.COMPLETED,
            SessionStatus.FAILED,
            SessionStatus.STOPPED,
        }:
            session = self._create_manager_session(portfolio, payload)

        if session.status == SessionStatus.PENDING:
            kickoff = payload.initial_message or portfolio.goal or f"Manage portfolio: {portfolio.name}"
            self.session_supervisor.start_session(session.id, initial_message=kickoff)

        self.db.expire_all()
        session_record = self.db.get(SessionModel, session.id)
        if session_record is None:
            raise NotFoundError(f"Session not found: {session.id}")

        self.event_service.record_event(
            EventCreate(
                category=EventCategory.SYSTEM,
                event_type="portfolio.manager_started",
                level=EventLevel.INFO,
                source=EventSourceRef(kind="api", role=SessionRole.MANAGER, id="portfolios.manager.start"),
                session_id=session_record.id,
                payload={
                    "portfolio_id": str(portfolio.id),
                    "manager_session_id": str(session_record.id),
                    "runtime_provider": self.runtime_service.runtime_from_metadata(
                        session_record.metadata_json
                    ).resolved_provider,
                },
            )
        )
        return self._session_read(session_record)

    def list_inbox(
        self,
        portfolio_id: UUID,
        *,
        status: ProjectCheckpointStatus | None = ProjectCheckpointStatus.OPEN,
    ) -> list[ProjectCheckpointRead]:
        portfolio = self.db.get(Portfolio, portfolio_id)
        if portfolio is None:
            raise NotFoundError(f"Portfolio not found: {portfolio_id}")

        stmt = (
            select(ProjectCheckpoint, Project.name, Project.slug)
            .join(Project, Project.id == ProjectCheckpoint.project_id)
            .where(ProjectCheckpoint.portfolio_id == portfolio.id)
            .order_by(ProjectCheckpoint.source_occurred_at.desc(), ProjectCheckpoint.created_at.desc())
        )
        if status is not None:
            stmt = stmt.where(ProjectCheckpoint.status == status)

        rows = self.db.execute(stmt).all()
        return [self._checkpoint_read(checkpoint, project_name, project_slug) for checkpoint, project_name, project_slug in rows]

    def respond_to_checkpoint(
        self,
        portfolio_id: UUID,
        checkpoint_id: UUID,
        payload: ProjectCheckpointRespondRequest,
    ) -> ProjectCheckpointRead:
        portfolio = self.db.get(Portfolio, portfolio_id)
        if portfolio is None:
            raise NotFoundError(f"Portfolio not found: {portfolio_id}")

        checkpoint = self.db.get(ProjectCheckpoint, checkpoint_id)
        if checkpoint is None or checkpoint.portfolio_id != portfolio.id:
            raise NotFoundError(f"Checkpoint not found: {checkpoint_id}")
        if checkpoint.status != ProjectCheckpointStatus.OPEN:
            raise ConflictError(f"Checkpoint {checkpoint_id} is already {checkpoint.status.value}.")

        project = self.db.get(Project, checkpoint.project_id)
        if project is None:
            raise NotFoundError(f"Project not found: {checkpoint.project_id}")

        manager_session = self._current_manager_session(portfolio)
        manager_session_id = manager_session.id if manager_session is not None else portfolio.manager_session_id

        action = payload.action
        message = payload.message.strip() if payload.message is not None else None
        now_details = dict(payload.details)
        routed_session_id: UUID | None = None
        existing_response_details = (
            dict(checkpoint.response_details_json)
            if isinstance(checkpoint.response_details_json, dict)
            else {}
        )

        if action == ProjectCheckpointAction.APPROVE:
            if checkpoint.kind != ProjectCheckpointKind.COMPLETION:
                raise ValidationError("Only completion checkpoints can be approved.")
            checkpoint.status = ProjectCheckpointStatus.RESOLVED
            checkpoint.resolution = ProjectCheckpointResolution.APPROVED
            checkpoint.response_message = message or checkpoint.summary
            project.completion_summary = checkpoint.response_message
        elif action == ProjectCheckpointAction.REQUEST_CHANGES:
            if checkpoint.kind != ProjectCheckpointKind.COMPLETION:
                raise ValidationError("Only completion checkpoints can request changes.")
            if not message:
                raise ValidationError("A response message is required when requesting changes.")
            routed_session = self.project_service.deliver_manager_instruction(
                project.id,
                message=message,
                metadata={
                    "manager_action": action.value,
                    "manager_checkpoint_id": str(checkpoint.id),
                    "portfolio_id": str(portfolio.id),
                },
            )
            routed_session_id = routed_session.id
            checkpoint.status = ProjectCheckpointStatus.RESOLVED
            checkpoint.resolution = ProjectCheckpointResolution.CHANGES_REQUESTED
            checkpoint.response_message = message
            project.completion_summary = None
        elif action == ProjectCheckpointAction.ANSWER:
            if checkpoint.kind not in {
                ProjectCheckpointKind.QUESTION,
                ProjectCheckpointKind.BLOCKED,
                ProjectCheckpointKind.ERROR,
            }:
                raise ValidationError("Only question, blocked, or error checkpoints can be answered.")
            if not message:
                raise ValidationError("A response message is required when answering a checkpoint.")
            routed_session = self.project_service.deliver_manager_instruction(
                project.id,
                message=message,
                metadata={
                    "manager_action": action.value,
                    "manager_checkpoint_id": str(checkpoint.id),
                    "portfolio_id": str(portfolio.id),
                },
            )
            routed_session_id = routed_session.id
            checkpoint.status = ProjectCheckpointStatus.RESOLVED
            checkpoint.resolution = ProjectCheckpointResolution.ANSWERED
            checkpoint.response_message = message
        elif action == ProjectCheckpointAction.DISMISS:
            checkpoint.status = ProjectCheckpointStatus.DISMISSED
            checkpoint.resolution = ProjectCheckpointResolution.DISMISSED
            checkpoint.response_message = message
        else:
            raise ValidationError(f"Unsupported checkpoint action: {action.value}")

        checkpoint.manager_session_id = manager_session_id
        checkpoint.response_details_json = {
            **existing_response_details,
            **now_details,
            **({"routed_worker_session_id": str(routed_session_id)} if routed_session_id is not None else {}),
        }
        checkpoint.resolved_at = checkpoint.resolved_at or datetime.now(UTC)

        metadata = dict(project.metadata_json)
        manager_state = metadata.get("manager_state")
        if not isinstance(manager_state, dict):
            manager_state = {}
        manager_state.update(
            {
                "last_checkpoint_id": str(checkpoint.id),
                "last_action": action.value,
                "last_resolved_at": checkpoint.resolved_at.isoformat(),
                "manager_session_id": str(manager_session_id) if manager_session_id is not None else None,
            }
        )
        if checkpoint.resolution is not None:
            manager_state["resolution"] = checkpoint.resolution.value
        metadata["manager_state"] = manager_state
        project.metadata_json = metadata

        self.db.commit()
        self.db.refresh(checkpoint)
        self.db.refresh(project)

        self.event_service.record_event(
            EventCreate(
                category=EventCategory.PROJECT,
                event_type="project.checkpoint_resolved",
                level=EventLevel.INFO,
                source=EventSourceRef(kind="api", role=SessionRole.MANAGER, id="portfolios.inbox.respond"),
                project_id=project.id,
                session_id=manager_session_id,
                payload={
                    "portfolio_id": str(portfolio.id),
                    "checkpoint_id": str(checkpoint.id),
                    "action": action.value,
                    "resolution": checkpoint.resolution.value if checkpoint.resolution is not None else None,
                    "routed_worker_session_id": str(routed_session_id) if routed_session_id is not None else None,
                },
            )
        )
        return self._checkpoint_read(checkpoint, project.name, project.slug)

    def _create_manager_session(
        self,
        portfolio: Portfolio,
        payload: PortfolioManagerStartRequest,
    ) -> SessionModel:
        launch_plan = self.session_supervisor.plan_session(
            role=SessionRole.MANAGER,
            command_override=payload.command_override,
            runtime_preference=payload.runtime_preference,
            allow_simulation_fallback=payload.allow_simulation_fallback,
            simulation_mode=payload.simulation_mode,
        )
        workspace_path = self._manager_workspace_path(portfolio)
        Path(workspace_path).mkdir(parents=True, exist_ok=True)

        metadata = dict(payload.metadata)
        if payload.model is not None:
            metadata["model"] = payload.model
        metadata.update(
            {
                "session_kind": "portfolio_manager",
                "preferred_engine": payload.runtime_preference or metadata.get("preferred_engine") or "auto",
                "allow_simulation_fallback": payload.allow_simulation_fallback,
                "portfolio_goal": portfolio.goal,
                "simulation_mode": launch_plan.simulation_mode,
                "launch_notes": launch_plan.notes,
                "runtime": launch_plan.runtime.model_dump(mode="json"),
            }
        )
        session = SessionModel(
            portfolio_id=portfolio.id,
            role=SessionRole.MANAGER,
            status=launch_plan.initial_status,
            transport=launch_plan.transport,
            adapter_kind=launch_plan.adapter_kind,
            command=launch_plan.command,
            workspace_path=workspace_path,
            blocked_reason=launch_plan.blocked_reason,
            metadata_json=metadata,
            runtime_metadata_json={},
        )
        self.db.add(session)
        self.db.flush()

        portfolio.manager_session_id = session.id
        portfolio.manager_workspace_path = workspace_path
        self.db.commit()
        self.db.refresh(portfolio)
        self.db.refresh(session)
        return session

    def _current_manager_session(self, portfolio: Portfolio) -> SessionModel | None:
        if portfolio.manager_session_id is not None:
            session = self.db.get(SessionModel, portfolio.manager_session_id)
            if session is not None:
                return session
        return self.db.scalar(
            select(SessionModel)
            .where(
                SessionModel.portfolio_id == portfolio.id,
                SessionModel.role == SessionRole.MANAGER,
                SessionModel.project_id.is_(None),
            )
            .order_by(SessionModel.created_at.desc())
        )

    def _manager_workspace_path(self, portfolio: Portfolio) -> str:
        return str(
            Path(self.settings.orchestrator_workspaces_root).expanduser().resolve()
            / "_portfolio_managers"
            / portfolio.slug
        )

    def _session_read(self, session: SessionModel) -> SessionRead:
        payload = SessionRead.model_validate(session).model_dump(mode="python")
        payload["runtime"] = self.runtime_service.runtime_from_metadata(session.metadata_json)
        return SessionRead.model_validate(payload)

    def _checkpoint_read(
        self,
        checkpoint: ProjectCheckpoint,
        project_name: str,
        project_slug: str,
    ) -> ProjectCheckpointRead:
        review_context = checkpoint.details_json.get("review_context", {})
        if not isinstance(review_context, dict):
            review_context = {}

        artifact_ids: list[str] = []
        diff_info = review_context.get("diff", {})
        if not isinstance(diff_info, dict):
            diff_info = {}
        diff_artifact_id = diff_info.get("artifact_id")
        if diff_artifact_id:
            artifact_ids.append(diff_artifact_id)
        checks = review_context.get("checks", [])
        if isinstance(checks, list):
            for check in checks:
                if not isinstance(check, dict):
                    continue
                artifact_id = check.get("artifact_id")
                if artifact_id:
                    artifact_ids.append(artifact_id)
        artifacts = [
            artifact
            for artifact in (
                self.db.get(Artifact, UUID(artifact_id))
                for artifact_id in dict.fromkeys(artifact_ids)
            )
            if artifact is not None
        ]
        return ProjectCheckpointRead(
            id=checkpoint.id,
            portfolio_id=checkpoint.portfolio_id,
            project_id=checkpoint.project_id,
            project_name=project_name,
            project_slug=project_slug,
            source_session_id=checkpoint.source_session_id,
            manager_session_id=checkpoint.manager_session_id,
            source_parsed_event_id=checkpoint.source_parsed_event_id,
            kind=checkpoint.kind,
            status=checkpoint.status,
            summary=checkpoint.summary,
            details=checkpoint.details_json,
            artifacts=[
                ProjectCheckpointArtifactRead(
                    id=artifact.id,
                    kind=artifact.kind,
                    uri=artifact.uri,
                    content_type=artifact.content_type,
                    size_bytes=artifact.size_bytes,
                    metadata=artifact.metadata_json,
                )
                for artifact in artifacts
            ],
            resolution=checkpoint.resolution,
            response_message=checkpoint.response_message,
            response_details=checkpoint.response_details_json,
            source_occurred_at=checkpoint.source_occurred_at,
            resolved_at=checkpoint.resolved_at,
            created_at=checkpoint.created_at,
            updated_at=checkpoint.updated_at,
        )

    def _build_unique_slug(self, name: str) -> str:
        base = slugify(name)
        slug = base
        counter = 2
        while self.db.scalar(select(Portfolio.id).where(Portfolio.slug == slug)) is not None:
            slug = f"{base}-{counter}"
            counter += 1
        return slug
