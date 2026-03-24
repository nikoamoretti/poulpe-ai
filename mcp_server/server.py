#!/usr/bin/env python3
"""
Poulpe MCP Bridge — lets Codex workers communicate with the manager.

Workers call `ask_manager()` as a blocking MCP tool. Under the hood:
  1. POST /api/v1/checkpoints  → creates an OPEN checkpoint
  2. Poll GET /api/v1/checkpoints/{id}/poll every few seconds
  3. The portfolio automation loop detects the open checkpoint,
     launches a Claude Code manager turn, and resolves it
  4. Once status != "open", return the manager's response to the worker

Environment variables (set by the session launcher):
  POULPE_API_URL      – e.g. http://localhost:8000/api/v1
  POULPE_PROJECT_ID   – UUID of the project this worker belongs to
  POULPE_SESSION_ID   – UUID of this worker session (optional)
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

# ---------------------------------------------------------------------------
# Config from env
# ---------------------------------------------------------------------------
API_URL = os.environ.get("POULPE_API_URL", "http://localhost:8000/api/v1")
PROJECT_ID = os.environ.get("POULPE_PROJECT_ID", "")
SESSION_ID = os.environ.get("POULPE_SESSION_ID")

POLL_INTERVAL = float(os.environ.get("POULPE_POLL_INTERVAL", "3"))
POLL_TIMEOUT = float(os.environ.get("POULPE_POLL_TIMEOUT", "300"))  # 5 min max

# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------
_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(base_url=API_URL, timeout=30.0)
    return _client


# ---------------------------------------------------------------------------
# Checkpoint helpers
# ---------------------------------------------------------------------------
async def _create_checkpoint(kind: str, summary: str, details: dict[str, Any] | None = None) -> dict:
    client = _get_client()
    body: dict[str, Any] = {
        "project_id": PROJECT_ID,
        "kind": kind,
        "summary": summary,
        "details": details or {},
    }
    if SESSION_ID:
        body["session_id"] = SESSION_ID

    resp = await client.post("/checkpoints", json=body)
    resp.raise_for_status()
    return resp.json()


async def _poll_until_resolved(checkpoint_id: str) -> dict:
    """Poll until the checkpoint leaves 'open' status."""
    client = _get_client()
    elapsed = 0.0
    while elapsed < POLL_TIMEOUT:
        resp = await client.get(f"/checkpoints/{checkpoint_id}/poll")
        resp.raise_for_status()
        data = resp.json()
        if data["status"] != "open":
            return data
        await asyncio.sleep(POLL_INTERVAL)
        elapsed += POLL_INTERVAL

    return {"error": f"Timed out after {POLL_TIMEOUT}s waiting for manager response"}


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------
app = Server("poulpe-bridge")


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="ask_manager",
            description=(
                "Ask the project manager a question and wait for their response. "
                "Use this when you need guidance, clarification, or a decision. "
                "The call blocks until the manager answers (up to 5 minutes)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "Your question for the manager.",
                    },
                    "context": {
                        "type": "string",
                        "description": "Optional context or details to help the manager answer.",
                        "default": "",
                    },
                },
                "required": ["question"],
            },
        ),
        Tool(
            name="report_progress",
            description=(
                "Report progress to the manager without blocking. "
                "Use this to keep the manager informed about milestones, "
                "completed subtasks, or status updates."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "Brief summary of what you accomplished or where you are.",
                    },
                    "details": {
                        "type": "object",
                        "description": "Optional structured details (files changed, tests passed, etc).",
                        "default": {},
                    },
                },
                "required": ["summary"],
            },
        ),
        Tool(
            name="report_blocked",
            description=(
                "Report that you are blocked and need help. "
                "The call blocks until the manager provides guidance (up to 5 minutes)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "What is blocking you.",
                    },
                    "details": {
                        "type": "string",
                        "description": "Optional additional context about the blocker.",
                        "default": "",
                    },
                },
                "required": ["reason"],
            },
        ),
        Tool(
            name="claim_completion",
            description=(
                "Declare that your task is complete. "
                "The manager will review and either approve or request changes. "
                "The call blocks until the manager responds."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "Summary of what was accomplished.",
                    },
                    "details": {
                        "type": "object",
                        "description": "Optional structured details (files changed, test results, etc).",
                        "default": {},
                    },
                },
                "required": ["summary"],
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    if not PROJECT_ID:
        return [TextContent(type="text", text="Error: POULPE_PROJECT_ID not set")]

    try:
        if name == "ask_manager":
            checkpoint = await _create_checkpoint(
                kind="question",
                summary=arguments["question"],
                details={"context": arguments.get("context", "")},
            )
            result = await _poll_until_resolved(checkpoint["id"])
            if "error" in result:
                return [TextContent(type="text", text=f"Error: {result['error']}")]
            msg = result.get("response_message") or "Manager resolved the checkpoint without a message."
            return [TextContent(type="text", text=msg)]

        elif name == "report_progress":
            await _create_checkpoint(
                kind="completion",  # progress is a soft completion checkpoint
                summary=f"[Progress] {arguments['summary']}",
                details=arguments.get("details", {}),
            )
            return [TextContent(type="text", text="Progress reported to manager.")]

        elif name == "report_blocked":
            checkpoint = await _create_checkpoint(
                kind="blocked",
                summary=arguments["reason"],
                details={"context": arguments.get("details", "")},
            )
            result = await _poll_until_resolved(checkpoint["id"])
            if "error" in result:
                return [TextContent(type="text", text=f"Error: {result['error']}")]
            msg = result.get("response_message") or "Manager resolved the blocker without a message."
            return [TextContent(type="text", text=msg)]

        elif name == "claim_completion":
            checkpoint = await _create_checkpoint(
                kind="completion",
                summary=arguments["summary"],
                details=arguments.get("details", {}),
            )
            result = await _poll_until_resolved(checkpoint["id"])
            if "error" in result:
                return [TextContent(type="text", text=f"Error: {result['error']}")]
            resolution = result.get("resolution", "unknown")
            msg = result.get("response_message") or ""
            return [TextContent(type="text", text=f"Manager decision: {resolution}. {msg}".strip())]

        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]

    except httpx.HTTPStatusError as e:
        return [TextContent(type="text", text=f"API error: {e.response.status_code} — {e.response.text}")]
    except Exception as e:
        return [TextContent(type="text", text=f"Error: {type(e).__name__}: {e}")]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
async def main():
    if not PROJECT_ID:
        print("WARNING: POULPE_PROJECT_ID not set — tools will fail", file=sys.stderr)
    print(f"Poulpe MCP Bridge starting (api={API_URL}, project={PROJECT_ID})", file=sys.stderr)
    async with stdio_server() as (read_stream, write_stream):
        init_options = app.create_initialization_options()
        await app.run(read_stream, write_stream, init_options)


if __name__ == "__main__":
    asyncio.run(main())
