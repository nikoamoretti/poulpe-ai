from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import Field

from app.core.enums import ReviewStatus
from app.schemas.common import ORMModel


class ReviewCreate(ORMModel):
    project_id: UUID
    task_id: UUID
    requester_session_id: UUID | None = None
    reviewer_session_id: UUID | None = None
    diff_artifact_id: UUID | None = None
    summary: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReviewDecision(ORMModel):
    decision: ReviewStatus
    note: str | None = None
    approved_by: str | None = None


class ReviewRead(ORMModel):
    id: UUID
    project_id: UUID
    task_id: UUID
    requester_session_id: UUID | None = None
    reviewer_session_id: UUID | None = None
    diff_artifact_id: UUID | None = None
    status: ReviewStatus
    summary: str | None = None
    decision_note: str | None = None
    lint_status: str | None = None
    test_status: str | None = None
    human_approved_by: str | None = None
    human_approved_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict, validation_alias="metadata_json")
    created_at: datetime
    updated_at: datetime
