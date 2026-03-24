#!/usr/bin/env python3
"""
Poulpe Research Bridge — Web search and competitor analysis MCP tools.

Provides research capabilities for the Business Research Agent.
Uses DuckDuckGo search (no API key needed) and basic web scraping.
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

USER_AGENT = "Mozilla/5.0 (compatible; PoulpeResearchBot/1.0)"

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT},
            timeout=30.0,
            follow_redirects=True,
        )
    return _client


app = Server("poulpe-research-bridge")


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="web_search",
            description=(
                "Search the web using DuckDuckGo. Returns titles, URLs, and snippets. "
                "Use this to discover market opportunities, competitors, and trends."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query.",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Max results to return.",
                        "default": 10,
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="fetch_page_text",
            description=(
                "Fetch a web page and return its text content (stripped of HTML). "
                "Use for reading landing pages, blog posts, or product descriptions."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "URL to fetch.",
                    },
                    "max_length": {
                        "type": "integer",
                        "description": "Max characters to return.",
                        "default": 5000,
                    },
                },
                "required": ["url"],
            },
        ),
        Tool(
            name="analyze_competitor",
            description=(
                "Analyze a competitor's website. Returns page title, meta description, "
                "visible text summary, and basic structure."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Competitor's website URL.",
                    },
                },
                "required": ["url"],
            },
        ),
    ]


async def _ddg_search(query: str, max_results: int = 10) -> list[dict[str, str]]:
    """Search DuckDuckGo HTML and extract results."""
    client = _get_client()
    resp = await client.get(
        "https://html.duckduckgo.com/html/",
        params={"q": query},
    )
    resp.raise_for_status()
    text = resp.text

    results: list[dict[str, str]] = []
    # Simple extraction from DDG HTML results
    import re

    for match in re.finditer(
        r'<a rel="nofollow" class="result__a" href="([^"]+)"[^>]*>(.*?)</a>.*?'
        r'<a class="result__snippet"[^>]*>(.*?)</a>',
        text,
        re.DOTALL,
    ):
        if len(results) >= max_results:
            break
        url = match.group(1)
        title = re.sub(r"<[^>]+>", "", match.group(2)).strip()
        snippet = re.sub(r"<[^>]+>", "", match.group(3)).strip()
        results.append({"url": url, "title": title, "snippet": snippet})

    return results


def _extract_text(html: str, max_length: int = 5000) -> str:
    """Extract visible text from HTML, stripping tags."""
    import re

    # Remove script/style
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
    # Remove tags
    text = re.sub(r"<[^>]+>", " ", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_length]


def _extract_meta(html: str) -> dict[str, str]:
    """Extract title and meta description from HTML."""
    import re

    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.DOTALL | re.IGNORECASE)
    title = re.sub(r"<[^>]+>", "", title_match.group(1)).strip() if title_match else ""

    desc_match = re.search(
        r'<meta[^>]*name=["\']description["\'][^>]*content=["\']([^"\']*)["\']',
        html,
        re.IGNORECASE,
    )
    if not desc_match:
        desc_match = re.search(
            r'<meta[^>]*content=["\']([^"\']*)["\'][^>]*name=["\']description["\']',
            html,
            re.IGNORECASE,
        )
    description = desc_match.group(1).strip() if desc_match else ""

    return {"title": title, "description": description}


@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    client = _get_client()

    try:
        if name == "web_search":
            results = await _ddg_search(
                arguments["query"],
                arguments.get("max_results", 10),
            )
            if not results:
                return [TextContent(type="text", text="No results found.")]
            lines = []
            for i, r in enumerate(results, 1):
                lines.append(f"{i}. {r['title']}\n   {r['url']}\n   {r['snippet']}")
            return [TextContent(type="text", text="\n\n".join(lines))]

        elif name == "fetch_page_text":
            resp = await client.get(arguments["url"])
            resp.raise_for_status()
            max_len = arguments.get("max_length", 5000)
            text = _extract_text(resp.text, max_len)
            return [TextContent(type="text", text=text)]

        elif name == "analyze_competitor":
            resp = await client.get(arguments["url"])
            resp.raise_for_status()
            meta = _extract_meta(resp.text)
            text = _extract_text(resp.text, 3000)
            return [
                TextContent(
                    type="text",
                    text=(
                        f"Title: {meta['title']}\n"
                        f"Description: {meta['description']}\n"
                        f"---\n"
                        f"Page content (first 3000 chars):\n{text}"
                    ),
                )
            ]

        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]

    except httpx.HTTPStatusError as e:
        return [TextContent(type="text", text=f"HTTP error: {e.response.status_code} — {e.response.text[:200]}")]
    except Exception as e:
        return [TextContent(type="text", text=f"Error: {type(e).__name__}: {e}")]


async def main() -> None:
    print("Poulpe Research Bridge starting", file=sys.stderr)
    async with stdio_server() as (read_stream, write_stream):
        init_options = app.create_initialization_options()
        await app.run(read_stream, write_stream, init_options)


if __name__ == "__main__":
    asyncio.run(main())
