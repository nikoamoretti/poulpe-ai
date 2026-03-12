from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from app.core.enums import SessionRole
from app.adapters.process_supervisor import ProcessCallbacks, ProcessRuntimeSnapshot


@dataclass(slots=True)
class AgentSessionConfig:
    session_id: str
    role: SessionRole
    command: str
    cwd: str
    workspace_path: str | None = None
    model: str | None = None
    simulation_mode: bool = True
    startup_message: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class AgentAdapter(Protocol):
    kind: str

    def start(
        self,
        session_config: AgentSessionConfig,
        *,
        callbacks: ProcessCallbacks,
    ) -> ProcessRuntimeSnapshot: ...

    def send(self, session_id: str, message: str) -> None: ...

    def interrupt(self, session_id: str) -> None: ...

    def stop(self, session_id: str) -> ProcessRuntimeSnapshot | None: ...

    def get_status(self, session_id: str) -> ProcessRuntimeSnapshot | None: ...

    def shutdown(self) -> None: ...
