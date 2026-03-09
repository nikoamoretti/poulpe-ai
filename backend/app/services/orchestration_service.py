from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from itertools import combinations
from pathlib import PurePosixPath
from uuid import UUID

from sqlalchemy import select

from app.adapters.repo_inspector import RepoInspectorAdapter
from app.adapters.redis_bus import RedisBusAdapter
from app.core.config import Settings
from app.core.database import DatabaseManager
from app.core.enums import (
    EventCategory,
    EventLevel,
    ProjectStatus,
    SessionRole,
    SessionStatus,
    TaskStatus,
    WorkspaceStatus,
)
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.event_stream import EventStreamBroker
from app.models.event import Event
from app.models.project import Project
from app.models.review import Review
from app.models.session import Session as SessionModel
from app.models.task import Task
from app.models.workspace import Workspace
from app.schemas.event import EventCreate, EventSourceRef
from app.schemas.orchestrator import (
    OrchestratorActionRead,
    OrchestratorProjectTickRead,
    OrchestratorTickRead,
)
from app.schemas.review import ReviewCreate
from app.schemas.task import (
    TaskAssignmentRead,
    TaskAssignmentRequest,
    TaskBlockedRequest,
    TaskCompletedRequest,
    TaskRead,
)
from app.services.event_service import EventService
from app.services.review_service import ReviewService
from app.services.command_runner import CommandRunner
from app.services.session_supervisor import SessionSupervisor
from app.services.worktree_manager import WorktreeManager
from app.services.workspace_service import WorkspaceService

logger = logging.getLogger(__name__)

ACTIVE_TASK_STATUSES = {
    TaskStatus.PENDING,
    TaskStatus.IN_PROGRESS,
    TaskStatus.BLOCKED,
    TaskStatus.REVIEW,
}
TERMINAL_TASK_STATUSES = {
    TaskStatus.DONE,
    TaskStatus.CANCELED,
}
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


