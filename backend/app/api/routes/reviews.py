from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import get_review_service
from app.schemas.review import ReviewApprove, ReviewCreate, ReviewMergeReady, ReviewRead, ReviewReject
from app.services.review_service import ReviewService

router = APIRouter(prefix="/reviews", tags=["legacy-reviews"])


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
    return service.create_review(payload)


@router.get("/{review_id}", response_model=ReviewRead)
def get_review(
    review_id: UUID,
    service: ReviewService = Depends(get_review_service),
) -> ReviewRead:
    return service.get_review(review_id)


@router.post("/{review_id}/approve", response_model=ReviewRead)
def approve_review(
    review_id: UUID,
    payload: ReviewApprove,
    service: ReviewService = Depends(get_review_service),
) -> ReviewRead:
    return service.approve_review(review_id, payload)


@router.post("/{review_id}/reject", response_model=ReviewRead)
def reject_review(
    review_id: UUID,
    payload: ReviewReject,
    service: ReviewService = Depends(get_review_service),
) -> ReviewRead:
    return service.reject_review(review_id, payload)


@router.post("/{review_id}/merge-ready", response_model=ReviewRead)
def mark_review_merge_ready(
    review_id: UUID,
    payload: ReviewMergeReady,
    service: ReviewService = Depends(get_review_service),
) -> ReviewRead:
    return service.mark_merge_ready(review_id, payload)
