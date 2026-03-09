from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select

from app.adapters.agent_adapter import AgentAdapter, AgentSessionConfig
from app.adapters.event_parser import EventParserAdapter, ParsedEventBlock
from app.adapters.process_supervisor import ProcessCallbacks, ProcessRuntimeSnapshot
from app.adapters.redis_bus import RedisBusAdapter
from app.core.config import Settings
from app.core.database import DatabaseManager
from app.core.enums import (
    EventCategory,
    EventLevel,
    SessionRole,
    SessionStatus,
    SessionTransport,
    StructuredEventStatus,
    StructuredEventType,
    TranscriptStream,
)
from app.core.errors import InfrastructureError, NotFoundError, ValidationError
from app.core.event_stream import EventStreamBroker
from app.models.parsed_session_event import ParsedSessionEvent
from app.models.project import Project
from app.models.session import Session as SessionModel
from app.models.transcript_chunk import TranscriptChunk
from app.schemas.event import EventCreate, EventSourceRef
from app.schemas.structured_event import ParsedSessionEventRead
from app.schemas.transcript import TranscriptChunkRead
from app.services.event_service import EventService


@dataclass(slots=True)
class SessionLaunchPlan:
    transport: SessionTransport
    command: str
    initial_status: SessionStatus
    notes: str
    adapter_kind: str
    simulation_mode: bool


