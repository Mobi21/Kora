"""Integration test for auth relay — tests the full request->response flow."""

import asyncio
import pytest
from unittest.mock import AsyncMock

from kora_v2.daemon.auth_relay import AuthRelay


class TestAuthRelayIntegration:
    @pytest.mark.asyncio
    async def test_full_flow_approve_once(self):
        """Full flow: request -> broadcast -> response -> result."""
        messages_sent = []

        async def mock_broadcast(msg):
            messages_sent.append(msg)

        relay = AuthRelay()
        relay.set_broadcast(mock_broadcast)

        async def user_approves():
            await asyncio.sleep(0.05)
            req_id = messages_sent[0]["request_id"]
            relay.receive_response(req_id, approved=True, scope="allow_once")

        asyncio.create_task(user_approves())
        result = await relay.request_permission("write_file", {"path": "/x"})

        assert result is True
        assert len(messages_sent) == 1
        assert messages_sent[0]["type"] == "auth_request"
        assert messages_sent[0]["tool"] == "write_file"
        # allow_once should NOT add session grant
        assert "write_file" not in relay.session_grants

    @pytest.mark.asyncio
    async def test_full_flow_approve_always(self):
        """allow_always adds session grant, second call skips prompt."""
        messages_sent = []

        async def mock_broadcast(msg):
            messages_sent.append(msg)

        relay = AuthRelay()
        relay.set_broadcast(mock_broadcast)

        async def user_approves_always():
            await asyncio.sleep(0.05)
            req_id = messages_sent[0]["request_id"]
            relay.receive_response(req_id, approved=True, scope="allow_always")

        asyncio.create_task(user_approves_always())
        result1 = await relay.request_permission("edit_file", {"path": "/a"})
        assert result1 is True
        assert "edit_file" in relay.session_grants

        # Second call should skip prompt
        result2 = await relay.request_permission("edit_file", {"path": "/b"})
        assert result2 is True
        assert len(messages_sent) == 1  # only one broadcast

    @pytest.mark.asyncio
    async def test_concurrent_requests(self):
        """Multiple concurrent auth requests should each get their own event."""
        messages_sent = []

        async def mock_broadcast(msg):
            messages_sent.append(msg)

        relay = AuthRelay()
        relay.set_broadcast(mock_broadcast)

        async def respond_to_all():
            await asyncio.sleep(0.1)
            for msg in messages_sent:
                relay.receive_response(msg["request_id"], approved=True)

        asyncio.create_task(respond_to_all())

        results = await asyncio.gather(
            relay.request_permission("tool_a", {}),
            relay.request_permission("tool_b", {}),
        )
        assert results == [True, True]
        assert len(messages_sent) == 2
