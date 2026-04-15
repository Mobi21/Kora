"""Tests for auth relay threading in dispatch.execute_tool()."""

import json
import pytest
from unittest.mock import MagicMock, AsyncMock

from kora_v2.graph.dispatch import execute_tool, check_tool_auth


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
        from kora_v2.tools.types import AuthLevel
        from kora_v2.daemon.auth_relay import AuthRelay

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
