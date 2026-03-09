from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import get_orchestrator_service, get_task_service
from app.schemas.task import (
    TaskAssignmentRead,
    TaskAssignmentRequest,
    TaskBlockedRequest,
    TaskCompletedRequest,
    TaskCreate,
    TaskRead,
)
from app.services.orchestration_service import OrchestratorService
from app.services.task_service import TaskService

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("", response_model=list[TaskRead])
def list_tasks(
    project_id: UUID | None = Query(default=None),
    service: TaskService = Depends(get_task_service),
) -> list[TaskRead]:
    return service.list_tasks(project_id=project_id)


@router.post("", response_model=TaskRead, status_code=status.HTTP_201_CREATED)
def create_task(
    payload: TaskCreate,
    service: TaskService = Depends(get_task_service),
) -> TaskRead:
    return service.create_task(payload)


@router.get("/{task_id}", response_model=TaskRead)
def get_task(
    task_id: UUID,
    service: TaskService = Depends(get_task_service),
) -> TaskRead:
    return service.get_task(task_id)


@router.post("/{task_id}/assign", response_model=TaskAssignmentRead)
def assign_task(
    task_id: UUID,
    payload: TaskAssignmentRequest,
    service: OrchestratorService = Depends(get_orchestrator_service),
) -> TaskAssignmentRead:
    return service.assign_task(task_id, payload)


@router.post("/{task_id}/block", response_model=TaskRead)
def block_task(
    task_id: UUID,
    payload: TaskBlockedRequest,
    service: OrchestratorService = Depends(get_orchestrator_service),
) -> TaskRead:
    return service.mark_task_blocked(task_id, payload)


@router.post("/{task_id}/complete", response_model=TaskRead)
def complete_task(
    task_id: UUID,
    payload: TaskCompletedRequest,
    service: OrchestratorService = Depends(get_orchestrator_service),
) -> TaskRead:
    return service.mark_task_completed(task_id, payload)
