from fastapi import APIRouter, Depends

from app.api.deps import get_orchestrator_service
from app.schemas.orchestrator import OrchestratorTickRead, OrchestratorTickRequest
from app.services.orchestration_service import OrchestratorService

router = APIRouter(prefix="/orchestrator", tags=["orchestrator"])


@router.post("/tick", response_model=OrchestratorTickRead)
def trigger_orchestrator_tick(
    payload: OrchestratorTickRequest,
    service: OrchestratorService = Depends(get_orchestrator_service),
) -> OrchestratorTickRead:
    return service.tick(project_id=payload.project_id)
