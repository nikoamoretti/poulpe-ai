from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import Field

from app.core.enums import ProjectStatus
from app.schemas.common import ORMModel


class PortfolioCreate(ORMModel):
    name: str
    goal: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class PortfolioRead(ORMModel):
    id: UUID
    name: str
    slug: str
    goal: str
    status: ProjectStatus
    manager_session_id: UUID | None = None
    manager_workspace_path: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict, validation_alias="metadata_json")
    created_at: datetime
    updated_at: datetime


class PortfolioManagerStartRequest(ORMModel):
    command_override: str | None = None
    runtime_preference: str | None = None
    allow_simulation_fallback: bool | None = None
    simulation_mode: bool | None = None
    model: str | None = None
    initial_message: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class PortfolioAutomationActionRead(ORMModel):
    kind: str
    portfolio_id: UUID
    project_id: UUID | None = None
    checkpoint_id: UUID | None = None
    session_id: UUID | None = None
    detail: str
    payload: dict[str, Any] = Field(default_factory=dict)


class PortfolioAutomationTickRead(ORMModel):
    portfolio_id: UUID
    started_at: datetime
    completed_at: datetime
    actions: list[PortfolioAutomationActionRead] = Field(default_factory=list)
