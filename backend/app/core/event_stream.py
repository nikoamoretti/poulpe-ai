from __future__ import annotations

import asyncio
import threading
from collections import defaultdict
from dataclasses import dataclass
from uuid import UUID

from app.schemas.event import EventEnvelope


@dataclass(slots=True, eq=False)
class _Subscriber:
    queue: asyncio.Queue[EventEnvelope]
    loop: asyncio.AbstractEventLoop


class EventSubscription:
    def __init__(
        self,
        broker: "EventStreamBroker",
        key: tuple[str, str],
        subscriber: _Subscriber,
    ) -> None:
        self._broker = broker
        self.key = key
        self.queue = subscriber.queue
        self._subscriber = subscriber

    def close(self) -> None:
        self._broker.unsubscribe(self.key, self._subscriber)


class EventStreamBroker:
    def __init__(self) -> None:
        self._subscriptions: dict[tuple[str, str], set[_Subscriber]] = defaultdict(set)
        self._lock = threading.Lock()

    def subscribe(
        self,
        *,
        project_id: UUID | None = None,
        session_id: UUID | None = None,
    ) -> EventSubscription:
        if project_id is not None:
            key = ("project", str(project_id))
        elif session_id is not None:
            key = ("session", str(session_id))
        else:
            key = ("global", "*")

        subscriber = _Subscriber(
            queue=asyncio.Queue(maxsize=100),
            loop=asyncio.get_running_loop(),
        )
        with self._lock:
            self._subscriptions[key].add(subscriber)
        return EventSubscription(self, key, subscriber)

    def unsubscribe(self, key: tuple[str, str], subscriber: _Subscriber) -> None:
        with self._lock:
            subscribers = self._subscriptions.get(key)
            if not subscribers:
                return
            subscribers.discard(subscriber)
            if not subscribers:
                self._subscriptions.pop(key, None)

    def publish(self, event: EventEnvelope) -> None:
        candidate_keys = [("global", "*")]
        if event.project_id is not None:
            candidate_keys.append(("project", str(event.project_id)))
        if event.session_id is not None:
            candidate_keys.append(("session", str(event.session_id)))

        with self._lock:
            subscribers = [
                subscriber
                for key in candidate_keys
                for subscriber in self._subscriptions.get(key, set())
            ]

        for subscriber in subscribers:
            subscriber.loop.call_soon_threadsafe(self._deliver, subscriber.queue, event)

    @staticmethod
    def _deliver(queue: asyncio.Queue[EventEnvelope], event: EventEnvelope) -> None:
        if queue.full():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        queue.put_nowait(event)
