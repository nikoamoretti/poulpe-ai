from __future__ import annotations

import errno
import os
import pty
import signal
import subprocess
import sys
import termios
import threading
import time
import traceback
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from app.core.enums import TranscriptStream
from app.core.errors import ConflictError, ValidationError


@dataclass(slots=True)
class ProcessLaunchSpec:
    session_id: str
    command: list[str]
    cwd: str
    env: dict[str, str] = field(default_factory=dict)
    heartbeat_interval_seconds: float = 2.0


@dataclass(slots=True)
class ProcessRuntimeSnapshot:
    session_id: str
    pid: int | None
    command: list[str]
    cwd: str
    running: bool
    exit_code: int | None
    started_at: datetime | None
    ended_at: datetime | None
    last_heartbeat_at: datetime | None
    stop_requested: bool = False


@dataclass(slots=True)
class ProcessCallbacks:
    on_output: Callable[[str, TranscriptStream, str], None]
    on_heartbeat: Callable[[str, datetime], None]
    on_exit: Callable[[str, int], None]


@dataclass(slots=True)
class _ProcessHandle:
    spec: ProcessLaunchSpec
    process: subprocess.Popen[bytes]
    master_fd: int
    stderr_pipe: object | None
    callbacks: ProcessCallbacks
    snapshot: ProcessRuntimeSnapshot
    lock: threading.RLock = field(default_factory=threading.RLock)
    threads: list[threading.Thread] = field(default_factory=list)


