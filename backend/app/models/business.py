from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, Enum, ForeignKey, JSON, Numeric, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import BusinessStatus, BusinessType
from app.models.base import Base


class Business(Base):
    __tablename__ = "businesses"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    portfolio_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(200), unique=True, index=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    business_type: Mapped[BusinessType] = mapped_column(
        Enum(BusinessType, name="business_type", native_enum=False),
        default=BusinessType.SAAS,
        nullable=False,
    )
    domain: Mapped[str | None] = mapped_column(String(253))
    infra_state: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    budget_monthly_usd: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), default=Decimal("0.00"), nullable=False
    )
    total_revenue_usd: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=Decimal("0.00"), nullable=False
    )
    total_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=Decimal("0.00"), nullable=False
    )
    daily_cycle_cron: Mapped[str] = mapped_column(String(50), default="0 8 * * *", nullable=False)
    active_agent_types: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[BusinessStatus] = mapped_column(
        Enum(BusinessStatus, name="business_status", native_enum=False),
        default=BusinessStatus.SETUP,
        nullable=False,
    )
    metrics_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
