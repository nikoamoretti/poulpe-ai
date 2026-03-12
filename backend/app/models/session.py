from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, JSON, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import SessionRole, SessionStatus, SessionTransport
from app.models.base import Base


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    portfolio_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("portfolios.id", ondelete="SET NULL"),
        index=True,
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("tasks.id", ondelete="SET NULL"))
    supervisor_session_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("sessions.id", ondelete="SET NULL")
    )
    role: Mapped[SessionRole] = mapped_column(
        Enum(SessionRole, name="session_role", native_enum=False),
        nullable=False,
    )
    status: Mapped[SessionStatus] = mapped_column(
        Enum(SessionStatus, name="session_status", native_enum=False),
        default=SessionStatus.PENDING,
        nullable=False,
    )
    transport: Mapped[SessionTransport] = mapped_column(
        Enum(SessionTransport, name="session_transport", native_enum=False),
        default=SessionTransport.LOCAL_PROCESS,
        nullable=False,
    )
    adapter_kind: Mapped[str] = mapped_column(String(120), default="codex_local", nullable=False)
    branch_name: Mapped[str | None] = mapped_column(String(255))
    workspace_path: Mapped[str | None] = mapped_column(Text)
    command: Mapped[str | None] = mapped_column(Text)
    pid: Mapped[int | None] = mapped_column(Integer)
    exit_code: Mapped[int | None] = mapped_column(Integer)
    blocked_reason: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict, nullable=False)
    runtime_metadata_json: Mapped[dict[str, Any]] = mapped_column("runtime_metadata", JSON, default=dict, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
