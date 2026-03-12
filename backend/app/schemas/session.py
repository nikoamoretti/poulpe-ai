from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import Field

from app.core.enums import SessionRole, SessionStatus, SessionTransport
from app.schemas.common import ORMModel
from app.schemas.runtime import RuntimeSelectionRead


class SessionCreate(ORMModel):
    project_id: UUID
    task_id: UUID | None = None
    supervisor_session_id: UUID | None = None
    role: SessionRole
    command_override: str | None = None
    adapter_kind: str | None = None
    runtime_preference: str | None = None
    allow_simulation_fallback: bool | None = None
    simulation_mode: bool | None = None
    model: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SessionStartRequest(ORMModel):
    initial_message: str | None = None


class SessionMessageRequest(ORMModel):
    message: str


class SessionRead(ORMModel):
    id: UUID
    portfolio_id: UUID | None = None
    project_id: UUID | None = None
    task_id: UUID | None = None
    supervisor_session_id: UUID | None = None
    role: SessionRole
    status: SessionStatus
    transport: SessionTransport
    adapter_kind: str
    branch_name: str | None = None
    workspace_path: str | None = None
    command: str | None = None
    pid: int | None = None
    exit_code: int | None = None
    blocked_reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict, validation_alias="metadata_json")
    runtime_metadata: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias="runtime_metadata_json",
    )
    started_at: datetime | None = None
    ended_at: datetime | None = None
    last_heartbeat_at: datetime | None = None
    runtime: RuntimeSelectionRead = Field(default_factory=RuntimeSelectionRead)
    created_at: datetime
    updated_at: datetime
