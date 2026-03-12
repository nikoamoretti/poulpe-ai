from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import get_project_service
from app.schemas.project import ProjectCreate, ProjectInstructionRequest, ProjectRead, ProjectStartRequest
from app.schemas.session import SessionRead
from app.services.project_service import ProjectService

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("", response_model=list[ProjectRead])
def list_projects(
    portfolio_id: UUID | None = Query(default=None),
    service: ProjectService = Depends(get_project_service),
) -> list[ProjectRead]:
    return service.list_projects(portfolio_id=portfolio_id)


@router.post("", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
def create_project(
    payload: ProjectCreate,
    service: ProjectService = Depends(get_project_service),
) -> ProjectRead:
    return service.create_project(payload)


@router.get("/{project_id}", response_model=ProjectRead)
def get_project(
    project_id: UUID,
    service: ProjectService = Depends(get_project_service),
) -> ProjectRead:
    return service.get_project(project_id)


@router.get("/{project_id}/scope-options", response_model=list[str])
def list_scope_options(
    project_id: UUID,
    service: ProjectService = Depends(get_project_service),
) -> list[str]:
    return service.list_scope_options(project_id)


@router.post("/{project_id}/start", response_model=SessionRead)
def start_project(
    project_id: UUID,
    payload: ProjectStartRequest,
    service: ProjectService = Depends(get_project_service),
) -> SessionRead:
    return service.start_project(project_id, payload)


@router.post("/{project_id}/manager-instructions", response_model=SessionRead)
def send_project_manager_instruction(
    project_id: UUID,
    payload: ProjectInstructionRequest,
    service: ProjectService = Depends(get_project_service),
) -> SessionRead:
    return service.deliver_manager_instruction(
        project_id,
        message=payload.message,
        metadata=payload.metadata,
    )
