from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.enums import (
    BusinessCycleStatus,
    BusinessStatus,
    EventCategory,
    EventLevel,
)
from app.core.errors import ConflictError, NotFoundError
from app.models.business import Business
from app.models.business_cycle import BusinessCycle
from app.schemas.business import BusinessCycleRead, BusinessCycleTriggerResponse
from app.schemas.event import EventCreate, EventSourceRef
from app.services.event_service import EventService

logger = logging.getLogger(__name__)


def _cron_matches_now(cron_expr: str) -> bool:
    """Check if a simple cron expression matches the current UTC time.

    Supports standard 5-field cron: minute hour day_of_month month day_of_week.
    Only handles literal values and '*' (no ranges, steps, or lists).
    """
    parts = cron_expr.strip().split()
    if len(parts) != 5:
        return False

    now = datetime.now(UTC)
    fields = [now.minute, now.hour, now.day, now.month, now.weekday()]
    # cron uses 0=Sunday but Python weekday() uses 0=Monday
    # Convert Python weekday to cron: (py_weekday + 1) % 7
    fields[4] = (fields[4] + 1) % 7

    for part, value in zip(parts, fields):
        if part == "*":
            continue
        try:
            if int(part) != value:
                return False
        except ValueError:
            return False
    return True


class BusinessCycleService:
    def __init__(
        self,
        db: Session,
        settings: Settings,
        event_service: EventService,
    ) -> None:
        self.db = db
        self.settings = settings
        self.event_service = event_service

    # ── queries ─────────────────────────────────────────────────────

    def list_cycles(
        self, business_id: UUID, *, limit: int = 30
    ) -> list[BusinessCycleRead]:
        stmt = (
            select(BusinessCycle)
            .where(BusinessCycle.business_id == business_id)
            .order_by(BusinessCycle.created_at.desc())
            .limit(limit)
        )
        records = self.db.scalars(stmt).all()
        return [BusinessCycleRead.model_validate(r) for r in records]

    def get_cycle(self, cycle_id: UUID) -> BusinessCycleRead:
        cycle = self.db.get(BusinessCycle, cycle_id)
        if cycle is None:
            raise NotFoundError(f"Business cycle not found: {cycle_id}")
        return BusinessCycleRead.model_validate(cycle)

    # ── trigger ─────────────────────────────────────────────────────

    def trigger_cycle(self, business_id: UUID) -> BusinessCycleTriggerResponse:
        """Manually trigger a daily cycle for a business."""
        business = self.db.get(Business, business_id)
        if business is None:
            raise NotFoundError(f"Business not found: {business_id}")

        today = datetime.now(UTC).strftime("%Y-%m-%d")
        existing = self.db.scalar(
            select(BusinessCycle).where(
                and_(
                    BusinessCycle.business_id == business_id,
                    BusinessCycle.cycle_date == today,
                )
            )
        )
        if existing is not None:
            if existing.status in (
                BusinessCycleStatus.RUNNING,
                BusinessCycleStatus.COMPLETED,
            ):
                raise ConflictError(
                    f"Cycle already exists for {today} with status: {existing.status}"
                )
            # Re-trigger a failed/pending cycle
            existing.status = BusinessCycleStatus.PENDING
            existing.error_message = None
            self.db.commit()
            self.db.refresh(existing)
            return BusinessCycleTriggerResponse(
                cycle=BusinessCycleRead.model_validate(existing),
                detail=f"Re-triggered existing cycle for {today}",
            )

        cycle = self._create_cycle(business, today)
        return BusinessCycleTriggerResponse(
            cycle=BusinessCycleRead.model_validate(cycle),
            detail=f"Created new cycle for {today}",
        )

    def submit_feedback(self, cycle_id: UUID, feedback: str) -> BusinessCycleRead:
        """Submit human feedback for a cycle."""
        cycle = self.db.get(BusinessCycle, cycle_id)
        if cycle is None:
            raise NotFoundError(f"Business cycle not found: {cycle_id}")
        cycle.human_feedback = feedback
        self.db.commit()
        self.db.refresh(cycle)
        return BusinessCycleRead.model_validate(cycle)

    # ── cron check (called from background loop) ────────────────────

    def check_and_trigger(self) -> None:
        """Check all active businesses and trigger cycles whose cron matches now."""
        businesses = self.db.scalars(
            select(Business).where(Business.status == BusinessStatus.ACTIVE)
        ).all()

        today = datetime.now(UTC).strftime("%Y-%m-%d")

        for business in businesses:
            if not _cron_matches_now(business.daily_cycle_cron):
                continue

            existing = self.db.scalar(
                select(BusinessCycle).where(
                    and_(
                        BusinessCycle.business_id == business.id,
                        BusinessCycle.cycle_date == today,
                    )
                )
            )
            if existing is not None:
                continue

            try:
                self._create_cycle(business, today)
                logger.info(
                    "auto-triggered daily cycle for business %s (%s)",
                    business.name,
                    business.id,
                )
            except Exception:
                logger.exception(
                    "failed to auto-trigger cycle for business %s", business.id
                )

    # ── update helpers ──────────────────────────────────────────────

    def mark_running(self, cycle_id: UUID, ceo_session_id: UUID) -> None:
        cycle = self.db.get(BusinessCycle, cycle_id)
        if cycle is None:
            return
        cycle.status = BusinessCycleStatus.RUNNING
        cycle.ceo_session_id = ceo_session_id
        cycle.started_at = datetime.now(UTC)
        self.db.commit()

    def mark_completed(
        self,
        cycle_id: UUID,
        *,
        ceo_plan: dict | None = None,
        agent_results: dict | None = None,
        metrics_after: dict | None = None,
    ) -> None:
        cycle = self.db.get(BusinessCycle, cycle_id)
        if cycle is None:
            return
        cycle.status = BusinessCycleStatus.COMPLETED
        cycle.completed_at = datetime.now(UTC)
        if ceo_plan is not None:
            cycle.ceo_plan = ceo_plan
        if agent_results is not None:
            cycle.agent_results = agent_results
        if metrics_after is not None:
            cycle.metrics_after = metrics_after
        self.db.commit()

    def mark_failed(self, cycle_id: UUID, error: str) -> None:
        cycle = self.db.get(BusinessCycle, cycle_id)
        if cycle is None:
            return
        cycle.status = BusinessCycleStatus.FAILED
        cycle.error_message = error
        cycle.completed_at = datetime.now(UTC)
        self.db.commit()

    # ── private ─────────────────────────────────────────────────────

    def _create_cycle(self, business: Business, date_str: str) -> BusinessCycle:
        cycle = BusinessCycle(
            business_id=business.id,
            cycle_date=date_str,
            status=BusinessCycleStatus.PENDING,
            metrics_before=business.metrics_snapshot,
        )
        self.db.add(cycle)
        self.db.commit()
        self.db.refresh(cycle)

        self.event_service.record_event(
            EventCreate(
                category=EventCategory.SYSTEM,
                event_type="business.cycle_created",
                level=EventLevel.INFO,
                source=EventSourceRef(kind="service", id="business-cycle"),
                payload={
                    "business_id": str(business.id),
                    "cycle_id": str(cycle.id),
                    "cycle_date": date_str,
                },
            )
        )
        return cycle
