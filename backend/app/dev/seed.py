from __future__ import annotations

import argparse
import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from uuid import UUID

from sqlalchemy import func, select

from app.core.config import Settings, get_settings
from app.core.container import ServiceContainer, build_container
from app.core.enums import EventCategory, EventLevel, SessionRole, TaskStatus
from app.models import Base
from app.models.event import Event
from app.models.project import Project
from app.models.review import Review
from app.models.session import Session as SessionModel
from app.models.task import Task
from app.models.workspace import Workspace
from app.schemas.event import EventCreate, EventSourceRef
from app.schemas.project import ProjectCreate, ProjectRead
from app.schemas.review import ReviewCreate
from app.schemas.session import SessionCreate, SessionRead
from app.schemas.task import TaskAssignmentRequest, TaskCreate, TaskRead
from app.services.event_service import EventService
from app.services.project_service import ProjectService
from app.services.review_service import ReviewService
from app.services.session_service import SessionService
from app.services.task_service import TaskService
from app.services.workspace_service import WorkspaceService

logger = logging.getLogger(__name__)

DEMO_PROJECT_NAME = "Poulpe AI Demo"
DEMO_COMMIT_MESSAGE = "Seed demo repository"
DEMO_GIT_USER_NAME = "Poulpe AI"
DEMO_GIT_USER_EMAIL = "demo@poulpe-ai.local"
DEMO_EVENT_SOURCE_ID = "demo-seed"

DEMO_SAMPLE_TASKS = [
    {
        "id": "frontend",
        "label": "Frontend polish",
        "title": "Make the review screen easier to scan",
        "description": (
            "Simplify the approval view so changed files, check results, and the final "
            "decision are obvious at a glance."
        ),
        "scope": ["frontend"],
        "engine": "auto",
    },
    {
        "id": "backend",
        "label": "Backend cleanup",
        "title": "Tighten structured event handling",
        "description": (
            "Improve worker event parsing so malformed output is easier to debug and valid "
            "progress updates stay readable."
        ),
        "scope": ["backend"],
        "engine": "auto",
    },
    {
        "id": "docs",
        "label": "Docs update",
        "title": "Explain the approval flow clearly",
        "description": (
            "Document the path from task start to approval so a new developer can follow the "
            "workflow quickly."
        ),
        "scope": ["docs"],
        "engine": "auto",
    },
]

DEMO_HOW_IT_WORKS = [
    {
        "title": "1. Enter a task",
        "detail": "Describe the change you want in the active repo workspace.",
    },
    {
        "title": "2. Narrow the scope",
        "detail": "Optionally pick a folder or file area so the task stays focused.",
    },
    {
        "title": "3. Start work",
        "detail": "The app creates the internal task, agent, worktree, and runtime setup.",
    },
    {
        "title": "4. Review the result",
        "detail": "When work is done, inspect the summary, changed files, checks, and approve or request changes.",
    },
]

DEMO_REPO_FILES: dict[str, str] = {
    "README.md": """# Demo Local Agent Repo

This repository exists to exercise the local-first orchestrator end-to-end.

## Areas

- `backend/app/services`: worker-oriented backend changes
- `frontend/components`: operator console UI work
- `docs`: orchestration and review notes
""",
    "backend/app/services/worker_events.py": """def normalize_worker_event(payload: dict[str, object]) -> dict[str, object]:
    return {
        "type": payload.get("type", "progress"),
        "summary": payload.get("summary", "no summary"),
    }
""",
    "frontend/components/ReviewPanel.tsx": """export function ReviewPanel() {
  return (
    <section>
      <h2>Review detail</h2>
      <p>Show diff summary, checks, and approval state.</p>
    </section>
  );
}
""",
    "docs/orchestrator-notes.md": """# Orchestrator Notes

- Manager sessions supervise worker and reviewer sessions.
- Worker sessions write structured progress blocks.
- Reviewer sessions evaluate diffs, checks, and human approval state.
""",
    "tests/test_worker_events.py": """from backend.app.services.worker_events import normalize_worker_event


def test_normalize_worker_event_defaults() -> None:
    assert normalize_worker_event({})["type"] == "progress"
""",
}


