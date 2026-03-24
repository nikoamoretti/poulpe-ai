#!/usr/bin/env python3
"""
Poulpe Deploy Bridge — Vercel deployment MCP tools for business engineer agents.

Provides tools to deploy projects to Vercel, check deployment status,
and retrieve live URLs.

Environment variables:
  VERCEL_TOKEN — Vercel API token (required)
  VERCEL_TEAM_ID — Optional Vercel team/org ID
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

VERCEL_API = "https://api.vercel.com"
VERCEL_TOKEN = os.environ.get("VERCEL_TOKEN", "")
VERCEL_TEAM_ID = os.environ.get("VERCEL_TEAM_ID")

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        headers = {"Authorization": f"Bearer {VERCEL_TOKEN}"}
        _client = httpx.AsyncClient(base_url=VERCEL_API, headers=headers, timeout=60.0)
    return _client


def _team_params() -> dict[str, str]:
    if VERCEL_TEAM_ID:
        return {"teamId": VERCEL_TEAM_ID}
    return {}


app = Server("poulpe-deploy-bridge")


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="deploy_to_vercel",
            description=(
                "Deploy a project directory to Vercel. "
                "Triggers a new deployment via the Vercel CLI or API. "
                "Returns the deployment ID and URL."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "project_name": {
                        "type": "string",
                        "description": "Vercel project name to deploy to.",
                    },
                    "git_repo": {
                        "type": "string",
                        "description": "GitHub repo in 'owner/repo' format.",
                    },
                    "git_branch": {
                        "type": "string",
                        "description": "Branch to deploy from.",
                        "default": "main",
                    },
                },
                "required": ["project_name"],
            },
        ),
        Tool(
            name="get_deploy_status",
            description="Check the status of a Vercel deployment.",
            inputSchema={
                "type": "object",
                "properties": {
                    "deployment_id": {
                        "type": "string",
                        "description": "The Vercel deployment ID.",
                    },
                },
                "required": ["deployment_id"],
            },
        ),
        Tool(
            name="get_deploy_url",
            description="Get the live URL for a Vercel project.",
            inputSchema={
                "type": "object",
                "properties": {
                    "project_name": {
                        "type": "string",
                        "description": "The Vercel project name.",
                    },
                },
                "required": ["project_name"],
            },
        ),
        Tool(
            name="list_deployments",
            description="List recent deployments for a Vercel project.",
            inputSchema={
                "type": "object",
                "properties": {
                    "project_name": {
                        "type": "string",
                        "description": "The Vercel project name.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max deployments to return.",
                        "default": 5,
                    },
                },
                "required": ["project_name"],
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    if not VERCEL_TOKEN:
        return [TextContent(type="text", text="Error: VERCEL_TOKEN not set")]

    client = _get_client()

    try:
        if name == "deploy_to_vercel":
            project_name = arguments["project_name"]
            git_repo = arguments.get("git_repo")
            git_branch = arguments.get("git_branch", "main")

            if git_repo:
                # Create deployment from git
                body: dict[str, Any] = {
                    "name": project_name,
                    "gitSource": {
                        "type": "github",
                        "repo": git_repo,
                        "ref": git_branch,
                    },
                }
                resp = await client.post(
                    "/v13/deployments",
                    json=body,
                    params=_team_params(),
                )
                resp.raise_for_status()
                data = resp.json()
                return [
                    TextContent(
                        type="text",
                        text=(
                            f"Deployment created!\n"
                            f"ID: {data.get('id')}\n"
                            f"URL: https://{data.get('url')}\n"
                            f"State: {data.get('readyState', 'BUILDING')}"
                        ),
                    )
                ]
            else:
                return [
                    TextContent(
                        type="text",
                        text=(
                            "To deploy, push your code to GitHub and provide git_repo parameter. "
                            "Example: git_repo='owner/repo-name'"
                        ),
                    )
                ]

        elif name == "get_deploy_status":
            deployment_id = arguments["deployment_id"]
            resp = await client.get(
                f"/v13/deployments/{deployment_id}",
                params=_team_params(),
            )
            resp.raise_for_status()
            data = resp.json()
            return [
                TextContent(
                    type="text",
                    text=(
                        f"Deployment: {data.get('id')}\n"
                        f"URL: https://{data.get('url')}\n"
                        f"State: {data.get('readyState')}\n"
                        f"Created: {data.get('createdAt')}"
                    ),
                )
            ]

        elif name == "get_deploy_url":
            project_name = arguments["project_name"]
            resp = await client.get(
                f"/v9/projects/{project_name}",
                params=_team_params(),
            )
            resp.raise_for_status()
            data = resp.json()
            aliases = data.get("alias", [])
            latest = data.get("latestDeployments", [{}])
            url = aliases[0]["domain"] if aliases else (
                latest[0].get("url") if latest else "no deployment found"
            )
            return [TextContent(type="text", text=f"Live URL: https://{url}")]

        elif name == "list_deployments":
            project_name = arguments["project_name"]
            limit = arguments.get("limit", 5)
            resp = await client.get(
                "/v6/deployments",
                params={**_team_params(), "projectId": project_name, "limit": str(limit)},
            )
            resp.raise_for_status()
            data = resp.json()
            deployments = data.get("deployments", [])
            if not deployments:
                return [TextContent(type="text", text="No deployments found.")]
            lines = []
            for d in deployments:
                lines.append(
                    f"- {d.get('uid')} | https://{d.get('url')} | "
                    f"{d.get('state')} | {d.get('created')}"
                )
            return [TextContent(type="text", text="\n".join(lines))]

        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]

    except httpx.HTTPStatusError as e:
        return [TextContent(type="text", text=f"Vercel API error: {e.response.status_code} — {e.response.text}")]
    except Exception as e:
        return [TextContent(type="text", text=f"Error: {type(e).__name__}: {e}")]


async def main() -> None:
    if not VERCEL_TOKEN:
        print("WARNING: VERCEL_TOKEN not set — tools will fail", file=sys.stderr)
    print("Poulpe Deploy Bridge starting", file=sys.stderr)
    async with stdio_server() as (read_stream, write_stream):
        init_options = app.create_initialization_options()
        await app.run(read_stream, write_stream, init_options)


if __name__ == "__main__":
    asyncio.run(main())
