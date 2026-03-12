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
    ReviewStatus,
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
from app.schemas.session import SessionCreate
from app.schemas.task import (
    TaskAssignmentRead,
    TaskAssignmentRequest,
    TaskBlockedRequest,
    TaskCompletedRequest,
    TaskRead,
)
from app.services.runtime_service import RuntimeService
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
        runtime_service: RuntimeService | None = None,
    ) -> None:
        self.settings = settings
        self.database = database
        self.redis_bus = redis_bus
        self.event_broker = event_broker
        self.worktree_manager = worktree_manager
        self.repo_inspector = repo_inspector
        self.command_runner = command_runner
        self.session_supervisor = session_supervisor
        self.runtime_service = runtime_service

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

            # Process manager plan events → auto-create child tasks + workers
            plan_actions = self._process_manager_plans(
                db=db,
                event_service=event_service,
                workspace_service=workspace_service,
                project=project,
                sessions=sessions,
                new_events=list(new_events),
            )
            actions.extend(plan_actions)

            # Refresh tasks + sessions after plan processing (new children may have been created)
            if plan_actions:
                db.flush()
                tasks = db.scalars(select(Task).where(Task.project_id == project_id)).all()
                sessions = db.scalars(select(SessionModel).where(SessionModel.project_id == project_id)).all()

            # Auto-retry failed workers (create new worker, re-assign, start)
            retry_actions = self._retry_failed_workers(
                db=db,
                event_service=event_service,
                workspace_service=workspace_service,
                project=project,
                tasks=tasks,
                sessions=sessions,
            )
            actions.extend(retry_actions)

            # Auto-complete parent tasks when all children are done
            completion_actions = self._complete_parent_tasks(
                db=db,
                event_service=event_service,
                tasks=tasks,
            )
            actions.extend(completion_actions)

            # Auto-start unblocked workers whose dependencies just resolved
            start_actions = self._start_unblocked_workers(
                db=db,
                event_service=event_service,
                workspace_service=workspace_service,
                tasks=tasks,
                sessions=sessions,
            )
            actions.extend(start_actions)

            # Launch manager review sessions for completed workers
            review_launch_actions = self._launch_manager_reviews(
                db=db,
                event_service=event_service,
                workspace_service=workspace_service,
                project=project,
                tasks=tasks,
                sessions=sessions,
            )
            actions.extend(review_launch_actions)

            # Process completed manager reviews (approve or request changes)
            # Refresh tasks/sessions if reviews were launched
            if review_launch_actions:
                db.flush()
                tasks = db.scalars(select(Task).where(Task.project_id == project_id)).all()
                sessions = db.scalars(select(SessionModel).where(SessionModel.project_id == project_id)).all()
            review_result_actions = self._process_manager_review_results(
                db=db,
                event_service=event_service,
                workspace_service=workspace_service,
                project=project,
                tasks=tasks,
                sessions=sessions,
            )
            actions.extend(review_result_actions)

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

    def _process_manager_plans(
        self,
        *,
        db,
        event_service: EventService,
        workspace_service: WorkspaceService,
        project: Project,
        sessions: list[SessionModel],
        new_events: list[Event],
    ) -> list[OrchestratorActionRead]:
        """Read plan events from manager sessions and auto-create child tasks + workers."""
        actions: list[OrchestratorActionRead] = []
        manager_session_ids = {s.id for s in sessions if s.role == SessionRole.MANAGER}
        if not manager_session_ids:
            return actions

        for event in new_events:
            if event.session_id not in manager_session_ids:
                continue
            if event.event_type != "session.progress":
                continue
            payload = event.payload or {}
            details = payload.get("details") or {}
            plan = details.get("plan")
            if not isinstance(plan, dict):
                continue
            plan_tasks = plan.get("tasks")
            if not isinstance(plan_tasks, list) or not plan_tasks:
                continue

            # Find the parent task (goal) linked to the manager session
            manager_session = db.get(SessionModel, event.session_id)
            parent_task_id = manager_session.task_id if manager_session else None

            # Check if we already processed this plan (idempotency)
            manager_meta = dict(manager_session.metadata_json) if manager_session else {}
            manager_orch = dict(manager_meta.get("orchestrator", {}))
            if str(event.id) in manager_orch.get("processed_plan_event_ids", []):
                continue

            logger.info(
                "processing manager plan event %s with %d tasks for project %s",
                event.id, len(plan_tasks), project.id,
            )

            # Build index for depends_on resolution
            created_task_ids: list[UUID] = []

            for idx, plan_task in enumerate(plan_tasks):
                if not isinstance(plan_task, dict):
                    continue
                title = str(plan_task.get("title", "")).strip()
                if not title:
                    continue
                description = str(plan_task.get("description", "")).strip() or title
                scope = plan_task.get("scope", [])
                if not isinstance(scope, list):
                    scope = []
                acceptance_criteria = plan_task.get("acceptance_criteria", [])
                if not isinstance(acceptance_criteria, list):
                    acceptance_criteria = []
                priority = int(plan_task.get("priority", idx + 1))

                # Resolve depends_on_index to task IDs
                depends_on_indices = plan_task.get("depends_on_index", [])
                if not isinstance(depends_on_indices, list):
                    depends_on_indices = []
                dependency_task_ids = []
                for dep_idx in depends_on_indices:
                    if isinstance(dep_idx, int) and 0 <= dep_idx < len(created_task_ids):
                        dependency_task_ids.append(created_task_ids[dep_idx])

                # Create the child task
                child_task = Task(
                    project_id=project.id,
                    parent_task_id=parent_task_id,
                    title=title[:200],
                    description=description,
                    status=TaskStatus.PENDING,
                    priority=priority,
                    acceptance_criteria=[str(c) for c in acceptance_criteria if c],
                    metadata_json={
                        "request": {"scope": [str(s) for s in scope if s], "engine": "auto"},
                        "created_by": "manager_plan",
                        "plan_event_id": str(event.id),
                    },
                )
                db.add(child_task)
                db.flush()  # get the ID
                created_task_ids.append(child_task.id)

                event_service.record_event(
                    EventCreate(
                        category=EventCategory.TASK,
                        event_type="task.created",
                        level=EventLevel.INFO,
                        source=EventSourceRef(kind="service", role=SessionRole.MANAGER, id="orchestrator.plan"),
                        project_id=project.id,
                        task_id=child_task.id,
                        session_id=event.session_id,
                        payload={"title": title, "priority": priority, "parent_task_id": str(parent_task_id) if parent_task_id else None},
                    )
                )
                actions.append(
                    OrchestratorActionRead(
                        kind="task_created_from_plan",
                        project_id=project.id,
                        task_id=child_task.id,
                        session_id=event.session_id,
                        detail=f"Manager created task: {title}",
                        payload={"parent_task_id": str(parent_task_id) if parent_task_id else None},
                    )
                )

            # Now create worker sessions, assign tasks, and start them
            for idx, child_task_id in enumerate(created_task_ids):
                child_task = db.get(Task, child_task_id)
                if child_task is None:
                    continue
                plan_task = plan_tasks[idx] if idx < len(plan_tasks) else {}
                scope = plan_task.get("scope", []) if isinstance(plan_task, dict) else []
                if not isinstance(scope, list):
                    scope = []

                # Resolve dependency IDs
                depends_on_indices = plan_task.get("depends_on_index", []) if isinstance(plan_task, dict) else []
                if not isinstance(depends_on_indices, list):
                    depends_on_indices = []
                dep_ids = []
                for dep_idx in depends_on_indices:
                    if isinstance(dep_idx, int) and 0 <= dep_idx < len(created_task_ids):
                        dep_ids.append(created_task_ids[dep_idx])

                # Create worker session
                launch_plan = self.session_supervisor.plan_session(
                    role=SessionRole.WORKER,
                    runtime_preference="auto",
                    allow_simulation_fallback=True,
                )
                worker_meta = {
                    "preferred_engine": "auto",
                    "created_from": "manager_plan",
                    "simulation_mode": launch_plan.simulation_mode,
                    "launch_notes": launch_plan.notes,
                    "runtime": launch_plan.runtime.model_dump(mode="json"),
                }

                worker_session = SessionModel(
                    project_id=project.id,
                    task_id=child_task.id,
                    supervisor_session_id=event.session_id,
                    role=SessionRole.WORKER,
                    status=launch_plan.initial_status,
                    transport=launch_plan.transport,
                    adapter_kind=launch_plan.adapter_kind,
                    command=launch_plan.command,
                    blocked_reason=launch_plan.blocked_reason,
                    metadata_json=worker_meta,
                    runtime_metadata_json={},
                )
                db.add(worker_session)
                db.flush()

                # Provision workspace for the worker
                repo_info = self.repo_inspector.inspect(project.repo_path, project.default_branch)
                workspace_plan = self.worktree_manager.plan_workspace(
                    project_slug=project.slug,
                    project_id=project.id,
                    role=SessionRole.WORKER,
                    task_id=child_task.id,
                    session_id=worker_session.id,
                    base_branch=project.default_branch,
                )
                workspace = Workspace(
                    project_id=project.id,
                    session_id=worker_session.id,
                    branch_name=workspace_plan.branch_name,
                    base_branch=workspace_plan.base_branch,
                    base_commit=repo_info.current_commit or "",
                    head_commit=repo_info.current_commit,
                    workspace_path=workspace_plan.workspace_path,
                    status=workspace_plan.status,
                    metadata_json={
                        "ownership": {
                            "session_id": str(worker_session.id),
                            "path_lock_owner": str(worker_session.id),
                            "path_locks": [],
                        }
                    },
                )
                db.add(workspace)
                worker_session.branch_name = workspace.branch_name
                worker_session.workspace_path = workspace.workspace_path
                db.flush()

                # Assign task to worker
                allowed_paths = [str(s).strip() for s in scope if str(s).strip()]
                task_metadata = dict(child_task.metadata_json)
                orchestration = self._task_orchestration(task_metadata)
                orchestration.update({
                    "assigned_session_id": str(worker_session.id),
                    "allowed_paths": allowed_paths,
                    "dependency_task_ids": [str(d) for d in dep_ids],
                    "active_dependency_task_ids": [str(d) for d in dep_ids],  # resolved in next tick
                    "last_assigned_at": datetime.now(UTC).isoformat(),
                })
                if dep_ids:
                    orchestration["blocked_reason"] = "waiting_on_dependencies"
                    child_task.status = TaskStatus.BLOCKED
                else:
                    child_task.status = TaskStatus.IN_PROGRESS
                child_task.metadata_json = self._store_task_orchestration(task_metadata, orchestration)

                worker_session.metadata_json = {
                    **dict(worker_session.metadata_json),
                    "assignment": {
                        "task_id": str(child_task.id),
                        "allowed_paths": allowed_paths,
                        "dependency_task_ids": [str(d) for d in dep_ids],
                    },
                }
                db.flush()

                event_service.record_event(
                    EventCreate(
                        category=EventCategory.TASK,
                        event_type="task.assigned",
                        level=EventLevel.INFO,
                        source=EventSourceRef(kind="service", role=SessionRole.MANAGER, id="orchestrator.plan"),
                        project_id=project.id,
                        task_id=child_task.id,
                        session_id=worker_session.id,
                        payload={"allowed_paths": allowed_paths},
                    )
                )

                actions.append(
                    OrchestratorActionRead(
                        kind="worker_created_from_plan",
                        project_id=project.id,
                        task_id=child_task.id,
                        session_id=worker_session.id,
                        detail=f"Auto-created worker for: {child_task.title}",
                        payload={},
                    )
                )

            # Mark plan event as processed (idempotency)
            processed_ids = list(manager_orch.get("processed_plan_event_ids", []))
            processed_ids.append(str(event.id))
            manager_orch["processed_plan_event_ids"] = processed_ids
            manager_meta["orchestrator"] = manager_orch
            if manager_session:
                manager_session.metadata_json = manager_meta

            # Update parent task status
            if parent_task_id:
                parent_task = db.get(Task, parent_task_id)
                if parent_task and parent_task.status == TaskStatus.PENDING:
                    parent_task.status = TaskStatus.IN_PROGRESS

            db.commit()

            # Provision workspaces and start worker sessions that don't have unresolved dependencies
            for idx, child_task_id in enumerate(created_task_ids):
                child_task = db.get(Task, child_task_id)
                if child_task and child_task.status == TaskStatus.IN_PROGRESS:
                    orch = self._task_orchestration(child_task.metadata_json)
                    worker_sid = orch.get("assigned_session_id")
                    if worker_sid:
                        try:
                            workspace_service.provision_session_workspace(UUID(worker_sid))
                        except Exception as exc:
                            logger.warning("failed to provision workspace for worker %s: %s", worker_sid, exc)
                        try:
                            self.session_supervisor.start_session(UUID(worker_sid))
                        except Exception as exc:
                            logger.warning("failed to auto-start worker %s: %s", worker_sid, exc)

        return actions

    def _retry_failed_workers(
        self,
        *,
        db,
        event_service: EventService,
        workspace_service: WorkspaceService,
        project: Project,
        tasks: list[Task],
        sessions: list[SessionModel],
    ) -> list[OrchestratorActionRead]:
        """When a worker session FAILED, create a new worker session and re-start the task.

        Only retries once — tracked via ``retry_count`` in task orchestration metadata.
        """
        actions: list[OrchestratorActionRead] = []
        session_by_id = {s.id: s for s in sessions}

        for task in tasks:
            if task.status not in {TaskStatus.BLOCKED}:
                continue
            task_metadata = dict(task.metadata_json)
            orchestration = self._task_orchestration(task_metadata)
            blocked_reason = orchestration.get("blocked_reason", "")
            if blocked_reason not in {"failed", "stopped", "session_blocked", "needs_changes"}:
                continue
            retry_count = int(orchestration.get("retry_count", 0))
            max_retries = self.settings.orchestrator_manager_review_max_rounds if blocked_reason == "needs_changes" else 1
            if retry_count >= max_retries:
                continue

            assigned_sid = orchestration.get("assigned_session_id")
            if not assigned_sid:
                continue
            old_session = session_by_id.get(UUID(assigned_sid))
            if old_session is None:
                continue
            # For needs_changes, the old worker completed successfully but was rejected by manager
            allowed_old_statuses = {SessionStatus.FAILED, SessionStatus.STOPPED}
            if blocked_reason == "needs_changes":
                allowed_old_statuses.add(SessionStatus.COMPLETED)
            if old_session.status not in allowed_old_statuses:
                continue

            task_record = db.get(Task, task.id)
            if task_record is None:
                continue

            logger.info("auto-retrying failed worker for task %s (retry %d)", task.id, retry_count + 1)

            # Create new worker session
            launch_plan = self.session_supervisor.plan_session(
                role=SessionRole.WORKER,
                runtime_preference="auto",
                allow_simulation_fallback=True,
            )
            worker_session = SessionModel(
                project_id=project.id,
                task_id=task.id,
                supervisor_session_id=old_session.supervisor_session_id,
                role=SessionRole.WORKER,
                status=launch_plan.initial_status,
                transport=launch_plan.transport,
                adapter_kind=launch_plan.adapter_kind,
                command=launch_plan.command,
                blocked_reason=launch_plan.blocked_reason,
                metadata_json={
                    "preferred_engine": "auto",
                    "created_from": "auto_retry",
                    "retry_of_session_id": str(old_session.id),
                    "simulation_mode": launch_plan.simulation_mode,
                    "launch_notes": launch_plan.notes,
                    "runtime": launch_plan.runtime.model_dump(mode="json"),
                },
                runtime_metadata_json={},
            )
            db.add(worker_session)
            db.flush()

            # Provision workspace
            repo_info = self.repo_inspector.inspect(project.repo_path, project.default_branch)
            workspace_plan = self.worktree_manager.plan_workspace(
                project_slug=project.slug,
                project_id=project.id,
                role=SessionRole.WORKER,
                task_id=task.id,
                session_id=worker_session.id,
                base_branch=project.default_branch,
            )
            workspace = Workspace(
                project_id=project.id,
                session_id=worker_session.id,
                branch_name=workspace_plan.branch_name,
                base_branch=workspace_plan.base_branch,
                base_commit=repo_info.current_commit or "",
                head_commit=repo_info.current_commit,
                workspace_path=workspace_plan.workspace_path,
                status=workspace_plan.status,
                metadata_json={"ownership": {"session_id": str(worker_session.id)}},
            )
            db.add(workspace)
            worker_session.branch_name = workspace.branch_name
            worker_session.workspace_path = workspace.workspace_path
            db.flush()

            # Update task metadata
            orchestration["assigned_session_id"] = str(worker_session.id)
            orchestration["retry_count"] = retry_count + 1
            orchestration["blocked_reason"] = None
            orchestration["last_assigned_at"] = datetime.now(UTC).isoformat()
            task_record.status = TaskStatus.IN_PROGRESS
            task_record.metadata_json = self._store_task_orchestration(task_metadata, orchestration)
            db.flush()

            event_service.record_event(
                EventCreate(
                    category=EventCategory.TASK,
                    event_type="task.assigned",
                    level=EventLevel.INFO,
                    source=EventSourceRef(kind="service", role=SessionRole.WORKER, id="orchestrator.retry"),
                    project_id=project.id,
                    task_id=task.id,
                    session_id=worker_session.id,
                    payload={"retry_count": retry_count + 1, "previous_session_id": str(old_session.id)},
                )
            )
            actions.append(
                OrchestratorActionRead(
                    kind="worker_retried",
                    project_id=project.id,
                    task_id=task.id,
                    session_id=worker_session.id,
                    detail=f"Auto-retried failed worker (attempt {retry_count + 1}).",
                    payload={"retry_of_session_id": str(old_session.id)},
                )
            )
            db.commit()

            # Provision workspace and start the new worker
            try:
                workspace_service.provision_session_workspace(worker_session.id)
            except Exception as exc:
                logger.warning("failed to provision workspace for retried worker %s: %s", worker_session.id, exc)
            try:
                self.session_supervisor.start_session(worker_session.id)
            except Exception as exc:
                logger.warning("failed to start retried worker %s: %s", worker_session.id, exc)

        return actions

    def _complete_parent_tasks(
        self,
        *,
        db,
        event_service: EventService,
        tasks: list[Task],
    ) -> list[OrchestratorActionRead]:
        """Auto-complete parent (goal) tasks when ALL their children are DONE."""
        actions: list[OrchestratorActionRead] = []

        # Group child tasks by parent_task_id
        children_by_parent: dict[UUID, list[Task]] = {}
        for task in tasks:
            if task.parent_task_id:
                children_by_parent.setdefault(task.parent_task_id, []).append(task)

        for parent_id, children in children_by_parent.items():
            if not children:
                continue
            if not all(c.status == TaskStatus.DONE for c in children):
                continue

            parent = db.get(Task, parent_id)
            if parent is None or parent.status in {TaskStatus.DONE, TaskStatus.CANCELED}:
                continue

            logger.info("auto-completing parent task %s — all %d children done", parent_id, len(children))
            parent.status = TaskStatus.DONE
            parent_meta = dict(parent.metadata_json)
            orch = self._task_orchestration(parent_meta)
            orch["auto_completed"] = True
            orch["completed_at"] = datetime.now(UTC).isoformat()
            orch["completion_summary"] = f"All {len(children)} subtasks completed successfully."
            parent.metadata_json = self._store_task_orchestration(parent_meta, orch)
            db.flush()

            event_service.record_event(
                EventCreate(
                    category=EventCategory.TASK,
                    event_type="task.completed",
                    level=EventLevel.INFO,
                    source=EventSourceRef(kind="service", id="orchestrator.parent_completion"),
                    project_id=parent.project_id,
                    task_id=parent.id,
                    payload={
                        "auto_completed": True,
                        "child_count": len(children),
                    },
                )
            )
            actions.append(
                OrchestratorActionRead(
                    kind="parent_task_completed",
                    project_id=parent.project_id,
                    task_id=parent.id,
                    detail=f"Goal completed — all {len(children)} subtasks done.",
                    payload={},
                )
            )
            db.commit()

        return actions

    def _start_unblocked_workers(
        self,
        *,
        db,
        event_service: EventService,
        workspace_service: WorkspaceService,
        tasks: list[Task],
        sessions: list[SessionModel],
    ) -> list[OrchestratorActionRead]:
        """Start worker sessions for tasks that were just unblocked from dependency resolution."""
        actions: list[OrchestratorActionRead] = []
        session_by_id = {s.id: s for s in sessions}

        for task in tasks:
            if task.status != TaskStatus.IN_PROGRESS:
                continue
            orchestration = self._task_orchestration(task.metadata_json)
            assigned_sid = orchestration.get("assigned_session_id")
            if not assigned_sid:
                continue

            session = session_by_id.get(UUID(assigned_sid))
            if session is None:
                # Session might be newly created — reload
                with self.database.session() as fresh_db:
                    session = fresh_db.get(SessionModel, UUID(assigned_sid))
            if session is None or session.status != SessionStatus.PENDING:
                continue

            # This task is IN_PROGRESS with a PENDING worker — provision workspace and start it
            logger.info("auto-starting unblocked worker %s for task %s", assigned_sid, task.id)
            try:
                workspace_service.provision_session_workspace(UUID(assigned_sid))
            except Exception as exc:
                logger.warning("failed to provision workspace for worker %s: %s", assigned_sid, exc)
            try:
                self.session_supervisor.start_session(UUID(assigned_sid))
                actions.append(
                    OrchestratorActionRead(
                        kind="worker_auto_started",
                        project_id=task.project_id,
                        task_id=task.id,
                        session_id=UUID(assigned_sid),
                        detail="Auto-started worker after dependencies resolved.",
                        payload={},
                    )
                )
            except Exception as exc:
                logger.warning("failed to auto-start unblocked worker %s: %s", assigned_sid, exc)

        return actions

    def _launch_manager_reviews(
        self,
        *,
        db,
        event_service: EventService,
        workspace_service: WorkspaceService,
        project: Project,
        tasks: list[Task],
        sessions: list[SessionModel],
    ) -> list[OrchestratorActionRead]:
        """Spawn manager review sessions for completed worker tasks awaiting review."""
        if not self.settings.orchestrator_manager_review_enabled:
            return []

        actions: list[OrchestratorActionRead] = []

        for task in tasks:
            if task.status != TaskStatus.REVIEW:
                continue
            task_metadata = dict(task.metadata_json)
            orchestration = self._task_orchestration(task_metadata)
            if not orchestration.get("awaiting_manager_review"):
                continue
            if orchestration.get("reviewer_session_id"):
                continue  # Already launched a review session

            # Check review round limits
            review_round = int(orchestration.get("review_round", 0))
            if review_round >= self.settings.orchestrator_manager_review_max_rounds:
                logger.info("auto-approving task %s after %d review rounds", task.id, review_round)
                task_record = db.get(Task, task.id)
                if task_record:
                    task_record.status = TaskStatus.DONE
                    orchestration["awaiting_manager_review"] = False
                    orchestration["auto_approved_reason"] = "max_review_rounds"
                    task_record.metadata_json = self._store_task_orchestration(task_metadata, orchestration)
                    db.flush()
                    actions.append(OrchestratorActionRead(
                        kind="task_auto_approved",
                        project_id=project.id, task_id=task.id, session_id=None,
                        detail=f"Auto-approved after {review_round} review rounds.",
                        payload={},
                    ))
                continue

            # Get the worker's diff
            worker_sid = orchestration.get("assigned_session_id")
            if not worker_sid:
                continue

            diff_text = ""
            changed_files: list[str] = []
            try:
                workspace_status = workspace_service.get_session_workspace(UUID(worker_sid))
                diff_read = workspace_service.get_diff(workspace_status.id)
                diff_text = diff_read.diff or ""
                changed_files = diff_read.changed_files or []
            except Exception as exc:
                logger.warning("could not get diff for review of task %s: %s", task.id, exc)
                # If no diff available, auto-approve
                task_record = db.get(Task, task.id)
                if task_record:
                    task_record.status = TaskStatus.DONE
                    orchestration["awaiting_manager_review"] = False
                    orchestration["auto_approved_reason"] = "no_diff_available"
                    task_record.metadata_json = self._store_task_orchestration(task_metadata, orchestration)
                    db.flush()
                    actions.append(OrchestratorActionRead(
                        kind="task_auto_approved",
                        project_id=project.id, task_id=task.id, session_id=None,
                        detail="Auto-approved (could not retrieve diff for review).",
                        payload={},
                    ))
                continue

            # Build review prompt
            try:
                review_packet = self.task_packet_service.build_manager_review_packet(
                    UUID(worker_sid),  # temporary — we'll create a new session below
                    diff=diff_text,
                    changed_files=changed_files,
                )
            except Exception:
                # build_manager_review_packet needs a session with a task — use the task directly
                review_packet = None

            if not review_packet:
                # Fallback: build manually
                acceptance = "\n".join(f"- {c}" for c in task.acceptance_criteria if c) or "- Complete the task."
                review_packet = (
                    f"Review this worker's completed task.\n\n"
                    f"Task: {task.title}\nDescription: {task.description}\n\n"
                    f"Acceptance criteria:\n{acceptance}\n\n"
                    f"Changed files: {', '.join(changed_files[:10])}\n\n"
                    f"Diff:\n```diff\n{diff_text[:8000]}\n```\n\n"
                    f"Emit [[EVENT]] with type 'complete', result 'approved' or 'needs_changes'.\n"
                )

            # Create manager review session
            launch_plan = self.session_supervisor.plan_session(
                role=SessionRole.MANAGER,
                runtime_preference="auto",
                allow_simulation_fallback=True,
            )
            reviewer_session = SessionModel(
                project_id=project.id,
                task_id=task.id,
                role=SessionRole.MANAGER,
                status=launch_plan.initial_status,
                transport=launch_plan.transport,
                adapter_kind=launch_plan.adapter_kind,
                command=launch_plan.command,
                workspace_path=project.repo_path,
                metadata_json={
                    "is_review": True,
                    "review_of_session_id": worker_sid,
                    "review_round": review_round + 1,
                    "simulation_mode": launch_plan.simulation_mode,
                    "runtime": launch_plan.runtime.model_dump(mode="json"),
                },
                runtime_metadata_json={},
            )
            db.add(reviewer_session)
            db.flush()

            # Update task metadata
            task_record = db.get(Task, task.id)
            if task_record:
                orchestration["reviewer_session_id"] = str(reviewer_session.id)
                orchestration["review_round"] = review_round + 1
                task_record.metadata_json = self._store_task_orchestration(task_metadata, orchestration)
                db.flush()

            # Commit so the session is visible to the supervisor's separate DB session
            db.commit()

            # Start the review session
            try:
                self.session_supervisor.start_session(reviewer_session.id, initial_message=review_packet)
            except Exception as exc:
                logger.warning("failed to start manager review session %s: %s", reviewer_session.id, exc)

            actions.append(OrchestratorActionRead(
                kind="manager_review_launched",
                project_id=project.id, task_id=task.id, session_id=reviewer_session.id,
                detail=f"Manager reviewing worker output (round {review_round + 1}).",
                payload={"review_round": review_round + 1},
            ))

        return actions

    def _process_manager_review_results(
        self,
        *,
        db,
        event_service: EventService,
        workspace_service: WorkspaceService,
        project: Project,
        tasks: list[Task],
        sessions: list[SessionModel],
    ) -> list[OrchestratorActionRead]:
        """Process completed manager review sessions — approve or request changes."""
        actions: list[OrchestratorActionRead] = []
        session_by_id = {s.id: s for s in sessions}

        for task in tasks:
            if task.status != TaskStatus.REVIEW:
                continue
            task_metadata = dict(task.metadata_json)
            orchestration = self._task_orchestration(task_metadata)
            reviewer_sid = orchestration.get("reviewer_session_id")
            if not reviewer_sid:
                continue

            reviewer = session_by_id.get(UUID(reviewer_sid))
            if reviewer is None:
                with self.database.session() as fresh_db:
                    reviewer = fresh_db.get(SessionModel, UUID(reviewer_sid))
            if reviewer is None or reviewer.status not in {SessionStatus.COMPLETED, SessionStatus.FAILED}:
                continue

            task_record = db.get(Task, task.id)
            if task_record is None:
                continue

            # If reviewer failed, auto-approve
            if reviewer.status == SessionStatus.FAILED:
                logger.warning("manager review session %s failed, auto-approving task %s", reviewer_sid, task.id)
                task_record.status = TaskStatus.DONE
                orchestration["awaiting_manager_review"] = False
                orchestration["reviewer_session_id"] = None
                orchestration["auto_approved_reason"] = "reviewer_failed"
                task_record.metadata_json = self._store_task_orchestration(task_metadata, orchestration)
                db.flush()
                actions.append(OrchestratorActionRead(
                    kind="task_auto_approved",
                    project_id=project.id, task_id=task.id, session_id=UUID(reviewer_sid),
                    detail="Auto-approved (manager review session failed).",
                    payload={},
                ))
                continue

            # Parse the reviewer's verdict from structured events
            from app.models.parsed_session_event import ParsedSessionEvent
            verdict_events = db.scalars(
                select(ParsedSessionEvent)
                .where(
                    ParsedSessionEvent.session_id == UUID(reviewer_sid),
                    ParsedSessionEvent.event_type == "complete",
                )
                .order_by(ParsedSessionEvent.sequence.desc())
                .limit(1)
            ).all()

            verdict = "approved"  # default if no verdict found
            feedback: list[str] = []
            if verdict_events:
                payload = verdict_events[0].payload_json or {}
                result = payload.get("result", "")
                details = payload.get("details", {})
                if isinstance(details, dict):
                    v = details.get("verdict", result)
                    if v in ("needs_changes", "rejected"):
                        verdict = "needs_changes"
                        fb = details.get("feedback", [])
                        if isinstance(fb, list):
                            feedback = [str(f) for f in fb if f]
                        elif isinstance(fb, str):
                            feedback = [fb]
                elif result in ("needs_changes", "rejected"):
                    verdict = "needs_changes"

            if verdict == "approved":
                # Extract manager's notes from the verdict event
                verdict_notes = ""
                if verdict_events:
                    payload = verdict_events[0].payload_json or {}
                    details = payload.get("details", {})
                    if isinstance(details, dict):
                        verdict_notes = details.get("notes", "")
                    summary_text = payload.get("summary", "")
                    # Skip generic adapter messages as summaries
                    generic_msgs = {"Real Codex execution finished.", "Real Claude Code execution finished."}
                    if not verdict_notes and summary_text and summary_text not in generic_msgs:
                        verdict_notes = summary_text

                task_record.status = TaskStatus.DONE
                orchestration["awaiting_manager_review"] = False
                orchestration["reviewer_session_id"] = None
                orchestration["completion_summary"] = verdict_notes or "Manager approved — task complete."
                orchestration["completed_at"] = datetime.now(UTC).isoformat()
                task_record.metadata_json = self._store_task_orchestration(task_metadata, orchestration)
                db.flush()

                # Also auto-approve any legacy Review records for this task
                from app.models.review import Review as ReviewModel
                pending_reviews = db.scalars(
                    select(ReviewModel).where(
                        ReviewModel.task_id == task.id,
                        ReviewModel.status.in_(["pending", "running", "needs_changes"]),
                    )
                ).all()
                for pr in pending_reviews:
                    pr.status = ReviewStatus.APPROVED
                    db.flush()

                actions.append(OrchestratorActionRead(
                    kind="task_manager_approved",
                    project_id=project.id, task_id=task.id, session_id=UUID(reviewer_sid),
                    detail=f"Manager approved: {verdict_notes}" if verdict_notes else "Manager approved worker output.",
                    payload={"summary": verdict_notes},
                ))
            else:
                # Needs changes — send back to worker with feedback
                logger.info("manager requested changes for task %s: %s", task.id, feedback)
                orchestration["awaiting_manager_review"] = False
                orchestration["reviewer_session_id"] = None
                orchestration["manager_feedback"] = feedback
                orchestration["blocked_reason"] = "needs_changes"
                task_record.status = TaskStatus.BLOCKED
                task_record.metadata_json = self._store_task_orchestration(task_metadata, orchestration)
                db.flush()
                actions.append(OrchestratorActionRead(
                    kind="task_needs_changes",
                    project_id=project.id, task_id=task.id, session_id=UUID(reviewer_sid),
                    detail=f"Manager requested changes: {'; '.join(feedback[:3])}",
                    payload={"feedback": feedback},
                ))

        return actions

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
            elif task_record.status == TaskStatus.BLOCKED:
                # Dependencies resolved — unblock regardless of the current blocked_reason
                # (scope_conflict, waiting_on_dependencies, etc. are all resolved once deps are done)
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
            # Manager-planned tasks go through automated manager review — no human review needed
            created_by = task_metadata.get("created_by", "")
            if created_by == "manager_plan":
                task_record.status = TaskStatus.REVIEW
                orchestration["blocked_reason"] = None
                orchestration["awaiting_manager_review"] = True
                orchestration["review_requested_at"] = now.isoformat()
                actions.append(
                    OrchestratorActionRead(
                        kind="task_awaiting_manager_review",
                        project_id=task_record.project_id,
                        task_id=task_record.id,
                        session_id=session_record.id,
                        detail="Worker completed — queued for automated manager review.",
                        payload={},
                    )
                )
            else:
                # Non-manager tasks: create a review for human approval
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

            # Never block manager-planned tasks for scope conflicts — the manager
            # already planned execution order with dependencies. Scope overlap is
            # expected and handled by the dependency chain.
            if action.kind == "scope_conflict" and task_metadata.get("created_by") == "manager_plan":
                return False

            conflict_reason = action.kind
            existing_conflicts = list(orchestration.get("conflicts", []))
            rendered_conflict = {
                "kind": action.kind,
                "detail": action.detail,
                "payload": action.payload,
            }
            if rendered_conflict in existing_conflicts and orchestration.get("blocked_reason") == conflict_reason:
                return False
            # Cap conflict accumulation to prevent metadata bloat
            if len(existing_conflicts) > 20:
                existing_conflicts = existing_conflicts[-10:]
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
