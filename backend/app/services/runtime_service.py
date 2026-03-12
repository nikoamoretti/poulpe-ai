from __future__ import annotations

import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.core.config import Settings
from app.core.enums import SessionRole, SessionStatus
from app.schemas.runtime import RuntimeCapabilityRead, RuntimeSelectionRead, RuntimeStatusRead

REAL_PROVIDERS = ("codex", "claude_code")
AUTO_PROVIDER_ORDER = ("codex", "claude_code")
PROVIDER_LABELS = {
    "auto": "Auto",
    "codex": "Codex",
    "claude_code": "Claude Code",
    "simulated": "Simulated",
    "none": "No runtime",
}


@dataclass(slots=True)
class RuntimeProbe:
    configured: bool
    available: bool
    summary: str


@dataclass(slots=True)
class RuntimeLaunchPlan:
    command: str
    initial_status: SessionStatus
    simulation_mode: bool
    blocked_reason: str | None
    runtime: RuntimeSelectionRead
    notes: str


class RuntimeService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def get_runtime_status(self, *, role: SessionRole) -> RuntimeStatusRead:
        selections = {
            "auto": self.resolve_launch(role=role, requested_provider="auto").runtime,
            "codex": self.resolve_launch(
                role=role,
                requested_provider="codex",
                allow_simulation_fallback=False,
            ).runtime,
            "claude_code": self.resolve_launch(
                role=role,
                requested_provider="claude_code",
                allow_simulation_fallback=False,
            ).runtime,
        }
        providers = [self._provider_capability(provider, role=role) for provider in (*REAL_PROVIDERS, "simulated")]
        supported_real_providers = [
            capability.provider
            for capability in providers
            if capability.provider in REAL_PROVIDERS and capability.available
        ]
        return RuntimeStatusRead(
            role=role,
            selections=selections,
            providers=providers,
            supported_real_providers=supported_real_providers,
        )

    def resolve_launch(
        self,
        *,
        role: SessionRole,
        requested_provider: str | None = None,
        command_override: str | None = None,
        simulation_mode: bool | None = None,
        allow_simulation_fallback: bool | None = None,
    ) -> RuntimeLaunchPlan:
        requested = requested_provider or "auto"
        if requested not in {"auto", "codex", "claude_code"}:
            requested = "auto"

        if allow_simulation_fallback is None:
            allow_simulation_fallback = requested == "auto"

        if simulation_mode is True:
            runtime = RuntimeSelectionRead(
                requested_provider=requested,
                resolved_provider="simulated",
                configured=True,
                available=True,
                simulated=True,
                disconnected=False,
                can_start=True,
                command=command_override,
                summary="Using the simulated dev runtime by explicit request.",
            )
            return RuntimeLaunchPlan(
                command=command_override or self._fallback_command(role),
                initial_status=SessionStatus.PENDING,
                simulation_mode=True,
                blocked_reason=None,
                runtime=runtime,
                notes=runtime.summary,
            )

        if simulation_mode is False:
            provider = requested if requested in REAL_PROVIDERS else self._infer_provider(command_override) or "codex"
            return self._resolve_real_only(
                role=role,
                requested_provider=requested,
                provider=provider,
                command=command_override or self._provider_command(provider, role),
            )

        if requested == "auto":
            for provider in AUTO_PROVIDER_ORDER:
                command = command_override or self._provider_command(provider, role)
                probe = self._probe_provider(provider, command)
                if probe.available:
                    runtime = RuntimeSelectionRead(
                        requested_provider="auto",
                        resolved_provider=provider,
                        configured=probe.configured,
                        available=True,
                        simulated=False,
                        disconnected=False,
                        can_start=True,
                        command=command,
                        summary=f"Using a real {PROVIDER_LABELS[provider]} process.",
                    )
                    return RuntimeLaunchPlan(
                        command=command,
                        initial_status=SessionStatus.PENDING,
                        simulation_mode=False,
                        blocked_reason=None,
                        runtime=runtime,
                        notes=runtime.summary,
                    )

            runtime = RuntimeSelectionRead(
                requested_provider="auto",
                resolved_provider="simulated",
                configured=True,
                available=True,
                simulated=True,
                disconnected=False,
                can_start=True,
                command=command_override,
                summary="No real runtime is available. Falling back to the simulated dev runtime.",
            )
            return RuntimeLaunchPlan(
                command=command_override or self._fallback_command(role),
                initial_status=SessionStatus.PENDING,
                simulation_mode=True,
                blocked_reason=None,
                runtime=runtime,
                notes=runtime.summary,
            )

        provider = requested
        if allow_simulation_fallback:
            command = command_override or self._provider_command(provider, role)
            probe = self._probe_provider(provider, command)
            if probe.available:
                return self._resolve_real_only(
                    role=role,
                    requested_provider=requested,
                    provider=provider,
                    command=command,
                )

            runtime = RuntimeSelectionRead(
                requested_provider=requested,
                resolved_provider="simulated",
                configured=True,
                available=True,
                simulated=True,
                disconnected=False,
                can_start=True,
                command=command,
                summary=f"{probe.summary} Falling back to the simulated dev runtime.",
            )
            return RuntimeLaunchPlan(
                command=command,
                initial_status=SessionStatus.PENDING,
                simulation_mode=True,
                blocked_reason=None,
                runtime=runtime,
                notes=runtime.summary,
            )

        return self._resolve_real_only(
            role=role,
            requested_provider=requested,
            provider=provider,
            command=command_override or self._provider_command(provider, role),
        )

    def runtime_from_metadata(self, metadata: dict[str, object]) -> RuntimeSelectionRead:
        runtime = metadata.get("runtime")
        if isinstance(runtime, dict):
            return RuntimeSelectionRead.model_validate(runtime)
        return RuntimeSelectionRead()

    def _resolve_real_only(
        self,
        *,
        role: SessionRole,
        requested_provider: str,
        provider: str,
        command: str,
    ) -> RuntimeLaunchPlan:
        probe = self._probe_provider(provider, command)
        if probe.available:
            runtime = RuntimeSelectionRead(
                requested_provider=requested_provider if requested_provider in {"auto", "codex", "claude_code"} else "auto",
                resolved_provider=provider if provider in {"codex", "claude_code"} else "none",
                configured=True,
                available=True,
                simulated=False,
                disconnected=False,
                can_start=True,
                command=command,
                summary=f"Using a real {PROVIDER_LABELS.get(provider, 'runtime')} process.",
            )
            return RuntimeLaunchPlan(
                command=command,
                initial_status=SessionStatus.PENDING,
                simulation_mode=False,
                blocked_reason=None,
                runtime=runtime,
                notes=runtime.summary,
            )

        runtime = RuntimeSelectionRead(
            requested_provider=requested_provider if requested_provider in {"auto", "codex", "claude_code"} else "auto",
            resolved_provider="none",
            configured=probe.configured,
            available=False,
            simulated=False,
            disconnected=True,
            can_start=False,
            command=command or None,
            summary=probe.summary,
        )
        return RuntimeLaunchPlan(
            command=command,
            initial_status=SessionStatus.BLOCKED,
            simulation_mode=False,
            blocked_reason="runtime_disconnected",
            runtime=runtime,
            notes=runtime.summary,
        )

    def _provider_capability(self, provider: str, *, role: SessionRole) -> RuntimeCapabilityRead:
        if provider == "simulated":
            return RuntimeCapabilityRead(
                provider="simulated",
                label="Simulated dev runtime",
                configured=True,
                available=True,
                simulated=True,
                disconnected=False,
                command=None,
                summary="Always available for development and testing.",
            )

        command = self._provider_command(provider, role)
        probe = self._probe_provider(provider, command)
        return RuntimeCapabilityRead(
            provider=provider,  # type: ignore[arg-type]
            label=PROVIDER_LABELS[provider],
            configured=probe.configured,
            available=probe.available,
            simulated=False,
            disconnected=not probe.available,
            command=command or None,
            summary=probe.summary,
        )

    def _provider_command(self, provider: str, role: SessionRole) -> str:
        template = {
            "codex": self.settings.codex_runtime_command_template,
            "claude_code": self.settings.claude_code_runtime_command_template,
        }.get(provider, "")
        return template.format(role=role.value).strip() if template else ""

    @staticmethod
    def _fallback_command(role: SessionRole) -> str:
        return f"simulated {role.value}"

    @staticmethod
    def _infer_provider(command: str | None) -> str | None:
        if not command or not command.strip():
            return None
        executable = shlex.split(command)[0]
        name = Path(executable).name.lower()
        if name == "codex":
            return "codex"
        if name in {"claude-code", "claude"}:
            return "claude_code"
        return None

    @staticmethod
    def _command_available(command: str | None) -> bool:
        if not command or not command.strip():
            return False
        executable = shlex.split(command)[0]
        path = Path(executable).expanduser()
        if "/" in executable:
            return path.exists() and path.is_file()
        return shutil.which(executable) is not None

    def _probe_provider(self, provider: str, command: str | None) -> RuntimeProbe:
        label = PROVIDER_LABELS.get(provider, "runtime")
        configured = bool((command or "").strip())
        if not configured:
            return RuntimeProbe(
                configured=False,
                available=False,
                summary=f"{label} runtime command is not configured.",
            )
        if not self._command_available(command):
            return RuntimeProbe(
                configured=True,
                available=False,
                summary=f"{label} CLI is not installed or not on PATH.",
            )
        if provider != "codex":
            return RuntimeProbe(
                configured=True,
                available=True,
                summary=f"Real {label} process is available.",
            )

        executable = shlex.split(command or "")[0]
        try:
            result = subprocess.run(
                [executable, "login", "status"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except Exception as exc:
            return RuntimeProbe(
                configured=True,
                available=False,
                summary=(
                    "Codex CLI is installed but readiness could not be verified. "
                    f"Run `codex login status` manually. ({exc})"
                ),
            )

        if result.returncode != 0:
            detail = self._first_line(result.stdout, result.stderr) or "Codex CLI is not authenticated."
            return RuntimeProbe(
                configured=True,
                available=False,
                summary=f"Codex CLI is installed but not ready. {detail} Run `codex login`.",
            )

        return RuntimeProbe(
            configured=True,
            available=True,
            summary="Real Codex process is available.",
        )

    @staticmethod
    def _first_line(*chunks: str) -> str | None:
        for chunk in chunks:
            for line in chunk.splitlines():
                cleaned = line.strip()
                if cleaned:
                    return cleaned
        return None
