from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import get_session_service, get_workspace_service
from app.schemas.common import ApiMessage
from app.schemas.session import (
    SessionCreate,
    SessionMessageRequest,
    SessionRead,
    SessionStartRequest,
)
from app.schemas.structured_event import ParsedSessionEventRead
from app.schemas.transcript import TranscriptChunkRead
from app.schemas.workspace import WorkspaceStatusRead
from app.services.session_service import SessionService
from app.services.workspace_service import WorkspaceService

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


@router.post("/{session_id}/start", response_model=SessionRead)
def start_session(
    session_id: UUID,
    payload: SessionStartRequest,
    service: SessionService = Depends(get_session_service),
) -> SessionRead:
    return service.start_session(session_id, payload)


@router.get("/{session_id}/workspace", response_model=WorkspaceStatusRead)
def get_session_workspace(
    session_id: UUID,
    service: WorkspaceService = Depends(get_workspace_service),
) -> WorkspaceStatusRead:
    return service.get_session_workspace(session_id)


@router.post("/{session_id}/workspace", response_model=WorkspaceStatusRead)
def create_session_workspace(
    session_id: UUID,
    service: WorkspaceService = Depends(get_workspace_service),
) -> WorkspaceStatusRead:
    return service.provision_session_workspace(session_id)


@router.post("/{session_id}/messages", response_model=ApiMessage)
def send_session_instruction(
    session_id: UUID,
    payload: SessionMessageRequest,
    service: SessionService = Depends(get_session_service),
) -> ApiMessage:
    return service.send_instruction(session_id, payload)


@router.post("/{session_id}/interrupt", response_model=ApiMessage)
def interrupt_session(
    session_id: UUID,
    service: SessionService = Depends(get_session_service),
) -> ApiMessage:
    return service.interrupt_session(session_id)


@router.get("/{session_id}/transcript", response_model=list[TranscriptChunkRead])
def get_session_transcript(
    session_id: UUID,
    limit: int = Query(default=200, ge=1, le=1000),
    service: SessionService = Depends(get_session_service),
) -> list[TranscriptChunkRead]:
    return service.list_transcript(session_id, limit=limit)


@router.get("/{session_id}/structured-events", response_model=list[ParsedSessionEventRead])
def get_session_structured_events(
    session_id: UUID,
    limit: int = Query(default=200, ge=1, le=1000),
    service: SessionService = Depends(get_session_service),
) -> list[ParsedSessionEventRead]:
    return service.list_structured_events(session_id, limit=limit)


@router.post("/{session_id}/stop", response_model=ApiMessage)
def stop_session(
    session_id: UUID,
    service: SessionService = Depends(get_session_service),
) -> ApiMessage:
    return service.stop_session(session_id)
