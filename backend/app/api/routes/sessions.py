from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import get_session_service
from app.schemas.common import ApiMessage
from app.schemas.session import SessionCreate, SessionRead
from app.services.session_service import SessionService

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.get("", response_model=list[SessionRead])
def list_sessions(
    project_id: UUID | None = Query(default=None),
    service: SessionService = Depends(get_session_service),
) -> list[SessionRead]:
    return service.list_sessions(project_id=project_id)


@router.post("", response_model=SessionRead, status_code=status.HTTP_201_CREATED)
def create_session(
    payload: SessionCreate,
    service: SessionService = Depends(get_session_service),
) -> SessionRead:
    return service.create_session(payload)


@router.get("/{session_id}", response_model=SessionRead)
def get_session(
    session_id: UUID,
    service: SessionService = Depends(get_session_service),
) -> SessionRead:
    return service.get_session(session_id)


@router.post("/{session_id}/stop", response_model=ApiMessage)
def stop_session(
    session_id: UUID,
    service: SessionService = Depends(get_session_service),
) -> ApiMessage:
    return service.stop_session(session_id)


@router.get("/stub/operator-note", response_model=ApiMessage, include_in_schema=False)
def operator_note() -> ApiMessage:
    return ApiMessage(
        detail="Session spawning and supervision are still stubbed in the scaffold.",
        generated_at=datetime.now(UTC),
    )