class OrchestratorService:
    def __init__(
        self,
        *,
        settings: Settings,
        database: DatabaseManager,
        redis_bus: RedisBusAdapter,
        event_broker: EventStreamBroker,
        worktree_manager: WorktreeManager,
        repo_inspector: RepoInspectorAdapter,
        command_runner: CommandRunner,
        session_supervisor: SessionSupervisor,
    ) -> None:
        self.settings = settings
        self.database = database
        self.redis_bus = redis_bus
        self.event_broker = event_broker
        self.worktree_manager = worktree_manager
        self.repo_inspector = repo_inspector
        self.command_runner = command_runner
        self.session_supervisor = session_supervisor

    def describe_flow(self) -> list[str]:
        return [
            "poll new project events since the last orchestrator cursor",
            "reconcile dependency, session, and workspace state for each assigned task",
            "block conflicting or stalled work deterministically",
            "queue review when a worker session completes successfully",
            "request a summary when a running session has gone silent too long",
        ]

    def assign_task(self, task_id: UUID, payload: TaskAssignmentRequest) -> TaskAssignmentRead:
        now = datetime.now(UTC)
        with self.database.session() as db:
            event_service = self._event_service(db)
            task = db.get(Task, task_id)
            if task is None:
                raise NotFoundError(f"Task not found: {task_id}")

            session = db.get(SessionModel, payload.session_id)
            if session is None:
                raise NotFoundError(f"Session not found: {payload.session_id}")
            if session.project_id != task.project_id:
                raise ValidationError("Task and session must belong to the same project.")
            if session.role != SessionRole.WORKER:
                raise ValidationError("Only worker sessions can be assigned coding tasks.")
            if session.task_id is not None and session.task_id != task.id:
                raise ValidationError(
                    "This v0 orchestrator only supports sessions already attached to the same task."
                )

            allowed_paths = self._normalize_allowed_paths(payload.allowed_paths)
            dependency_ids = self._validate_dependency_ids(db, task, payload.dependency_task_ids)
            conflicts = self._detect_assignment_scope_conflicts(db, task, allowed_paths)
            if conflicts:
                raise ConflictError("Assignment conflicts detected: " + "; ".join(conflicts))

            unresolved_dependencies = [
                dependency_id
                for dependency_id in dependency_ids
                if (dependency := db.get(Task, dependency_id)) is not None and dependency.status != TaskStatus.DONE
            ]

            task_metadata = dict(task.metadata_json)
            orchestration = self._task_orchestration(task_metadata)
            orchestration.update(
                {
                    "assigned_session_id": str(session.id),
                    "allowed_paths": allowed_paths,
                    "dependency_task_ids": [str(dependency_id) for dependency_id in dependency_ids],
                    "active_dependency_task_ids": [str(dependency_id) for dependency_id in unresolved_dependencies],
                    "assignment_note": payload.note,
                    "last_assigned_at": now.isoformat(),
                    "conflicts": [],
                }
            )
            if unresolved_dependencies:
                orchestration["blocked_reason"] = "waiting_on_dependencies"
                task.status = TaskStatus.BLOCKED
                task_event_type = "task.dependencies_waiting"
                task_detail = (
                    f"Task {task.id} is waiting on dependencies: "
                    + ", ".join(str(dependency_id) for dependency_id in unresolved_dependencies)
                )
            else:
                orchestration["blocked_reason"] = None
                task.status = TaskStatus.IN_PROGRESS
                task_event_type = "task.assigned"
                task_detail = f"Assigned task {task.id} to session {session.id}"

            task.metadata_json = self._store_task_orchestration(task_metadata, orchestration)

            session_metadata = dict(session.metadata_json)
            session_metadata["assignment"] = {
                "task_id": str(task.id),
                "allowed_paths": allowed_paths,
                "dependency_task_ids": [str(dependency_id) for dependency_id in dependency_ids],
            }
            session.metadata_json = session_metadata

            db.commit()
            db.refresh(task)

            event_service.record_event(
                EventCreate(
                    category=EventCategory.TASK,
                    event_type=task_event_type,
                    level=EventLevel.INFO if not unresolved_dependencies else EventLevel.WARN,
                    source=EventSourceRef(kind="service", id="orchestrator.assign"),
                    project_id=task.project_id,
                    task_id=task.id,
                    session_id=session.id,
                    payload={
                        "allowed_paths": allowed_paths,
                        "dependency_task_ids": [str(dependency_id) for dependency_id in dependency_ids],
                        "unresolved_dependency_task_ids": [
                            str(dependency_id) for dependency_id in unresolved_dependencies
                        ],
                    },
                )
            )

            logger.info(task_detail)
            return TaskAssignmentRead(
                task=TaskRead.model_validate(task),
                assigned_session_id=session.id,
                allowed_paths=allowed_paths,
                dependency_task_ids=dependency_ids,
                conflicts=conflicts,
            )

    def mark_task_blocked(self, task_id: UUID, payload: TaskBlockedRequest) -> TaskRead:
        with self.database.session() as db:
            event_service = self._event_service(db)
            task = db.get(Task, task_id)
            if task is None:
                raise NotFoundError(f"Task not found: {task_id}")

            task_metadata = dict(task.metadata_json)
            orchestration = self._task_orchestration(task_metadata)
            orchestration["blocked_reason"] = payload.reason
            orchestration["blocked_at"] = datetime.now(UTC).isoformat()
            orchestration["blocked_note"] = payload.note
            task.metadata_json = self._store_task_orchestration(task_metadata, orchestration)
            task.status = TaskStatus.BLOCKED
            db.commit()
            db.refresh(task)

            event_service.record_event(
                EventCreate(
                    category=EventCategory.TASK,
                    event_type="task.blocked",
                    level=EventLevel.WARN,
                    source=EventSourceRef(kind="api", id="tasks.block"),
                    project_id=task.project_id,
                    task_id=task.id,
                    payload={"reason": payload.reason, "note": payload.note},
                )
            )
            return TaskRead.model_validate(task)

    def mark_task_completed(self, task_id: UUID, payload: TaskCompletedRequest) -> TaskRead:
        with self.database.session() as db:
            event_service = self._event_service(db)
            task = db.get(Task, task_id)
            if task is None:
                raise NotFoundError(f"Task not found: {task_id}")

            task_metadata = dict(task.metadata_json)
            orchestration = self._task_orchestration(task_metadata)
            orchestration["blocked_reason"] = None
            orchestration["completed_at"] = datetime.now(UTC).isoformat()
            orchestration["completion_summary"] = payload.summary
            orchestration["completion_note"] = payload.note
            task.metadata_json = self._store_task_orchestration(task_metadata, orchestration)
            task.status = TaskStatus.DONE
            db.commit()
            db.refresh(task)

            event_service.record_event(
                EventCreate(
                    category=EventCategory.TASK,
                    event_type="task.completed",
                    level=EventLevel.INFO,
                    source=EventSourceRef(kind="api", id="tasks.complete"),
                    project_id=task.project_id,
                    task_id=task.id,
                    payload={"summary": payload.summary, "note": payload.note},
                )
            )
            return TaskRead.model_validate(task)

    def tick(self, *, project_id: UUID | None = None) -> OrchestratorTickRead:
        started_at = datetime.now(UTC)
        project_ids = self._project_ids(project_id)
        project_results = [self._tick_project(current_project_id) for current_project_id in project_ids]
        completed_at = datetime.now(UTC)
        logger.info(
            "orchestrator tick completed",
            extra={
                "projects": [str(result.project_id) for result in project_results],
                "actions": sum(len(result.actions) for result in project_results),
            },
        )
        return OrchestratorTickRead(
            started_at=started_at,
            completed_at=completed_at,
            projects=project_results,
        )

    def _tick_project(self, project_id: UUID) -> OrchestratorProjectTickRead:
        queued_summary_requests: list[tuple[UUID, UUID]] = []
        with self.database.session() as db:
            event_service = self._event_service(db)
            workspace_service = WorkspaceService(
                db=db,
                event_service=event_service,
                worktree_manager=self.worktree_manager,
                repo_inspector=self.repo_inspector,
                command_runner=self.command_runner,
            )
            review_service = ReviewService(
                db=db,
                event_service=event_service,
                workspace_service=workspace_service,
            )

            project = db.get(Project, project_id)
            if project is None:
                raise NotFoundError(f"Project not found: {project_id}")

            project_metadata = dict(project.metadata_json)
            project_orchestration = self._project_orchestration(project_metadata)
            last_sequence = int(project_orchestration.get("last_event_sequence", 0) or 0)

            new_events = db.scalars(
                select(Event)
                .where(Event.project_id == project_id, Event.sequence > last_sequence)
                .order_by(Event.sequence.asc())
            ).all()

            sessions = db.scalars(select(SessionModel).where(SessionModel.project_id == project_id)).all()
            tasks = db.scalars(select(Task).where(Task.project_id == project_id)).all()
            workspaces = db.scalars(select(Workspace).where(Workspace.project_id == project_id)).all()
            reviews = db.scalars(
                select(Review).where(Review.project_id == project_id).order_by(Review.created_at.asc())
            ).all()

            session_by_id = {session.id: session for session in sessions}
            workspace_by_session_id = {
                workspace.session_id: workspace
                for workspace in workspaces
                if workspace.session_id is not None
            }
            latest_review_by_task_id = {}
            for review in reviews:
                latest_review_by_task_id[review.task_id] = review

            actions: list[OrchestratorActionRead] = []
            now = datetime.now(UTC)

            for task in tasks:
                task_actions = self._reconcile_dependencies(task, session_by_id.get(self._assigned_session_id(task)))
                for action in task_actions:
                    actions.append(action)
                    self._emit_action_event(event_service, action)

            changed_files_by_task_id = self._changed_files_by_task(
                project=project,
                tasks=tasks,
                session_by_id=session_by_id,
                workspace_by_session_id=workspace_by_session_id,
            )

            for task in tasks:
                task_actions, request_summary = self._reconcile_task_state(
                    db=db,
                    review_service=review_service,
                    task=task,
                    session=session_by_id.get(self._assigned_session_id(task)),
                    latest_review=latest_review_by_task_id.get(task.id),
                    now=now,
                )
                for action in task_actions:
                    actions.append(action)
                    self._emit_action_event(event_service, action)
                if request_summary is not None:
                    queued_summary_requests.append(request_summary)

            for action in self._detect_scope_conflicts(tasks):
                if self._block_task_for_action(action):
                    actions.append(action)
                    self._emit_action_event(event_service, action)

            for action in self._detect_changed_file_conflicts(tasks, changed_files_by_task_id):
                if self._block_task_for_action(action):
                    actions.append(action)
                    self._emit_action_event(event_service, action)

            highest_sequence = max([last_sequence, *[event.sequence for event in new_events]], default=last_sequence)
            project_orchestration["last_event_sequence"] = highest_sequence
            project_metadata["orchestrator"] = project_orchestration
            project.metadata_json = project_metadata
            db.commit()

        for session_id, related_task_id in queued_summary_requests:
            self._request_session_summary(project_id=project_id, session_id=session_id, task_id=related_task_id)

        logger.info(
            "orchestrator project tick",
            extra={
                "project_id": str(project_id),
                "processed_event_count": len(new_events),
                "action_count": len(actions),
            },
        )
        return OrchestratorProjectTickRead(
            project_id=project_id,
            processed_event_count=len(new_events),
            last_event_sequence=highest_sequence,
            actions=actions,
        )

    def _reconcile_dependencies(
        self,
        task: Task,
        session: SessionModel | None,
    ) -> list[OrchestratorActionRead]:
        orchestration = self._task_orchestration(task.metadata_json)
        dependency_ids = [
            UUID(raw_dependency_id)
            for raw_dependency_id in orchestration.get("dependency_task_ids", [])
            if raw_dependency_id
        ]
        if not dependency_ids:
            return []
        actions: list[OrchestratorActionRead] = []
        with self.database.session() as db:
            unresolved = [
                dependency_id
                for dependency_id in dependency_ids
                if (dependency := db.get(Task, dependency_id)) is not None and dependency.status != TaskStatus.DONE
            ]

            task_record = db.get(Task, task.id)
            if task_record is None:
                return []
            task_metadata = dict(task_record.metadata_json)
            current_orchestration = self._task_orchestration(task_metadata)
            current_orchestration["active_dependency_task_ids"] = [str(dependency_id) for dependency_id in unresolved]

            if unresolved:
                if task_record.status != TaskStatus.BLOCKED or current_orchestration.get("blocked_reason") != "waiting_on_dependencies":
                    current_orchestration["blocked_reason"] = "waiting_on_dependencies"
                    task_record.status = TaskStatus.BLOCKED
                    actions.append(
                        OrchestratorActionRead(
                            kind="task_blocked",
                            project_id=task_record.project_id,
                            task_id=task_record.id,
                            session_id=self._assigned_session_id(task_record),
                            detail="Task is waiting on dependencies.",
                            payload={"dependency_task_ids": [str(dependency_id) for dependency_id in unresolved]},
                        )
                    )
            elif current_orchestration.get("blocked_reason") == "waiting_on_dependencies":
                current_orchestration["blocked_reason"] = None
                task_record.status = (
                    TaskStatus.IN_PROGRESS if session is not None else TaskStatus.PENDING
                )
                actions.append(
                    OrchestratorActionRead(
                        kind="task_unblocked",
                        project_id=task_record.project_id,
                        task_id=task_record.id,
                        session_id=self._assigned_session_id(task_record),
                        detail="Dependency chain resolved.",
                        payload={},
                    )
                )

            task_record.metadata_json = self._store_task_orchestration(task_metadata, current_orchestration)
            db.commit()
        return actions

    def _reconcile_task_state(
        self,
        *,
        db,
        review_service: ReviewService,
        task: Task,
        session: SessionModel | None,
        latest_review: Review | None,
        now: datetime,
    ) -> tuple[list[OrchestratorActionRead], tuple[UUID, UUID] | None]:
        actions: list[OrchestratorActionRead] = []
        request_summary: tuple[UUID, UUID] | None = None

        task_record = db.get(Task, task.id)
        if task_record is None:
            return actions, request_summary
        task_metadata = dict(task_record.metadata_json)
        orchestration = self._task_orchestration(task_metadata)

        if session is None:
            task_record.metadata_json = self._store_task_orchestration(task_metadata, orchestration)
            return actions, request_summary

        session_record = db.get(SessionModel, session.id)
        if session_record is None:
            return actions, request_summary

        if session_record.status in {SessionStatus.RUNNING, SessionStatus.STARTING}:
            if orchestration.get("blocked_reason") in {None, "waiting_on_dependencies"} and task_record.status in {
                TaskStatus.PENDING,
                TaskStatus.BLOCKED,
            }:
                task_record.status = TaskStatus.IN_PROGRESS
                orchestration["blocked_reason"] = None
        elif session_record.status == SessionStatus.BLOCKED:
            if task_record.status != TaskStatus.BLOCKED or orchestration.get("blocked_reason") != (
                session_record.blocked_reason or "session_blocked"
            ):
                task_record.status = TaskStatus.BLOCKED
                orchestration["blocked_reason"] = session_record.blocked_reason or "session_blocked"
                actions.append(
                    OrchestratorActionRead(
                        kind="task_blocked",
                        project_id=task_record.project_id,
                        task_id=task_record.id,
                        session_id=session_record.id,
                        detail="Task surfaced as blocked from the assigned session.",
                        payload={"reason": orchestration["blocked_reason"]},
                    )
                )
        elif session_record.status in {SessionStatus.FAILED, SessionStatus.STOPPED}:
            if task_record.status not in TERMINAL_TASK_STATUSES:
                task_record.status = TaskStatus.BLOCKED
                orchestration["blocked_reason"] = session_record.blocked_reason or session_record.status.value
                actions.append(
                    OrchestratorActionRead(
                        kind="task_blocked",
                        project_id=task_record.project_id,
                        task_id=task_record.id,
                        session_id=session_record.id,
                        detail="Assigned session stopped before task completion.",
                        payload={"reason": orchestration["blocked_reason"]},
                    )
                )
        elif session_record.status == SessionStatus.COMPLETED and task_record.status not in {
            TaskStatus.REVIEW,
            TaskStatus.DONE,
        }:
            if latest_review is None:
                review = review_service.create_review(
                    ReviewCreate(
                        project_id=task_record.project_id,
                        task_id=task_record.id,
                        requester_session_id=session_record.id,
                        summary="Queued automatically by the orchestrator after worker completion.",
                        metadata={"auto_queued": True},
                    )
                )
                orchestration["review_id"] = str(review.id)
                actions.append(
                    OrchestratorActionRead(
                        kind="review_queued",
                        project_id=task_record.project_id,
                        task_id=task_record.id,
                        session_id=session_record.id,
                        detail="Queued review for completed worker task.",
                        payload={"review_id": str(review.id)},
                    )
                )
            task_record.status = TaskStatus.REVIEW
            orchestration["blocked_reason"] = None
            orchestration["review_requested_at"] = now.isoformat()

        if self._session_is_idle(session_record, now=now):
            session_metadata = dict(session_record.metadata_json)
            session_orchestration = self._session_orchestration(session_metadata)
            last_requested_at = self._parse_datetime(session_orchestration.get("last_summary_request_at"))
            cooldown = timedelta(seconds=self.settings.orchestrator_summary_request_cooldown_seconds)
            if last_requested_at is None or now - last_requested_at >= cooldown:
                session_orchestration["last_summary_request_at"] = now.isoformat()
                session_metadata["orchestrator"] = session_orchestration
                session_record.metadata_json = session_metadata
                actions.append(
                    OrchestratorActionRead(
                        kind="summary_requested",
                        project_id=task_record.project_id,
                        task_id=task_record.id,
                        session_id=session_record.id,
                        detail="Requested a heartbeat or brief summary from a silent session.",
                        payload={},
                    )
                )
                request_summary = (session_record.id, task_record.id)

        task_record.metadata_json = self._store_task_orchestration(task_metadata, orchestration)
        db.commit()
        return actions, request_summary

    def _detect_scope_conflicts(self, tasks: list[Task]) -> list[OrchestratorActionRead]:
        actions: list[OrchestratorActionRead] = []
        for left, right in combinations(tasks, 2):
            if left.project_id != right.project_id:
                continue
            if left.status in TERMINAL_TASK_STATUSES or right.status in TERMINAL_TASK_STATUSES:
                continue
            left_paths = self._allowed_paths(left)
            right_paths = self._allowed_paths(right)
            overlapping_paths = self._overlapping_paths(left_paths, right_paths)
            if not overlapping_paths:
                continue
            detail = "Allowed path scopes overlap across active tasks."
            payload = {
                "other_task_id": str(right.id),
                "overlapping_paths": overlapping_paths,
            }
            actions.append(
                OrchestratorActionRead(
                    kind="scope_conflict",
                    project_id=left.project_id,
                    task_id=left.id,
                    session_id=self._assigned_session_id(left),
                    detail=detail,
                    payload=payload,
                )
            )
            actions.append(
                OrchestratorActionRead(
                    kind="scope_conflict",
                    project_id=right.project_id,
                    task_id=right.id,
                    session_id=self._assigned_session_id(right),
                    detail=detail,
                    payload={
                        "other_task_id": str(left.id),
                        "overlapping_paths": overlapping_paths,
                    },
                )
            )
        return actions

    def _detect_changed_file_conflicts(
        self,
        tasks: list[Task],
        changed_files_by_task_id: dict[UUID, set[str]],
    ) -> list[OrchestratorActionRead]:
        actions: list[OrchestratorActionRead] = []
        for left, right in combinations(tasks, 2):
            if left.project_id != right.project_id:
                continue
            left_changed_files = changed_files_by_task_id.get(left.id, set())
            right_changed_files = changed_files_by_task_id.get(right.id, set())
            overlapping_files = sorted(left_changed_files & right_changed_files)
            if not overlapping_files:
                continue
            detail = "Two active tasks are modifying the same files."
            actions.append(
                OrchestratorActionRead(
                    kind="changed_files_conflict",
                    project_id=left.project_id,
                    task_id=left.id,
                    session_id=self._assigned_session_id(left),
                    detail=detail,
                    payload={
                        "other_task_id": str(right.id),
                        "changed_files": overlapping_files,
                    },
                )
            )
            actions.append(
                OrchestratorActionRead(
                    kind="changed_files_conflict",
                    project_id=right.project_id,
                    task_id=right.id,
                    session_id=self._assigned_session_id(right),
                    detail=detail,
                    payload={
                        "other_task_id": str(left.id),
                        "changed_files": overlapping_files,
                    },
                )
            )
        return actions

    def _block_task_for_action(self, action: OrchestratorActionRead) -> bool:
        if action.task_id is None:
            return False
        with self.database.session() as db:
            task = db.get(Task, action.task_id)
            if task is None or task.status in TERMINAL_TASK_STATUSES:
                return False
            task_metadata = dict(task.metadata_json)
            orchestration = self._task_orchestration(task_metadata)
            conflict_reason = action.kind
            existing_conflicts = list(orchestration.get("conflicts", []))
            rendered_conflict = {
                "kind": action.kind,
                "detail": action.detail,
                "payload": action.payload,
            }
            if rendered_conflict in existing_conflicts and orchestration.get("blocked_reason") == conflict_reason:
                return False
            existing_conflicts.append(rendered_conflict)
            orchestration["conflicts"] = existing_conflicts
            orchestration["blocked_reason"] = conflict_reason
            task.metadata_json = self._store_task_orchestration(task_metadata, orchestration)
            task.status = TaskStatus.BLOCKED
            db.commit()
            return True

    def _changed_files_by_task(
        self,
        *,
        project: Project,
        tasks: list[Task],
        session_by_id: dict[UUID, SessionModel],
        workspace_by_session_id: dict[UUID, Workspace],
    ) -> dict[UUID, set[str]]:
        changed_files_by_task: dict[UUID, set[str]] = {}
        for task in tasks:
            session_id = self._assigned_session_id(task)
            if session_id is None:
                continue
            session = session_by_id.get(session_id)
            workspace = workspace_by_session_id.get(session_id)
            if session is None or workspace is None:
                continue
            if session.status not in ACTIVE_SESSION_STATUSES:
                continue
            if workspace.status not in {WorkspaceStatus.READY, WorkspaceStatus.DIRTY, WorkspaceStatus.PLANNED}:
                continue
            try:
                changed_files = self.worktree_manager.get_changed_files(
                    repo_path=project.repo_path,
                    workspace_path=workspace.workspace_path,
                    base_ref=workspace.base_commit,
                )
            except Exception as exc:
                logger.warning("failed to inspect changed files for task %s: %s", task.id, exc)
                continue
            changed_files_by_task[task.id] = set(changed_files)
        return changed_files_by_task

    def _request_session_summary(self, *, project_id: UUID, session_id: UUID, task_id: UUID) -> None:
        message = (
            "Emit a [[EVENT]] heartbeat or progress summary with the current status, next step, "
            "and any blockers."
        )
        try:
            self.session_supervisor.send(session_id, message)
        except Exception as exc:
            logger.warning("failed to request summary from session %s: %s", session_id, exc)
            with self.database.session() as db:
                self._event_service(db).record_event(
                    EventCreate(
                        category=EventCategory.SESSION,
                        event_type="session.summary_request_failed",
                        level=EventLevel.WARN,
                        source=EventSourceRef(kind="service", id="orchestrator.tick"),
                        project_id=project_id,
                        task_id=task_id,
                        session_id=session_id,
                        payload={"error": str(exc)},
                    )
                )
            return

        with self.database.session() as db:
            self._event_service(db).record_event(
                EventCreate(
                    category=EventCategory.SESSION,
                    event_type="session.summary_requested",
                    level=EventLevel.INFO,
                    source=EventSourceRef(kind="service", id="orchestrator.tick"),
                    project_id=project_id,
                    task_id=task_id,
                    session_id=session_id,
                    payload={"message": message},
                )
            )

    def _emit_action_event(self, event_service: EventService, action: OrchestratorActionRead) -> None:
        event_type = {
            "task_blocked": "task.blocked",
            "task_unblocked": "task.unblocked",
            "review_queued": "task.review_queued",
            "summary_requested": "session.summary_requested_pending",
            "scope_conflict": "task.conflict_detected",
            "changed_files_conflict": "task.conflict_detected",
        }.get(action.kind)
        if event_type is None:
            return
        level = EventLevel.WARN if "blocked" in action.kind or "conflict" in action.kind else EventLevel.INFO
        category = EventCategory.SESSION if action.kind == "summary_requested" else EventCategory.TASK
        event_service.record_event(
            EventCreate(
                category=category,
                event_type=event_type,
                level=level,
                source=EventSourceRef(kind="service", id="orchestrator.tick"),
                project_id=action.project_id,
                task_id=action.task_id,
                session_id=action.session_id,
                payload=action.payload | {"detail": action.detail},
            )
        )

    def _project_ids(self, project_id: UUID | None) -> list[UUID]:
        with self.database.session() as db:
            if project_id is not None:
                project = db.get(Project, project_id)
                if project is None:
                    raise NotFoundError(f"Project not found: {project_id}")
                return [project_id]

            return [
                project.id
                for project in db.scalars(
                    select(Project).where(Project.status == ProjectStatus.ACTIVE).order_by(Project.created_at.asc())
                ).all()
            ]

    def _validate_dependency_ids(self, db, task: Task, dependency_task_ids: list[UUID]) -> list[UUID]:
        unique_ids: list[UUID] = []
        for dependency_id in dependency_task_ids:
            if dependency_id == task.id:
                raise ValidationError("A task cannot depend on itself.")
            if dependency_id in unique_ids:
                continue
            dependency = db.get(Task, dependency_id)
            if dependency is None:
                raise NotFoundError(f"Dependency task not found: {dependency_id}")
            if dependency.project_id != task.project_id:
                raise ValidationError("Dependency tasks must belong to the same project.")
            unique_ids.append(dependency_id)
        return unique_ids

    def _detect_assignment_scope_conflicts(self, db, task: Task, allowed_paths: list[str]) -> list[str]:
        if not allowed_paths:
            return []
        conflicts: list[str] = []
        other_tasks = db.scalars(select(Task).where(Task.project_id == task.project_id, Task.id != task.id)).all()
        for other_task in other_tasks:
            if other_task.status in TERMINAL_TASK_STATUSES:
                continue
            other_allowed_paths = self._allowed_paths(other_task)
            overlapping_paths = self._overlapping_paths(allowed_paths, other_allowed_paths)
            if not overlapping_paths:
                continue
            conflicts.append(
                f"task {other_task.id} overlaps on {', '.join(overlapping_paths)}"
            )
        return conflicts

    @staticmethod
    def _normalize_allowed_paths(paths: list[str]) -> list[str]:
        normalized_paths: list[str] = []
        for raw_path in paths:
            candidate = raw_path.strip().replace("\\", "/").strip("/")
            if not candidate:
                continue
            path = PurePosixPath(candidate)
            if str(path).startswith("../") or ".." in path.parts:
                raise ValidationError(f"Allowed paths must be repo-relative and stay within the repo: {raw_path}")
            rendered = str(path)
            if rendered not in normalized_paths:
                normalized_paths.append(rendered)
        return sorted(normalized_paths)

    def _session_is_idle(self, session: SessionModel, *, now: datetime) -> bool:
        if session.status not in {SessionStatus.STARTING, SessionStatus.RUNNING}:
            return False
        if session.last_heartbeat_at is None:
            return True
        last_heartbeat_at = session.last_heartbeat_at
        if last_heartbeat_at.tzinfo is None:
            last_heartbeat_at = last_heartbeat_at.replace(tzinfo=UTC)
        return now - last_heartbeat_at >= timedelta(
            seconds=session.metadata_json.get(
                "orchestrator_idle_seconds_override",
                self.settings.orchestrator_idle_session_seconds,
            )
        )

    @staticmethod
    def _parse_datetime(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None

    @staticmethod
    def _task_orchestration(metadata: dict) -> dict:
        return dict(metadata.get("orchestrator", {}))

    @staticmethod
    def _session_orchestration(metadata: dict) -> dict:
        return dict(metadata.get("orchestrator", {}))

    @staticmethod
    def _project_orchestration(metadata: dict) -> dict:
        return dict(metadata.get("orchestrator", {}))

    @staticmethod
    def _store_task_orchestration(metadata: dict, orchestration: dict) -> dict:
        metadata["orchestrator"] = orchestration
        return metadata

    @staticmethod
    def _assigned_session_id(task: Task) -> UUID | None:
        session_id = task.metadata_json.get("orchestrator", {}).get("assigned_session_id")
        return UUID(session_id) if session_id else None

    @staticmethod
    def _allowed_paths(task: Task) -> list[str]:
        raw_paths = task.metadata_json.get("orchestrator", {}).get("allowed_paths", [])
        return [str(path) for path in raw_paths if path]

    @staticmethod
    def _overlapping_paths(left_paths: list[str], right_paths: list[str]) -> list[str]:
        overlaps: list[str] = []
        for left_path in left_paths:
            left = PurePosixPath(left_path)
            for right_path in right_paths:
                right = PurePosixPath(right_path)
                if left == right or left in right.parents or right in left.parents:
                    overlap = str(left if len(left.parts) <= len(right.parts) else right)
                    if overlap not in overlaps:
                        overlaps.append(overlap)
        return sorted(overlaps)

    def _event_service(self, db) -> EventService:
        return EventService(
            db=db,
            redis_bus=self.redis_bus,
            event_broker=self.event_broker,
        )
