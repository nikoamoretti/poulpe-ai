#!/usr/bin/env python3
"""
Poulpe Email Bridge — Email sending MCP tools via Resend API.

Free tier: 100 emails/day, 1 custom domain.

Environment variables:
  RESEND_API_KEY — Resend API key (required)
  RESEND_FROM_EMAIL — Default sender email (e.g. "hello@yourdomain.com")
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

RESEND_API = "https://api.resend.com"
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
RESEND_FROM = os.environ.get("RESEND_FROM_EMAIL", "onboarding@resend.dev")

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        headers = {"Authorization": f"Bearer {RESEND_API_KEY}"}
        _client = httpx.AsyncClient(base_url=RESEND_API, headers=headers, timeout=30.0)
    return _client


app = Server("poulpe-email-bridge")


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="send_email",
            description=(
                "Send a single email via Resend. "
                "Use for transactional emails (welcome, receipts) or marketing."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "to": {
                        "type": "string",
                        "description": "Recipient email address.",
                    },
                    "subject": {
                        "type": "string",
                        "description": "Email subject line.",
                    },
                    "html": {
                        "type": "string",
                        "description": "HTML body of the email.",
                    },
                    "text": {
                        "type": "string",
                        "description": "Plain text fallback body.",
                        "default": "",
                    },
                    "from_email": {
                        "type": "string",
                        "description": "Sender email. Defaults to RESEND_FROM_EMAIL env var.",
                        "default": "",
                    },
                },
                "required": ["to", "subject", "html"],
            },
        ),
        Tool(
            name="send_digest",
            description=(
                "Send a daily business digest email to the owner. "
                "Accepts structured data and formats it into a readable email."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "to": {
                        "type": "string",
                        "description": "Owner's email address.",
                    },
                    "business_name": {
                        "type": "string",
                        "description": "Name of the business.",
                    },
                    "metrics": {
                        "type": "object",
                        "description": "Key metrics (revenue, users, etc.).",
                    },
                    "actions_today": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "What agents did today.",
                    },
                    "plan_tomorrow": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "What's planned for tomorrow.",
                    },
                    "needs_approval": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Items needing human approval.",
                    },
                    "dashboard_url": {
                        "type": "string",
                        "description": "URL to the Poulpe dashboard.",
                        "default": "",
                    },
                },
                "required": ["to", "business_name"],
            },
        ),
        Tool(
            name="draft_email",
            description="Draft an email without sending. Returns formatted preview.",
            inputSchema={
                "type": "object",
                "properties": {
                    "to": {"type": "string", "description": "Recipient."},
                    "subject": {"type": "string", "description": "Subject."},
                    "html": {"type": "string", "description": "HTML body."},
                },
                "required": ["to", "subject", "html"],
            },
        ),
    ]


def _build_digest_html(args: dict[str, Any]) -> str:
    """Build a simple HTML email for the daily digest."""
    biz = args.get("business_name", "Business")
    metrics = args.get("metrics", {})
    actions = args.get("actions_today", [])
    plan = args.get("plan_tomorrow", [])
    approvals = args.get("needs_approval", [])
    dashboard = args.get("dashboard_url", "")

    metrics_html = "".join(f"<li><b>{k}:</b> {v}</li>" for k, v in metrics.items())
    actions_html = "".join(f"<li>{a}</li>" for a in actions) or "<li>No actions taken</li>"
    plan_html = "".join(f"<li>{p}</li>" for p in plan) or "<li>No plan set</li>"

    approval_section = ""
    if approvals:
        approval_items = "".join(f"<li>⚠️ {a}</li>" for a in approvals)
        approval_section = f"<h3>Needs Your Approval</h3><ul>{approval_items}</ul>"

    dashboard_link = f'<p><a href="{dashboard}">View Dashboard →</a></p>' if dashboard else ""

    return f"""
    <div style="font-family: -apple-system, sans-serif; max-width: 600px; margin: 0 auto;">
        <h2>{biz} — Daily Digest</h2>
        <h3>Metrics</h3>
        <ul>{metrics_html or '<li>No metrics available</li>'}</ul>
        <h3>What Happened Today</h3>
        <ul>{actions_html}</ul>
        <h3>Tomorrow's Plan</h3>
        <ul>{plan_html}</ul>
        {approval_section}
        {dashboard_link}
        <hr>
        <p style="color: #888; font-size: 12px;">Sent by Poulpe Autonomous Business Agent</p>
    </div>
    """


@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    if name == "draft_email":
        return [
            TextContent(
                type="text",
                text=(
                    f"DRAFT EMAIL\n"
                    f"To: {arguments['to']}\n"
                    f"Subject: {arguments['subject']}\n"
                    f"---\n"
                    f"{arguments['html']}\n"
                    f"---\n"
                    f"Status: Ready for review. Use send_email to deliver."
                ),
            )
        ]

    if not RESEND_API_KEY:
        return [TextContent(type="text", text="Error: RESEND_API_KEY not set")]

    client = _get_client()

    try:
        if name == "send_email":
            body = {
                "from": arguments.get("from_email") or RESEND_FROM,
                "to": [arguments["to"]],
                "subject": arguments["subject"],
                "html": arguments["html"],
            }
            if arguments.get("text"):
                body["text"] = arguments["text"]
            resp = await client.post("/emails", json=body)
            resp.raise_for_status()
            data = resp.json()
            return [TextContent(type="text", text=f"Email sent! ID: {data.get('id')}")]

        elif name == "send_digest":
            html = _build_digest_html(arguments)
            biz = arguments.get("business_name", "Business")
            body = {
                "from": RESEND_FROM,
                "to": [arguments["to"]],
                "subject": f"[{biz}] Daily Digest",
                "html": html,
            }
            resp = await client.post("/emails", json=body)
            resp.raise_for_status()
            data = resp.json()
            return [TextContent(type="text", text=f"Digest sent! ID: {data.get('id')}")]

        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]

    except httpx.HTTPStatusError as e:
        return [TextContent(type="text", text=f"Resend API error: {e.response.status_code} — {e.response.text[:200]}")]
    except Exception as e:
        return [TextContent(type="text", text=f"Error: {type(e).__name__}: {e}")]


async def main() -> None:
    if not RESEND_API_KEY:
        print("WARNING: RESEND_API_KEY not set — send tools will fail", file=sys.stderr)
    print("Poulpe Email Bridge starting", file=sys.stderr)
    async with stdio_server() as (read_stream, write_stream):
        init_options = app.create_initialization_options()
        await app.run(read_stream, write_stream, init_options)


if __name__ == "__main__":
    asyncio.run(main())
