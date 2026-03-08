from dataclasses import dataclass, field


@dataclass(slots=True)
class ProcessLaunchSpec:
    session_id: str
    command: list[str]
    cwd: str
    env: dict[str, str] = field(default_factory=dict)


class ProcessSupervisorAdapter:
    """Supervise local terminal processes for manager, worker, and reviewer sessions."""

    def launch(self, spec: ProcessLaunchSpec) -> None:
        raise NotImplementedError("Session process supervision is not implemented yet.")

    def stop(self, session_id: str) -> None:
        raise NotImplementedError("Session stop handling is not implemented yet.")

