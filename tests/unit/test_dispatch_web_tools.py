"""Unit tests for search_web and fetch_url supervisor tools."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from kora_v2.graph.dispatch import (
    SUPERVISOR_TOOLS,
    _resolve_auth_context,
    execute_tool,
)
from kora_v2.mcp.results import MCPContentBlock, MCPToolResult
from kora_v2.tools.types import AuthLevel


def _mk_result(text: str, **kwargs: object) -> MCPToolResult:
    """Build a minimal MCPToolResult with a single text block."""
    return MCPToolResult(
        server=str(kwargs.get("server", "test")),
        tool=str(kwargs.get("tool", "test")),
        is_error=bool(kwargs.get("is_error", False)),
        content=[MCPContentBlock(type="text", text=text)],
        raw={},
    )


class TestToolDefinitions:
    """The web tools should appear in SUPERVISOR_TOOLS."""

    def test_search_web_in_tools(self) -> None:
        names = {t["name"] for t in SUPERVISOR_TOOLS}
        assert "search_web" in names

    def test_fetch_url_in_tools(self) -> None:
        names = {t["name"] for t in SUPERVISOR_TOOLS}
        assert "fetch_url" in names

    def test_search_web_schema(self) -> None:
        tool = next(t for t in SUPERVISOR_TOOLS if t["name"] == "search_web")
        props = tool["input_schema"]["properties"]
        assert "query" in props
        assert "count" in props
        assert tool["input_schema"]["required"] == ["query"]

    def test_fetch_url_schema(self) -> None:
        tool = next(t for t in SUPERVISOR_TOOLS if t["name"] == "fetch_url")
        props = tool["input_schema"]["properties"]
        assert "url" in props
        assert "max_chars" in props
        assert tool["input_schema"]["required"] == ["url"]


class TestAuthContext:
    """search_web and fetch_url are ALWAYS_ALLOWED / low risk."""

    def test_search_web_always_allowed(self) -> None:
        level, risk = _resolve_auth_context("search_web", {"query": "hi"})
        assert level == AuthLevel.ALWAYS_ALLOWED
        assert risk == "low"

    def test_fetch_url_always_allowed(self) -> None:
        level, risk = _resolve_auth_context("fetch_url", {"url": "https://x"})
        assert level == AuthLevel.ALWAYS_ALLOWED
        assert risk == "low"


class TestSearchWebUnavailable:
    """Without a configured brave_search MCP, search_web returns an explicit failure dict.

    The silent DuckDuckGo fallback was removed in Task 9. All unavailable-MCP
    paths now return a structured failure with ``degraded: true`` and
    ``next_options: ["browser.open"]`` so the model can acknowledge the failure
    and use an approved alternative.
    """

    @pytest.mark.asyncio
    async def test_no_container_returns_explicit_failure(self) -> None:
        result = await execute_tool(
            "search_web",
            {"query": "weather today", "count": 3},
            container=None,
        )
        parsed = json.loads(result)
        # Must be an explicit failure, NOT a DuckDuckGo fallback result
        assert parsed.get("degraded") is True
        assert parsed.get("results") == []
        assert parsed.get("source") != "fallback"
        assert "browser.open" in parsed.get("next_options", [])

    @pytest.mark.asyncio
    async def test_no_mcp_manager_returns_explicit_failure(self) -> None:
        container = MagicMock()
        container.mcp_manager = None
        container.settings.security.auth_mode = "trust_all"
        container.session_manager = None
        result = await execute_tool(
            "search_web",
            {"query": "news"},
            container=container,
        )
        parsed = json.loads(result)
        assert parsed.get("degraded") is True
        assert "browser.open" in parsed.get("next_options", [])

    @pytest.mark.asyncio
    async def test_brave_search_not_configured_returns_explicit_failure(self) -> None:
        container = MagicMock()
        container.mcp_manager.get_server_info = MagicMock(return_value=None)
        container.settings.security.auth_mode = "trust_all"
        container.session_manager = None
        result = await execute_tool(
            "search_web",
            {"query": "news"},
            container=container,
        )
        parsed = json.loads(result)
        assert parsed.get("degraded") is True
        assert parsed["failed_path"] == "mcp.brave_search.brave_web_search"

    @pytest.mark.asyncio
    async def test_empty_query_returns_error(self) -> None:
        result = await execute_tool("search_web", {"query": "  "}, container=None)
        parsed = json.loads(result)
        assert "error" in parsed


class TestSearchWebWithMCP:
    """When MCP is available, results are parsed from brave web shape."""

    @pytest.mark.asyncio
    async def test_parses_brave_response(self) -> None:
        brave_payload = json.dumps({
            "web": {
                "results": [
                    {
                        "title": "Hello",
                        "url": "https://example.com",
                        "description": "A greeting page.",
                    },
                    {
                        "title": "World",
                        "url": "https://example.org",
                        "description": "Another page.",
                    },
                ]
            }
        })

        container = MagicMock()
        container.mcp_manager.get_server_info = MagicMock(
            return_value=MagicMock(name="brave_info")
        )
        container.mcp_manager.call_tool = AsyncMock(
            return_value=_mk_result(brave_payload, server="brave_search", tool="brave_web_search")
        )
        container.settings.security.auth_mode = "trust_all"
        container.session_manager = None

        result = await execute_tool(
            "search_web",
            {"query": "hello", "count": 2},
            container=container,
        )
        parsed = json.loads(result)
        assert len(parsed["results"]) == 2
        assert parsed["results"][0]["title"] == "Hello"
        assert parsed["results"][0]["url"] == "https://example.com"
        container.mcp_manager.call_tool.assert_awaited_once()


class TestFetchUrlNoFallback:
    """fetch_url returns explicit failure dicts when MCP is unavailable.

    The urllib fallback was removed in Task 9.  All unavailable-MCP paths now
    return a structured failure with ``degraded: true`` and
    ``next_options: ["browser.open"]``.
    """

    @pytest.mark.asyncio
    async def test_no_container_returns_explicit_failure(self) -> None:
        result = await execute_tool(
            "fetch_url",
            {"url": "https://example.com/page", "max_chars": 500},
            container=None,
        )
        parsed = json.loads(result)
        # Must be an explicit failure, NOT a urllib-scraped result
        assert parsed.get("degraded") is True
        assert parsed.get("source") != "urllib"
        assert parsed["failed_path"] == "mcp.fetch.fetch"
        assert "browser.open" in parsed.get("next_options", [])

    @pytest.mark.asyncio
    async def test_missing_url_returns_error(self) -> None:
        result = await execute_tool("fetch_url", {"url": ""}, container=None)
        parsed = json.loads(result)
        assert "error" in parsed
        assert parsed["chars"] == 0

    @pytest.mark.asyncio
    async def test_fetch_server_missing_returns_explicit_failure(self) -> None:
        container = MagicMock()
        container.mcp_manager.get_server_info = MagicMock(return_value=None)
        container.settings.security.auth_mode = "trust_all"
        container.session_manager = None
        result = await execute_tool(
            "fetch_url",
            {"url": "https://nowhere.invalid"},
            container=container,
        )
        parsed = json.loads(result)
        assert parsed.get("degraded") is True
        assert parsed["failed_path"] == "mcp.fetch.fetch"

    @pytest.mark.asyncio
    async def test_uses_mcp_when_available(self) -> None:
        container = MagicMock()
        container.mcp_manager.get_server_info = MagicMock(
            return_value=MagicMock(name="fetch_info")
        )
        container.mcp_manager.call_tool = AsyncMock(
            return_value=_mk_result("Fetched article body text.", server="fetch", tool="fetch")
        )
        container.settings.security.auth_mode = "trust_all"
        container.session_manager = None

        result = await execute_tool(
            "fetch_url",
            {"url": "https://example.com", "max_chars": 1000},
            container=container,
        )
        parsed = json.loads(result)
        assert parsed["source"] == "mcp"
        assert "Fetched article body text" in parsed["content"]
        container.mcp_manager.call_tool.assert_awaited_once_with(
            "fetch", "fetch", {"url": "https://example.com"}
        )


