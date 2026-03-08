from app.schemas.common import ORMModel


class HealthResponse(ORMModel):
    status: str
    service: str
    version: str
    checks: dict[str, str]
