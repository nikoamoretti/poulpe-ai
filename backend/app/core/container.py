from __future__ import annotations

from dataclasses import dataclass

from app.adapters.redis_bus import RedisBusAdapter
from app.adapters.repo_inspector import RepoInspectorAdapter
from app.core.config import Settings
from app.core.database import DatabaseManager, build_database_manager
from app.core.event_stream import EventStreamBroker
from app.services.command_runner import CommandRunner
from app.services.orchestration_service import OrchestratorService
from app.services.session_supervisor import SessionSupervisor
from app.services.worktree_manager import WorktreeManager


@dataclass(slots=True)
class ServiceContainer:
    settings: Settings
    database: DatabaseManager
    redis_bus: RedisBusAdapter
    event_broker: EventStreamBroker
    command_runner: CommandRunner
    repo_inspector: RepoInspectorAdapter
    session_supervisor: SessionSupervisor
    worktree_manager: WorktreeManager
    orchestrator: OrchestratorService

    def ensure_local_dirs(self) -> None:
        self.settings.ensure_local_dirs()

    def shutdown(self) -> None:
        self.redis_bus.close()
        self.database.dispose()

    def health_checks(self) -> dict[str, str]:
        database_status = "ok"
        redis_status = "disabled" if not self.settings.redis_enabled else "error"

        try:
            self.database.ping()
        except Exception:
            database_status = "error"

        if self.settings.redis_enabled:
            redis_status = "ok" if self.redis_bus.ping() else "error"

        return {
            "database": database_status,
            "redis": redis_status,
        }


def build_container(settings: Settings) -> ServiceContainer:
    database = build_database_manager(settings)
    redis_bus = RedisBusAdapter(settings.redis_url, enabled=settings.redis_enabled)
    event_broker = EventStreamBroker()
    command_runner = CommandRunner()
    repo_inspector = RepoInspectorAdapter(command_runner)
    session_supervisor = SessionSupervisor()
    worktree_manager = WorktreeManager(settings)
    orchestrator = OrchestratorService(worktree_manager, session_supervisor)

    return ServiceContainer(
        settings=settings,
        database=database,
        redis_bus=redis_bus,
        event_broker=event_broker,
        command_runner=command_runner,
        repo_inspector=repo_inspector,
        session_supervisor=session_supervisor,
        worktree_manager=worktree_manager,
        orchestrator=orchestrator,
    )
