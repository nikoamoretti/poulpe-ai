from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_event_service
from app.core.enums import (
    EventCategory,
    EventLevel,
    ProjectCheckpointKind,
    ProjectCheckpointStatus,
)
from app.core.errors import NotFoundError
from app.models.project import Project
from app.models.project_checkpoint import ProjectCheckpoint
from app.schemas.event import EventCreate, EventSourceRef
from app.schemas.project_checkpoint import (
    ProjectCheckpointCreateRequest,
    ProjectCheckpointRead,
)
from app.services.event_service import EventService

router = APIRouter(prefix="/checkpoints", tags=["checkpoints"])


def _checkpoint_to_read(checkpoint: ProjectCheckpoint, project: Project) -> ProjectCheckpointRead:
    return ProjectCheckpointRead(
        id=checkpoint.id,
        portfolio_id=checkpoint.portfolio_id,
        project_id=checkpoint.project_id,
        project_name=project.name,
        project_slug=project.slug,
        source_session_id=checkpoint.source_session_id,
        manager_session_id=checkpoint.manager_session_id,
        source_parsed_event_id=checkpoint.source_parsed_event_id,
        kind=checkpoint.kind,
        status=checkpoint.status,
        summary=checkpoint.summary,
        details=checkpoint.details_json,
        artifacts=[],
        resolution=checkpoint.resolution,
        response_message=checkpoint.response_message,
        response_details=checkpoint.response_details_json,
        source_occurred_at=checkpoint.source_occurred_at,
        resolved_at=checkpoint.resolved_at,
        created_at=checkpoint.created_at,
        updated_at=checkpoint.updated_at,
    )


@router.post("", response_model=ProjectCheckpointRead, status_code=status.HTTP_201_CREATED)
def create_checkpoint(
    payload: ProjectCheckpointCreateRequest,
    db: Session = Depends(get_db),
    event_service: EventService = Depends(get_event_service),
) -> ProjectCheckpointRead:
    project = db.get(Project, payload.project_id)
    if project is None:
        raise NotFoundError(f"Project not found: {payload.project_id}")
    if project.portfolio_id is None:
        raise NotFoundError(f"Project {payload.project_id} has no portfolio")

    now = datetime.now(UTC)

    checkpoint = ProjectCheckpoint(
        portfolio_id=project.portfolio_id,
        project_id=project.id,
        source_session_id=payload.session_id,
        kind=payload.kind,
        status=ProjectCheckpointStatus.OPEN,
        summary=payload.summary,
        details_json=payload.details,
        source_occurred_at=now,
    )
    db.add(checkpoint)
    db.commit()
    db.refresh(checkpoint)

    event_service.record_event(
        EventCreate(
            category=EventCategory.PROJECT,
            event_type="project.checkpoint_opened",
            level=EventLevel.INFO,
            source=EventSourceRef(kind="service", role=None, id="mcp-bridge"),
            project_id=project.id,
            session_id=payload.session_id,
            payload={
                "checkpoint_id": str(checkpoint.id),
                "portfolio_id": str(checkpoint.portfolio_id),
                "kind": checkpoint.kind.value,
                "summary": checkpoint.summary,
            },
            occurred_at=now,
        )
    )

    return _checkpoint_to_read(checkpoint, project)


@router.get("/{checkpoint_id}", response_model=ProjectCheckpointRead)
def get_checkpoint(
    checkpoint_id: UUID,
    db: Session = Depends(get_db),
) -> ProjectCheckpointRead:
    checkpoint = db.get(ProjectCheckpoint, checkpoint_id)
    if checkpoint is None:
        raise NotFoundError(f"Checkpoint not found: {checkpoint_id}")

    project = db.get(Project, checkpoint.project_id)
    if project is None:
        raise NotFoundError(f"Project not found: {checkpoint.project_id}")

    return _checkpoint_to_read(checkpoint, project)


@router.get("/{checkpoint_id}/poll", response_model=ProjectCheckpointRead)
def poll_checkpoint(
    checkpoint_id: UUID,
    db: Session = Depends(get_db),
) -> ProjectCheckpointRead:
    checkpoint = db.get(ProjectCheckpoint, checkpoint_id)
    if checkpoint is None:
        raise NotFoundError(f"Checkpoint not found: {checkpoint_id}")

    project = db.get(Project, checkpoint.project_id)
    if project is None:
        raise NotFoundError(f"Project not found: {checkpoint.project_id}")

    return _checkpoint_to_read(checkpoint, project)
