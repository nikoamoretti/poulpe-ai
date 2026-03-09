from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Enum, ForeignKey, JSON, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import EventLevel, StructuredEventStatus, StructuredEventType, TranscriptStream
from app.models.base import Base


class ParsedSessionEvent(Base):
    __tablename__ = "parsed_session_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("sessions.id", ondelete="CASCADE"), index=True, nullable=False
    )
    sequence: Mapped[int] = mapped_column(nullable=False)
    transcript_sequence: Mapped[int | None] = mapped_column(nullable=True)
    stream: Mapped[TranscriptStream] = mapped_column(
        Enum(TranscriptStream, name="transcript_stream", native_enum=False),
        nullable=False,
    )
    status: Mapped[StructuredEventStatus] = mapped_column(
        Enum(StructuredEventStatus, name="structured_event_status", native_enum=False),
        nullable=False,
    )
    event_type: Mapped[StructuredEventType | None] = mapped_column(
        Enum(StructuredEventType, name="structured_event_type", native_enum=False),
        nullable=True,
    )
    declared_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    level: Mapped[EventLevel | None] = mapped_column(
        Enum(EventLevel, name="event_level", native_enum=False),
        nullable=True,
    )
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column("payload", JSON, default=dict, nullable=False)
    raw_block: Mapped[str] = mapped_column(Text, nullable=False)
    validation_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
