from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import ConfigDict, Field, TypeAdapter

from app.core.enums import (
    EventLevel,
    StructuredEventStatus,
    StructuredEventType,
    TestCommandStatus,
    TranscriptStream,
)
from app.schemas.common import ORMModel


class StructuredEventBase(ORMModel):
    model_config = ConfigDict(extra="allow", from_attributes=True, populate_by_name=True)

    type: StructuredEventType
    summary: str = Field(min_length=1, max_length=500)
    level: EventLevel = EventLevel.INFO
    timestamp: datetime | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class StartEvent(StructuredEventBase):
    type: Literal[StructuredEventType.START]
    phase: str | None = None
    next_step: str | None = None


class ProgressEvent(StructuredEventBase):
    type: Literal[StructuredEventType.PROGRESS]
    progress: int | None = Field(default=None, ge=0, le=100)
    files: list[str] = Field(default_factory=list)
    next_step: str | None = None


class QuestionEvent(StructuredEventBase):
    type: Literal[StructuredEventType.QUESTION]
    question: str = Field(min_length=1)
    choices: list[str] = Field(default_factory=list)


class BlockedEvent(StructuredEventBase):
    type: Literal[StructuredEventType.BLOCKED]
    reason: str = Field(min_length=1)
    needs: list[str] = Field(default_factory=list)


class TestsRunEvent(StructuredEventBase):
    type: Literal[StructuredEventType.TESTS_RUN]
    command: str = Field(min_length=1)
    status: TestCommandStatus
    exit_code: int
    passed: int | None = Field(default=None, ge=0)
    failed: int | None = Field(default=None, ge=0)


class CompleteEvent(StructuredEventBase):
    type: Literal[StructuredEventType.COMPLETE]
    result: str | None = None
    files: list[str] = Field(default_factory=list)


class ErrorEvent(StructuredEventBase):
    type: Literal[StructuredEventType.ERROR]
    error: str = Field(min_length=1)
    retryable: bool = False


class HeartbeatEvent(StructuredEventBase):
    type: Literal[StructuredEventType.HEARTBEAT]
    phase: str | None = None
    progress: int | None = Field(default=None, ge=0, le=100)


StructuredEventPayload = Annotated[
    StartEvent
    | ProgressEvent
    | QuestionEvent
    | BlockedEvent
    | TestsRunEvent
    | CompleteEvent
    | ErrorEvent
    | HeartbeatEvent,
    Field(discriminator="type"),
]

STRUCTURED_EVENT_ADAPTER = TypeAdapter(StructuredEventPayload)


class ParsedSessionEventRead(ORMModel):
    id: UUID
    session_id: UUID
    sequence: int
    transcript_sequence: int | None
    stream: TranscriptStream
    status: StructuredEventStatus
    event_type: StructuredEventType | None
    declared_type: str | None
    level: EventLevel | None
    summary: str | None
    payload: dict[str, Any] = Field(default_factory=dict, validation_alias="payload_json")
    raw_block: str
    validation_error: str | None
    occurred_at: datetime
    created_at: datetime
