from __future__ import annotations

from app.adapters.event_parser import EventParserAdapter
from app.runtime.claude_code_exec_worker import contains_structured_complete as claude_contains_complete
from app.runtime.codex_exec_worker import contains_structured_complete as codex_contains_complete


def test_question_event_can_be_normalized_from_details() -> None:
    parser = EventParserAdapter()

    result = parser.extract_blocks(
        '[[EVENT]]{"type":"question","summary":"Need a decision","details":{"question":"Use ALPHA or BETA?","choices":["ALPHA","BETA"]}}[[/EVENT]]'
    )

    assert len(result.blocks) == 1
    block = result.blocks[0]
    assert block.is_valid is True
    assert block.event is not None
    assert block.event.type.value == "question"
    assert block.event.question == "Use ALPHA or BETA?"
    assert block.event.choices == ["ALPHA", "BETA"]


def test_tests_run_event_can_be_normalized_from_details() -> None:
    parser = EventParserAdapter()

    result = parser.extract_blocks(
        '[[EVENT]]{"type":"tests_run","summary":"Verified the workspace","details":{"checks":["cat status.txt","git status --short"],"result":"status.txt contains ALPHA"}}[[/EVENT]]'
    )

    assert len(result.blocks) == 1
    block = result.blocks[0]
    assert block.is_valid is True
    assert block.event is not None
    assert block.event.type.value == "tests_run"
    assert block.event.command == "cat status.txt && git status --short"
    assert block.event.status.value == "passed"
    assert block.event.exit_code == 0


def test_runtime_bridges_detect_structured_complete_blocks() -> None:
    payload = """[[EVENT]]
{"type":"complete","summary":"Done"}
[[/EVENT]]"""

    assert codex_contains_complete(payload) is True
    assert claude_contains_complete(payload) is True
