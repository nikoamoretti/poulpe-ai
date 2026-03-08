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
