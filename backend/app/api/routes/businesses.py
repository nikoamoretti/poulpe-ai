from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import get_business_cycle_service, get_business_service
from app.core.enums import BusinessStatus
from app.schemas.business import (
    BusinessCreate,
    BusinessCycleFeedbackRequest,
    BusinessCycleRead,
    BusinessCycleTriggerResponse,
    BusinessRead,
    BusinessUpdate,
)
from app.services.business_cycle_service import BusinessCycleService
from app.services.business_service import BusinessService

router = APIRouter(prefix="/businesses", tags=["businesses"])


# ── Business CRUD ───────────────────────────────────────────────────


@router.get("", response_model=list[BusinessRead])
def list_businesses(
    status_filter: BusinessStatus | None = Query(default=None, alias="status"),
    service: BusinessService = Depends(get_business_service),
) -> list[BusinessRead]:
    return service.list_businesses(status=status_filter)


@router.post("", response_model=BusinessRead, status_code=status.HTTP_201_CREATED)
def create_business(
    payload: BusinessCreate,
    service: BusinessService = Depends(get_business_service),
) -> BusinessRead:
    return service.create_business(payload)


@router.get("/{business_id}", response_model=BusinessRead)
def get_business(
    business_id: UUID,
    service: BusinessService = Depends(get_business_service),
) -> BusinessRead:
    return service.get_business(business_id)


@router.patch("/{business_id}", response_model=BusinessRead)
def update_business(
    business_id: UUID,
    payload: BusinessUpdate,
    service: BusinessService = Depends(get_business_service),
) -> BusinessRead:
    return service.update_business(business_id, payload)


@router.post("/{business_id}/activate", response_model=BusinessRead)
def activate_business(
    business_id: UUID,
    service: BusinessService = Depends(get_business_service),
) -> BusinessRead:
    return service.activate_business(business_id)


@router.post("/{business_id}/pause", response_model=BusinessRead)
def pause_business(
    business_id: UUID,
    service: BusinessService = Depends(get_business_service),
) -> BusinessRead:
    return service.pause_business(business_id)


# ── Business Cycles ─────────────────────────────────────────────────


@router.get("/{business_id}/cycles", response_model=list[BusinessCycleRead])
def list_business_cycles(
    business_id: UUID,
    limit: int = Query(default=30, ge=1, le=100),
    service: BusinessCycleService = Depends(get_business_cycle_service),
) -> list[BusinessCycleRead]:
    return service.list_cycles(business_id, limit=limit)


@router.post(
    "/{business_id}/cycles/trigger",
    response_model=BusinessCycleTriggerResponse,
    status_code=status.HTTP_201_CREATED,
)
def trigger_business_cycle(
    business_id: UUID,
    service: BusinessCycleService = Depends(get_business_cycle_service),
) -> BusinessCycleTriggerResponse:
    return service.trigger_cycle(business_id)


@router.get("/cycles/{cycle_id}", response_model=BusinessCycleRead)
def get_business_cycle(
    cycle_id: UUID,
    service: BusinessCycleService = Depends(get_business_cycle_service),
) -> BusinessCycleRead:
    return service.get_cycle(cycle_id)


@router.post("/cycles/{cycle_id}/feedback", response_model=BusinessCycleRead)
def submit_cycle_feedback(
    cycle_id: UUID,
    payload: BusinessCycleFeedbackRequest,
    service: BusinessCycleService = Depends(get_business_cycle_service),
) -> BusinessCycleRead:
    return service.submit_feedback(cycle_id, payload.feedback)
