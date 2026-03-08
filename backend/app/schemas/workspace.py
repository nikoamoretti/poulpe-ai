from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import Field

from app.core.enums import WorkspaceStatus
from app.schemas.common import ORMModel


class WorkspaceRead(ORMModel):
    id: UUID
    project_id: UUID
    session_id: UUID | None = None
    branch_name: str
    base_branch: str
    base_commit: str
    head_commit: str | None = None
    workspace_path: str
    status: WorkspaceStatus
    metadata: dict[str, Any] = Field(default_factory=dict, validation_alias="metadata_json")
    created_at: datetime
    updated_at: datetime


class WorkspaceStatusRead(WorkspaceRead):
    changed_files: list[str] = Field(default_factory=list)


class WorkspaceDiffRead(ORMModel):
    workspace_id: UUID
    session_id: UUID | None = None
    branch_name: str
    base_branch: str
    base_commit: str
    head_commit: str | None = None
    workspace_path: str
    status: WorkspaceStatus
    changed_files: list[str] = Field(default_factory=list)
    diff: str


class WorkspaceCommandRequest(ORMModel):
    command: str
    timeout_seconds: int = 300
    env: dict[str, str] = Field(default_factory=dict)


class WorkspaceCommandRead(ORMModel):
    workspace_id: UUID
    session_id: UUID | None = None
    kind: str
    command: str
    cwd: str
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False
    duration_ms: int
    status: WorkspaceStatus
    changed_files: list[str] = Field(default_factory=list)
    executed_at: datetime
