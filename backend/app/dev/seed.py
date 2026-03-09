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
from app.core.enums import SessionRole, TaskStatus
from app.models import Base
from app.models.project import Project
from app.models.review import Review
from app.models.session import Session as SessionModel
from app.models.task import Task
from app.models.workspace import Workspace
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

DEMO_PROJECT_NAME = "Local Agent Orchestrator Demo"
DEMO_COMMIT_MESSAGE = "Seed demo repository"
DEMO_GIT_USER_NAME = "Local Agent Orchestrator"
DEMO_GIT_USER_EMAIL = "demo@local-agent-orchestrator.local"

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
            project_service = ProjectService(
                db=db,
                event_service=event_service,
                repo_inspector=active_container.repo_inspector,
            )
            task_service = TaskService(db=db, event_service=event_service)
            workspace_service = WorkspaceService(
                db=db,
                event_service=event_service,
                worktree_manager=active_container.worktree_manager,
                repo_inspector=active_container.repo_inspector,
                command_runner=active_container.command_runner,
            )
            session_service = SessionService(
                db=db,
                event_service=event_service,
                session_supervisor=active_container.session_supervisor,
                worktree_manager=active_container.worktree_manager,
                repo_inspector=active_container.repo_inspector,
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
                title="Stabilize worker structured events",
                description="Keep the worker event stream readable, typed, and recoverable when output is malformed.",
                acceptance_criteria=[
                    "parse [[EVENT]] blocks",
                    "persist structured events separately from raw transcript",
                    "surface malformed blocks for debugging",
                ],
            )
            review_task = _ensure_task(
                task_service,
                db,
                project.id,
                title="Polish review handoff panel",
                description="Package diff summary, test outcomes, and reviewer notes for operators.",
                acceptance_criteria=[
                    "show diff summary",
                    "show lint and test outcomes",
                    "capture reviewer notes and human approval state",
                ],
            )
            blocked_task = _ensure_task(
                task_service,
                db,
                project.id,
                title="Document merge queue handoff",
                description="Clarify what happens after review approval and before merge execution.",
                acceptance_criteria=[
                    "document merge-ready gate",
                    "document human approval requirement",
                    "document where merge execution plugs in later",
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
        return ProjectRead.model_validate(existing)
    return project_service.create_project(
        ProjectCreate(
            name=DEMO_PROJECT_NAME,
            repo_path=str(repo_path),
            metadata={
                "seeded_demo": True,
                "notes": "Created automatically for local operator-console development.",
            },
        )
    )


def _ensure_task(
    task_service: TaskService,
    db,
    project_id: UUID,
    *,
    title: str,
    description: str,
    acceptance_criteria: list[str],
) -> TaskRead:
    existing = db.scalar(
        select(Task).where(Task.project_id == project_id, Task.title == title)
    )
    if existing is not None:
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
    current_content = review_file.read_text(encoding="utf-8")
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
