from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import func as sa_func, select
from sqlalchemy.orm import Session

from app.adapters.redis_bus import RedisBusAdapter
from app.adapters.repo_inspector import RepoInspectorAdapter
from app.core.config import Settings
from app.core.database import DatabaseManager
from app.core.enums import (
    EventCategory,
    EventLevel,
    ProjectCheckpointAction,
    ProjectCheckpointKind,
    ProjectCheckpointStatus,
    ProjectStatus,
    SessionRole,
    SessionStatus,
    StructuredEventType,
)
from app.core.errors import NotFoundError
from app.core.event_stream import EventStreamBroker
from app.models.portfolio import Portfolio
from app.models.project import Project
from app.models.project_checkpoint import ProjectCheckpoint
from app.models.session import Session as SessionModel
from app.models.parsed_session_event import ParsedSessionEvent
from app.schemas.event import EventCreate, EventSourceRef
from app.schemas.portfolio import (
    PortfolioAutomationActionRead,
    PortfolioAutomationTickRead,
    PortfolioManagerStartRequest,
)
from app.schemas.project import ProjectCreate, ProjectStartRequest
from app.schemas.project_checkpoint import ProjectCheckpointRespondRequest
from app.services.command_runner import CommandRunner
from app.services.event_service import EventService
from app.services.portfolio_service import PortfolioService
from app.services.project_service import ProjectService
from app.services.runtime_service import RuntimeService
from app.services.session_supervisor import SessionSupervisor
from app.services.workspace_service import WorkspaceService
from app.services.worktree_manager import WorktreeManager

logger = logging.getLogger(__name__)

ACTIVE_SESSION_STATUSES = {
    SessionStatus.PENDING,
    SessionStatus.STARTING,
    SessionStatus.RUNNING,
    SessionStatus.BLOCKED,
}
TERMINAL_SESSION_STATUSES = {
    SessionStatus.COMPLETED,
    SessionStatus.FAILED,
    SessionStatus.STOPPED,
}


@dataclass(slots=True)
class _ScopedServices:
    event_service: EventService
    project_service: ProjectService
    portfolio_service: PortfolioService


