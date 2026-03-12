from __future__ import annotations

from dataclasses import dataclass

from app.adapters.claude_code_local import ClaudeCodeLocalAdapter
from app.adapters.codex_local import CodexLocalAdapter
from app.adapters.event_parser import EventParserAdapter
from app.adapters.redis_bus import RedisBusAdapter
from app.adapters.repo_inspector import RepoInspectorAdapter
from app.adapters.process_supervisor import ProcessSupervisorAdapter
from app.core.config import Settings
from app.core.database import DatabaseManager, build_database_manager
from app.core.event_stream import EventStreamBroker
from app.services.command_runner import CommandRunner
from app.services.orchestration_service import OrchestratorService
from app.services.portfolio_automation_service import PortfolioAutomationService
from app.services.runtime_service import RuntimeService
from app.services.task_packet_service import TaskPacketService
from app.services.session_supervisor import SessionSupervisor
from app.services.worktree_manager import WorktreeManager


@dataclass(slots=True)
class ServiceContainer:
    settings: Settings
    database: DatabaseManager
    redis_bus: RedisBusAdapter
    event_broker: EventStreamBroker
    event_parser: EventParserAdapter
    command_runner: CommandRunner
    repo_inspector: RepoInspectorAdapter
    runtime_service: RuntimeService
    task_packet_service: TaskPacketService
    process_supervisor: ProcessSupervisorAdapter
    session_supervisor: SessionSupervisor
    worktree_manager: WorktreeManager
    orchestrator: OrchestratorService
    portfolio_automation: PortfolioAutomationService

    def ensure_local_dirs(self) -> None:
        self.settings.ensure_local_dirs()

    def shutdown(self) -> None:
        self.session_supervisor.shutdown()
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
    event_parser = EventParserAdapter()
    command_runner = CommandRunner()
    repo_inspector = RepoInspectorAdapter(command_runner)
    runtime_service = RuntimeService(settings)
    task_packet_service = TaskPacketService(database)
    process_supervisor = ProcessSupervisorAdapter(
        stop_grace_seconds=settings.session_stop_grace_seconds,
    )
    worktree_manager = WorktreeManager(settings, command_runner)
    codex_adapter = CodexLocalAdapter(
        process_supervisor,
        default_simulation_mode=settings.codex_simulation_mode_default,
        heartbeat_interval_seconds=settings.session_heartbeat_interval_seconds,
    )
    claude_code_adapter = ClaudeCodeLocalAdapter(
        process_supervisor,
        default_simulation_mode=settings.codex_simulation_mode_default,
        heartbeat_interval_seconds=settings.session_heartbeat_interval_seconds,
    )
    session_supervisor = SessionSupervisor(
        settings=settings,
        database=database,
        redis_bus=redis_bus,
        event_broker=event_broker,
        event_parser=event_parser,
        runtime_service=runtime_service,
        task_packet_service=task_packet_service,
        worktree_manager=worktree_manager,
        adapters={
            codex_adapter.kind: codex_adapter,
            claude_code_adapter.kind: claude_code_adapter,
        },
    )
    orchestrator = OrchestratorService(
        settings=settings,
        database=database,
        redis_bus=redis_bus,
        event_broker=event_broker,
        worktree_manager=worktree_manager,
        repo_inspector=repo_inspector,
        command_runner=command_runner,
        session_supervisor=session_supervisor,
        runtime_service=runtime_service,
    )
    portfolio_automation = PortfolioAutomationService(
        settings=settings,
        database=database,
        redis_bus=redis_bus,
        event_broker=event_broker,
        repo_inspector=repo_inspector,
        command_runner=command_runner,
        runtime_service=runtime_service,
        session_supervisor=session_supervisor,
        worktree_manager=worktree_manager,
    )

    return ServiceContainer(
        settings=settings,
        database=database,
        redis_bus=redis_bus,
        event_broker=event_broker,
        event_parser=event_parser,
        command_runner=command_runner,
        repo_inspector=repo_inspector,
        runtime_service=runtime_service,
        task_packet_service=task_packet_service,
        process_supervisor=process_supervisor,
        session_supervisor=session_supervisor,
        worktree_manager=worktree_manager,
        orchestrator=orchestrator,
        portfolio_automation=portfolio_automation,
    )
