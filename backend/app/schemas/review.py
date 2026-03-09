from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import Field

from app.core.enums import ArtifactKind, ReviewStatus
from app.schemas.common import ORMModel


class ReviewCreate(ORMModel):
    project_id: UUID
    task_id: UUID | None = None
    session_id: UUID | None = None
    requester_session_id: UUID | None = None
    reviewer_session_id: UUID | None = None
    summary: str | None = None
    lint_command: str | None = None
    test_command: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReviewApprove(ORMModel):
    note: str | None = None
    reviewer_session_id: UUID | None = None


class ReviewReject(ORMModel):
    note: str
    status: ReviewStatus = ReviewStatus.NEEDS_CHANGES
    reviewer_session_id: UUID | None = None


class ReviewMergeReady(ORMModel):
    approved_by: str
    note: str | None = None


class ReviewArtifactRead(ORMModel):
    id: UUID
    kind: ArtifactKind
    uri: str
    content_type: str
    size_bytes: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReviewDiffSummaryRead(ORMModel):
    artifact_id: UUID | None = None
    summary: str
    changed_files: list[str] = Field(default_factory=list)
    diff_preview: str = ""


class ReviewCheckRead(ORMModel):
    artifact_id: UUID | None = None
    command: str | None = None
    status: str | None = None
    returncode: int | None = None
    timed_out: bool = False
    duration_ms: int | None = None
    summary: str | None = None


class ReviewApprovalRead(ORMModel):
    reviewer_status: ReviewStatus
    human_approved: bool = False
    human_approved_by: str | None = None
    human_approved_at: datetime | None = None
    merge_ready: bool = False
    merge_ready_by: str | None = None
    merge_ready_at: datetime | None = None
    note: str | None = None


class ReviewRead(ORMModel):
    id: UUID
    project_id: UUID
    task_id: UUID
    requester_session_id: UUID | None = None
    reviewer_session_id: UUID | None = None
    status: ReviewStatus
    summary: str | None = None
    reviewer_notes: str | None = None
    prompt_template_path: str | None = None
    review_packet: dict[str, Any] = Field(default_factory=dict)
    diff: ReviewDiffSummaryRead
    lint: ReviewCheckRead | None = None
    tests: ReviewCheckRead | None = None
    approval: ReviewApprovalRead
    artifacts: list[ReviewArtifactRead] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
