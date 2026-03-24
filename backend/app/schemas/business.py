from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import Field

from app.core.enums import BusinessCycleStatus, BusinessStatus, BusinessType
from app.schemas.common import ORMModel


# ── Business schemas ──────────────────────────────────────────────────


class BusinessCreate(ORMModel):
    name: str
    portfolio_id: UUID
    description: str = ""
    business_type: BusinessType = BusinessType.SAAS
    domain: str | None = None
    budget_monthly_usd: Decimal = Decimal("50.00")
    daily_cycle_cron: str = "0 8 * * *"
    active_agent_types: list[str] = Field(default_factory=lambda: ["ceo", "engineer"])
    metadata: dict[str, Any] = Field(default_factory=dict)


class BusinessUpdate(ORMModel):
    name: str | None = None
    description: str | None = None
    business_type: BusinessType | None = None
    domain: str | None = None
    budget_monthly_usd: Decimal | None = None
    daily_cycle_cron: str | None = None
    active_agent_types: list[str] | None = None
    status: BusinessStatus | None = None
    infra_state: dict[str, Any] | None = None
    metrics_snapshot: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None


class BusinessRead(ORMModel):
    id: UUID
    portfolio_id: UUID
    name: str
    slug: str
    description: str
    business_type: BusinessType
    domain: str | None = None
    infra_state: dict[str, Any] = Field(default_factory=dict)
    budget_monthly_usd: Decimal
    total_revenue_usd: Decimal
    total_cost_usd: Decimal
    daily_cycle_cron: str
    active_agent_types: list[str] = Field(default_factory=list)
    status: BusinessStatus
    metrics_snapshot: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict, validation_alias="metadata_json")
    created_at: datetime
    updated_at: datetime


# ── BusinessCycle schemas ─────────────────────────────────────────────


class BusinessCycleRead(ORMModel):
    id: UUID
    business_id: UUID
    cycle_date: str
    status: BusinessCycleStatus
    ceo_session_id: UUID | None = None
    ceo_plan: dict[str, Any] = Field(default_factory=dict)
    agent_results: dict[str, Any] = Field(default_factory=dict)
    metrics_before: dict[str, Any] = Field(default_factory=dict)
    metrics_after: dict[str, Any] = Field(default_factory=dict)
    human_feedback: str | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class BusinessCycleTriggerResponse(ORMModel):
    cycle: BusinessCycleRead
    detail: str


class BusinessCycleFeedbackRequest(ORMModel):
    feedback: str
