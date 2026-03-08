from __future__ import annotations

from dataclasses import dataclass

from app.core.enums import SessionRole, SessionStatus, SessionTransport


@dataclass(slots=True)
class SessionLaunchPlan:
    transport: SessionTransport
    command: str
    initial_status: SessionStatus
    notes: str


class SessionSupervisor:
    def plan_session(
        self,
        *,
        role: SessionRole,
        command_override: str | None = None,
    ) -> SessionLaunchPlan:
        default_commands = {
            SessionRole.MANAGER: "codex manager",
            SessionRole.WORKER: "codex worker",
            SessionRole.REVIEWER: "codex reviewer",
        }
        return SessionLaunchPlan(
            transport=SessionTransport.LOCAL_PROCESS,
            command=command_override or default_commands[role],
            initial_status=SessionStatus.PENDING,
            notes="Process creation is intentionally stubbed in the backend foundation.",
        )

    def stop_session(self, current_status: SessionStatus) -> SessionStatus:
        if current_status == SessionStatus.RUNNING:
            return SessionStatus.STOPPED
        return SessionStatus.STOPPED