class PortfolioAutomationService:
    def __init__(
        self,
        *,
        settings: Settings,
        database: DatabaseManager,
        redis_bus: RedisBusAdapter,
        event_broker: EventStreamBroker,
        repo_inspector: RepoInspectorAdapter,
        command_runner: CommandRunner,
        runtime_service: RuntimeService,
        session_supervisor: SessionSupervisor,
        worktree_manager: WorktreeManager,
    ) -> None:
        self.settings = settings
        self.database = database
        self.redis_bus = redis_bus
        self.event_broker = event_broker
        self.repo_inspector = repo_inspector
        self.command_runner = command_runner
        self.runtime_service = runtime_service
        self.session_supervisor = session_supervisor
        self.worktree_manager = worktree_manager

    def tick_all(self) -> list[PortfolioAutomationTickRead]:
        with self.database.session() as db:
            portfolio_ids = list(db.scalars(select(Portfolio.id).order_by(Portfolio.created_at.asc())).all())

        results: list[PortfolioAutomationTickRead] = []
        for portfolio_id in portfolio_ids:
            try:
                results.append(self.tick(portfolio_id))
            except Exception:
                logger.exception("portfolio automation tick failed for portfolio=%s", portfolio_id)
        return results

    def tick(self, portfolio_id: UUID) -> PortfolioAutomationTickRead:
        started_at = datetime.now(UTC)
        actions: list[PortfolioAutomationActionRead] = []

        with self.database.session() as db:
            services = self._services(db)
            portfolio = db.get(Portfolio, portfolio_id)
            if portfolio is None:
                raise NotFoundError(f"Portfolio not found: {portfolio_id}")

            open_checkpoints = self._open_checkpoints(db, portfolio.id)
            manager_session = self._current_manager_session(db, portfolio)

            if open_checkpoints and manager_session is None:
                manager = services.portfolio_service.start_manager_session(
                    portfolio.id,
                    self._manager_start_request(portfolio, manager_session),
                )
                actions.append(
                    self._action(
                        kind="manager_started",
                        portfolio_id=portfolio.id,
                        session_id=manager.id,
                        detail="Started the primary portfolio manager session for automation.",
                    )
                )
                db.expire_all()
                portfolio = db.get(Portfolio, portfolio_id)
                if portfolio is None:
                    raise NotFoundError(f"Portfolio not found: {portfolio_id}")
                manager_session = self._current_manager_session(db, portfolio)

            if not open_checkpoints:
                # Check if portfolio needs planning (goal decomposition)
                planning_action = self._maybe_plan_portfolio(
                    db=db,
                    services=services,
                    portfolio=portfolio,
                    manager_session=manager_session,
                )
                if planning_action is not None:
                    actions.append(planning_action)

                completed_at = datetime.now(UTC)
                return PortfolioAutomationTickRead(
                    portfolio_id=portfolio.id,
                    started_at=started_at,
                    completed_at=completed_at,
                    actions=actions,
                )

            turn_action = self._process_existing_turns(
                db=db,
                services=services,
                portfolio=portfolio,
            )
            if turn_action is not None:
                actions.append(turn_action)

            active_turn = self._active_manager_turn(db, portfolio.id)
            if active_turn is not None:
                actions.append(
                    self._action(
                        kind="manager_turn_active",
                        portfolio_id=portfolio.id,
                        project_id=active_turn.project_id,
                        checkpoint_id=self._checkpoint_id_from_session(active_turn),
                        session_id=active_turn.id,
                        detail="A portfolio manager turn is already running.",
                    )
                )
                completed_at = datetime.now(UTC)
                return PortfolioAutomationTickRead(
                    portfolio_id=portfolio.id,
                    started_at=started_at,
                    completed_at=completed_at,
                    actions=actions,
                )

            checkpoint = self._next_automatable_checkpoint(db, portfolio.id)
            if checkpoint is not None and manager_session is not None:
                actions.append(
                    self._launch_manager_turn(
                        db=db,
                        services=services,
                        portfolio=portfolio,
                        manager_session=manager_session,
                        checkpoint=checkpoint,
                    )
                )

        completed_at = datetime.now(UTC)
        return PortfolioAutomationTickRead(
            portfolio_id=portfolio_id,
            started_at=started_at,
            completed_at=completed_at,
            actions=actions,
        )

    def _services(self, db: Session) -> _ScopedServices:
        event_service = EventService(
            db=db,
            redis_bus=self.redis_bus,
            event_broker=self.event_broker,
        )
        workspace_service = WorkspaceService(
            db=db,
            event_service=event_service,
            worktree_manager=self.worktree_manager,
            repo_inspector=self.repo_inspector,
            command_runner=self.command_runner,
        )
        project_service = ProjectService(
            db=db,
            settings=self.settings,
            event_service=event_service,
            command_runner=self.command_runner,
            repo_inspector=self.repo_inspector,
            runtime_service=self.runtime_service,
            session_supervisor=self.session_supervisor,
            workspace_service=workspace_service,
        )
        portfolio_service = PortfolioService(
            db=db,
            event_service=event_service,
            settings=self.settings,
            runtime_service=self.runtime_service,
            session_supervisor=self.session_supervisor,
            project_service=project_service,
        )
        return _ScopedServices(
            event_service=event_service,
            project_service=project_service,
            portfolio_service=portfolio_service,
        )

    def _manager_start_request(
        self,
        portfolio: Portfolio,
        manager_session: SessionModel | None,
    ) -> PortfolioManagerStartRequest:
        if manager_session is None:
            return PortfolioManagerStartRequest(
                runtime_preference="auto",
                allow_simulation_fallback=True,
                initial_message=portfolio.goal or f"Manage portfolio: {portfolio.name}",
                metadata={"automation": {"autostarted": True}},
            )

        preferred_engine = manager_session.metadata_json.get("preferred_engine")
        allow_simulation_fallback = manager_session.metadata_json.get("allow_simulation_fallback")
        simulation_mode = manager_session.metadata_json.get("simulation_mode")
        model = manager_session.metadata_json.get("model")
        return PortfolioManagerStartRequest(
            command_override=manager_session.command,
            runtime_preference=str(preferred_engine) if preferred_engine else None,
            allow_simulation_fallback=(
                bool(allow_simulation_fallback) if allow_simulation_fallback is not None else None
            ),
            simulation_mode=bool(simulation_mode) if simulation_mode is not None else None,
            model=str(model) if model else None,
            initial_message=portfolio.goal or f"Manage portfolio: {portfolio.name}",
            metadata={"automation": {"autostarted": True}},
        )

    def _current_manager_session(self, db: Session, portfolio: Portfolio) -> SessionModel | None:
        if portfolio.manager_session_id is not None:
            session = db.get(SessionModel, portfolio.manager_session_id)
            if session is not None:
                return session
        return db.scalar(
            select(SessionModel)
            .where(
                SessionModel.portfolio_id == portfolio.id,
                SessionModel.role == SessionRole.MANAGER,
                SessionModel.project_id.is_(None),
                SessionModel.supervisor_session_id.is_(None),
            )
            .order_by(SessionModel.created_at.desc())
        )

    def _manager_turn_sessions(self, db: Session, portfolio_id: UUID) -> list[SessionModel]:
        sessions = db.scalars(
            select(SessionModel)
            .where(
                SessionModel.portfolio_id == portfolio_id,
                SessionModel.role == SessionRole.MANAGER,
                SessionModel.supervisor_session_id.is_not(None),
            )
            .order_by(SessionModel.created_at.asc())
        ).all()
        return [
            session
            for session in sessions
            if str(session.metadata_json.get("session_kind") or "") == "portfolio_manager_turn"
        ]

    def _active_manager_turn(self, db: Session, portfolio_id: UUID) -> SessionModel | None:
        for session in reversed(self._manager_turn_sessions(db, portfolio_id)):
            if self._turn_processed(session):
                continue
            if session.status in ACTIVE_SESSION_STATUSES:
                return session
        return None

    def _process_existing_turns(
        self,
        *,
        db: Session,
        services: _ScopedServices,
        portfolio: Portfolio,
    ) -> PortfolioAutomationActionRead | None:
        for turn_session in self._manager_turn_sessions(db, portfolio.id):
            if self._turn_processed(turn_session):
                continue
            if turn_session.status not in TERMINAL_SESSION_STATUSES:
                continue
            return self._resolve_completed_turn(
                db=db,
                services=services,
                portfolio=portfolio,
                turn_session=turn_session,
            )
        return None

    def _resolve_completed_turn(
        self,
        *,
        db: Session,
        services: _ScopedServices,
        portfolio: Portfolio,
        turn_session: SessionModel,
    ) -> PortfolioAutomationActionRead:
        checkpoint_id = self._checkpoint_id_from_session(turn_session)
        if checkpoint_id is None:
            self._mark_turn_processed(db, turn_session, reason="checkpoint_id_missing")
            return self._action(
                kind="manager_turn_failed",
                portfolio_id=portfolio.id,
                project_id=turn_session.project_id,
                session_id=turn_session.id,
                detail="Manager turn finished without a checkpoint reference.",
            )

        checkpoint = db.get(ProjectCheckpoint, checkpoint_id)
        if checkpoint is None:
            self._mark_turn_processed(db, turn_session, reason="checkpoint_missing")
            return self._action(
                kind="manager_turn_failed",
                portfolio_id=portfolio.id,
                project_id=turn_session.project_id,
                checkpoint_id=checkpoint_id,
                session_id=turn_session.id,
                detail="Manager turn finished but the checkpoint no longer exists.",
            )
        if checkpoint.status != ProjectCheckpointStatus.OPEN:
            self._mark_turn_processed(db, turn_session, reason="checkpoint_already_closed")
            return self._action(
                kind="manager_turn_skipped",
                portfolio_id=portfolio.id,
                project_id=checkpoint.project_id,
                checkpoint_id=checkpoint.id,
                session_id=turn_session.id,
                detail="Manager turn finished after the checkpoint had already been closed.",
            )

        if turn_session.status != SessionStatus.COMPLETED:
            return self._handoff_to_human(
                db=db,
                services=services,
                portfolio=portfolio,
                checkpoint=checkpoint,
                turn_session=turn_session,
                reason=f"turn_session_{turn_session.status.value}",
            )

        complete_event = db.scalar(
            select(ParsedSessionEvent)
            .where(
                ParsedSessionEvent.session_id == turn_session.id,
                ParsedSessionEvent.event_type == StructuredEventType.COMPLETE,
            )
            .order_by(ParsedSessionEvent.sequence.desc())
        )
        if complete_event is None:
            ended_at = self._coerce_utc(turn_session.ended_at)
            if ended_at is None or (datetime.now(UTC) - ended_at).total_seconds() < 1.0:
                return self._action(
                    kind="manager_turn_waiting_for_events",
                    portfolio_id=portfolio.id,
                    project_id=checkpoint.project_id,
                    checkpoint_id=checkpoint.id,
                    session_id=turn_session.id,
                    detail="Manager turn finished, but structured events are still settling.",
                )
            return self._handoff_to_human(
                db=db,
                services=services,
                portfolio=portfolio,
                checkpoint=checkpoint,
                turn_session=turn_session,
                reason="no_complete_decision_event",
            )

        payload = dict(complete_event.payload_json)
        result = str(payload.get("result") or payload.get("action") or "").strip().lower()
        details = payload.get("details")
        if not isinstance(details, dict):
            details = {}
        response_message = str(
            payload.get("response_message")
            or details.get("response_message")
            or payload.get("message")
            or complete_event.summary
            or ""
        ).strip() or None
        action = self._map_result_to_action(checkpoint.kind, result)
        if action is None:
            return self._handoff_to_human(
                db=db,
                services=services,
                portfolio=portfolio,
                checkpoint=checkpoint,
                turn_session=turn_session,
                reason=f"unsupported_result:{result or 'missing'}",
            )

        if action in {
            ProjectCheckpointAction.ANSWER,
            ProjectCheckpointAction.REQUEST_CHANGES,
        } and not response_message:
            return self._handoff_to_human(
                db=db,
                services=services,
                portfolio=portfolio,
                checkpoint=checkpoint,
                turn_session=turn_session,
                reason="response_message_missing",
            )

        try:
            services.portfolio_service.respond_to_checkpoint(
                portfolio.id,
                checkpoint.id,
                ProjectCheckpointRespondRequest(
                    action=action,
                    message=response_message,
                    details={
                        "automation": {
                            "state": "resolved",
                            "turn_session_id": str(turn_session.id),
                            "resolved_at": datetime.now(UTC).isoformat(),
                            "result": result,
                            "parsed_event_id": str(complete_event.id),
                        }
                    },
                ),
            )
        except Exception as exc:
            return self._handoff_to_human(
                db=db,
                services=services,
                portfolio=portfolio,
                checkpoint=checkpoint,
                turn_session=turn_session,
                reason=f"checkpoint_response_failed:{exc}",
            )

        self._mark_turn_processed(db, turn_session, reason=f"resolved:{result}")
        services.event_service.record_event(
            EventCreate(
                category=EventCategory.PROJECT,
                event_type="portfolio.manager_turn_resolved",
                level=EventLevel.INFO,
                source=EventSourceRef(kind="service", role=SessionRole.MANAGER, id="portfolio-automation"),
                project_id=checkpoint.project_id,
                session_id=turn_session.id,
                payload={
                    "portfolio_id": str(portfolio.id),
                    "checkpoint_id": str(checkpoint.id),
                    "action": action.value,
                    "result": result,
                },
            )
        )
        return self._action(
            kind="manager_turn_resolved",
            portfolio_id=portfolio.id,
            project_id=checkpoint.project_id,
            checkpoint_id=checkpoint.id,
            session_id=turn_session.id,
            detail=f"Resolved checkpoint {checkpoint.id} with manager result {result}.",
            payload={"action": action.value, "response_message": response_message},
        )

    def _next_automatable_checkpoint(self, db: Session, portfolio_id: UUID) -> ProjectCheckpoint | None:
        checkpoints = db.scalars(
            select(ProjectCheckpoint)
            .where(
                ProjectCheckpoint.portfolio_id == portfolio_id,
                ProjectCheckpoint.status == ProjectCheckpointStatus.OPEN,
            )
            .order_by(ProjectCheckpoint.source_occurred_at.asc(), ProjectCheckpoint.created_at.asc())
        ).all()
        for checkpoint in checkpoints:
            automation = self._automation_state(checkpoint)
            state = str(automation.get("state") or "")
            if state == "waiting_for_human":
                continue
            if state == "running":
                turn_session_id = self._uuid_from_value(automation.get("turn_session_id"))
                if turn_session_id is not None:
                    turn_session = db.get(SessionModel, turn_session_id)
                    if turn_session is not None and not self._turn_processed(turn_session):
                        continue
                self._set_checkpoint_automation_state(
                    db,
                    checkpoint,
                    {
                        "state": "waiting_for_human",
                        "reason": "stale_running_turn_state",
                        "updated_at": datetime.now(UTC).isoformat(),
                    },
                )
                continue
            return checkpoint
        return None

    def _launch_manager_turn(
        self,
        *,
        db: Session,
        services: _ScopedServices,
        portfolio: Portfolio,
        manager_session: SessionModel,
        checkpoint: ProjectCheckpoint,
    ) -> PortfolioAutomationActionRead:
        Path(self._manager_workspace_path(portfolio)).mkdir(parents=True, exist_ok=True)

        preferred_engine = manager_session.metadata_json.get("preferred_engine")
        allow_simulation_fallback = manager_session.metadata_json.get("allow_simulation_fallback")
        simulation_mode = manager_session.metadata_json.get("simulation_mode")
        model = manager_session.metadata_json.get("model")
        launch_plan = self.session_supervisor.plan_session(
            role=SessionRole.MANAGER,
            command_override=manager_session.command,
            runtime_preference=str(preferred_engine) if preferred_engine else None,
            allow_simulation_fallback=(
                bool(allow_simulation_fallback) if allow_simulation_fallback is not None else True
            ),
            simulation_mode=bool(simulation_mode) if simulation_mode is not None else None,
        )

        metadata: dict[str, Any] = {
            "session_kind": "portfolio_manager_turn",
            "checkpoint_id": str(checkpoint.id),
            "checkpoint_kind": checkpoint.kind.value,
            "preferred_engine": preferred_engine or "auto",
            "allow_simulation_fallback": allow_simulation_fallback,
            "simulation_mode": launch_plan.simulation_mode,
            "launch_notes": launch_plan.notes,
            "runtime": launch_plan.runtime.model_dump(mode="json"),
            "automation": {
                "checkpoint_id": str(checkpoint.id),
                "manager_session_id": str(manager_session.id),
                "launched_at": datetime.now(UTC).isoformat(),
            },
        }
        if model is not None:
            metadata["model"] = str(model)

        turn_session = SessionModel(
            portfolio_id=portfolio.id,
            project_id=checkpoint.project_id,
            supervisor_session_id=manager_session.id,
            role=SessionRole.MANAGER,
            status=launch_plan.initial_status,
            transport=launch_plan.transport,
            adapter_kind=launch_plan.adapter_kind,
            command=launch_plan.command,
            workspace_path=manager_session.workspace_path or self._manager_workspace_path(portfolio),
            blocked_reason=launch_plan.blocked_reason,
            metadata_json=metadata,
            runtime_metadata_json={},
        )
        db.add(turn_session)
        db.flush()

        self._set_checkpoint_automation_state(
            db,
            checkpoint,
            {
                "state": "running",
                "turn_session_id": str(turn_session.id),
                "manager_session_id": str(manager_session.id),
                "launched_at": datetime.now(UTC).isoformat(),
            },
        )
        db.commit()
        db.refresh(turn_session)

        if turn_session.status == SessionStatus.PENDING:
            try:
                self.session_supervisor.start_session(turn_session.id)
            except Exception as exc:
                return self._handoff_to_human(
                    db=db,
                    services=services,
                    portfolio=portfolio,
                    checkpoint=checkpoint,
                    turn_session=turn_session,
                    reason=f"turn_start_failed:{exc}",
                )
        elif turn_session.status == SessionStatus.BLOCKED:
            return self._handoff_to_human(
                db=db,
                services=services,
                portfolio=portfolio,
                checkpoint=checkpoint,
                turn_session=turn_session,
                reason=turn_session.blocked_reason or "turn_launch_blocked",
            )

        services.event_service.record_event(
            EventCreate(
                category=EventCategory.PROJECT,
                event_type="portfolio.manager_turn_started",
                level=EventLevel.INFO,
                source=EventSourceRef(kind="service", role=SessionRole.MANAGER, id="portfolio-automation"),
                project_id=checkpoint.project_id,
                session_id=turn_session.id,
                payload={
                    "portfolio_id": str(portfolio.id),
                    "checkpoint_id": str(checkpoint.id),
                    "manager_session_id": str(manager_session.id),
                },
            )
        )
        return self._action(
            kind="manager_turn_started",
            portfolio_id=portfolio.id,
            project_id=checkpoint.project_id,
            checkpoint_id=checkpoint.id,
            session_id=turn_session.id,
            detail=f"Started a portfolio manager turn for checkpoint {checkpoint.id}.",
        )

    # ── Portfolio planning (goal decomposition) ──

    def _maybe_plan_portfolio(
        self,
        *,
        db: Session,
        services: _ScopedServices,
        portfolio: Portfolio,
        manager_session: SessionModel | None,
    ) -> PortfolioAutomationActionRead | None:
        """If the portfolio has a goal but no projects, launch a planning turn."""
        if not self._needs_planning(db, portfolio):
            return None
        if manager_session is None:
            return None

        # Check if a planning turn is already running
        active = self._active_planning_turn(db, portfolio.id)
        if active is not None:
            return self._action(
                kind="planning_turn_active",
                portfolio_id=portfolio.id,
                session_id=active.id,
                detail="A portfolio planning turn is already running.",
            )

        # Check if a completed planning turn needs to be resolved
        resolve_action = self._process_completed_planning_turns(
            db=db,
            services=services,
            portfolio=portfolio,
        )
        if resolve_action is not None:
            return resolve_action

        # If still needs planning after resolving, launch a new turn
        if not self._needs_planning(db, portfolio):
            return None

        return self._launch_planning_turn(
            db=db,
            services=services,
            portfolio=portfolio,
            manager_session=manager_session,
        )

    def _needs_planning(self, db: Session, portfolio: Portfolio) -> bool:
        """Portfolio needs planning if it has a goal but no projects.

        Returns False if a planning turn has already been attempted (even if it failed),
        to prevent infinite retry loops.
        """
        if not portfolio.goal or not portfolio.goal.strip():
            return False
        project_count = db.scalar(
            select(sa_func.count(Project.id)).where(
                Project.portfolio_id == portfolio.id,
                Project.status == ProjectStatus.ACTIVE,
            )
        )
        if project_count > 0:
            return False
        # If any planning turns have already been attempted, don't retry
        existing_planning_turns = self._planning_turn_sessions(db, portfolio.id)
        if existing_planning_turns:
            return False
        return True

    def _planning_turn_sessions(self, db: Session, portfolio_id: UUID) -> list[SessionModel]:
        sessions = db.scalars(
            select(SessionModel)
            .where(
                SessionModel.portfolio_id == portfolio_id,
                SessionModel.role == SessionRole.MANAGER,
                SessionModel.supervisor_session_id.is_not(None),
            )
            .order_by(SessionModel.created_at.asc())
        ).all()
        return [
            session
            for session in sessions
            if str(session.metadata_json.get("session_kind") or "") == "portfolio_planning_turn"
        ]

    def _active_planning_turn(self, db: Session, portfolio_id: UUID) -> SessionModel | None:
        for session in reversed(self._planning_turn_sessions(db, portfolio_id)):
            if self._turn_processed(session):
                continue
            if session.status in ACTIVE_SESSION_STATUSES:
                return session
        return None

    def _launch_planning_turn(
        self,
        *,
        db: Session,
        services: _ScopedServices,
        portfolio: Portfolio,
        manager_session: SessionModel,
    ) -> PortfolioAutomationActionRead:
        Path(self._manager_workspace_path(portfolio)).mkdir(parents=True, exist_ok=True)

        preferred_engine = manager_session.metadata_json.get("preferred_engine")
        allow_simulation_fallback = manager_session.metadata_json.get("allow_simulation_fallback")
        simulation_mode = manager_session.metadata_json.get("simulation_mode")
        model = manager_session.metadata_json.get("model")
        launch_plan = self.session_supervisor.plan_session(
            role=SessionRole.MANAGER,
            command_override=manager_session.command,
            runtime_preference=str(preferred_engine) if preferred_engine else None,
            allow_simulation_fallback=(
                bool(allow_simulation_fallback) if allow_simulation_fallback is not None else True
            ),
            simulation_mode=bool(simulation_mode) if simulation_mode is not None else None,
        )

        metadata: dict[str, Any] = {
            "session_kind": "portfolio_planning_turn",
            "preferred_engine": preferred_engine or "auto",
            "allow_simulation_fallback": allow_simulation_fallback,
            "simulation_mode": launch_plan.simulation_mode,
            "launch_notes": launch_plan.notes,
            "runtime": launch_plan.runtime.model_dump(mode="json"),
            "automation": {
                "manager_session_id": str(manager_session.id),
                "launched_at": datetime.now(UTC).isoformat(),
            },
        }
        if model is not None:
            metadata["model"] = str(model)

        turn_session = SessionModel(
            portfolio_id=portfolio.id,
            project_id=None,
            supervisor_session_id=manager_session.id,
            role=SessionRole.MANAGER,
            status=launch_plan.initial_status,
            transport=launch_plan.transport,
            adapter_kind=launch_plan.adapter_kind,
            command=launch_plan.command,
            workspace_path=manager_session.workspace_path or self._manager_workspace_path(portfolio),
            blocked_reason=launch_plan.blocked_reason,
            metadata_json=metadata,
            runtime_metadata_json={},
        )
        db.add(turn_session)
        db.flush()
        db.commit()
        db.refresh(turn_session)

        if turn_session.status == SessionStatus.PENDING:
            try:
                self.session_supervisor.start_session(turn_session.id)
            except Exception as exc:
                self._mark_turn_processed(db, turn_session, reason=f"planning_start_failed:{exc}")
                return self._action(
                    kind="planning_turn_failed",
                    portfolio_id=portfolio.id,
                    session_id=turn_session.id,
                    detail=f"Failed to start planning turn: {exc}",
                )
        elif turn_session.status == SessionStatus.BLOCKED:
            self._mark_turn_processed(db, turn_session, reason=turn_session.blocked_reason or "planning_blocked")
            return self._action(
                kind="planning_turn_blocked",
                portfolio_id=portfolio.id,
                session_id=turn_session.id,
                detail=f"Planning turn blocked: {turn_session.blocked_reason}",
            )

        services.event_service.record_event(
            EventCreate(
                category=EventCategory.PROJECT,
                event_type="portfolio.planning_turn_started",
                level=EventLevel.INFO,
                source=EventSourceRef(kind="service", role=SessionRole.MANAGER, id="portfolio-automation"),
                payload={
                    "portfolio_id": str(portfolio.id),
                    "manager_session_id": str(manager_session.id),
                    "planning_session_id": str(turn_session.id),
                },
            )
        )
        return self._action(
            kind="planning_turn_started",
            portfolio_id=portfolio.id,
            session_id=turn_session.id,
            detail=f"Started a portfolio planning turn to decompose the goal.",
        )

    def _process_completed_planning_turns(
        self,
        *,
        db: Session,
        services: _ScopedServices,
        portfolio: Portfolio,
    ) -> PortfolioAutomationActionRead | None:
        for turn_session in self._planning_turn_sessions(db, portfolio.id):
            if self._turn_processed(turn_session):
                continue
            if turn_session.status not in TERMINAL_SESSION_STATUSES:
                continue
            return self._resolve_completed_planning_turn(
                db=db,
                services=services,
                portfolio=portfolio,
                turn_session=turn_session,
            )
        return None

    def _resolve_completed_planning_turn(
        self,
        *,
        db: Session,
        services: _ScopedServices,
        portfolio: Portfolio,
        turn_session: SessionModel,
    ) -> PortfolioAutomationActionRead:
        if turn_session.status != SessionStatus.COMPLETED:
            self._mark_turn_processed(db, turn_session, reason=f"planning_turn_{turn_session.status.value}")
            return self._action(
                kind="planning_turn_failed",
                portfolio_id=portfolio.id,
                session_id=turn_session.id,
                detail=f"Planning turn ended with status {turn_session.status.value}.",
            )

        complete_event = db.scalar(
            select(ParsedSessionEvent)
            .where(
                ParsedSessionEvent.session_id == turn_session.id,
                ParsedSessionEvent.event_type == StructuredEventType.COMPLETE,
            )
            .order_by(ParsedSessionEvent.sequence.desc())
        )
        if complete_event is None:
            ended_at = self._coerce_utc(turn_session.ended_at)
            if ended_at is None or (datetime.now(UTC) - ended_at).total_seconds() < 1.0:
                return self._action(
                    kind="planning_turn_waiting_for_events",
                    portfolio_id=portfolio.id,
                    session_id=turn_session.id,
                    detail="Planning turn finished, but structured events are still settling.",
                )
            self._mark_turn_processed(db, turn_session, reason="no_complete_event")
            return self._action(
                kind="planning_turn_failed",
                portfolio_id=portfolio.id,
                session_id=turn_session.id,
                detail="Planning turn completed without a structured complete event.",
            )

        payload = dict(complete_event.payload_json)
        result = str(payload.get("result") or "").strip().lower()

        if result == "decompose":
            return self._handle_decompose_result(
                db=db,
                services=services,
                portfolio=portfolio,
                turn_session=turn_session,
                payload=payload,
            )
        elif result == "single_project":
            return self._handle_single_project_result(
                db=db,
                services=services,
                portfolio=portfolio,
                turn_session=turn_session,
                payload=payload,
            )
        else:
            self._mark_turn_processed(db, turn_session, reason=f"unknown_planning_result:{result}")
            return self._action(
                kind="planning_turn_failed",
                portfolio_id=portfolio.id,
                session_id=turn_session.id,
                detail=f"Planning turn returned unknown result: {result}",
            )

    def _handle_decompose_result(
        self,
        *,
        db: Session,
        services: _ScopedServices,
        portfolio: Portfolio,
        turn_session: SessionModel,
        payload: dict[str, Any],
    ) -> PortfolioAutomationActionRead:
        projects_data = payload.get("projects")
        if not isinstance(projects_data, list) or len(projects_data) < 1:
            self._mark_turn_processed(db, turn_session, reason="decompose_no_projects")
            return self._action(
                kind="planning_turn_failed",
                portfolio_id=portfolio.id,
                session_id=turn_session.id,
                detail="Decompose result missing projects list.",
            )

        created_project_ids: list[UUID] = []
        # Create a parent project to group sub-projects
        try:
            parent_project = services.project_service.create_project(
                ProjectCreate(
                    portfolio_id=portfolio.id,
                    name=f"{portfolio.name} - Root",
                    create_repo=True,
                    objective=portfolio.goal,
                    metadata={"is_parent": True, "decomposed": True},
                )
            )
        except Exception as exc:
            self._mark_turn_processed(db, turn_session, reason=f"parent_project_create_failed:{exc}")
            return self._action(
                kind="planning_turn_failed",
                portfolio_id=portfolio.id,
                session_id=turn_session.id,
                detail=f"Failed to create parent project: {exc}",
            )

        for sub in projects_data:
            if not isinstance(sub, dict):
                continue
            name = str(sub.get("name") or "").strip()
            objective = str(sub.get("objective") or "").strip()
            if not name or not objective:
                continue

            try:
                sub_project = services.project_service.create_project(
                    ProjectCreate(
                        portfolio_id=portfolio.id,
                        parent_project_id=parent_project.id,
                        name=name,
                        create_repo=True,
                        objective=objective,
                        metadata={"decomposed_from": str(parent_project.id)},
                    )
                )
                created_project_ids.append(sub_project.id)
            except Exception as exc:
                logger.warning(
                    "Failed to create sub-project %r for portfolio %s: %s",
                    name,
                    portfolio.id,
                    exc,
                )
                continue

        if not created_project_ids:
            self._mark_turn_processed(db, turn_session, reason="decompose_all_projects_failed")
            return self._action(
                kind="planning_turn_failed",
                portfolio_id=portfolio.id,
                session_id=turn_session.id,
                detail="Decompose result: all sub-project creations failed.",
            )

        # Start workers on each sub-project
        started_count = 0
        for project_id in created_project_ids:
            try:
                services.project_service.start_project(
                    project_id,
                    ProjectStartRequest(
                        runtime_preference=str(
                            turn_session.metadata_json.get("preferred_engine") or "auto"
                        ),
                        allow_simulation_fallback=turn_session.metadata_json.get("allow_simulation_fallback"),
                        simulation_mode=turn_session.metadata_json.get("simulation_mode"),
                        model=turn_session.metadata_json.get("model"),
                        metadata={"auto_started_by_planning": True},
                    ),
                )
                started_count += 1
            except Exception as exc:
                logger.warning(
                    "Failed to start sub-project %s: %s",
                    project_id,
                    exc,
                )

        self._mark_turn_processed(db, turn_session, reason=f"decomposed:{len(created_project_ids)}_projects")

        services.event_service.record_event(
            EventCreate(
                category=EventCategory.PROJECT,
                event_type="portfolio.planning_decomposed",
                level=EventLevel.INFO,
                source=EventSourceRef(kind="service", role=SessionRole.MANAGER, id="portfolio-automation"),
                payload={
                    "portfolio_id": str(portfolio.id),
                    "parent_project_id": str(parent_project.id),
                    "sub_project_ids": [str(pid) for pid in created_project_ids],
                    "started_count": started_count,
                },
            )
        )
        return self._action(
            kind="planning_decomposed",
            portfolio_id=portfolio.id,
            session_id=turn_session.id,
            detail=f"Decomposed portfolio goal into {len(created_project_ids)} sub-projects, started {started_count} workers.",
            payload={
                "parent_project_id": str(parent_project.id),
                "sub_project_ids": [str(pid) for pid in created_project_ids],
            },
        )

    def _handle_single_project_result(
        self,
        *,
        db: Session,
        services: _ScopedServices,
        portfolio: Portfolio,
        turn_session: SessionModel,
        payload: dict[str, Any],
    ) -> PortfolioAutomationActionRead:
        project_data = payload.get("project")
        if not isinstance(project_data, dict):
            project_data = {"name": portfolio.name, "objective": portfolio.goal}

        name = str(project_data.get("name") or portfolio.name).strip()
        objective = str(project_data.get("objective") or portfolio.goal).strip()

        try:
            project = services.project_service.create_project(
                ProjectCreate(
                    portfolio_id=portfolio.id,
                    name=name,
                    create_repo=True,
                    objective=objective,
                    metadata={"single_project_planning": True},
                )
            )
        except Exception as exc:
            self._mark_turn_processed(db, turn_session, reason=f"single_project_create_failed:{exc}")
            return self._action(
                kind="planning_turn_failed",
                portfolio_id=portfolio.id,
                session_id=turn_session.id,
                detail=f"Failed to create project: {exc}",
            )

        try:
            services.project_service.start_project(
                project.id,
                ProjectStartRequest(
                    runtime_preference=str(
                        turn_session.metadata_json.get("preferred_engine") or "auto"
                    ),
                    allow_simulation_fallback=turn_session.metadata_json.get("allow_simulation_fallback"),
                    simulation_mode=turn_session.metadata_json.get("simulation_mode"),
                    model=turn_session.metadata_json.get("model"),
                    metadata={"auto_started_by_planning": True},
                ),
            )
        except Exception as exc:
            logger.warning("Failed to start project %s: %s", project.id, exc)

        self._mark_turn_processed(db, turn_session, reason="single_project_created")

        services.event_service.record_event(
            EventCreate(
                category=EventCategory.PROJECT,
                event_type="portfolio.planning_single_project",
                level=EventLevel.INFO,
                source=EventSourceRef(kind="service", role=SessionRole.MANAGER, id="portfolio-automation"),
                payload={
                    "portfolio_id": str(portfolio.id),
                    "project_id": str(project.id),
                },
            )
        )
        return self._action(
            kind="planning_single_project",
            portfolio_id=portfolio.id,
            session_id=turn_session.id,
            detail=f"Created single project '{name}' and started worker.",
            payload={"project_id": str(project.id)},
        )

    def _handoff_to_human(
        self,
        *,
        db: Session,
        services: _ScopedServices,
        portfolio: Portfolio,
        checkpoint: ProjectCheckpoint,
        turn_session: SessionModel,
        reason: str,
    ) -> PortfolioAutomationActionRead:
        self._set_checkpoint_automation_state(
            db,
            checkpoint,
            {
                "state": "waiting_for_human",
                "turn_session_id": str(turn_session.id),
                "manager_session_id": str(turn_session.supervisor_session_id) if turn_session.supervisor_session_id else None,
                "reason": reason,
                "updated_at": datetime.now(UTC).isoformat(),
            },
        )
        self._mark_turn_processed(db, turn_session, reason=reason)
        services.event_service.record_event(
            EventCreate(
                category=EventCategory.PROJECT,
                event_type="portfolio.manager_turn_human_input_required",
                level=EventLevel.WARN,
                source=EventSourceRef(kind="service", role=SessionRole.MANAGER, id="portfolio-automation"),
                project_id=checkpoint.project_id,
                session_id=turn_session.id,
                payload={
                    "portfolio_id": str(portfolio.id),
                    "checkpoint_id": str(checkpoint.id),
                    "reason": reason,
                },
            )
        )
        return self._action(
            kind="manager_turn_human_input_required",
            portfolio_id=portfolio.id,
            project_id=checkpoint.project_id,
            checkpoint_id=checkpoint.id,
            session_id=turn_session.id,
            detail=f"Manager automation handed checkpoint {checkpoint.id} back to a human: {reason}.",
        )

    def _mark_turn_processed(self, db: Session, turn_session: SessionModel, *, reason: str) -> None:
        session_record = db.get(SessionModel, turn_session.id)
        if session_record is None:
            return
        metadata = dict(session_record.metadata_json)
        automation = metadata.get("automation")
        if not isinstance(automation, dict):
            automation = {}
        automation.update(
            {
                "processed_at": datetime.now(UTC).isoformat(),
                "processed_reason": reason,
            }
        )
        metadata["automation"] = automation
        metadata["automation_processed_at"] = automation["processed_at"]
        session_record.metadata_json = metadata
        db.commit()
        db.refresh(session_record)

    def _open_checkpoints(self, db: Session, portfolio_id: UUID) -> list[ProjectCheckpoint]:
        return db.scalars(
            select(ProjectCheckpoint)
            .where(
                ProjectCheckpoint.portfolio_id == portfolio_id,
                ProjectCheckpoint.status == ProjectCheckpointStatus.OPEN,
            )
            .order_by(ProjectCheckpoint.source_occurred_at.asc(), ProjectCheckpoint.created_at.asc())
        ).all()

    @staticmethod
    def _map_result_to_action(
        checkpoint_kind: ProjectCheckpointKind,
        result: str,
    ) -> ProjectCheckpointAction | None:
        if not result:
            return None
        if checkpoint_kind == ProjectCheckpointKind.COMPLETION:
            if result == ProjectCheckpointAction.APPROVE.value:
                return ProjectCheckpointAction.APPROVE
            if result == ProjectCheckpointAction.REQUEST_CHANGES.value:
                return ProjectCheckpointAction.REQUEST_CHANGES
            if result == ProjectCheckpointAction.DISMISS.value:
                return ProjectCheckpointAction.DISMISS
            return None
        if result == ProjectCheckpointAction.ANSWER.value:
            return ProjectCheckpointAction.ANSWER
        if result == ProjectCheckpointAction.DISMISS.value:
            return ProjectCheckpointAction.DISMISS
        return None

    @staticmethod
    def _automation_state(checkpoint: ProjectCheckpoint) -> dict[str, Any]:
        if not isinstance(checkpoint.response_details_json, dict):
            return {}
        automation = checkpoint.response_details_json.get("automation")
        return automation if isinstance(automation, dict) else {}

    def _set_checkpoint_automation_state(
        self,
        db: Session,
        checkpoint: ProjectCheckpoint,
        state: dict[str, Any],
    ) -> None:
        checkpoint_record = db.get(ProjectCheckpoint, checkpoint.id)
        if checkpoint_record is None:
            return
        response_details = (
            dict(checkpoint_record.response_details_json)
            if isinstance(checkpoint_record.response_details_json, dict)
            else {}
        )
        response_details["automation"] = state
        checkpoint_record.response_details_json = response_details
        db.commit()
        db.refresh(checkpoint_record)

    @staticmethod
    def _turn_processed(session: SessionModel) -> bool:
        return bool(session.metadata_json.get("automation_processed_at"))

    @staticmethod
    def _checkpoint_id_from_session(session: SessionModel) -> UUID | None:
        return PortfolioAutomationService._uuid_from_value(
            session.metadata_json.get("checkpoint_id")
            or (session.metadata_json.get("automation", {}) or {}).get("checkpoint_id")
        )

    @staticmethod
    def _uuid_from_value(value: Any) -> UUID | None:
        if value is None:
            return None
        try:
            return UUID(str(value))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _coerce_utc(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def _manager_workspace_path(self, portfolio: Portfolio) -> str:
        return str(
            Path(self.settings.orchestrator_workspaces_root).expanduser().resolve()
            / "_portfolio_managers"
            / portfolio.slug
        )

    @staticmethod
    def _action(
        *,
        kind: str,
        portfolio_id: UUID,
        detail: str,
        project_id: UUID | None = None,
        checkpoint_id: UUID | None = None,
        session_id: UUID | None = None,
        payload: dict[str, Any] | None = None,
    ) -> PortfolioAutomationActionRead:
        return PortfolioAutomationActionRead(
            kind=kind,
            portfolio_id=portfolio_id,
            project_id=project_id,
            checkpoint_id=checkpoint_id,
            session_id=session_id,
            detail=detail,
            payload=payload or {},
        )
