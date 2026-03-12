from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import Field

from app.core.enums import ProjectStatus
from app.schemas.common import ORMModel


class ProjectCreate(ORMModel):
    portfolio_id: UUID | None = None
    name: str
    repo_path: str | None = None
    create_repo: bool = False
    default_branch: str | None = None
    objective: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProjectRead(ORMModel):
    id: UUID
    portfolio_id: UUID | None = None
    name: str
    slug: str
    repo_path: str
    default_branch: str
    objective: str
    status: ProjectStatus
    worker_session_id: UUID | None = None
    completion_summary: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict, validation_alias="metadata_json")
    created_at: datetime
    updated_at: datetime


class ProjectStartRequest(ORMModel):
    command_override: str | None = None
    runtime_preference: str | None = None
    allow_simulation_fallback: bool | None = None
    simulation_mode: bool | None = None
    model: str | None = None
    initial_message: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProjectInstructionRequest(ORMModel):
    message: str
    metadata: dict[str, Any] = Field(default_factory=dict)
