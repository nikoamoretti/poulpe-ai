from __future__ import annotations

import argparse

from app.core.container import build_container
from app.core.config import get_settings
from app.models import Base
from app.schemas.project import ProjectCreate
from app.schemas.session import SessionCreate
from app.schemas.task import TaskCreate
from app.core.enums import SessionRole
from app.services.event_service import EventService
from app.services.project_service import ProjectService
from app.services.session_service import SessionService
from app.services.task_service import TaskService


def seed_demo_data(repo_path: str) -> None:
    settings = get_settings()
    container = build_container(settings)
    container.ensure_local_dirs()
    if settings.auto_create_schema:
        Base.metadata.create_all(container.database.engine)

    with container.database.session() as db:
        event_service = EventService(
            db=db,
            redis_bus=container.redis_bus,
            event_broker=container.event_broker,
        )
        project_service = ProjectService(
            db=db,
            event_service=event_service,
            repo_inspector=container.repo_inspector,
        )
        task_service = TaskService(db=db, event_service=event_service)
        session_service = SessionService(
            db=db,
            event_service=event_service,
            session_supervisor=container.session_supervisor,
            worktree_manager=container.worktree_manager,
            repo_inspector=container.repo_inspector,
        )

        project = project_service.create_project(
            ProjectCreate(name="Seeded Demo Project", repo_path=repo_path)
        )
        task = task_service.create_task(
            TaskCreate(
                project_id=project.id,
                title="Seeded top-level task",
                description="Validate the backend foundation wiring.",
                acceptance_criteria=["Create project", "Create task", "Create worker session"],
            )
        )
        session_service.create_session(
            SessionCreate(
                project_id=project.id,
                task_id=task.id,
                role=SessionRole.WORKER,
                command_override="codex worker --demo",
            )
        )

    container.shutdown()


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed demo backend data.")
    parser.add_argument("repo_path", help="Path to a local git repository to attach to the seeded project.")
    args = parser.parse_args()
    seed_demo_data(args.repo_path)


if __name__ == "__main__":
    main()