@dataclass(slots=True)
class DemoSeedReport:
    seeded: bool
    reason: str
    repo_path: str
    project_id: str | None = None
    task_ids: list[str] = field(default_factory=list)
    session_ids: list[str] = field(default_factory=list)
    review_ids: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)


def ensure_demo_repo(container: ServiceContainer, settings: Settings) -> Path:
    repo_path = (
        Path(settings.orchestrator_repos_root).expanduser().resolve() / settings.seed_demo_repo_name
    )
    repo_path.mkdir(parents=True, exist_ok=True)

    is_git_repo = container.command_runner.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=repo_path,
        check=False,
    ).returncode == 0
    if not is_git_repo:
        if any(repo_path.iterdir()):
            raise RuntimeError(
                f"Demo repo path exists but is not an empty git repository: {repo_path}"
            )
        container.command_runner.run(["git", "init", "-b", "main"], cwd=repo_path)

    head_exists = container.command_runner.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_path,
        check=False,
    ).returncode == 0
    if head_exists:
        return repo_path

    for relative_path, content in DEMO_REPO_FILES.items():
        file_path = repo_path / relative_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")

    container.command_runner.run(["git", "add", "."], cwd=repo_path)
    container.command_runner.run(
        [
            "git",
            "-c",
            f"user.name={DEMO_GIT_USER_NAME}",
            "-c",
            f"user.email={DEMO_GIT_USER_EMAIL}",
            "commit",
            "-m",
            DEMO_COMMIT_MESSAGE,
        ],
        cwd=repo_path,
    )
    logger.info("created demo repository at %s", repo_path)
    return repo_path


