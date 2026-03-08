from __future__ import annotations

import os
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from app.core.errors import InfrastructureError, ValidationError


@dataclass(slots=True)
class CommandResult:
    command: list[str]
    command_text: str
    cwd: str | None
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool
    duration_ms: int


class CommandRunner:
    _SHELL_BLOCKLIST = (
        "rm -rf",
        "git reset --hard",
        "git clean -fd",
        "git clean -xdf",
        "git checkout --",
        "git branch -d",
        "git branch -D",
        "git worktree remove",
        "sudo ",
        "mkfs",
    )

    def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path | str | None = None,
        env: Mapping[str, str] | None = None,
        timeout: int | float | None = 30,
        check: bool = True,
        allow_destructive: bool = False,
    ) -> CommandResult:
        command_list = list(command)
        if not command_list:
            raise ValidationError("Command cannot be empty.")
        if not allow_destructive and self._is_destructive_command(command_list):
            raise ValidationError(
                f"Blocked destructive command by default: {shlex.join(command_list)}. "
                "Pass allow_destructive=True only for explicit cleanup flows."
            )

        resolved_cwd = self._resolve_cwd(cwd)
        merged_env = os.environ.copy()
        if env:
            merged_env.update(env)

        started_at = time.monotonic()
        command_text = shlex.join(command_list)
        try:
            completed = subprocess.run(
                command_list,
                cwd=str(resolved_cwd) if resolved_cwd is not None else None,
                env=merged_env,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            result = CommandResult(
                command=command_list,
                command_text=command_text,
                cwd=str(resolved_cwd) if resolved_cwd is not None else None,
                returncode=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
                timed_out=False,
                duration_ms=int((time.monotonic() - started_at) * 1000),
            )
        except subprocess.TimeoutExpired as exc:
            timeout_message = (
                f"Command timed out after {timeout} seconds: {command_text}"
                if timeout is not None
                else f"Command timed out: {command_text}"
            )
            result = CommandResult(
                command=command_list,
                command_text=command_text,
                cwd=str(resolved_cwd) if resolved_cwd is not None else None,
                returncode=124,
                stdout=exc.stdout or "",
                stderr=(exc.stderr or "") + timeout_message,
                timed_out=True,
                duration_ms=int((time.monotonic() - started_at) * 1000),
            )

        if check and result.returncode != 0:
            self._raise_for_result(result)
        return result

    def run_shell(
        self,
        command: str,
        *,
        cwd: Path | str | None = None,
        env: Mapping[str, str] | None = None,
        timeout: int | float | None = 30,
        check: bool = True,
        allow_destructive: bool = False,
        shell_executable: str = "/bin/sh",
    ) -> CommandResult:
        if not command.strip():
            raise ValidationError("Shell command cannot be empty.")
        if not allow_destructive and self._is_destructive_shell_command(command):
            raise ValidationError(
                f"Blocked destructive shell command by default: {command}. "
                "Pass allow_destructive=True only for explicit cleanup flows."
            )
        return self.run(
            [shell_executable, "-lc", command],
            cwd=cwd,
            env=env,
            timeout=timeout,
            check=check,
            allow_destructive=True,
        )

    def _resolve_cwd(self, cwd: Path | str | None) -> Path | None:
        if cwd is None:
            return None

        resolved = Path(cwd).expanduser().resolve()
        if not resolved.exists():
            raise ValidationError(f"Command cwd does not exist: {resolved}")
        if not resolved.is_dir():
            raise ValidationError(f"Command cwd is not a directory: {resolved}")
        return resolved

    def _raise_for_result(self, result: CommandResult) -> None:
        context = result.stderr.strip() or result.stdout.strip() or "No command output was captured."
        if result.timed_out:
            raise InfrastructureError(
                f"Command timed out in {result.cwd or '.'}: {result.command_text}\n{context}"
            )
        raise InfrastructureError(
            f"Command failed in {result.cwd or '.'} with exit code {result.returncode}: "
            f"{result.command_text}\n{context}"
        )

    def _is_destructive_command(self, command: Sequence[str]) -> bool:
        head = list(command[:3])
        if not head:
            return False

        executable = command[0]
        if executable == "rm":
            flags = "".join(token.lstrip("-") for token in command[1:] if token.startswith("-"))
            return "r" in flags and "f" in flags

        if executable != "git" or len(command) < 2:
            return False

        subcommand = command[1]
        if subcommand == "reset":
            return any(token == "--hard" or token.startswith("--hard=") for token in command[2:])
        if subcommand == "clean":
            flags = "".join(token.lstrip("-") for token in command[2:] if token.startswith("-"))
            return "f" in flags and ("d" in flags or "x" in flags)
        if subcommand == "checkout":
            return "--" in command[2:]
        if subcommand == "branch":
            return any(token in {"-d", "-D", "--delete"} for token in command[2:])
        if subcommand == "worktree":
            return len(command) >= 3 and command[2] == "remove"
        return False

    def _is_destructive_shell_command(self, command: str) -> bool:
        lowered = command.lower()
        return any(pattern in lowered for pattern in self._SHELL_BLOCKLIST)
