from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from app.core.enums import EventLevel, StructuredEventType
from app.schemas.structured_event import STRUCTURED_EVENT_ADAPTER, StructuredEventPayload

START_MARKER = "[[EVENT]]"
END_MARKER = "[[/EVENT]]"
LEGACY_START_MARKER = "<<<ORCHESTRATOR_EVENT>>>"
LEGACY_END_MARKER = "<<<END_ORCHESTRATOR_EVENT>>>"
MARKER_PAIRS = (
    (START_MARKER, END_MARKER),
    (LEGACY_START_MARKER, LEGACY_END_MARKER),
)
LEGACY_EVENT_TYPE_MAP = {
    "session.running": StructuredEventType.START.value,
    "session.started": StructuredEventType.START.value,
    "session.heartbeat": StructuredEventType.HEARTBEAT.value,
    "session.blocked": StructuredEventType.BLOCKED.value,
    "session.completed": StructuredEventType.COMPLETE.value,
    "session.failed": StructuredEventType.ERROR.value,
    "task.progress": StructuredEventType.PROGRESS.value,
}
DEFAULT_LEVEL_BY_TYPE = {
    StructuredEventType.START.value: EventLevel.INFO.value,
    StructuredEventType.PROGRESS.value: EventLevel.INFO.value,
    StructuredEventType.QUESTION.value: EventLevel.WARN.value,
    StructuredEventType.BLOCKED.value: EventLevel.WARN.value,
    StructuredEventType.TESTS_RUN.value: EventLevel.INFO.value,
    StructuredEventType.COMPLETE.value: EventLevel.INFO.value,
    StructuredEventType.ERROR.value: EventLevel.ERROR.value,
    StructuredEventType.HEARTBEAT.value: EventLevel.DEBUG.value,
}


@dataclass(slots=True)
class ParsedEventBlock:
    raw_block: str
    raw_payload: str
    declared_type: str | None
    event: StructuredEventPayload | None
    normalized_payload: dict[str, Any]
    validation_error: str | None = None

    @property
    def is_valid(self) -> bool:
        return self.event is not None


@dataclass(slots=True)
class EventParseResult:
    blocks: list[ParsedEventBlock]
    remainder: str


class EventParserAdapter:
    """Extract and validate structured event payloads embedded inside session output."""

    def extract_blocks(self, text: str) -> EventParseResult:
        cursor = 0
        blocks: list[ParsedEventBlock] = []
        remainder = ""

        while True:
            located = self._find_next_start(text, cursor)
            if located is None:
                break

            start_index, start_marker, end_marker = located
            end_index = text.find(end_marker, start_index + len(start_marker))
            if end_index < 0:
                remainder = text[start_index:]
                break

            raw_block = text[start_index : end_index + len(end_marker)]
            raw_payload = text[start_index + len(start_marker) : end_index].strip()
            blocks.append(self._parse_block(raw_block=raw_block, raw_payload=raw_payload))
            cursor = end_index + len(end_marker)

        return EventParseResult(blocks=blocks, remainder=remainder)

    @staticmethod
    def _find_next_start(text: str, cursor: int) -> tuple[int, str, str] | None:
        candidates = []
        for start_marker, end_marker in MARKER_PAIRS:
            start_index = text.find(start_marker, cursor)
            if start_index >= 0:
                candidates.append((start_index, start_marker, end_marker))
        if not candidates:
            return None
        return min(candidates, key=lambda item: item[0])

    def _parse_block(self, *, raw_block: str, raw_payload: str) -> ParsedEventBlock:
        try:
            payload = json.loads(raw_payload)
        except json.JSONDecodeError as exc:
            return ParsedEventBlock(
                raw_block=raw_block,
                raw_payload=raw_payload,
                declared_type=None,
                event=None,
                normalized_payload={},
                validation_error=f"Malformed JSON: {exc.msg} at line {exc.lineno} column {exc.colno}",
            )

        if not isinstance(payload, dict):
            return ParsedEventBlock(
                raw_block=raw_block,
                raw_payload=raw_payload,
                declared_type=None,
                event=None,
                normalized_payload={},
                validation_error="Structured event payload must be a JSON object.",
            )

        normalized = self._normalize_payload(payload)
        declared_type = str(payload.get("type") or payload.get("event_type") or "") or None
        try:
            event = STRUCTURED_EVENT_ADAPTER.validate_python(normalized)
        except ValidationError as exc:
            return ParsedEventBlock(
                raw_block=raw_block,
                raw_payload=raw_payload,
                declared_type=declared_type,
                event=None,
                normalized_payload=normalized,
                validation_error=exc.errors(include_url=False)[0]["msg"],
            )

        return ParsedEventBlock(
            raw_block=raw_block,
            raw_payload=raw_payload,
            declared_type=declared_type,
            event=event,
            normalized_payload=event.model_dump(mode="python"),
        )

    def _normalize_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(payload)
        raw_type = normalized.pop("event_type", normalized.get("type"))
        if raw_type is None:
            normalized["type"] = ""
        else:
            normalized["type"] = LEGACY_EVENT_TYPE_MAP.get(str(raw_type), str(raw_type))

        if not normalized.get("summary"):
            summary = normalized.get("message")
            if summary is None:
                summary = normalized.get("question") or normalized.get("reason") or normalized.get("error")
            if summary is None and normalized.get("type") == StructuredEventType.TESTS_RUN.value:
                summary = f"Ran {normalized.get('command', 'test command')}"
            if summary is None and normalized.get("type") == StructuredEventType.HEARTBEAT.value:
                summary = "Heartbeat"
            if summary is not None:
                normalized["summary"] = str(summary)

        if normalized.get("level") is None:
            normalized["level"] = DEFAULT_LEVEL_BY_TYPE.get(
                str(normalized.get("type", "")),
                EventLevel.INFO.value,
            )

        if "payload" in normalized and isinstance(normalized["payload"], dict):
            payload_details = normalized.pop("payload")
            details = normalized.get("details")
            if not isinstance(details, dict):
                details = {}
            normalized["details"] = {**payload_details, **details}
            for key, value in payload_details.items():
                normalized.setdefault(key, value)

        return normalized
