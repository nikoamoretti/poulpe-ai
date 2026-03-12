from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Enum, ForeignKey, JSON, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import (
    ProjectCheckpointKind,
    ProjectCheckpointResolution,
    ProjectCheckpointStatus,
)
from app.models.base import Base


class ProjectCheckpoint(Base):
    __tablename__ = "project_checkpoints"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    portfolio_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("portfolios.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("projects.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    source_session_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("sessions.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    manager_session_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("sessions.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    source_parsed_event_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("parsed_session_events.id", ondelete="SET NULL"),
        unique=True,
        nullable=True,
    )
    kind: Mapped[ProjectCheckpointKind] = mapped_column(
        Enum(ProjectCheckpointKind, name="project_checkpoint_kind", native_enum=False),
        nullable=False,
    )
    status: Mapped[ProjectCheckpointStatus] = mapped_column(
        Enum(ProjectCheckpointStatus, name="project_checkpoint_status", native_enum=False),
        default=ProjectCheckpointStatus.OPEN,
        nullable=False,
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    details_json: Mapped[dict[str, Any]] = mapped_column("details", JSON, default=dict, nullable=False)
    resolution: Mapped[ProjectCheckpointResolution | None] = mapped_column(
        Enum(ProjectCheckpointResolution, name="project_checkpoint_resolution", native_enum=False),
        nullable=True,
    )
    response_message: Mapped[str | None] = mapped_column(Text)
    response_details_json: Mapped[dict[str, Any]] = mapped_column(
        "response_details",
        JSON,
        default=dict,
        nullable=False,
    )
    source_occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
