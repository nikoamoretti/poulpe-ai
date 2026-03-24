from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.enums import (
    BusinessStatus,
    EventCategory,
    EventLevel,
)
from app.core.errors import ConflictError, NotFoundError
from app.core.text import slugify
from app.models.business import Business
from app.models.portfolio import Portfolio
from app.schemas.business import BusinessCreate, BusinessRead, BusinessUpdate
from app.schemas.event import EventCreate, EventSourceRef
from app.services.event_service import EventService

logger = logging.getLogger(__name__)


class BusinessService:
    def __init__(self, db: Session, event_service: EventService) -> None:
        self.db = db
        self.event_service = event_service

    # ── queries ─────────────────────────────────────────────────────

    def list_businesses(
        self, *, status: BusinessStatus | None = None
    ) -> list[BusinessRead]:
        stmt = select(Business).order_by(Business.created_at.desc())
        if status is not None:
            stmt = stmt.where(Business.status == status)
        records = self.db.scalars(stmt).all()
        return [BusinessRead.model_validate(record) for record in records]

    def get_business(self, business_id: UUID) -> BusinessRead:
        business = self.db.get(Business, business_id)
        if business is None:
            raise NotFoundError(f"Business not found: {business_id}")
        return BusinessRead.model_validate(business)

    # ── mutations ───────────────────────────────────────────────────

    def create_business(self, payload: BusinessCreate) -> BusinessRead:
        portfolio = self.db.get(Portfolio, payload.portfolio_id)
        if portfolio is None:
            raise NotFoundError(f"Portfolio not found: {payload.portfolio_id}")

        slug = self._build_unique_slug(payload.name)
        business = Business(
            name=payload.name,
            slug=slug,
            portfolio_id=payload.portfolio_id,
            description=payload.description.strip(),
            business_type=payload.business_type,
            domain=payload.domain,
            budget_monthly_usd=payload.budget_monthly_usd,
            daily_cycle_cron=payload.daily_cycle_cron,
            active_agent_types=payload.active_agent_types,
            status=BusinessStatus.SETUP,
            metadata_json=payload.metadata,
        )
        self.db.add(business)
        self.db.commit()
        self.db.refresh(business)

        self.event_service.record_event(
            EventCreate(
                category=EventCategory.SYSTEM,
                event_type="business.created",
                level=EventLevel.INFO,
                source=EventSourceRef(kind="api", id="businesses.create"),
                payload={
                    "business_id": str(business.id),
                    "name": business.name,
                    "business_type": business.business_type.value,
                    "portfolio_id": str(business.portfolio_id),
                },
            )
        )
        return BusinessRead.model_validate(business)

    def update_business(
        self, business_id: UUID, payload: BusinessUpdate
    ) -> BusinessRead:
        business = self.db.get(Business, business_id)
        if business is None:
            raise NotFoundError(f"Business not found: {business_id}")

        update_data = payload.model_dump(exclude_unset=True)
        if "metadata" in update_data:
            update_data["metadata_json"] = update_data.pop("metadata")
        for field, value in update_data.items():
            setattr(business, field, value)

        self.db.commit()
        self.db.refresh(business)

        self.event_service.record_event(
            EventCreate(
                category=EventCategory.SYSTEM,
                event_type="business.updated",
                level=EventLevel.INFO,
                source=EventSourceRef(kind="api", id="businesses.update"),
                payload={
                    "business_id": str(business.id),
                    "updated_fields": list(update_data.keys()),
                },
            )
        )
        return BusinessRead.model_validate(business)

    def activate_business(self, business_id: UUID) -> BusinessRead:
        business = self.db.get(Business, business_id)
        if business is None:
            raise NotFoundError(f"Business not found: {business_id}")
        if business.status == BusinessStatus.ACTIVE:
            raise ConflictError("Business is already active")
        business.status = BusinessStatus.ACTIVE
        self.db.commit()
        self.db.refresh(business)

        self.event_service.record_event(
            EventCreate(
                category=EventCategory.SYSTEM,
                event_type="business.activated",
                level=EventLevel.INFO,
                source=EventSourceRef(kind="api", id="businesses.activate"),
                payload={"business_id": str(business.id)},
            )
        )
        return BusinessRead.model_validate(business)

    def pause_business(self, business_id: UUID) -> BusinessRead:
        business = self.db.get(Business, business_id)
        if business is None:
            raise NotFoundError(f"Business not found: {business_id}")
        business.status = BusinessStatus.PAUSED
        self.db.commit()
        self.db.refresh(business)

        self.event_service.record_event(
            EventCreate(
                category=EventCategory.SYSTEM,
                event_type="business.paused",
                level=EventLevel.INFO,
                source=EventSourceRef(kind="api", id="businesses.pause"),
                payload={"business_id": str(business.id)},
            )
        )
        return BusinessRead.model_validate(business)

    # ── helpers ─────────────────────────────────────────────────────

    def _build_unique_slug(self, name: str) -> str:
        base = slugify(name)
        slug = base
        counter = 1
        while self.db.scalar(select(Business.id).where(Business.slug == slug)) is not None:
            slug = f"{base}-{counter}"
            counter += 1
        return slug
