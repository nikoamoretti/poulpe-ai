from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.core.enums import SessionRole
from app.schemas.common import ORMModel

RuntimeProvider = Literal["auto", "codex", "claude_code", "simulated", "none"]


class RuntimeSelectionRead(ORMModel):
    requested_provider: RuntimeProvider = "auto"
    resolved_provider: RuntimeProvider = "none"
    configured: bool = False
    available: bool = False
    simulated: bool = False
    disconnected: bool = True
    can_start: bool = False
    command: str | None = None
    summary: str = "No runtime connected."


class RuntimeCapabilityRead(ORMModel):
    provider: RuntimeProvider
    label: str
    configured: bool
    available: bool
    simulated: bool
    disconnected: bool
    command: str | None = None
    summary: str


class RuntimeStatusRead(ORMModel):
    role: SessionRole
    selections: dict[str, RuntimeSelectionRead] = Field(default_factory=dict)
    providers: list[RuntimeCapabilityRead] = Field(default_factory=list)
    supported_real_providers: list[str] = Field(default_factory=list)