def seed_demo_environment(
    settings: Settings,
    *,
    repo_path: str | None = None,
    if_empty: bool = False,
    container: ServiceContainer | None = None,
) -> DemoSeedReport:
    owned_container = container is None
    active_container = container or build_container(settings)
    active_container.ensure_local_dirs()
    if settings.auto_create_schema:
        Base.metadata.create_all(active_container.database.engine)

    try:
        resolved_repo_path = (
            Path(repo_path).expanduser().resolve()
            if repo_path is not None
            else ensure_demo_repo(active_container, settings)
        )
        logger.info("seeding demo data using repo %s", resolved_repo_path)

        with active_container.database.session() as db:
            project_count = int(db.scalar(select(func.count(Project.id))) or 0)
            existing_project = db.scalar(
                select(Project).where(Project.repo_path == str(resolved_repo_path))
            )
            if existing_project is None and if_empty and project_count > 0:
                return DemoSeedReport(
                    seeded=False,
                    reason="database_not_empty",
                    repo_path=str(resolved_repo_path),
                )

            event_service = EventService(
                db=db,
                redis_bus=active_container.redis_bus,
                event_broker=active_container.event_broker,
            )
            workspace_service = WorkspaceService(
                db=db,
                event_service=event_service,
                worktree_manager=active_container.worktree_manager,
                repo_inspector=active_container.repo_inspector,
                command_runner=active_container.command_runner,
            )
            project_service = ProjectService(
                db=db,
                settings=settings,
                event_service=event_service,
                command_runner=active_container.command_runner,
                repo_inspector=active_container.repo_inspector,
                runtime_service=active_container.runtime_service,
                session_supervisor=active_container.session_supervisor,
                workspace_service=workspace_service,
            )
            task_service = TaskService(db=db, event_service=event_service)
            session_service = SessionService(
                db=db,
                event_service=event_service,
                session_supervisor=active_container.session_supervisor,
                worktree_manager=active_container.worktree_manager,
                repo_inspector=active_container.repo_inspector,
                runtime_service=active_container.runtime_service,
                workspace_service=workspace_service,
            )
            review_service = ReviewService(
                db=db,
                event_service=event_service,
                workspace_service=workspace_service,
            )

            project = _ensure_project(project_service, db, resolved_repo_path)

            active_task = _ensure_task(
                task_service,
                db,
                project.id,
                title="Tighten structured event handling",
                aliases=["Stabilize worker structured events"],
                description="Improve worker event parsing so malformed output is easier to debug and valid progress updates stay readable.",
                acceptance_criteria=[
                    "keep valid event blocks readable",
                    "surface malformed event blocks clearly",
                    "preserve structured events separately from the raw transcript",
                ],
            )
            review_task = _ensure_task(
                task_service,
                db,
                project.id,
                title="Make the review screen easier to scan",
                aliases=["Polish review handoff panel"],
                description="Simplify the approval view so changed files, check results, and the final decision are obvious at a glance.",
                acceptance_criteria=[
                    "highlight changed files",
                    "show lint and test outcomes clearly",
                    "keep the approval decision easy to scan",
                ],
            )
            blocked_task = _ensure_task(
                task_service,
                db,
                project.id,
                title="Explain the approval flow clearly",
                aliases=["Document merge queue handoff"],
                description="Document the path from task start to approval so a new developer can follow the workflow quickly.",
                acceptance_criteria=[
                    "describe the approval handoff clearly",
                    "mention the human approval gate",
                    "note where merge execution plugs in later",
                ],
            )

            manager_session = _ensure_session(
                session_service,
                workspace_service,
                db,
                project.id,
                role=SessionRole.MANAGER,
                task_id=None,
                command_override="codex manager --demo",
            )
            reviewer_session = _ensure_session(
                session_service,
                workspace_service,
                db,
                project.id,
                role=SessionRole.REVIEWER,
                task_id=None,
                command_override="codex reviewer --demo",
            )
            active_worker_session = _ensure_session(
                session_service,
                workspace_service,
                db,
                project.id,
                role=SessionRole.WORKER,
                task_id=active_task.id,
                command_override="codex worker --demo-active",
            )
            review_worker_session = _ensure_session(
                session_service,
                workspace_service,
                db,
                project.id,
                role=SessionRole.WORKER,
                task_id=review_task.id,
                command_override="codex worker --demo-review",
            )
            blocked_worker_session = _ensure_session(
                session_service,
                workspace_service,
                db,
                project.id,
                role=SessionRole.WORKER,
                task_id=blocked_task.id,
                command_override="codex worker --demo-blocked",
            )

            review = _ensure_review(
                db=db,
                review_service=review_service,
                project_id=project.id,
                task=review_task,
                worker_session=review_worker_session,
            )

            _ensure_demo_activity(
                db=db,
                event_service=event_service,
                project_id=project.id,
                active_task=active_task,
                review_task=review_task,
                blocked_task=blocked_task,
                active_worker_session=active_worker_session,
                review_worker_session=review_worker_session,
                blocked_worker_session=blocked_worker_session,
                review_id=review.id,
            )

            task_ids = [str(active_task.id), str(review_task.id), str(blocked_task.id)]
            session_ids = [
                str(manager_session.id),
                str(reviewer_session.id),
                str(active_worker_session.id),
                str(review_worker_session.id),
                str(blocked_worker_session.id),
            ]
            review_ids = [str(review.id)]

        _assign_demo_tasks(
            active_container,
            active_task_id=active_task.id,
            active_session_id=active_worker_session.id,
            review_task_id=review_task.id,
            review_session_id=review_worker_session.id,
            blocked_task_id=blocked_task.id,
            blocked_session_id=blocked_worker_session.id,
        )

        with active_container.database.session() as db:
            review_task_record = db.get(Task, review_task.id)
            if review_task_record is not None:
                review_task_record.status = TaskStatus.REVIEW
                db.commit()

        report = DemoSeedReport(
            seeded=True,
            reason="seeded_or_verified",
            repo_path=str(resolved_repo_path),
            project_id=str(project.id),
            task_ids=task_ids,
            session_ids=session_ids,
            review_ids=review_ids,
        )
        logger.info("demo data ready: %s", report.to_json())
        return report
    finally:
        if owned_container:
            active_container.shutdown()


def _ensure_project(
    project_service: ProjectService,
    db,
    repo_path: Path,
) -> ProjectRead:
    existing = db.scalar(select(Project).where(Project.repo_path == str(repo_path)))
    if existing is not None:
        metadata = dict(existing.metadata_json)
        metadata.setdefault("seeded_demo", True)
        metadata.setdefault(
            "helper_text",
            "Use the sample tasks to start a frontend, backend, or docs workflow in under a minute.",
        )
        metadata["demo"] = {
            "sample_tasks": DEMO_SAMPLE_TASKS,
            "how_it_works": DEMO_HOW_IT_WORKS,
            "default_flow": "start_task_to_review",
        }
        existing.metadata_json = metadata
        db.commit()
        db.refresh(existing)
        return ProjectRead.model_validate(existing)
    return project_service.create_project(
        ProjectCreate(
            name=DEMO_PROJECT_NAME,
            repo_path=str(repo_path),
            metadata={
                "seeded_demo": True,
                "notes": "Created automatically for local workspace-console development.",
                "helper_text": (
                    "Use the sample tasks to start a frontend, backend, or docs workflow in under a minute."
                ),
                "demo": {
                    "sample_tasks": DEMO_SAMPLE_TASKS,
                    "how_it_works": DEMO_HOW_IT_WORKS,
                    "default_flow": "start_task_to_review",
                },
            },
        )
    )


