from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import Field

from app.schemas.common import ORMModel


class OrchestratorTickRequest(ORMModel):
    project_id: UUID | None = None


class OrchestratorActionRead(ORMModel):
    kind: str
    project_id: UUID
    task_id: UUID | None = None
    session_id: UUID | None = None
    detail: str
    payload: dict[str, Any] = Field(default_factory=dict)


class OrchestratorProjectTickRead(ORMModel):
    project_id: UUID
    processed_event_count: int
    last_event_sequence: int
    actions: list[OrchestratorActionRead] = Field(default_factory=list)


class OrchestratorTickRead(ORMModel):
    started_at: datetime
    completed_at: datetime
    projects: list[OrchestratorProjectTickRead] = Field(default_factory=list)
