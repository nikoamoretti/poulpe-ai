from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.enums import EventCategory, EventLevel, TaskStatus
from app.core.errors import NotFoundError, ValidationError
from app.models.project import Project
from app.models.task import Task
from app.schemas.event import EventCreate, EventSourceRef
from app.schemas.task import TaskCreate, TaskRead
from app.services.event_service import EventService


class TaskService:
    def __init__(self, db: Session, event_service: EventService) -> None:
        self.db = db
        self.event_service = event_service

    def list_tasks(self, project_id: UUID | None = None) -> list[TaskRead]:
        stmt = select(Task).order_by(Task.created_at.desc())
        if project_id is not None:
            stmt = stmt.where(Task.project_id == project_id)
        records = self.db.scalars(stmt).all()
        return [TaskRead.model_validate(record) for record in records]

    def get_task(self, task_id: UUID) -> TaskRead:
        task = self.db.get(Task, task_id)
        if task is None:
            raise NotFoundError(f"Task not found: {task_id}")
        return TaskRead.model_validate(task)

    def create_task(self, payload: TaskCreate) -> TaskRead:
        project = self.db.get(Project, payload.project_id)
        if project is None:
            raise NotFoundError(f"Project not found: {payload.project_id}")

        if payload.parent_task_id is not None:
            parent_task = self.db.get(Task, payload.parent_task_id)
            if parent_task is None:
                raise NotFoundError(f"Parent task not found: {payload.parent_task_id}")
            if parent_task.project_id != payload.project_id:
                raise ValidationError("Parent task must belong to the same project.")

        task = Task(
            project_id=payload.project_id,
            parent_task_id=payload.parent_task_id,
            title=payload.title,
            description=payload.description,
            status=TaskStatus.PENDING,
            priority=payload.priority,
            acceptance_criteria=payload.acceptance_criteria,
            metadata_json=payload.metadata,
        )
        self.db.add(task)
        self.db.commit()
        self.db.refresh(task)

        self.event_service.record_event(
            EventCreate(
                category=EventCategory.TASK,
                event_type="task.created",
                level=EventLevel.INFO,
                source=EventSourceRef(kind="api", id="tasks.create"),
                project_id=task.project_id,
                task_id=task.id,
                payload={"title": task.title, "priority": task.priority},
            )
        )
        return TaskRead.model_validate(task)