def _ensure_task(
    task_service: TaskService,
    db,
    project_id: UUID,
    *,
    title: str,
    aliases: list[str] | None = None,
    description: str,
    acceptance_criteria: list[str],
) -> TaskRead:
    candidate_titles = [title, *(aliases or [])]
    existing = db.scalar(
        select(Task).where(Task.project_id == project_id, Task.title.in_(candidate_titles))
    )
    if existing is not None:
        existing.title = title
        metadata = dict(existing.metadata_json)
        metadata.setdefault("seeded_demo", True)
        existing.metadata_json = metadata
        existing.acceptance_criteria = acceptance_criteria
        existing.description = description
        db.commit()
        db.refresh(existing)
        return TaskRead.model_validate(existing)
    return task_service.create_task(
        TaskCreate(
            project_id=project_id,
            title=title,
            description=description,
            acceptance_criteria=acceptance_criteria,
            metadata={"seeded_demo": True},
        )
    )


def _ensure_session(
    session_service: SessionService,
    workspace_service: WorkspaceService,
    db,
    project_id: UUID,
    *,
    role: SessionRole,
    task_id: UUID | None,
    command_override: str,
) -> SessionRead:
    stmt = select(SessionModel).where(
        SessionModel.project_id == project_id,
        SessionModel.role == role,
        SessionModel.command == command_override,
    )
    if task_id is None:
        stmt = stmt.where(SessionModel.task_id.is_(None))
    else:
        stmt = stmt.where(SessionModel.task_id == task_id)

    existing = db.scalar(stmt)
    if existing is not None:
        if role == SessionRole.WORKER and task_id is not None:
            workspace_service.provision_session_workspace(existing.id)
        return SessionRead.model_validate(existing)

    created = session_service.create_session(
        SessionCreate(
            project_id=project_id,
            task_id=task_id,
            role=role,
            command_override=command_override,
            simulation_mode=True,
            metadata={"seeded_demo": True},
        )
    )
    return created


def _ensure_review(
    *,
    db,
    review_service: ReviewService,
    project_id: UUID,
    task: TaskRead,
    worker_session: SessionRead,
) -> Review:
    existing = db.scalar(select(Review).where(Review.task_id == task.id))
    if existing is not None:
        return existing

    workspace = db.scalar(select(Workspace).where(Workspace.session_id == worker_session.id))
    if workspace is None:
        raise RuntimeError(f"Expected workspace for demo worker session {worker_session.id}")

    review_file = Path(workspace.workspace_path) / "frontend/components/ReviewPanel.tsx"
    review_file.parent.mkdir(parents=True, exist_ok=True)
    current_content = (
        review_file.read_text(encoding="utf-8")
        if review_file.exists()
        else DEMO_REPO_FILES["frontend/components/ReviewPanel.tsx"]
    )
    if "Operators need quick check summaries" not in current_content:
        review_file.write_text(
            current_content.replace(
                "Show diff summary, checks, and approval state.",
                "Show diff summary, checks, and approval state. Operators need quick check summaries.",
            ),
            encoding="utf-8",
        )

    review_read = review_service.create_review(
        ReviewCreate(
            project_id=project_id,
            task_id=task.id,
            requester_session_id=worker_session.id,
            summary="Pending reviewer pass for the seeded review handoff task.",
            lint_command="git diff --stat",
            test_command="git status --short",
            metadata={"seeded_demo": True},
        )
    )
    review = db.get(Review, review_read.id)
    if review is None:
        raise RuntimeError(f"Expected review to exist after creation: {review_read.id}")
    return review


