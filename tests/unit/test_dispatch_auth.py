"""Tests for auth relay threading in dispatch.execute_tool()."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kora_v2.graph.dispatch import check_tool_auth, execute_tool


class TestCheckToolAuth:
    @pytest.mark.asyncio
    async def test_always_allowed_returns_true(self):
        from kora_v2.tools.types import AuthLevel
        result = await check_tool_auth(
            tool_name="recall",
            tool_args={},
            auth_level=AuthLevel.ALWAYS_ALLOWED,
            auth_relay=None,
            auth_mode="prompt",
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_never_returns_false(self):
        from kora_v2.tools.types import AuthLevel
        result = await check_tool_auth(
            tool_name="delete_everything",
            tool_args={},
            auth_level=AuthLevel.NEVER,
            auth_relay=None,
            auth_mode="prompt",
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_ask_first_calls_relay(self):
        from kora_v2.daemon.auth_relay import AuthRelay
        from kora_v2.tools.types import AuthLevel

        relay = AuthRelay()
        relay.set_broadcast(AsyncMock())
        relay.request_permission = AsyncMock(return_value=True)

        result = await check_tool_auth(
            tool_name="edit_file",
            tool_args={"path": "/foo"},
            auth_level=AuthLevel.ASK_FIRST,
            auth_relay=relay,
            auth_mode="prompt",
        )
        assert result is True
        relay.request_permission.assert_called_once_with(
            "edit_file", {"path": "/foo"}, session_id=None, risk_level="unknown"
        )

    @pytest.mark.asyncio
    async def test_ask_first_no_relay_denies(self):
        from kora_v2.tools.types import AuthLevel
        result = await check_tool_auth(
            tool_name="edit_file",
            tool_args={},
            auth_level=AuthLevel.ASK_FIRST,
            auth_relay=None,
            auth_mode="prompt",
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_trust_all_skips_ask_first(self):
        from kora_v2.tools.types import AuthLevel
        result = await check_tool_auth(
            tool_name="edit_file",
            tool_args={},
            auth_level=AuthLevel.ASK_FIRST,
            auth_relay=None,
            auth_mode="trust_all",
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_trust_all_still_blocks_never(self):
        from kora_v2.tools.types import AuthLevel
        result = await check_tool_auth(
            tool_name="nuke",
            tool_args={},
            auth_level=AuthLevel.NEVER,
            auth_relay=None,
            auth_mode="trust_all",
        )
        assert result is False


class TestExecuteToolAuthParam:
    @pytest.mark.asyncio
    async def test_backward_compatible_without_auth_relay(self):
        """execute_tool still works without auth_relay (backward compat).

        ``recall`` without a container returns a structured error
        (no memory wiring) but the call completes without raising —
        backward compat is preserved.
        """
        result = await execute_tool("recall", {"query": "x"})
        data = json.loads(result)
        # Returns structured result/error JSON even with no container
        assert isinstance(data, dict)

    @pytest.mark.asyncio
    async def test_capability_action_requires_auth_and_passes_approval(self):
        from kora_v2.daemon.auth_relay import AuthRelay

        relay = AuthRelay()
        relay.request_permission = AsyncMock(return_value=True)

        container = MagicMock()
        container.settings.security.auth_mode = "prompt"
        container.settings.data_dir = None
        container.session_manager.active_session.session_id = "sess-1"

        capability_tools = [
            {
                "name": "workspace.calendar.create_event",
                "_requires_approval": True,
                "_read_only": False,
            }
        ]

        with patch(
            "kora_v2.graph.capability_bridge.collect_capability_tools",
            return_value=capability_tools,
        ), patch(
            "kora_v2.graph.capability_bridge.execute_capability_action",
            new=AsyncMock(return_value=json.dumps({"ok": True})),
        ) as execute_cap:
            result = await execute_tool(
                "workspace.calendar.create_event",
                {"title": "Standup"},
                container=container,
                auth_relay=relay,
            )

        assert json.loads(result) == {"ok": True}
        relay.request_permission.assert_awaited_once()
        execute_cap.assert_awaited_once_with(
            "workspace.calendar.create_event",
            {"title": "Standup", "approved": True},
            container,
        )
