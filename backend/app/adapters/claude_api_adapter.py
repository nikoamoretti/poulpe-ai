"""Lightweight Claude API adapter for reasoning-only agents (research, marketing).

Unlike ClaudeCodeLocalAdapter which spawns a full Claude Code process with
terminal access, this adapter calls the Anthropic SDK directly. It's cheaper
and faster for agents that don't need to execute code.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Default model for reasoning-only agents
DEFAULT_MODEL = "claude-sonnet-4-20250514"


@dataclass
class ClaudeAPIResponse:
    """Response from a Claude API call."""

    content: str
    model: str
    input_tokens: int
    output_tokens: int
    stop_reason: str
    events: list[dict[str, Any]] = field(default_factory=list)


_EVENT_PATTERN = re.compile(
    r"\[\[EVENT\]\]\s*(\{.*?\})\s*\[\[/EVENT\]\]",
    re.DOTALL,
)


def _extract_events(text: str) -> list[dict[str, Any]]:
    """Extract [[EVENT]] JSON blocks from text."""
    events: list[dict[str, Any]] = []
    for match in _EVENT_PATTERN.finditer(text):
        try:
            events.append(json.loads(match.group(1)))
        except json.JSONDecodeError:
            logger.warning("Failed to parse event JSON: %s", match.group(1)[:200])
    return events


class ClaudeAPIAdapter:
    """Calls the Anthropic Messages API for reasoning-only tasks.

    Usage:
        adapter = ClaudeAPIAdapter()
        response = adapter.call(system_prompt, user_message)
        # response.events contains parsed [[EVENT]] blocks
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        max_tokens: int = 4096,
    ) -> None:
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.model = model
        self.max_tokens = max_tokens
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                import anthropic
            except ImportError as exc:
                raise RuntimeError(
                    "anthropic package required for ClaudeAPIAdapter. "
                    "Install with: pip install anthropic"
                ) from exc
            self._client = anthropic.Anthropic(api_key=self.api_key)
        return self._client

    def call(
        self,
        system_prompt: str,
        user_message: str,
        *,
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> ClaudeAPIResponse:
        """Make a single API call and return the response with parsed events."""
        client = self._get_client()
        response = client.messages.create(
            model=model or self.model,
            max_tokens=max_tokens or self.max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )

        content = ""
        for block in response.content:
            if hasattr(block, "text"):
                content += block.text

        events = _extract_events(content)

        return ClaudeAPIResponse(
            content=content,
            model=response.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            stop_reason=response.stop_reason,
            events=events,
        )

    def call_for_plan(
        self,
        system_prompt: str,
        user_message: str,
        *,
        model: str | None = None,
    ) -> dict[str, Any] | None:
        """Make an API call and return the first 'complete' event's data, or None."""
        response = self.call(system_prompt, user_message, model=model)
        for event in response.events:
            if event.get("type") == "complete":
                return event
        return None