def _ensure_demo_activity(
    *,
    db,
    event_service: EventService,
    project_id: UUID,
    active_task: TaskRead,
    review_task: TaskRead,
    blocked_task: TaskRead,
    active_worker_session: SessionRead,
    review_worker_session: SessionRead,
    blocked_worker_session: SessionRead,
    review_id: UUID,
) -> None:
    existing_events = db.scalars(select(Event).where(Event.project_id == project_id)).all()
    if any(event.source.get("id") == DEMO_EVENT_SOURCE_ID for event in existing_events):
        return

    source = EventSourceRef(kind="service", id=DEMO_EVENT_SOURCE_ID)
    seeded_events = [
        EventCreate(
            category=EventCategory.SESSION,
            event_type="session.progress",
            level=EventLevel.INFO,
            source=source,
            project_id=project_id,
            task_id=active_task.id,
            session_id=active_worker_session.id,
            payload={
                "summary": "Agent inspected the backend event pipeline and started a focused parser cleanup.",
                "files": ["backend/app/services", "backend/app/adapters"],
            },
        ),
        EventCreate(
            category=EventCategory.SESSION,
            event_type="session.tests_run",
            level=EventLevel.INFO,
            source=source,
            project_id=project_id,
            task_id=active_task.id,
            session_id=active_worker_session.id,
            payload={
                "summary": "Ran a quick backend check after adjusting the worker event flow.",
                "command": "pytest -q tests/test_event_parser.py",
                "status": "passed",
                "exit_code": 0,
            },
        ),
        EventCreate(
            category=EventCategory.SESSION,
            event_type="session.complete",
            level=EventLevel.INFO,
            source=source,
            project_id=project_id,
            task_id=review_task.id,
            session_id=review_worker_session.id,
            payload={
                "summary": "Prepared the review screen changes and handed the result to approval.",
                "files": ["frontend/components/ReviewPanel.tsx"],
            },
        ),
        EventCreate(
            category=EventCategory.TASK,
            event_type="task.blocked",
            level=EventLevel.WARN,
            source=source,
            project_id=project_id,
            task_id=blocked_task.id,
            session_id=blocked_worker_session.id,
            payload={
                "summary": "Documentation work is waiting for the active backend task to settle.",
                "reason": "waiting_on_dependencies",
            },
        ),
        EventCreate(
            category=EventCategory.REVIEW,
            event_type="review.created",
            level=EventLevel.INFO,
            source=source,
            project_id=project_id,
            task_id=review_task.id,
            session_id=review_worker_session.id,
            payload={
                "summary": "Approval package is ready with changed files and captured checks.",
                "review_id": str(review_id),
            },
        ),
    ]
    for event in seeded_events:
        event_service.record_event(event)


def _assign_demo_tasks(
    container: ServiceContainer,
    *,
    active_task_id: UUID,
    active_session_id: UUID,
    review_task_id: UUID,
    review_session_id: UUID,
    blocked_task_id: UUID,
    blocked_session_id: UUID,
) -> None:
    container.orchestrator.assign_task(
        active_task_id,
        TaskAssignmentRequest(
            session_id=active_session_id,
            allowed_paths=["backend/app/services"],
            note="Seeded active worker task for dashboard demos.",
        ),
    )
    container.orchestrator.assign_task(
        review_task_id,
        TaskAssignmentRequest(
            session_id=review_session_id,
            allowed_paths=["frontend/components"],
            note="Seeded review-ready task for dashboard demos.",
        ),
    )
    container.orchestrator.assign_task(
        blocked_task_id,
        TaskAssignmentRequest(
            session_id=blocked_session_id,
            allowed_paths=["docs"],
            dependency_task_ids=[active_task_id],
            note="Seeded blocked task waiting on the active worker task.",
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed local demo data for the orchestrator.")
    parser.add_argument(
        "repo_path",
        nargs="?",
        help="Optional path to a local git repo to attach to the seeded project. Defaults to an autogenerated demo repo.",
    )
    parser.add_argument(
        "--if-empty",
        action="store_true",
        help="Only seed when the database has no projects yet.",
    )
    args = parser.parse_args()

    report = seed_demo_environment(
        get_settings(),
        repo_path=args.repo_path,
        if_empty=args.if_empty,
    )
    print(report.to_json())


if __name__ == "__main__":
    main()