class ProcessSupervisorAdapter:
    """Supervise local terminal processes for manager, worker, and reviewer sessions."""

    def __init__(self, *, stop_grace_seconds: float = 1.5, output_chunk_size: int = 4096) -> None:
        self.stop_grace_seconds = stop_grace_seconds
        self.output_chunk_size = output_chunk_size
        self._handles: dict[str, _ProcessHandle] = {}
        self._lock = threading.Lock()

    def launch(self, spec: ProcessLaunchSpec, *, callbacks: ProcessCallbacks) -> ProcessRuntimeSnapshot:
        cwd = Path(spec.cwd).expanduser().resolve()
        if not cwd.exists() or not cwd.is_dir():
            raise ValidationError(f"Session cwd does not exist: {cwd}")
        if not spec.command:
            raise ValidationError("Process launch command cannot be empty.")

        with self._lock:
            existing = self._handles.get(spec.session_id)
            if existing is not None and existing.snapshot.running:
                raise ConflictError(f"Session {spec.session_id} already has a running process.")

        master_fd, slave_fd = pty.openpty()
        self._disable_echo(slave_fd)
        process = subprocess.Popen(
            spec.command,
            cwd=str(cwd),
            env=spec.env,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        os.close(slave_fd)

        started_at = datetime.now(UTC)
        snapshot = ProcessRuntimeSnapshot(
            session_id=spec.session_id,
            pid=process.pid,
            command=list(spec.command),
            cwd=str(cwd),
            running=True,
            exit_code=None,
            started_at=started_at,
            ended_at=None,
            last_heartbeat_at=started_at,
            stop_requested=False,
        )
        handle = _ProcessHandle(
            spec=spec,
            process=process,
            master_fd=master_fd,
            stderr_pipe=process.stderr,
            callbacks=callbacks,
            snapshot=snapshot,
        )

        threads = [
            threading.Thread(target=self._read_stdout, args=(handle,), daemon=True),
            threading.Thread(target=self._read_stderr, args=(handle,), daemon=True),
            threading.Thread(target=self._heartbeat_loop, args=(handle,), daemon=True),
            threading.Thread(target=self._wait_loop, args=(handle,), daemon=True),
        ]
        handle.threads = threads
        with self._lock:
            self._handles[spec.session_id] = handle
        for thread in threads:
            thread.start()
        return self.get_status(spec.session_id) or snapshot

    def send(self, session_id: str, message: str) -> None:
        handle = self._require_handle(session_id)
        with handle.lock:
            if not handle.snapshot.running:
                raise ValidationError(f"Session {session_id} is not running.")
            payload = message if message.endswith("\n") else f"{message}\n"
            os.write(handle.master_fd, payload.encode("utf-8"))
            self._mark_heartbeat(handle)

    def interrupt(self, session_id: str) -> None:
        handle = self._require_handle(session_id)
        with handle.lock:
            if not handle.snapshot.running or handle.snapshot.pid is None:
                raise ValidationError(f"Session {session_id} is not running.")
            try:
                os.killpg(handle.snapshot.pid, signal.SIGINT)
            except ProcessLookupError:
                handle.snapshot.running = False
            self._mark_heartbeat(handle)

    def stop(self, session_id: str) -> ProcessRuntimeSnapshot | None:
        handle = self._handles.get(session_id)
        if handle is None:
            return None

        with handle.lock:
            if not handle.snapshot.running or handle.snapshot.pid is None:
                return self._copy_snapshot(handle.snapshot)
            handle.snapshot.stop_requested = True
            try:
                os.killpg(handle.snapshot.pid, signal.SIGTERM)
            except ProcessLookupError:
                handle.snapshot.running = False

        deadline = time.monotonic() + self.stop_grace_seconds
        while time.monotonic() < deadline:
            if handle.process.poll() is not None:
                break
            time.sleep(0.05)

        with handle.lock:
            if handle.process.poll() is None and handle.snapshot.pid is not None:
                try:
                    os.killpg(handle.snapshot.pid, signal.SIGKILL)
                except ProcessLookupError:
                    handle.snapshot.running = False
        return self.get_status(session_id)

    def get_status(self, session_id: str) -> ProcessRuntimeSnapshot | None:
        handle = self._handles.get(session_id)
        if handle is None:
            return None
        with handle.lock:
            return self._copy_snapshot(handle.snapshot)

    def shutdown(self) -> None:
        session_ids = list(self._handles.keys())
        for session_id in session_ids:
            self.stop(session_id)
        for handle in list(self._handles.values()):
            for thread in handle.threads:
                thread.join(timeout=0.2)

    def _require_handle(self, session_id: str) -> _ProcessHandle:
        handle = self._handles.get(session_id)
        if handle is None:
            raise ValidationError(f"Session {session_id} is not running under local supervision.")
        return handle

    def _read_stdout(self, handle: _ProcessHandle) -> None:
        while True:
            try:
                chunk = os.read(handle.master_fd, self.output_chunk_size)
            except OSError as exc:
                if exc.errno in {errno.EIO, errno.EBADF}:
                    break
                continue
            if not chunk:
                break
            self._mark_heartbeat(handle)
            self._safe_callback(
                handle.callbacks.on_output,
                handle.spec.session_id,
                TranscriptStream.STDOUT,
                chunk.decode("utf-8", errors="replace"),
            )

    def _read_stderr(self, handle: _ProcessHandle) -> None:
        if handle.stderr_pipe is None:
            return

        try:
            while True:
                chunk = handle.stderr_pipe.read(self.output_chunk_size)
                if not chunk:
                    break
                self._mark_heartbeat(handle)
                self._safe_callback(
                    handle.callbacks.on_output,
                    handle.spec.session_id,
                    TranscriptStream.STDERR,
                    chunk.decode("utf-8", errors="replace"),
                )
        finally:
            try:
                handle.stderr_pipe.close()
            except Exception:
                return

    def _heartbeat_loop(self, handle: _ProcessHandle) -> None:
        while handle.process.poll() is None:
            time.sleep(handle.spec.heartbeat_interval_seconds)
            if handle.process.poll() is not None:
                break
            now = self._mark_heartbeat(handle)
            self._safe_callback(handle.callbacks.on_heartbeat, handle.spec.session_id, now)

    def _wait_loop(self, handle: _ProcessHandle) -> None:
        exit_code = handle.process.wait()
        current_thread = threading.current_thread()
        # Give stdout/stderr reader threads a moment to flush the final chunks
        # before downstream code observes the session as completed.
        for thread in handle.threads:
            if thread is current_thread:
                continue
            thread.join(timeout=1.0)
        ended_at = datetime.now(UTC)
        with handle.lock:
            handle.snapshot.running = False
            handle.snapshot.exit_code = exit_code
            handle.snapshot.ended_at = ended_at
            handle.snapshot.last_heartbeat_at = ended_at
        self._cleanup_fds(handle)
        self._safe_callback(handle.callbacks.on_exit, handle.spec.session_id, exit_code)

    def _mark_heartbeat(self, handle: _ProcessHandle) -> datetime:
        now = datetime.now(UTC)
        with handle.lock:
            handle.snapshot.last_heartbeat_at = now
        return now

    def _cleanup_fds(self, handle: _ProcessHandle) -> None:
        try:
            os.close(handle.master_fd)
        except OSError:
            pass

    @staticmethod
    def _disable_echo(slave_fd: int) -> None:
        attrs = termios.tcgetattr(slave_fd)
        attrs[3] = attrs[3] & ~termios.ECHO
        termios.tcsetattr(slave_fd, termios.TCSANOW, attrs)

    @staticmethod
    def _copy_snapshot(snapshot: ProcessRuntimeSnapshot) -> ProcessRuntimeSnapshot:
        return ProcessRuntimeSnapshot(
            session_id=snapshot.session_id,
            pid=snapshot.pid,
            command=list(snapshot.command),
            cwd=snapshot.cwd,
            running=snapshot.running,
            exit_code=snapshot.exit_code,
            started_at=snapshot.started_at,
            ended_at=snapshot.ended_at,
            last_heartbeat_at=snapshot.last_heartbeat_at,
            stop_requested=snapshot.stop_requested,
        )

    @staticmethod
    def _safe_callback(callback: Callable[..., None], *args: object) -> None:
        try:
            callback(*args)
        except Exception:
            traceback.print_exc(file=sys.stderr)
