from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.adapters.redis_bus import RedisBusAdapter
from app.core.event_stream import EventStreamBroker
from app.models.event import Event
from app.schemas.event import EventCreate, EventEnvelope


class EventService:
    def __init__(
        self,
        db: Session,
        redis_bus: RedisBusAdapter,
        event_broker: EventStreamBroker,
    ) -> None:
        self.db = db
        self.redis_bus = redis_bus
        self.event_broker = event_broker

    def list_events(
        self,
        project_id: UUID | None = None,
        session_id: UUID | None = None,
        limit: int = 100,
    ) -> list[EventEnvelope]:
        stmt = select(Event).order_by(Event.sequence.desc()).limit(limit)
        if project_id is not None:
            stmt = stmt.where(Event.project_id == project_id)
        if session_id is not None:
            stmt = stmt.where(Event.session_id == session_id)
        records = self.db.scalars(stmt).all()
        return [EventEnvelope.model_validate(record) for record in records]

    def record_event(self, payload: EventCreate) -> EventEnvelope:
        event = Event(
            version="v1",
            sequence=self._next_sequence(),
            category=payload.category,
            event_type=payload.event_type,
            level=payload.level,
            source=payload.source.model_dump(mode="json"),
            project_id=payload.project_id,
            task_id=payload.task_id,
            session_id=payload.session_id,
            correlation_id=payload.correlation_id,
            causation_id=payload.causation_id,
            payload=payload.payload,
            raw_output=payload.raw_output,
            occurred_at=payload.occurred_at or datetime.now(UTC),
        )
        self.db.add(event)
        self.db.commit()
        self.db.refresh(event)

        envelope = EventEnvelope.model_validate(event)
        self.redis_bus.publish_event(envelope)
        self.event_broker.publish(envelope)
        return envelope

    def _next_sequence(self) -> int:
        current = self.db.scalar(select(func.max(Event.sequence)))
        return int(current or 0) + 1
