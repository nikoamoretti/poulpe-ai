from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Enum, ForeignKey, JSON, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import EventCategory, EventLevel
from app.models.base import Base


class Event(Base):
    __tablename__ = "events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="SET NULL"), index=True
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("tasks.id", ondelete="SET NULL"), index=True
    )
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("sessions.id", ondelete="SET NULL"), index=True
    )
    version: Mapped[str] = mapped_column(String(16), default="v1", nullable=False)
    sequence: Mapped[int] = mapped_column(nullable=False, index=True)
    category: Mapped[EventCategory] = mapped_column(
        Enum(EventCategory, name="event_category", native_enum=False),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    level: Mapped[EventLevel] = mapped_column(
        Enum(EventLevel, name="event_level", native_enum=False),
        nullable=False,
    )
    source: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    correlation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    causation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    raw_output: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
