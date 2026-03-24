from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import Field

from app.core.enums import (
    ArtifactKind,
    ProjectCheckpointAction,
    ProjectCheckpointKind,
    ProjectCheckpointResolution,
    ProjectCheckpointStatus,
)
from app.schemas.common import ORMModel


class ProjectCheckpointArtifactRead(ORMModel):
    id: UUID
    kind: ArtifactKind
    uri: str
    content_type: str
    size_bytes: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProjectCheckpointRead(ORMModel):
    id: UUID
    portfolio_id: UUID
    project_id: UUID
    project_name: str
    project_slug: str
    source_session_id: UUID | None = None
    manager_session_id: UUID | None = None
    source_parsed_event_id: UUID | None = None
    kind: ProjectCheckpointKind
    status: ProjectCheckpointStatus
    summary: str
    details: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[ProjectCheckpointArtifactRead] = Field(default_factory=list)
    resolution: ProjectCheckpointResolution | None = None
    response_message: str | None = None
    response_details: dict[str, Any] = Field(default_factory=dict)
    source_occurred_at: datetime
    resolved_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ProjectCheckpointRespondRequest(ORMModel):
    action: ProjectCheckpointAction
    message: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class ProjectCheckpointCreateRequest(ORMModel):
    project_id: UUID
    session_id: UUID | None = None
    kind: ProjectCheckpointKind
    summary: str
    details: dict[str, Any] = Field(default_factory=dict)
