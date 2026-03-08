from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import get_review_service
from app.schemas.review import ReviewCreate, ReviewDecision, ReviewRead
from app.services.review_service import ReviewService

router = APIRouter(prefix="/reviews", tags=["reviews"])


@router.get("", response_model=list[ReviewRead])
def list_reviews(
    project_id: UUID | None = Query(default=None),
    service: ReviewService = Depends(get_review_service),
) -> list[ReviewRead]:
    return service.list_reviews(project_id=project_id)


@router.post("", response_model=ReviewRead, status_code=status.HTTP_201_CREATED)
def create_review(
    payload: ReviewCreate,
    service: ReviewService = Depends(get_review_service),
) -> ReviewRead:
    return service.request_review(payload)


@router.get("/{review_id}", response_model=ReviewRead)
def get_review(
    review_id: UUID,
    service: ReviewService = Depends(get_review_service),
) -> ReviewRead:
    return service.get_review(review_id)


@router.post("/{review_id}/decision", response_model=ReviewRead)
def record_review_decision(
    review_id: UUID,
    payload: ReviewDecision,
    service: ReviewService = Depends(get_review_service),
) -> ReviewRead:
    return service.record_decision(review_id, payload)
