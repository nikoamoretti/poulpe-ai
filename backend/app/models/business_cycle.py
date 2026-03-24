from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Enum, ForeignKey, JSON, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import BusinessCycleStatus
from app.models.base import Base


class BusinessCycle(Base):
    __tablename__ = "business_cycles"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    cycle_date: Mapped[str] = mapped_column(String(10), nullable=False)  # YYYY-MM-DD
    status: Mapped[BusinessCycleStatus] = mapped_column(
        Enum(BusinessCycleStatus, name="business_cycle_status", native_enum=False),
        default=BusinessCycleStatus.PENDING,
        nullable=False,
    )
    ceo_session_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    ceo_plan: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    agent_results: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    metrics_before: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    metrics_after: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    human_feedback: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
