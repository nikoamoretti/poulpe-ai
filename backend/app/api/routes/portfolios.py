from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import get_portfolio_automation_service, get_portfolio_service
from app.core.enums import ProjectCheckpointStatus
from app.schemas.project_checkpoint import ProjectCheckpointRead, ProjectCheckpointRespondRequest
from app.schemas.portfolio import (
    PortfolioAutomationTickRead,
    PortfolioCreate,
    PortfolioManagerStartRequest,
    PortfolioRead,
)
from app.schemas.session import SessionRead
from app.services.portfolio_automation_service import PortfolioAutomationService
from app.services.portfolio_service import PortfolioService

router = APIRouter(prefix="/portfolios", tags=["portfolios"])


@router.get("", response_model=list[PortfolioRead])
def list_portfolios(service: PortfolioService = Depends(get_portfolio_service)) -> list[PortfolioRead]:
    return service.list_portfolios()


@router.post("", response_model=PortfolioRead, status_code=status.HTTP_201_CREATED)
def create_portfolio(
    payload: PortfolioCreate,
    service: PortfolioService = Depends(get_portfolio_service),
) -> PortfolioRead:
    return service.create_portfolio(payload)


@router.get("/{portfolio_id}", response_model=PortfolioRead)
def get_portfolio(
    portfolio_id: UUID,
    service: PortfolioService = Depends(get_portfolio_service),
) -> PortfolioRead:
    return service.get_portfolio(portfolio_id)


@router.get("/{portfolio_id}/inbox", response_model=list[ProjectCheckpointRead])
def list_portfolio_inbox(
    portfolio_id: UUID,
    status: ProjectCheckpointStatus | None = Query(default=ProjectCheckpointStatus.OPEN),
    service: PortfolioService = Depends(get_portfolio_service),
) -> list[ProjectCheckpointRead]:
    return service.list_inbox(portfolio_id, status=status)


@router.post("/{portfolio_id}/inbox/{checkpoint_id}/respond", response_model=ProjectCheckpointRead)
def respond_to_portfolio_checkpoint(
    portfolio_id: UUID,
    checkpoint_id: UUID,
    payload: ProjectCheckpointRespondRequest,
    service: PortfolioService = Depends(get_portfolio_service),
) -> ProjectCheckpointRead:
    return service.respond_to_checkpoint(portfolio_id, checkpoint_id, payload)


@router.post("/{portfolio_id}/manager/start", response_model=SessionRead)
def start_portfolio_manager(
    portfolio_id: UUID,
    payload: PortfolioManagerStartRequest,
    service: PortfolioService = Depends(get_portfolio_service),
) -> SessionRead:
    return service.start_manager_session(portfolio_id, payload)


@router.post("/{portfolio_id}/automation/tick", response_model=PortfolioAutomationTickRead)
def trigger_portfolio_automation_tick(
    portfolio_id: UUID,
    service: PortfolioAutomationService = Depends(get_portfolio_automation_service),
) -> PortfolioAutomationTickRead:
    return service.tick(portfolio_id)
