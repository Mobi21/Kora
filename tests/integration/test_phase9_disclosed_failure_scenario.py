"""Phase 9 disclosed failure scenario — focused dispatch-level integration test.

This is NOT a full supervisor graph test. It exercises the failure → alternative
path contract at the dispatch function level, validating:

1. search_web returns an explicit structured failure when MCP is unavailable,
   containing failed_path, degraded=True, next_options=["browser.open"]

2. fetch_url returns the same structured failure shape when MCP is unavailable

3. The structured failure dict contains the required fields for the supervisor
   prompt's failure-language rendering rules

4. browser.open (capability action) is NOT blocked by policy when the model
   chooses it as a fallback (policy lookup returns allowed)

5. The failure payload can be used to simulate the model's two-step choice:
   search_web fails → model chooses browser.open → not blocked
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from kora_v2.graph.dispatch import (
    _execute_fetch_url,
    _execute_search_web,
    _search_web_mcp_unavailable,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_container_without_mcp() -> MagicMock:
    """Container with no MCP manager (simulates offline / unconfigured)."""
    container = MagicMock()
    container.mcp_manager = None
    settings = MagicMock()
    settings.security.auth_mode = "trust_all"
    container.settings = settings
    return container


def _make_container_with_failing_mcp() -> MagicMock:
    """Container with MCP manager that raises on call_tool."""
    container = MagicMock()

    mcp = MagicMock()

    class _FakeServerInfo:
        pass

    mcp.get_server_info = MagicMock(return_value=_FakeServerInfo())
    mcp.call_tool = AsyncMock(side_effect=RuntimeError("MCP server unreachable"))
    container.mcp_manager = mcp

    settings = MagicMock()
    settings.security.auth_mode = "trust_all"
    container.settings = settings
    return container


def _parse_json_result(result: str) -> dict[str, Any]:
    try:
        return json.loads(result)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"Result is not valid JSON: {result!r}") from exc


# ---------------------------------------------------------------------------
# 1. search_web returns explicit structured failure when MCP unavailable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_web_no_mcp_returns_structured_failure() -> None:
    """When no MCP manager, search_web returns a structured failure dict."""
    container = _make_container_without_mcp()
    result_str = await _execute_search_web({"query": "latest AI news"}, container)
    result = _parse_json_result(result_str)

    assert "error" in result, "Failure result must have 'error' key"
    assert result.get("degraded") is True, "Failure result must have degraded=True"
    assert result.get("recoverable") is True, "Failure result must have recoverable=True"


@pytest.mark.asyncio
async def test_search_web_no_mcp_has_failed_path() -> None:
    """Structured failure must include failed_path for the supervisor prompt."""
    container = _make_container_without_mcp()
    result_str = await _execute_search_web({"query": "test query"}, container)
    result = _parse_json_result(result_str)

    assert "failed_path" in result, "Failure result must have 'failed_path'"
    assert "brave_search" in result["failed_path"], (
        f"failed_path should reference brave_search, got: {result['failed_path']!r}"
    )


@pytest.mark.asyncio
async def test_search_web_no_mcp_has_next_options_browser_open() -> None:
    """Structured failure must include next_options=['browser.open']."""
    container = _make_container_without_mcp()
    result_str = await _execute_search_web({"query": "test query"}, container)
    result = _parse_json_result(result_str)

    next_options = result.get("next_options", [])
    assert "browser.open" in next_options, (
        f"next_options must include 'browser.open', got: {next_options}"
    )


@pytest.mark.asyncio
async def test_search_web_mcp_call_fails_returns_structured_failure() -> None:
    """When MCP call raises, search_web returns structured failure."""
    container = _make_container_with_failing_mcp()
    result_str = await _execute_search_web({"query": "test query"}, container)
    result = _parse_json_result(result_str)

    assert result.get("degraded") is True
    assert "failed_path" in result
    assert result.get("next_options") == ["browser.open"]


# ---------------------------------------------------------------------------
# 2. fetch_url returns the same structured failure shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_url_no_mcp_returns_structured_failure() -> None:
    """When no MCP manager, fetch_url returns a structured failure dict."""
    container = _make_container_without_mcp()
    result_str = await _execute_fetch_url({"url": "https://example.com/"}, container)
    result = _parse_json_result(result_str)

    assert result.get("degraded") is True
    assert result.get("recoverable") is True
    assert "failed_path" in result
    assert "mcp.fetch" in result["failed_path"]


@pytest.mark.asyncio
async def test_fetch_url_no_mcp_has_next_options_browser_open() -> None:
    container = _make_container_without_mcp()
    result_str = await _execute_fetch_url({"url": "https://example.com/"}, container)
    result = _parse_json_result(result_str)

    next_options = result.get("next_options", [])
    assert "browser.open" in next_options


@pytest.mark.asyncio
async def test_fetch_url_mcp_call_fails_returns_structured_failure() -> None:
    container = _make_container_with_failing_mcp()
    # Patch get_server_info for fetch to return info (so it tries call_tool)
    container.mcp_manager.get_server_info = MagicMock(
        side_effect=lambda srv: MagicMock() if srv == "fetch" else None
    )
    result_str = await _execute_fetch_url({"url": "https://example.com/"}, container)
    result = _parse_json_result(result_str)

    assert result.get("degraded") is True
    assert result.get("next_options") == ["browser.open"]


# ---------------------------------------------------------------------------
# 3. The _search_web_mcp_unavailable helper produces the correct shape
# ---------------------------------------------------------------------------


def test_search_web_mcp_unavailable_shape() -> None:
    """_search_web_mcp_unavailable returns the standard failure shape."""
    result_str = _search_web_mcp_unavailable("test query", "test reason")
    result = json.loads(result_str)

    assert result["degraded"] is True
    assert result["recoverable"] is True
    assert "browser.open" in result["next_options"]
    assert "failed_path" in result
    assert result["query"] == "test query"
    assert "results" in result
    assert isinstance(result["results"], list)


def test_search_web_mcp_unavailable_error_contains_reason() -> None:
    result_str = _search_web_mcp_unavailable("q", "specific reason text")
    result = json.loads(result_str)
    assert "specific reason text" in result["error"]


# ---------------------------------------------------------------------------
# 4. browser.open is NOT blocked by workspace policy
# ---------------------------------------------------------------------------


def test_browser_open_not_denied_by_workspace_policy() -> None:
    """browser.open must be allowed regardless of workspace policy."""
    from kora_v2.capabilities.policy import PolicyKey, SessionState
    from kora_v2.capabilities.workspace.policy import build_default_policy

    # Workspace policy should have no opinion on browser.open
    ws_policy = build_default_policy(account="personal")
    key = PolicyKey(capability="browser", action="open")
    session = SessionState(session_id="test")
    decision = ws_policy.evaluate(key, session=session, task=None)

    # browser.open not in workspace policy → default = ALWAYS_ASK → allowed=True
    assert decision.allowed, (
        f"browser.open should be allowed by workspace policy, got allowed={decision.allowed}"
    )


def test_browser_open_allowed_by_browser_policy() -> None:
    """browser.open must be NEVER_ASK in the browser policy."""
    from kora_v2.capabilities.browser.policy import build_browser_policy
    from kora_v2.capabilities.policy import ApprovalMode, PolicyKey, SessionState

    browser_policy = build_browser_policy()
    # The policy uses the full action name "browser.open" not just "open"
    key = PolicyKey(capability="browser", action="browser.open")
    session = SessionState(session_id="test")
    decision = browser_policy.evaluate(key, session=session, task=None)

    assert decision.allowed, "browser.open must be allowed by browser policy"
    assert not decision.requires_prompt, "browser.open must not require a prompt"
    assert decision.mode == ApprovalMode.NEVER_ASK, (
        f"browser.open mode must be NEVER_ASK, got {decision.mode!r}"
    )


# ---------------------------------------------------------------------------
# 5. Simulate two-step: search_web fails → model chooses browser.open
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_step_failure_then_browser_open_not_blocked() -> None:
    """
    Simulate the full failure → alternative path:
    1. Model calls search_web → gets structured failure with next_options=['browser.open']
    2. Model reads next_options and chooses browser.open
    3. browser.open is not blocked by policy
    """
    # Step 1: search_web fails
    container = _make_container_without_mcp()
    search_result_str = await _execute_search_web(
        {"query": "latest news on AI agents"}, container
    )
    search_result = json.loads(search_result_str)

    # Assert the failure payload is correctly formed
    assert search_result["degraded"] is True
    assert "browser.open" in search_result["next_options"]

    # Step 2: Model reads the failure and decides to use browser.open
    # Verify browser.open is not blocked
    chosen_action = search_result["next_options"][0]
    assert chosen_action == "browser.open"

    # Step 3: Confirm browser.open is allowed
    from kora_v2.capabilities.browser.policy import build_browser_policy
    from kora_v2.capabilities.policy import PolicyKey, SessionState

    policy = build_browser_policy()
    key = PolicyKey(capability="browser", action="browser.open")
    decision = policy.evaluate(key, session=SessionState(session_id="s"), task=None)

    assert decision.allowed, (
        "browser.open must be allowed — model's fallback choice must not be blocked"
    )


@pytest.mark.asyncio
async def test_failure_payload_fields_for_supervisor_prompt_rendering() -> None:
    """
    The failure dict must contain all fields the supervisor prompt uses
    to render failure-language acknowledgements.
    """
    container = _make_container_without_mcp()
    result_str = await _execute_search_web({"query": "news"}, container)
    result = json.loads(result_str)

    # These are the fields the supervisor prompt's failure-language rules use
    required_keys_for_rendering = {
        "error",      # human-readable error message
        "failed_path",  # which MCP path failed (for logging / context)
        "degraded",   # tells model the system is degraded
        "recoverable",  # tells model whether to suggest retry
        "next_options",  # what the model should try next
    }
    missing = required_keys_for_rendering - set(result.keys())
    assert not missing, (
        f"Failure payload missing keys needed for prompt rendering: {missing}\n"
        f"Got: {list(result.keys())}"
    )
