from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.enums import EventCategory, EventLevel, ReviewStatus
from app.core.errors import NotFoundError
from app.models.review import Review
from app.models.task import Task
from app.schemas.event import EventCreate, EventSourceRef
from app.schemas.review import ReviewCreate, ReviewDecision, ReviewRead
from app.services.event_service import EventService


class ReviewService:
    def __init__(self, db: Session, event_service: EventService) -> None:
        self.db = db
        self.event_service = event_service

    def list_reviews(self, project_id: UUID | None = None) -> list[ReviewRead]:
        stmt = select(Review).order_by(Review.created_at.desc())
        if project_id is not None:
            stmt = stmt.where(Review.project_id == project_id)
        records = self.db.scalars(stmt).all()
        return [ReviewRead.model_validate(record) for record in records]

    def get_review(self, review_id: UUID) -> ReviewRead:
        review = self.db.get(Review, review_id)
        if review is None:
            raise NotFoundError(f"Review not found: {review_id}")
        return ReviewRead.model_validate(review)

    def request_review(self, payload: ReviewCreate) -> ReviewRead:
        task = self.db.get(Task, payload.task_id)
        if task is None:
            raise NotFoundError(f"Task not found: {payload.task_id}")

        review = Review(
            project_id=payload.project_id,
            task_id=payload.task_id,
            requester_session_id=payload.requester_session_id,
            reviewer_session_id=payload.reviewer_session_id,
            diff_artifact_id=payload.diff_artifact_id,
            status=ReviewStatus.PENDING,
            summary=payload.summary,
            metadata_json=payload.metadata,
        )
        self.db.add(review)
        self.db.commit()
        self.db.refresh(review)

        self.event_service.record_event(
            EventCreate(
                category=EventCategory.REVIEW,
                event_type="review.requested",
                level=EventLevel.INFO,
                source=EventSourceRef(kind="api", id="reviews.create"),
                project_id=review.project_id,
                task_id=review.task_id,
                session_id=review.requester_session_id,
                payload={"review_id": str(review.id), "status": review.status.value},
            )
        )
        return ReviewRead.model_validate(review)

    def record_decision(self, review_id: UUID, payload: ReviewDecision) -> ReviewRead:
        review = self.db.get(Review, review_id)
        if review is None:
            raise NotFoundError(f"Review not found: {review_id}")

        review.status = payload.decision
        review.decision_note = payload.note
        if payload.decision in {ReviewStatus.HUMAN_APPROVED, ReviewStatus.MERGE_READY}:
            review.human_approved_by = payload.approved_by
            review.human_approved_at = datetime.now(UTC)

        self.db.commit()
        self.db.refresh(review)

        self.event_service.record_event(
            EventCreate(
                category=EventCategory.REVIEW,
                event_type="review.decision_recorded",
                level=EventLevel.INFO,
                source=EventSourceRef(kind="api", id="reviews.decision"),
                project_id=review.project_id,
                task_id=review.task_id,
                session_id=review.reviewer_session_id,
                payload={"review_id": str(review.id), "decision": review.status.value},
            )
        )
        return ReviewRead.model_validate(review)
