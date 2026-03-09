from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path

from app.adapters.agent_adapter import AgentAdapter, AgentSessionConfig
from app.adapters.process_supervisor import (
    ProcessCallbacks,
    ProcessLaunchSpec,
    ProcessRuntimeSnapshot,
    ProcessSupervisorAdapter,
)
from app.core.errors import ValidationError


class CodexLocalAdapter(AgentAdapter):
    kind = "codex_local"

    def __init__(
        self,
        process_supervisor: ProcessSupervisorAdapter,
        *,
        default_simulation_mode: bool = True,
        heartbeat_interval_seconds: float = 2.0,
    ) -> None:
        self.process_supervisor = process_supervisor
        self.default_simulation_mode = default_simulation_mode
        self.heartbeat_interval_seconds = heartbeat_interval_seconds
        self.backend_root = Path(__file__).resolve().parents[2]
        self._simulation_modes: dict[str, bool] = {}

    def start(
        self,
        session_config: AgentSessionConfig,
        *,
        callbacks: ProcessCallbacks,
    ) -> ProcessRuntimeSnapshot:
        command = self._build_command(session_config)
        env = os.environ.copy()
        env.update(session_config.env)
        env["PYTHONUNBUFFERED"] = "1"
        env["ORCHESTRATOR_SESSION_ID"] = session_config.session_id
        env["ORCHESTRATOR_ROLE"] = session_config.role.value
        env["ORCHESTRATOR_WORKSPACE_PATH"] = session_config.workspace_path or ""
        env["ORCHESTRATOR_SIMULATION_MODE"] = "1" if session_config.simulation_mode else "0"

        python_path = env.get("PYTHONPATH")
        backend_root = str(self.backend_root)
        env["PYTHONPATH"] = backend_root if not python_path else f"{backend_root}{os.pathsep}{python_path}"

        spec = ProcessLaunchSpec(
            session_id=session_config.session_id,
            command=command,
            cwd=session_config.cwd,
            env=env,
            heartbeat_interval_seconds=self.heartbeat_interval_seconds,
        )
        self._simulation_modes[session_config.session_id] = session_config.simulation_mode
        return self.process_supervisor.launch(spec, callbacks=callbacks)

    def send(self, session_id: str, message: str) -> None:
        self.process_supervisor.send(session_id, message)

    def interrupt(self, session_id: str) -> None:
        if self._simulation_modes.get(session_id, self.default_simulation_mode):
            self.process_supervisor.send(session_id, "__INTERRUPT__")
            return
        self.process_supervisor.interrupt(session_id)

    def stop(self, session_id: str) -> ProcessRuntimeSnapshot | None:
        return self.process_supervisor.stop(session_id)

    def get_status(self, session_id: str) -> ProcessRuntimeSnapshot | None:
        return self.process_supervisor.get_status(session_id)

    def shutdown(self) -> None:
        self.process_supervisor.shutdown()

    def _build_command(self, session_config: AgentSessionConfig) -> list[str]:
        simulation_mode = session_config.simulation_mode
        if simulation_mode is None:
            simulation_mode = self.default_simulation_mode

        if simulation_mode:
            command = [
                sys.executable,
                "-m",
                "app.dev.codex_session_simulator",
                "--session-id",
                session_config.session_id,
                "--role",
                session_config.role.value,
            ]
            if session_config.workspace_path:
                command.extend(["--workspace-path", session_config.workspace_path])
            return command

        # This is the swap point for a real Codex CLI invocation.
        if not session_config.command.strip():
            raise ValidationError("Real Codex execution requires a non-empty command.")
        return shlex.split(session_config.command)
