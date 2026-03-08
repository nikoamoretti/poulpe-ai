from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.deps import get_workspace_service
from app.schemas.workspace import (
    WorkspaceCommandRead,
    WorkspaceCommandRequest,
    WorkspaceDiffRead,
    WorkspaceStatusRead,
)
from app.services.workspace_service import WorkspaceService

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


@router.get("/{workspace_id}", response_model=WorkspaceStatusRead)
def get_workspace(
    workspace_id: UUID,
    service: WorkspaceService = Depends(get_workspace_service),
) -> WorkspaceStatusRead:
    return service.get_workspace(workspace_id)


@router.get("/{workspace_id}/diff", response_model=WorkspaceDiffRead)
def get_workspace_diff(
    workspace_id: UUID,
    service: WorkspaceService = Depends(get_workspace_service),
) -> WorkspaceDiffRead:
    return service.get_diff(workspace_id)


@router.post("/{workspace_id}/commands", response_model=WorkspaceCommandRead)
def run_workspace_command(
    workspace_id: UUID,
    payload: WorkspaceCommandRequest,
    service: WorkspaceService = Depends(get_workspace_service),
) -> WorkspaceCommandRead:
    return service.run_command(workspace_id, payload)


@router.post("/{workspace_id}/lint", response_model=WorkspaceCommandRead)
def run_workspace_lint(
    workspace_id: UUID,
    payload: WorkspaceCommandRequest,
    service: WorkspaceService = Depends(get_workspace_service),
) -> WorkspaceCommandRead:
    return service.run_lint(workspace_id, payload)


@router.post("/{workspace_id}/tests", response_model=WorkspaceCommandRead)
def run_workspace_tests(
    workspace_id: UUID,
    payload: WorkspaceCommandRequest,
    service: WorkspaceService = Depends(get_workspace_service),
) -> WorkspaceCommandRead:
    return service.run_tests(workspace_id, payload)
