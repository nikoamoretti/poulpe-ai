from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import Field

from app.core.enums import ProjectStatus
from app.schemas.common import ORMModel


class ProjectCreate(ORMModel):
    name: str
    repo_path: str
    default_branch: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProjectRead(ORMModel):
    id: UUID
    name: str
    slug: str
    repo_path: str
    default_branch: str
    status: ProjectStatus
    metadata: dict[str, Any] = Field(default_factory=dict, validation_alias="metadata_json")
    created_at: datetime
    updated_at: datetime
