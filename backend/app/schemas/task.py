from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import Field

from app.core.enums import TaskStatus
from app.schemas.common import ORMModel


class TaskCreate(ORMModel):
    project_id: UUID
    parent_task_id: UUID | None = None
    title: str
    description: str = ""
    priority: int = 3
    acceptance_criteria: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class TaskAssignmentRequest(ORMModel):
    session_id: UUID
    allowed_paths: list[str] = Field(default_factory=list)
    dependency_task_ids: list[UUID] = Field(default_factory=list)
    note: str | None = None


class TaskAssignmentRead(ORMModel):
    task: "TaskRead"
    assigned_session_id: UUID | None = None
    allowed_paths: list[str] = Field(default_factory=list)
    dependency_task_ids: list[UUID] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)


class TaskBlockedRequest(ORMModel):
    reason: str
    note: str | None = None


class TaskCompletedRequest(ORMModel):
    summary: str | None = None
    note: str | None = None


class TaskRead(ORMModel):
    id: UUID
    project_id: UUID
    parent_task_id: UUID | None = None
    title: str
    description: str
    status: TaskStatus
    priority: int
    acceptance_criteria: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict, validation_alias="metadata_json")
    created_at: datetime
    updated_at: datetime


TaskAssignmentRead.model_rebuild()
