from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_event_service
from app.schemas.event import EventEnvelope
from app.services.event_service import EventService

router = APIRouter(prefix="/events", tags=["events"])


@router.get("", response_model=list[EventEnvelope])
def list_events(
    project_id: UUID | None = Query(default=None),
    session_id: UUID | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    service: EventService = Depends(get_event_service),
) -> list[EventEnvelope]:
    return service.list_events(project_id=project_id, session_id=session_id, limit=limit)
