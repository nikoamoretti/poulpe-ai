from fastapi import APIRouter, Depends, Query

from app.api.deps import get_runtime_service
from app.core.enums import SessionRole
from app.schemas.runtime import RuntimeStatusRead
from app.services.runtime_service import RuntimeService

router = APIRouter(prefix="/runtime", tags=["runtime"])


@router.get("", response_model=RuntimeStatusRead)
def get_runtime_status(
    role: SessionRole = Query(default=SessionRole.WORKER),
    service: RuntimeService = Depends(get_runtime_service),
) -> RuntimeStatusRead:
    return service.get_runtime_status(role=role)
