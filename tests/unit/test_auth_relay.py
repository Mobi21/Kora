"""Tests for AuthRelay — WebSocket tool authorization relay."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from kora_v2.daemon.auth_relay import AuthRelay


class TestAuthRelayBasic:
    @pytest.mark.asyncio
    async def test_request_approved(self):
        """Approved auth_response should make request_permission return True."""
        broadcast = AsyncMock()
        relay = AuthRelay()
        relay.set_broadcast(broadcast)

        async def approve_after_delay():
            await asyncio.sleep(0.05)
            # Find the request_id from the broadcast call
            call_args = broadcast.call_args[0][0]
            req_id = call_args["request_id"]
            relay.receive_response(req_id, approved=True)

        asyncio.create_task(approve_after_delay())
        result = await relay.request_permission("test_tool", {"key": "val"})
        assert result is True

    @pytest.mark.asyncio
    async def test_request_denied(self):
        """Denied auth_response should make request_permission return False."""
        broadcast = AsyncMock()
        relay = AuthRelay()
        relay.set_broadcast(broadcast)

        async def deny_after_delay():
            await asyncio.sleep(0.05)
            call_args = broadcast.call_args[0][0]
            req_id = call_args["request_id"]
            relay.receive_response(req_id, approved=False)

        asyncio.create_task(deny_after_delay())
        result = await relay.request_permission("test_tool", {"key": "val"})
        assert result is False

    @pytest.mark.asyncio
    async def test_timeout_denies(self):
        """If no response within timeout, permission is denied."""
        broadcast = AsyncMock()
        relay = AuthRelay()
        relay.set_broadcast(broadcast)

        result = await relay.request_permission(
            "test_tool", {}, timeout=0.1,
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_broadcast_called_with_auth_request(self):
        """request_permission should broadcast an auth_request message."""
        broadcast = AsyncMock()
        relay = AuthRelay()
        relay.set_broadcast(broadcast)

        # Will timeout, but we can check the broadcast
        await relay.request_permission("my_tool", {"arg": 1}, timeout=0.05)

        broadcast.assert_called_once()
        msg = broadcast.call_args[0][0]
        assert msg["type"] == "auth_request"
        assert msg["tool"] == "my_tool"
        assert msg["args"] == {"arg": 1}
        assert "request_id" in msg

    @pytest.mark.asyncio
    async def test_cleanup_after_response(self):
        """Pending state should be cleaned up after response."""
        broadcast = AsyncMock()
        relay = AuthRelay()
        relay.set_broadcast(broadcast)

        async def approve():
            await asyncio.sleep(0.05)
            call_args = broadcast.call_args[0][0]
            relay.receive_response(call_args["request_id"], approved=True)

        asyncio.create_task(approve())
        await relay.request_permission("tool", {})
        assert len(relay._pending) == 0
        assert len(relay._decisions) == 0


class TestAuthRelaySessionGrants:
    @pytest.mark.asyncio
    async def test_allow_always_grants_session_permission(self):
        broadcast = AsyncMock()
        relay = AuthRelay()
        relay.set_broadcast(broadcast)

        # First call: user approves with allow_always
        async def approve_always():
            await asyncio.sleep(0.05)
            call_args = broadcast.call_args[0][0]
            relay.receive_response(call_args["request_id"], approved=True, scope="allow_always")

        asyncio.create_task(approve_always())
        result = await relay.request_permission("edit_file", {"path": "/foo"})
        assert result is True
        assert "edit_file" in relay.session_grants

    @pytest.mark.asyncio
    async def test_session_grant_skips_prompt(self):
        broadcast = AsyncMock()
        relay = AuthRelay()
        relay.set_broadcast(broadcast)

        # Pre-grant the tool
        relay._session_grants.add("edit_file")

        # Should return True immediately without broadcasting
        result = await relay.request_permission("edit_file", {"path": "/bar"})
        assert result is True
        broadcast.assert_not_called()

    @pytest.mark.asyncio
    async def test_clear_session_grants(self):
        relay = AuthRelay()
        relay._session_grants.add("tool_a")
        relay._session_grants.add("tool_b")
        relay.clear_session_grants()
        assert len(relay.session_grants) == 0
