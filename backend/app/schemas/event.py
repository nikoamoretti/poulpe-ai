from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import Field

from app.core.enums import EventCategory, EventLevel, SessionRole
from app.schemas.common import ORMModel


class EventSourceRef(ORMModel):
    kind: str
    role: SessionRole | None = None
    id: str


class EventCreate(ORMModel):
    category: EventCategory
    event_type: str
    level: EventLevel = EventLevel.INFO
    source: EventSourceRef
    project_id: UUID | None = None
    task_id: UUID | None = None
    session_id: UUID | None = None
    correlation_id: UUID | None = None
    causation_id: UUID | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    raw_output: str | None = None
    occurred_at: datetime | None = None


class EventEnvelope(ORMModel):
    id: UUID
    version: str = "v1"
    sequence: int
    category: EventCategory
    event_type: str
    level: EventLevel
    source: EventSourceRef
    project_id: UUID | None = None
    task_id: UUID | None = None
    session_id: UUID | None = None
    correlation_id: UUID | None = None
    causation_id: UUID | None = None
    occurred_at: datetime
    payload: dict[str, Any] = Field(default_factory=dict)
    raw_output: str | None = None