class SessionSupervisor:
    def __init__(
        self,
        *,
        settings: Settings,
        database: DatabaseManager,
        redis_bus: RedisBusAdapter,
        event_broker: EventStreamBroker,
        event_parser: EventParserAdapter,
        adapters: dict[str, AgentAdapter],
        default_adapter_kind: str = "codex_local",
    ) -> None:
        self.settings = settings
        self.database = database
        self.redis_bus = redis_bus
        self.event_broker = event_broker
        self.event_parser = event_parser
        self.adapters = adapters
        self.default_adapter_kind = default_adapter_kind
        self._buffers: dict[tuple[str, str], str] = {}

    def plan_session(
        self,
        *,
        role: SessionRole,
        command_override: str | None = None,
        adapter_kind: str | None = None,
        simulation_mode: bool | None = None,
    ) -> SessionLaunchPlan:
        default_commands = {
            SessionRole.MANAGER: "codex manager",
            SessionRole.WORKER: "codex worker",
            SessionRole.REVIEWER: "codex reviewer",
        }
        resolved_adapter = adapter_kind or self.default_adapter_kind
        resolved_simulation = (
            self.settings.codex_simulation_mode_default
            if simulation_mode is None
            else simulation_mode
        )
        return SessionLaunchPlan(
            transport=SessionTransport.LOCAL_PROCESS,
            command=command_override or default_commands[role],
            initial_status=SessionStatus.PENDING,
            notes=(
                "Local PTY supervision is enabled. "
                "Codex sessions default to the dev simulator until simulation_mode is disabled."
            ),
            adapter_kind=resolved_adapter,
            simulation_mode=resolved_simulation,
        )

    def start_session(self, session_id: UUID, *, initial_message: str | None = None) -> None:
        session_id_str = str(session_id)
        with self.database.session() as db:
            session = db.get(SessionModel, session_id)
            if session is None:
                raise NotFoundError(f"Session not found: {session_id}")
            if session.status not in {SessionStatus.PENDING}:
                raise ValidationError(
                    f"Only pending sessions can be started. Current status is {session.status.value}."
                )
            project = db.get(Project, session.project_id)
            if project is None:
                raise NotFoundError(f"Project not found: {session.project_id}")

            cwd = session.workspace_path or project.repo_path
            if not cwd:
                raise ValidationError(f"Session {session_id} has no cwd for runtime launch.")

            simulation_mode = bool(
                session.metadata_json.get("simulation_mode", self.settings.codex_simulation_mode_default)
            )
            session.status = SessionStatus.STARTING
            session.started_at = datetime.now(UTC)
            session.ended_at = None
            session.last_heartbeat_at = session.started_at
            session.exit_code = None
            session.blocked_reason = None
            session.runtime_metadata_json = {
                **session.runtime_metadata_json,
                "cwd": cwd,
                "adapter_kind": session.adapter_kind,
                "simulation_mode": simulation_mode,
            }
            db.commit()

            session_role = session.role
            session_command = session.command or ""
            session_model = session.metadata_json.get("model")
            session_metadata = dict(session.metadata_json)
            workspace_path = session.workspace_path
            task_id = session.task_id
            project_id = session.project_id

        adapter = self._adapter_for_session(session_id)

        self._record_event(
            EventCreate(
                category=EventCategory.SESSION,
                event_type="session.starting",
                level=EventLevel.INFO,
                source=EventSourceRef(kind="service", role=session_role, id="session-supervisor"),
                project_id=project_id,
                task_id=task_id,
                session_id=session_id,
                payload={"adapter_kind": adapter.kind},
            )
        )
        self._record_transcript(
            session_id=session_id,
            stream=TranscriptStream.SYSTEM,
            content=f"Starting session with adapter {adapter.kind}",
            metadata={"phase": "start"},
        )

        config = AgentSessionConfig(
            session_id=session_id_str,
            role=session_role,
            command=session_command,
            cwd=cwd,
            workspace_path=workspace_path,
            model=session_model,
            simulation_mode=simulation_mode,
            metadata=session_metadata,
        )

        try:
            snapshot = adapter.start(
                config,
                callbacks=self._build_callbacks(),
            )
        except Exception as exc:
            self._mark_start_failed(session_id, exc)
            raise

        with self.database.session() as db:
            session = db.get(SessionModel, session_id)
            if session is None:
                raise NotFoundError(f"Session not found: {session_id}")
            session.pid = snapshot.pid
            session.last_heartbeat_at = snapshot.last_heartbeat_at
            session.runtime_metadata_json = {
                **session.runtime_metadata_json,
                "pid": snapshot.pid,
                "command": snapshot.command,
                "cwd": snapshot.cwd,
                "started_at": snapshot.started_at.isoformat() if snapshot.started_at else None,
            }
            db.commit()

        self._record_event(
            EventCreate(
                category=EventCategory.SESSION,
                event_type="session.started",
                level=EventLevel.INFO,
                source=EventSourceRef(kind="agent", role=session_role, id=adapter.kind),
                project_id=project_id,
                task_id=task_id,
                session_id=session_id,
                payload={"pid": snapshot.pid, "cwd": snapshot.cwd},
            )
        )
        if initial_message:
            self.send(session_id, initial_message)

    def send(self, session_id: UUID, message: str) -> None:
        if not message.strip():
            raise ValidationError("Message cannot be empty.")

        session = self._load_session(session_id)
        if session.status not in {SessionStatus.STARTING, SessionStatus.RUNNING, SessionStatus.BLOCKED}:
            raise ValidationError(
                f"Cannot send input to session {session_id} while it is {session.status.value}."
            )

        self._adapter_for_session(session_id).send(str(session_id), message)
        chunk = self._record_transcript(
            session_id=session_id,
            stream=TranscriptStream.STDIN,
            content=message,
            metadata={"direction": "operator_to_agent"},
        )
        self._touch_session(
            session_id,
            heartbeat_at=datetime.now(UTC),
            status_override=SessionStatus.RUNNING if session.status == SessionStatus.STARTING else None,
        )
        self._record_event(
            EventCreate(
                category=EventCategory.SESSION,
                event_type="session.instruction_sent",
                level=EventLevel.INFO,
                source=EventSourceRef(kind="api", id="sessions.messages"),
                project_id=session.project_id,
                task_id=session.task_id,
                session_id=session.id,
                payload={
                    "transcript_sequence": chunk.sequence,
                    "length": len(message),
                },
            )
        )

    def interrupt(self, session_id: UUID) -> None:
        session = self._load_session(session_id)
        if session.status not in {SessionStatus.STARTING, SessionStatus.RUNNING, SessionStatus.BLOCKED}:
            raise ValidationError(
                f"Cannot interrupt session {session_id} while it is {session.status.value}."
            )

        self._adapter_for_session(session_id).interrupt(str(session_id))
        self._record_transcript(
            session_id=session_id,
            stream=TranscriptStream.SYSTEM,
            content="Operator interrupt requested",
            metadata={"action": "interrupt"},
        )
        self._record_event(
            EventCreate(
                category=EventCategory.SESSION,
                event_type="session.interrupt_requested",
                level=EventLevel.WARN,
                source=EventSourceRef(kind="api", id="sessions.interrupt"),
                project_id=session.project_id,
                task_id=session.task_id,
                session_id=session.id,
            )
        )

    def stop(self, session_id: UUID) -> None:
        session = self._load_session(session_id)
        if session.status in {SessionStatus.COMPLETED, SessionStatus.FAILED, SessionStatus.STOPPED}:
            return

        now = datetime.now(UTC)
        with self.database.session() as db:
            session_record = db.get(SessionModel, session_id)
            if session_record is None:
                raise NotFoundError(f"Session not found: {session_id}")
            session_record.status = SessionStatus.STOPPED
            session_record.last_heartbeat_at = now
            session_record.runtime_metadata_json = {
                **session_record.runtime_metadata_json,
                "stop_requested_at": now.isoformat(),
            }
            db.commit()

        snapshot = self._adapter_for_session(session_id).stop(str(session_id))
        if snapshot is None or not snapshot.running:
            with self.database.session() as db:
                session_record = db.get(SessionModel, session_id)
                if session_record is not None and session_record.ended_at is None:
                    session_record.ended_at = now
                    db.commit()

        self._record_transcript(
            session_id=session_id,
            stream=TranscriptStream.SYSTEM,
            content="Operator stop requested",
            metadata={"action": "stop"},
        )
        self._record_event(
            EventCreate(
                category=EventCategory.SESSION,
                event_type="session.stop_requested",
                level=EventLevel.WARN,
                source=EventSourceRef(kind="api", id="sessions.stop"),
                project_id=session.project_id,
                task_id=session.task_id,
                session_id=session.id,
            )
        )

    def refresh_session_runtime(self, session_id: UUID) -> None:
        session = self._load_session(session_id)
        snapshot = self._adapter_for_session(session_id).get_status(str(session_id))
        if snapshot is None:
            return

        with self.database.session() as db:
            session_record = db.get(SessionModel, session_id)
            if session_record is None:
                raise NotFoundError(f"Session not found: {session_id}")
            session_record.pid = snapshot.pid
            session_record.exit_code = snapshot.exit_code
            session_record.last_heartbeat_at = snapshot.last_heartbeat_at
            if session_record.status == SessionStatus.STARTING and snapshot.running:
                session_record.status = SessionStatus.RUNNING
            if not snapshot.running and snapshot.exit_code is not None:
                session_record.ended_at = session_record.ended_at or snapshot.ended_at or datetime.now(UTC)
                if session_record.status != SessionStatus.STOPPED:
                    session_record.status = (
                        SessionStatus.COMPLETED if snapshot.exit_code == 0 else SessionStatus.FAILED
                    )
                    if session_record.status in {
                        SessionStatus.COMPLETED,
                        SessionStatus.FAILED,
                    }:
                        session_record.blocked_reason = None
            db.commit()

    def get_status(self, session_id: UUID) -> ProcessRuntimeSnapshot | None:
        return self._adapter_for_session(session_id).get_status(str(session_id))

    def list_transcript(self, session_id: UUID, limit: int = 200) -> list[TranscriptChunkRead]:
        self._load_session(session_id)
        with self.database.session() as db:
            stmt = (
                select(TranscriptChunk)
                .where(TranscriptChunk.session_id == session_id)
                .order_by(TranscriptChunk.sequence.desc())
                .limit(limit)
            )
            rows = list(reversed(db.scalars(stmt).all()))
        return [TranscriptChunkRead.model_validate(row) for row in rows]

    def list_structured_events(self, session_id: UUID, limit: int = 200) -> list[ParsedSessionEventRead]:
        self._load_session(session_id)
        with self.database.session() as db:
            stmt = (
                select(ParsedSessionEvent)
                .where(ParsedSessionEvent.session_id == session_id)
                .order_by(ParsedSessionEvent.sequence.desc())
                .limit(limit)
            )
            rows = list(reversed(db.scalars(stmt).all()))
        return [ParsedSessionEventRead.model_validate(row) for row in rows]

    def shutdown(self) -> None:
        for adapter in self.adapters.values():
            adapter.shutdown()

    def _build_callbacks(self) -> ProcessCallbacks:
        return ProcessCallbacks(
            on_output=self._handle_output,
            on_heartbeat=self._handle_heartbeat,
            on_exit=self._handle_exit,
        )

    def _handle_output(self, session_id: str, stream: TranscriptStream, text: str) -> None:
        session_uuid = UUID(session_id)
        chunk = self._record_transcript(
            session_id=session_uuid,
            stream=stream,
            content=text,
            metadata={"bytes": len(text.encode("utf-8", errors="replace"))},
        )
        self._touch_session(
            session_uuid,
            heartbeat_at=chunk.occurred_at,
            status_override=SessionStatus.RUNNING,
            promote_only=True,
        )
        session = self._load_session(session_uuid)
        self._record_event(
            EventCreate(
                category=EventCategory.SESSION,
                event_type="session.output",
                level=EventLevel.ERROR if stream == TranscriptStream.STDERR else EventLevel.INFO,
                source=EventSourceRef(kind="agent", role=session.role, id=session.adapter_kind),
                project_id=session.project_id,
                task_id=session.task_id,
                session_id=session.id,
                payload={
                    "stream": stream.value,
                    "transcript_sequence": chunk.sequence,
                    "preview": text[-1000:],
                },
                raw_output=text,
            )
        )
        self._process_structured_blocks(session, chunk)

    def _handle_heartbeat(self, session_id: str, occurred_at: datetime) -> None:
        session_uuid = UUID(session_id)
        session = self._touch_session(
            session_uuid,
            heartbeat_at=occurred_at,
            status_override=SessionStatus.RUNNING,
            promote_only=True,
        )
        self._record_event(
            EventCreate(
                category=EventCategory.SESSION,
                event_type="session.heartbeat",
                level=EventLevel.DEBUG,
                source=EventSourceRef(kind="agent", role=session.role, id=session.adapter_kind),
                project_id=session.project_id,
                task_id=session.task_id,
                session_id=session.id,
                payload={"heartbeat_at": occurred_at.isoformat()},
            )
        )

    def _handle_exit(self, session_id: str, exit_code: int) -> None:
        session_uuid = UUID(session_id)
        now = datetime.now(UTC)
        with self.database.session() as db:
            session = db.get(SessionModel, session_uuid)
            if session is None:
                return

            final_status = session.status
            if final_status != SessionStatus.STOPPED:
                final_status = SessionStatus.COMPLETED if exit_code == 0 else SessionStatus.FAILED
            session.status = final_status
            session.exit_code = exit_code
            session.ended_at = now
            session.last_heartbeat_at = now
            if final_status in {
                SessionStatus.COMPLETED,
                SessionStatus.FAILED,
                SessionStatus.STOPPED,
            }:
                session.blocked_reason = None
            session.runtime_metadata_json = {
                **session.runtime_metadata_json,
                "exit_code": exit_code,
                "ended_at": now.isoformat(),
            }
            project_id = session.project_id
            task_id = session.task_id
            role = session.role
            adapter_kind = session.adapter_kind
            db.commit()

        self._record_transcript(
            session_id=session_uuid,
            stream=TranscriptStream.SYSTEM,
            content=f"Process exited with code {exit_code}",
            metadata={"exit_code": exit_code},
            occurred_at=now,
        )
        self._record_event(
            EventCreate(
                category=EventCategory.SESSION,
                event_type={
                    SessionStatus.STOPPED: "session.stopped",
                    SessionStatus.COMPLETED: "session.completed",
                    SessionStatus.FAILED: "session.failed",
                }[final_status],
                level=EventLevel.INFO if final_status != SessionStatus.FAILED else EventLevel.ERROR,
                source=EventSourceRef(kind="agent", role=role, id=adapter_kind),
                project_id=project_id,
                task_id=task_id,
                session_id=session_uuid,
                payload={"exit_code": exit_code, "status": final_status.value},
            )
        )

    def _process_structured_blocks(self, session: SessionModel, chunk: TranscriptChunkRead) -> None:
        buffer_key = (str(session.id), chunk.stream.value)
        buffer = self._buffers.get(buffer_key, "") + chunk.content
        parse_result = self.event_parser.extract_blocks(buffer)
        remainder = parse_result.remainder
        if len(remainder) > 64000:
            remainder = remainder[-64000:]
        self._buffers[buffer_key] = remainder

        for block in parse_result.blocks:
            self._record_parsed_event(session, chunk, block)

    def _record_parsed_event(
        self,
        session: SessionModel,
        chunk: TranscriptChunkRead,
        block: ParsedEventBlock,
    ) -> None:
        occurred_at = (
            block.event.timestamp
            if block.is_valid and block.event is not None and block.event.timestamp is not None
            else datetime.now(UTC)
        )

        with self.database.session() as db:
            session_record = db.get(SessionModel, session.id)
            if session_record is None:
                return

            sequence = int(
                db.scalar(
                    select(func.max(ParsedSessionEvent.sequence)).where(
                        ParsedSessionEvent.session_id == session.id
                    )
                )
                or 0
            ) + 1
            session_record.last_heartbeat_at = occurred_at
            parsed_event = ParsedSessionEvent(
                session_id=session.id,
                sequence=sequence,
                transcript_sequence=chunk.sequence,
                stream=chunk.stream,
                status=StructuredEventStatus.VALID if block.is_valid else StructuredEventStatus.MALFORMED,
                event_type=block.event.type if block.is_valid and block.event is not None else None,
                declared_type=block.declared_type,
                level=block.event.level if block.is_valid and block.event is not None else None,
                summary=block.event.summary if block.is_valid and block.event is not None else None,
                payload_json=(
                    block.event.model_dump(mode="json")
                    if block.is_valid and block.event is not None
                    else block.normalized_payload
                ),
                raw_block=block.raw_block,
                validation_error=block.validation_error,
                occurred_at=occurred_at,
            )
            db.add(parsed_event)

            if block.is_valid and block.event is not None:
                self._apply_structured_event_state(session_record, block)

            project_id = session_record.project_id
            task_id = session_record.task_id
            role = session_record.role
            adapter_kind = session_record.adapter_kind
            db.commit()
            db.refresh(parsed_event)

            event_service = EventService(
                db=db,
                redis_bus=self.redis_bus,
                event_broker=self.event_broker,
            )
            if block.is_valid and block.event is not None:
                payload = {
                    **block.event.model_dump(mode="json"),
                    "parsed_event_id": str(parsed_event.id),
                    "transcript_sequence": chunk.sequence,
                    "stream": chunk.stream.value,
                }
                event_service.record_event(
                    EventCreate(
                        category=EventCategory.SESSION,
                        event_type=f"session.{block.event.type.value}",
                        level=block.event.level,
                        source=EventSourceRef(kind="agent", role=role, id=adapter_kind),
                        project_id=project_id,
                        task_id=task_id,
                        session_id=session.id,
                        payload=payload,
                        raw_output=block.raw_block,
                        occurred_at=occurred_at,
                    )
                )
            else:
                event_service.record_event(
                    EventCreate(
                        category=EventCategory.SESSION,
                        event_type="session.event_malformed",
                        level=EventLevel.WARN,
                        source=EventSourceRef(kind="agent", role=role, id=adapter_kind),
                        project_id=project_id,
                        task_id=task_id,
                        session_id=session.id,
                        payload={
                            "parsed_event_id": str(parsed_event.id),
                            "declared_type": block.declared_type,
                            "validation_error": block.validation_error,
                            "transcript_sequence": chunk.sequence,
                            "stream": chunk.stream.value,
                        },
                        raw_output=block.raw_block,
                        occurred_at=occurred_at,
                    )
                )

    @staticmethod
    def _apply_structured_event_state(session: SessionModel, block: ParsedEventBlock) -> None:
        if block.event is None or session.status in {
            SessionStatus.COMPLETED,
            SessionStatus.FAILED,
            SessionStatus.STOPPED,
        }:
            return

        event = block.event
        if event.type in {
            StructuredEventType.START,
            StructuredEventType.PROGRESS,
            StructuredEventType.TESTS_RUN,
        }:
            session.status = SessionStatus.RUNNING
            session.blocked_reason = None
            return

        if event.type == StructuredEventType.HEARTBEAT:
            if session.status == SessionStatus.STARTING:
                session.status = SessionStatus.RUNNING
            return

        if event.type == StructuredEventType.QUESTION:
            session.status = SessionStatus.BLOCKED
            session.blocked_reason = event.question
            return

        if event.type == StructuredEventType.BLOCKED:
            session.status = SessionStatus.BLOCKED
            session.blocked_reason = event.reason
            return

        if event.type == StructuredEventType.ERROR:
            session.status = SessionStatus.BLOCKED
            session.blocked_reason = event.error

    def _touch_session(
        self,
        session_id: UUID,
        *,
        heartbeat_at: datetime,
        status_override: SessionStatus | None = None,
        promote_only: bool = False,
    ) -> SessionModel:
        with self.database.session() as db:
            session = db.get(SessionModel, session_id)
            if session is None:
                raise NotFoundError(f"Session not found: {session_id}")
            session.last_heartbeat_at = heartbeat_at
            if status_override is not None:
                if session.status in {
                    SessionStatus.COMPLETED,
                    SessionStatus.FAILED,
                    SessionStatus.STOPPED,
                }:
                    pass
                elif promote_only:
                    if session.status == SessionStatus.STARTING:
                        session.status = status_override
                else:
                    session.status = status_override
            db.commit()
            db.refresh(session)
            return session

    def _record_transcript(
        self,
        *,
        session_id: UUID,
        stream: TranscriptStream,
        content: str,
        metadata: dict[str, Any] | None = None,
        occurred_at: datetime | None = None,
    ) -> TranscriptChunkRead:
        with self.database.session() as db:
            sequence = int(
                db.scalar(select(func.max(TranscriptChunk.sequence)).where(TranscriptChunk.session_id == session_id))
                or 0
            ) + 1
            chunk = TranscriptChunk(
                session_id=session_id,
                sequence=sequence,
                stream=stream,
                content=content,
                metadata_json=metadata or {},
                occurred_at=occurred_at or datetime.now(UTC),
            )
            db.add(chunk)
            db.commit()
            db.refresh(chunk)
            return TranscriptChunkRead.model_validate(chunk)

    def _record_event(self, payload: EventCreate) -> None:
        with self.database.session() as db:
            event_service = EventService(
                db=db,
                redis_bus=self.redis_bus,
                event_broker=self.event_broker,
            )
            event_service.record_event(payload)

    def _mark_start_failed(self, session_id: UUID, exc: Exception) -> None:
        detail = str(exc)
        with self.database.session() as db:
            session = db.get(SessionModel, session_id)
            if session is None:
                raise NotFoundError(f"Session not found: {session_id}")
            session.status = SessionStatus.FAILED
            session.ended_at = datetime.now(UTC)
            session.runtime_metadata_json = {
                **session.runtime_metadata_json,
                "start_error": detail,
            }
            project_id = session.project_id
            task_id = session.task_id
            role = session.role
            adapter_kind = session.adapter_kind
            db.commit()

        self._record_transcript(
            session_id=session_id,
            stream=TranscriptStream.SYSTEM,
            content=f"Session failed to start: {detail}",
            metadata={"phase": "start_error"},
        )
        self._record_event(
            EventCreate(
                category=EventCategory.SESSION,
                event_type="session.failed",
                level=EventLevel.ERROR,
                source=EventSourceRef(kind="agent", role=role, id=adapter_kind),
                project_id=project_id,
                task_id=task_id,
                session_id=session_id,
                payload={"reason": detail},
            )
        )

        raise InfrastructureError(f"Failed to start session {session_id}: {detail}")

    def _load_session(self, session_id: UUID) -> SessionModel:
        with self.database.session() as db:
            session = db.get(SessionModel, session_id)
            if session is None:
                raise NotFoundError(f"Session not found: {session_id}")
            db.expunge(session)
            return session

    def _adapter_for_session(self, session_id: UUID) -> AgentAdapter:
        session = self._load_session(session_id)
        adapter = self.adapters.get(session.adapter_kind)
        if adapter is None:
            raise ValidationError(f"Unknown session adapter: {session.adapter_kind}")
        return adapter

    @staticmethod
    def _category_for_event_type(event_type: str) -> EventCategory:
        if event_type.startswith("session."):
            return EventCategory.SESSION
        if event_type.startswith("workspace."):
            return EventCategory.WORKSPACE
        if event_type.startswith("review."):
            return EventCategory.REVIEW
        if event_type.startswith("project."):
            return EventCategory.PROJECT
        if event_type.startswith("task."):
            return EventCategory.TASK
        return EventCategory.EVENT
