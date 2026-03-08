import json
import re

from app.schemas.event import StructuredEventBlock

START_MARKER = "<<<ORCHESTRATOR_EVENT>>>"
END_MARKER = "<<<END_ORCHESTRATOR_EVENT>>>"
EVENT_BLOCK_PATTERN = re.compile(
    rf"{re.escape(START_MARKER)}\s*(.*?)\s*{re.escape(END_MARKER)}",
    re.DOTALL,
)


class EventParserAdapter:
    """Extract structured event payloads embedded inside session output."""

    def extract_blocks(self, text: str) -> list[StructuredEventBlock]:
        blocks: list[StructuredEventBlock] = []

        for match in EVENT_BLOCK_PATTERN.finditer(text):
            try:
                payload = json.loads(match.group(1))
                blocks.append(StructuredEventBlock.model_validate(payload))
            except json.JSONDecodeError:
                continue

        return blocks
