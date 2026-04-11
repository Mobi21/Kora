"""Tests verifying that search_web and fetch_url no longer silently fall back.

After Task 9, both tools return explicit structured failure dicts when the MCP
path is unavailable or fails.  DuckDuckGo and urllib fallbacks are gone.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from kora_v2.graph.dispatch import execute_tool
from kora_v2.mcp.results import MCPContentBlock, MCPToolResult


def _mk_result(text: str, **kwargs: object) -> MCPToolResult:
    """Build a minimal MCPToolResult with a single text block."""
    return MCPToolResult(
        server=str(kwargs.get("server", "test")),
        tool=str(kwargs.get("tool", "test")),
        is_error=bool(kwargs.get("is_error", False)),
        content=[MCPContentBlock(type="text", text=text)],
        raw={},
    )


def _make_container(*, mcp_manager: object = None) -> MagicMock:
    container = MagicMock()
    container.mcp_manager = mcp_manager
    container.settings.security.auth_mode = "trust_all"
    container.session_manager = None
    return container


class TestSearchWebNoFallback:
    """search_web must return an explicit failure dict when MCP is unavailable."""

    @pytest.mark.asyncio
    async def test_no_container_returns_explicit_failure(self) -> None:
        result = await execute_tool(
            "search_web",
            {"query": "weather today", "count": 3},
            container=None,
        )
        parsed = json.loads(result)
        # Must NOT be a list of search results from DuckDuckGo
        assert parsed.get("degraded") is True
        assert parsed.get("results") == []
        assert "failed_path" in parsed
        assert parsed["failed_path"] == "mcp.brave_search.brave_web_search"
        # next_options must suggest browser.open as alternative
        assert "browser.open" in parsed.get("next_options", [])
        # Must not have a "source": "fallback" field (old behaviour)
        assert parsed.get("source") != "fallback"

    @pytest.mark.asyncio
    async def test_no_mcp_manager_returns_explicit_failure(self) -> None:
        container = _make_container(mcp_manager=None)
        result = await execute_tool(
            "search_web",
            {"query": "news"},
            container=container,
        )
        parsed = json.loads(result)
        assert parsed.get("degraded") is True
        assert parsed.get("recoverable") is True
        assert "browser.open" in parsed.get("next_options", [])

    @pytest.mark.asyncio
    async def test_brave_search_server_missing_returns_explicit_failure(self) -> None:
        mcp = MagicMock()
        mcp.get_server_info = MagicMock(return_value=None)
        container = _make_container(mcp_manager=mcp)
        result = await execute_tool(
            "search_web",
            {"query": "latest AI news"},
            container=container,
        )
        parsed = json.loads(result)
        assert parsed.get("degraded") is True
        assert parsed["failed_path"] == "mcp.brave_search.brave_web_search"
        assert "browser.open" in parsed.get("next_options", [])

    @pytest.mark.asyncio
    async def test_mcp_call_raises_returns_explicit_failure(self) -> None:
        mcp = MagicMock()
        mcp.get_server_info = MagicMock(return_value=MagicMock(name="brave_info"))
        mcp.call_tool = AsyncMock(side_effect=RuntimeError("connection reset"))
        container = _make_container(mcp_manager=mcp)
        result = await execute_tool(
            "search_web",
            {"query": "python news"},
            container=container,
        )
        parsed = json.loads(result)
        assert parsed.get("degraded") is True
        assert "browser.open" in parsed.get("next_options", [])
        # Error message should reference the original exception
        assert "connection reset" in parsed.get("error", "")

    @pytest.mark.asyncio
    async def test_successful_brave_search_path_still_works(self) -> None:
        """The happy path must remain unaffected."""
        brave_payload = json.dumps({
            "web": {
                "results": [
                    {
                        "title": "Python 3.13 Released",
                        "url": "https://python.org/news",
                        "description": "New Python release.",
                    },
                ]
            }
        })
        mcp = MagicMock()
        mcp.get_server_info = MagicMock(return_value=MagicMock(name="brave_info"))
        mcp.call_tool = AsyncMock(
            return_value=_mk_result(brave_payload, server="brave_search", tool="brave_web_search")
        )
        container = _make_container(mcp_manager=mcp)
        result = await execute_tool(
            "search_web",
            {"query": "python 3.13", "count": 1},
            container=container,
        )
        parsed = json.loads(result)
        # Success path: has results, no degraded flag
        assert len(parsed["results"]) == 1
        assert parsed["results"][0]["title"] == "Python 3.13 Released"
        assert "degraded" not in parsed


class TestFetchUrlNoFallback:
    """fetch_url must return an explicit failure dict when MCP is unavailable."""

    @pytest.mark.asyncio
    async def test_no_container_returns_explicit_failure(self) -> None:
        result = await execute_tool(
            "fetch_url",
            {"url": "https://example.com/page"},
            container=None,
        )
        parsed = json.loads(result)
        assert parsed.get("degraded") is True
        assert parsed.get("recoverable") is True
        assert parsed["failed_path"] == "mcp.fetch.fetch"
        assert "browser.open" in parsed.get("next_options", [])
        # Must NOT have "source": "urllib" (old behaviour)
        assert parsed.get("source") != "urllib"

    @pytest.mark.asyncio
    async def test_no_mcp_manager_returns_explicit_failure(self) -> None:
        container = _make_container(mcp_manager=None)
        result = await execute_tool(
            "fetch_url",
            {"url": "https://example.com"},
            container=container,
        )
        parsed = json.loads(result)
        assert parsed.get("degraded") is True
        assert "browser.open" in parsed.get("next_options", [])

    @pytest.mark.asyncio
    async def test_fetch_server_missing_returns_explicit_failure(self) -> None:
        mcp = MagicMock()
        mcp.get_server_info = MagicMock(return_value=None)
        container = _make_container(mcp_manager=mcp)
        result = await execute_tool(
            "fetch_url",
            {"url": "https://example.com"},
            container=container,
        )
        parsed = json.loads(result)
        assert parsed.get("degraded") is True
        assert parsed["failed_path"] == "mcp.fetch.fetch"

    @pytest.mark.asyncio
    async def test_mcp_fetch_call_raises_returns_explicit_failure(self) -> None:
        mcp = MagicMock()
        mcp.get_server_info = MagicMock(return_value=MagicMock(name="fetch_info"))
        mcp.call_tool = AsyncMock(side_effect=OSError("timeout"))
        container = _make_container(mcp_manager=mcp)
        result = await execute_tool(
            "fetch_url",
            {"url": "https://example.com"},
            container=container,
        )
        parsed = json.loads(result)
        assert parsed.get("degraded") is True
        assert "timeout" in parsed.get("error", "")
        assert "browser.open" in parsed.get("next_options", [])

    @pytest.mark.asyncio
    async def test_missing_url_still_returns_error(self) -> None:
        result = await execute_tool("fetch_url", {"url": ""}, container=None)
        parsed = json.loads(result)
        assert "error" in parsed
        assert parsed["chars"] == 0

    @pytest.mark.asyncio
    async def test_successful_mcp_fetch_path_still_works(self) -> None:
        """The happy path must remain unaffected."""
        mcp = MagicMock()
        mcp.get_server_info = MagicMock(return_value=MagicMock(name="fetch_info"))
        mcp.call_tool = AsyncMock(
            return_value=_mk_result("Article body text here.", server="fetch", tool="fetch")
        )
        container = _make_container(mcp_manager=mcp)
        result = await execute_tool(
            "fetch_url",
            {"url": "https://example.com", "max_chars": 500},
            container=container,
        )
        parsed = json.loads(result)
        assert parsed["source"] == "mcp"
        assert "Article body text" in parsed["content"]
        assert "degraded" not in parsed
