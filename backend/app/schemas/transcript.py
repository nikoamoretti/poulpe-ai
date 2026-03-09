from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import Field

from app.core.enums import TranscriptStream
from app.schemas.common import ORMModel


class TranscriptChunkRead(ORMModel):
    id: UUID
    session_id: UUID
    sequence: int
    stream: TranscriptStream
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict, validation_alias="metadata_json")
    occurred_at: datetime
    created_at: datetime
