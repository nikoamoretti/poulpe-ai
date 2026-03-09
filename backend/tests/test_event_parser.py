from __future__ import annotations

from app.adapters.event_parser import (
    END_MARKER,
    LEGACY_END_MARKER,
    LEGACY_START_MARKER,
    START_MARKER,
    EventParserAdapter,
)
from app.core.enums import StructuredEventType


def test_event_parser_extracts_valid_new_protocol_block() -> None:
    parser = EventParserAdapter()

    result = parser.extract_blocks(
        "\n".join(
            [
                "worker noise",
                START_MARKER,
                '{"type":"progress","summary":"Implemented parser","progress":40}',
                END_MARKER,
            ]
        )
    )

    assert result.remainder == ""
    assert len(result.blocks) == 1
    block = result.blocks[0]
    assert block.is_valid
    assert block.event is not None
    assert block.event.type == StructuredEventType.PROGRESS
    assert block.event.progress == 40


def test_event_parser_keeps_partial_block_until_completed() -> None:
    parser = EventParserAdapter()

    first = parser.extract_blocks(
        "\n".join(
            [
                "progress noise",
                START_MARKER,
                '{"type":"question","summary":"Need input"',
            ]
        )
    )

    assert first.blocks == []
    assert first.remainder.startswith(START_MARKER)

    second = parser.extract_blocks(first.remainder + ',"question":"Proceed with the schema change?"}\n' + END_MARKER)
    assert second.remainder == ""
    assert len(second.blocks) == 1
    block = second.blocks[0]
    assert block.is_valid
    assert block.event is not None
    assert block.event.type == StructuredEventType.QUESTION
    assert block.event.question == "Proceed with the schema change?"


def test_event_parser_normalizes_legacy_blocks_and_preserves_invalid_payloads() -> None:
    parser = EventParserAdapter()

    result = parser.extract_blocks(
        "\n".join(
            [
                LEGACY_START_MARKER,
                '{"event_type":"session.running","summary":"Booted"}',
                LEGACY_END_MARKER,
                START_MARKER,
                '{"type":"tests_run","summary":"Ran tests","command":"pytest -q","status":"green","exit_code":0}',
                END_MARKER,
            ]
        )
    )

    assert len(result.blocks) == 2

    legacy = result.blocks[0]
    assert legacy.is_valid
    assert legacy.event is not None
    assert legacy.event.type == StructuredEventType.START

    invalid = result.blocks[1]
    assert not invalid.is_valid
    assert invalid.declared_type == "tests_run"
    assert invalid.validation_error is not None
    assert invalid.raw_block.startswith(START_MARKER)
