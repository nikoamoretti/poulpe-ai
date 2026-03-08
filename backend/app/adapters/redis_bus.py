from __future__ import annotations

import json

try:
    from redis import Redis
    from redis.exceptions import RedisError
except ImportError:  # pragma: no cover - exercised only in minimal local envs
    Redis = None

    class RedisError(Exception):
        pass

from app.schemas.event import EventEnvelope


class RedisBusAdapter:
    def __init__(self, redis_url: str, *, enabled: bool = True) -> None:
        self.enabled = enabled and Redis is not None
        self.channel_name = "orchestrator.events"
        self._client = Redis.from_url(redis_url, decode_responses=True) if self.enabled else None

    def publish_event(self, event: EventEnvelope) -> None:
        if not self._client:
            return
        try:
            self._client.publish(self.channel_name, json.dumps(event.model_dump(mode="json")))
        except RedisError:
            return

    def ping(self) -> bool:
        if not self._client:
            return False
        try:
            return bool(self._client.ping())
        except RedisError:
            return False

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
