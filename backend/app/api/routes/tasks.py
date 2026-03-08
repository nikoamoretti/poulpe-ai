from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import get_task_service
from app.schemas.task import TaskCreate, TaskRead
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
