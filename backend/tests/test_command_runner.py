from __future__ import annotations

from pathlib import Path

import pytest

from app.core.errors import ValidationError
from app.services.command_runner import CommandRunner


def test_command_runner_executes_and_captures_output(tmp_path: Path) -> None:
    runner = CommandRunner()

    result = runner.run_shell("printf 'runner-ok'", cwd=tmp_path)

    assert result.returncode == 0
    assert result.stdout == "runner-ok"
    assert result.stderr == ""
    assert result.cwd == str(tmp_path.resolve())
    assert result.duration_ms >= 0


def test_command_runner_blocks_destructive_shell_commands(tmp_path: Path) -> None:
    runner = CommandRunner()

    with pytest.raises(ValidationError, match="Blocked destructive shell command"):
        runner.run_shell("rm -rf .", cwd=tmp_path)


def test_command_runner_returns_timeout_result_when_check_is_disabled(tmp_path: Path) -> None:
    runner = CommandRunner()

    result = runner.run_shell("sleep 2", cwd=tmp_path, timeout=0.1, check=False)

    assert result.returncode == 124
    assert result.timed_out is True
    assert "timed out" in result.stderr.lower()
