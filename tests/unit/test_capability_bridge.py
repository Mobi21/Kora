"""Unit tests for kora_v2.graph.capability_bridge."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kora_v2.capabilities.base import StructuredFailure
from kora_v2.graph.capability_bridge import (
    _reset_action_registry,
    collect_capability_tools,
    execute_capability_action,
)


@pytest.fixture(autouse=True)
def reset_registry() -> None:
    """Reset the cached action registry before each test."""
    _reset_action_registry()
    yield
    _reset_action_registry()


class TestCollectCapabilityTools:
    """collect_capability_tools() builds a merged tool list from all packs."""

    def test_returns_nonempty_list(self) -> None:
        tools = collect_capability_tools()
        assert isinstance(tools, list)
        assert len(tools) > 0

    def test_includes_workspace_gmail_search(self) -> None:
        names = {t["name"] for t in collect_capability_tools()}
        assert "workspace.gmail.search" in names

    def test_includes_browser_open(self) -> None:
        names = {t["name"] for t in collect_capability_tools()}
        assert "browser.open" in names

    def test_includes_vault_write_note(self) -> None:
        names = {t["name"] for t in collect_capability_tools()}
        assert "vault.write_note" in names

    def test_each_tool_has_valid_input_schema(self) -> None:
        for tool in collect_capability_tools():
            schema = tool["input_schema"]
            assert isinstance(schema, dict), f"Bad schema for {tool['name']}"
            assert schema.get("type") == "object", f"Schema type not 'object' for {tool['name']}"
            assert "properties" in schema, f"No properties for {tool['name']}"

    def test_each_tool_has_name_and_description(self) -> None:
        for tool in collect_capability_tools():
            assert "name" in tool
            assert "description" in tool

    def test_internal_metadata_present(self) -> None:
        tools = collect_capability_tools()
        for tool in tools:
            assert "_capability" in tool
            assert "_requires_approval" in tool
            assert "_read_only" in tool

    def test_container_arg_accepted(self) -> None:
        container = MagicMock()
        tools = collect_capability_tools(container)
        assert isinstance(tools, list)
        assert len(tools) > 0

    def test_no_duplicate_names(self) -> None:
        tools = collect_capability_tools()
        names = [t["name"] for t in tools]
        assert len(names) == len(set(names)), "Duplicate tool names found"


class TestExecuteCapabilityAction:
    """execute_capability_action dispatches to the right pack by name prefix."""

    @pytest.mark.asyncio
    async def test_unknown_name_returns_none(self) -> None:
        """A name that isn't a known capability prefix should return None."""
        result = await execute_capability_action("totally_unknown", {}, container=None)
        assert result is None

    @pytest.mark.asyncio
    async def test_known_prefix_unknown_action_returns_error_dict(self) -> None:
        """workspace.nonexistent should return an error dict, not None and not crash."""
        result = await execute_capability_action("workspace.nonexistent", {}, container=None)
        assert result is not None
        parsed = json.loads(result)
        assert parsed.get("error") is True
        assert parsed["capability"] == "workspace"
        assert "not_registered" in parsed["reason"] or "not" in parsed.get("reason", "")

    @pytest.mark.asyncio
    async def test_browser_prefix_unknown_action_returns_error_dict(self) -> None:
        result = await execute_capability_action("browser.nonexistent_action", {}, container=None)
        assert result is not None
        parsed = json.loads(result)
        assert parsed.get("error") is True
        assert parsed["capability"] == "browser"

    @pytest.mark.asyncio
    async def test_vault_prefix_unknown_action_returns_error_dict(self) -> None:
        result = await execute_capability_action("vault.nonexistent", {}, container=None)
        assert result is not None
        parsed = json.loads(result)
        assert parsed.get("error") is True
        assert parsed["capability"] == "vault"

    @pytest.mark.asyncio
    async def test_structured_failure_serialized_with_stable_shape(self) -> None:
        """If a handler returns StructuredFailure, it must serialize to the stable shape."""
        failure = StructuredFailure(
            capability="workspace",
            action="workspace.gmail.search",
            path="mcp.google-workspace.search_messages",
            reason="auth_required",
            user_message="Gmail authentication is required.",
            recoverable=True,
        )

        # Patch the handler in the registry to return a StructuredFailure
        with patch("kora_v2.graph.capability_bridge._get_action_registry") as mock_registry_fn:
            mock_registry = MagicMock()
            mock_action = MagicMock()
            mock_action.handler = AsyncMock(return_value=failure)
            mock_action.name = "workspace.gmail.search"
            mock_action.capability = "workspace"
            mock_registry.get = MagicMock(return_value=mock_action)
            mock_registry_fn.return_value = mock_registry

            result = await execute_capability_action(
                "workspace.gmail.search", {}, container=None
            )

        assert result is not None
        parsed = json.loads(result)
        # Stable JSON shape required by task spec
        assert parsed["error"] is True
        assert parsed["capability"] == "workspace"
        assert parsed["action"] == "workspace.gmail.search"
        assert parsed["reason"] == "auth_required"
        assert "user_message" in parsed
        assert parsed["recoverable"] is True
        assert "failed_path" in parsed

    @pytest.mark.asyncio
    async def test_handler_exception_returns_error_dict(self) -> None:
        """Handler exceptions must be caught and returned as error dicts, not raised."""
        with patch("kora_v2.graph.capability_bridge._get_action_registry") as mock_registry_fn:
            mock_registry = MagicMock()
            mock_action = MagicMock()
            mock_action.handler = AsyncMock(side_effect=RuntimeError("unexpected crash"))
            mock_action.name = "workspace.gmail.search"
            mock_action.capability = "workspace"
            mock_registry.get = MagicMock(return_value=mock_action)
            mock_registry_fn.return_value = mock_registry

            result = await execute_capability_action(
                "workspace.gmail.search", {}, container=None
            )

        assert result is not None
        parsed = json.loads(result)
        assert parsed.get("error") is True
        assert parsed["reason"] == "handler_exception"
        assert "unexpected crash" in parsed["user_message"]

    @pytest.mark.asyncio
    async def test_successful_dict_result_is_json_serialized(self) -> None:
        success_payload = {"status": "ok", "messages": [{"id": "1", "subject": "Hi"}]}

        with patch("kora_v2.graph.capability_bridge._get_action_registry") as mock_registry_fn:
            mock_registry = MagicMock()
            mock_action = MagicMock()
            mock_action.handler = AsyncMock(return_value=success_payload)
            mock_action.name = "workspace.gmail.search"
            mock_action.capability = "workspace"
            mock_registry.get = MagicMock(return_value=mock_action)
            mock_registry_fn.return_value = mock_registry

            result = await execute_capability_action(
                "workspace.gmail.search", {"query": "hello"}, container=None
            )

        assert result is not None
        parsed = json.loads(result)
        assert parsed["status"] == "ok"
        assert len(parsed["messages"]) == 1

    @pytest.mark.asyncio
    async def test_dispatches_to_correct_pack_based_on_prefix(self) -> None:
        """The action returned by the registry should be called, not some other."""
        with patch("kora_v2.graph.capability_bridge._get_action_registry") as mock_registry_fn:
            mock_registry = MagicMock()
            browser_action = MagicMock()
            browser_action.handler = AsyncMock(return_value={"opened": True})
            browser_action.name = "browser.open"
            browser_action.capability = "browser"

            def _get(name: str):  # noqa: ANN001
                if name == "browser.open":
                    return browser_action
                return None

            mock_registry.get = MagicMock(side_effect=_get)
            mock_registry_fn.return_value = mock_registry

            result = await execute_capability_action(
                "browser.open", {"url": "https://example.com"}, container=None
            )

        assert result is not None
        parsed = json.loads(result)
        assert parsed["opened"] is True
        browser_action.handler.assert_awaited_once()
