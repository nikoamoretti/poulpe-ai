from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class ApiMessage(ORMModel):
    detail: str
    generated_at: datetime | None = None
