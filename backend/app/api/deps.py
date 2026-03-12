from collections.abc import Generator

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core.container import ServiceContainer
from app.core.database import get_db as get_database_session
from app.services.event_service import EventService
from app.services.orchestration_service import OrchestratorService
from app.services.portfolio_automation_service import PortfolioAutomationService
from app.services.portfolio_service import PortfolioService
from app.services.project_service import ProjectService
from app.services.review_service import ReviewService
from app.services.runtime_service import RuntimeService
from app.services.session_service import SessionService
from app.services.task_service import TaskService
from app.services.workspace_service import WorkspaceService


def get_container(request: Request) -> ServiceContainer:
    return request.app.state.container


def get_db(
    container: ServiceContainer = Depends(get_container),
) -> Generator[Session, None, None]:
    yield from get_database_session(container.database)


def get_event_service(
    db: Session = Depends(get_db),
    container: ServiceContainer = Depends(get_container),
) -> EventService:
    return EventService(
        db=db,
        redis_bus=container.redis_bus,
        event_broker=container.event_broker,
    )


def get_orchestrator_service(
    container: ServiceContainer = Depends(get_container),
) -> OrchestratorService:
    return container.orchestrator


def get_portfolio_automation_service(
    container: ServiceContainer = Depends(get_container),
) -> PortfolioAutomationService:
    return container.portfolio_automation


def get_runtime_service(
    container: ServiceContainer = Depends(get_container),
) -> RuntimeService:
    return container.runtime_service


def get_project_service(
    db: Session = Depends(get_db),
    event_service: EventService = Depends(get_event_service),
    container: ServiceContainer = Depends(get_container),
) -> ProjectService:
    workspace_service = WorkspaceService(
        db=db,
        event_service=event_service,
        worktree_manager=container.worktree_manager,
        repo_inspector=container.repo_inspector,
        command_runner=container.command_runner,
    )
    return ProjectService(
        db=db,
        settings=container.settings,
        event_service=event_service,
        command_runner=container.command_runner,
        repo_inspector=container.repo_inspector,
        runtime_service=container.runtime_service,
        session_supervisor=container.session_supervisor,
        workspace_service=workspace_service,
    )


def get_portfolio_service(
    db: Session = Depends(get_db),
    event_service: EventService = Depends(get_event_service),
    project_service: ProjectService = Depends(get_project_service),
    container: ServiceContainer = Depends(get_container),
) -> PortfolioService:
    return PortfolioService(
        db=db,
        event_service=event_service,
        settings=container.settings,
        runtime_service=container.runtime_service,
        session_supervisor=container.session_supervisor,
        project_service=project_service,
    )


def get_task_service(
    db: Session = Depends(get_db),
    event_service: EventService = Depends(get_event_service),
) -> TaskService:
    return TaskService(db=db, event_service=event_service)


def get_workspace_service(
    db: Session = Depends(get_db),
    event_service: EventService = Depends(get_event_service),
    container: ServiceContainer = Depends(get_container),
) -> WorkspaceService:
    return WorkspaceService(
        db=db,
        event_service=event_service,
        worktree_manager=container.worktree_manager,
        repo_inspector=container.repo_inspector,
        command_runner=container.command_runner,
    )


def get_session_service(
    db: Session = Depends(get_db),
    event_service: EventService = Depends(get_event_service),
    container: ServiceContainer = Depends(get_container),
) -> SessionService:
    workspace_service = WorkspaceService(
        db=db,
        event_service=event_service,
        worktree_manager=container.worktree_manager,
        repo_inspector=container.repo_inspector,
        command_runner=container.command_runner,
    )
    return SessionService(
        db=db,
        event_service=event_service,
        session_supervisor=container.session_supervisor,
        worktree_manager=container.worktree_manager,
        repo_inspector=container.repo_inspector,
        runtime_service=container.runtime_service,
        workspace_service=workspace_service,
    )


def get_review_service(
    db: Session = Depends(get_db),
    event_service: EventService = Depends(get_event_service),
    container: ServiceContainer = Depends(get_container),
) -> ReviewService:
    workspace_service = WorkspaceService(
        db=db,
        event_service=event_service,
        worktree_manager=container.worktree_manager,
        repo_inspector=container.repo_inspector,
        command_runner=container.command_runner,
    )
    return ReviewService(
        db=db,
        event_service=event_service,
        workspace_service=workspace_service,
    )
