#!/usr/bin/env python3
"""
Poulpe Social Bridge — Twitter/X posting MCP tools for marketing agents.

Environment variables:
  TWITTER_BEARER_TOKEN — Twitter API v2 bearer token
  TWITTER_API_KEY — Twitter API key (OAuth 1.0a)
  TWITTER_API_SECRET — Twitter API secret
  TWITTER_ACCESS_TOKEN — User access token
  TWITTER_ACCESS_SECRET — User access secret
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

TWITTER_API = "https://api.twitter.com"
TWITTER_BEARER = os.environ.get("TWITTER_BEARER_TOKEN", "")

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        headers = {"Authorization": f"Bearer {TWITTER_BEARER}"}
        _client = httpx.AsyncClient(base_url=TWITTER_API, headers=headers, timeout=30.0)
    return _client


app = Server("poulpe-social-bridge")


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="post_tweet",
            description=(
                "Post a tweet to Twitter/X. Requires PUBLISH_APPROVAL checkpoint "
                "before actually posting. Returns the tweet URL on success."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Tweet text (max 280 characters).",
                    },
                },
                "required": ["text"],
            },
        ),
        Tool(
            name="post_thread",
            description=(
                "Post a thread of tweets. Each item in the list becomes one tweet, "
                "chained as replies."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "tweets": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of tweet texts in order.",
                    },
                },
                "required": ["tweets"],
            },
        ),
        Tool(
            name="draft_tweet",
            description=(
                "Draft a tweet without posting. Returns the text for review. "
                "Use this to prepare content for human approval."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Draft tweet text.",
                    },
                    "purpose": {
                        "type": "string",
                        "description": "Why this tweet (product launch, update, engagement).",
                    },
                },
                "required": ["text"],
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    if name == "draft_tweet":
        text = arguments["text"]
        purpose = arguments.get("purpose", "general")
        char_count = len(text)
        return [
            TextContent(
                type="text",
                text=(
                    f"DRAFT TWEET ({char_count}/280 chars)\n"
                    f"Purpose: {purpose}\n"
                    f"---\n"
                    f"{text}\n"
                    f"---\n"
                    f"Status: Ready for review. Use post_tweet to publish."
                ),
            )
        ]

    if not TWITTER_BEARER:
        return [TextContent(type="text", text="Error: TWITTER_BEARER_TOKEN not set")]

    client = _get_client()

    try:
        if name == "post_tweet":
            text = arguments["text"]
            if len(text) > 280:
                return [TextContent(type="text", text=f"Error: Tweet too long ({len(text)}/280 chars)")]
            resp = await client.post("/2/tweets", json={"text": text})
            resp.raise_for_status()
            data = resp.json()
            tweet_id = data.get("data", {}).get("id")
            return [
                TextContent(
                    type="text",
                    text=f"Tweet posted! ID: {tweet_id}\nURL: https://twitter.com/i/web/status/{tweet_id}",
                )
            ]

        elif name == "post_thread":
            tweets = arguments["tweets"]
            if not tweets:
                return [TextContent(type="text", text="Error: No tweets provided")]

            posted: list[str] = []
            reply_to: str | None = None

            for tweet_text in tweets:
                body: dict[str, Any] = {"text": tweet_text}
                if reply_to:
                    body["reply"] = {"in_reply_to_tweet_id": reply_to}
                resp = await client.post("/2/tweets", json=body)
                resp.raise_for_status()
                data = resp.json()
                tweet_id = data.get("data", {}).get("id", "")
                posted.append(tweet_id)
                reply_to = tweet_id

            urls = [f"https://twitter.com/i/web/status/{tid}" for tid in posted]
            return [
                TextContent(
                    type="text",
                    text=f"Thread posted! {len(posted)} tweets.\n" + "\n".join(urls),
                )
            ]

        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]

    except httpx.HTTPStatusError as e:
        return [TextContent(type="text", text=f"Twitter API error: {e.response.status_code} — {e.response.text[:200]}")]
    except Exception as e:
        return [TextContent(type="text", text=f"Error: {type(e).__name__}: {e}")]


async def main() -> None:
    if not TWITTER_BEARER:
        print("WARNING: TWITTER_BEARER_TOKEN not set — post tools will fail", file=sys.stderr)
    print("Poulpe Social Bridge starting", file=sys.stderr)
    async with stdio_server() as (read_stream, write_stream):
        init_options = app.create_initialization_options()
        await app.run(read_stream, write_stream, init_options)


if __name__ == "__main__":
    asyncio.run(main())
