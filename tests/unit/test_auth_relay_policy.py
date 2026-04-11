"""Unit tests for AuthRelay.request_permission_with_policy and legacy compatibility."""

from __future__ import annotations

import asyncio
import warnings
from unittest.mock import AsyncMock

import pytest

from kora_v2.capabilities.policy import (
    ApprovalMode,
    PolicyKey,
    PolicyMatrix,
    PolicyRule,
    SessionState,
    TaskState,
)
from kora_v2.daemon.auth_relay import AuthRelay

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _never_ask_matrix(capability="workspace", action="gmail.send"):
    key = PolicyKey(capability=capability, action=action)
    rule = PolicyRule(key=key, mode=ApprovalMode.NEVER_ASK, reason="auto")
    return PolicyMatrix([rule])


def _deny_matrix(capability="workspace", action="gmail.send"):
    key = PolicyKey(capability=capability, action=action)
    rule = PolicyRule(key=key, mode=ApprovalMode.DENY, reason="forbidden")
    return PolicyMatrix([rule])


def _always_ask_matrix(capability="workspace", action="gmail.send"):
    key = PolicyKey(capability=capability, action=action)
    rule = PolicyRule(key=key, mode=ApprovalMode.ALWAYS_ASK, reason="prompt each time")
    return PolicyMatrix([rule])


def _session_matrix(capability="workspace", action="gmail.send"):
    key = PolicyKey(capability=capability, action=action)
    rule = PolicyRule(key=key, mode=ApprovalMode.FIRST_PER_SESSION, reason="once per session")
    return PolicyMatrix([rule])


# ---------------------------------------------------------------------------
# NEVER_ASK — no broadcast, returns approved Decision
# ---------------------------------------------------------------------------


class TestNeverAskPolicy:
    @pytest.mark.asyncio
    async def test_never_ask_returns_approved_without_broadcast(self):
        broadcast = AsyncMock()
        relay = AuthRelay()
        relay.set_broadcast(broadcast)

        matrix = _never_ask_matrix()
        key = PolicyKey(capability="workspace", action="gmail.send")
        session = SessionState(session_id="s1")

        decision = await relay.request_permission_with_policy(
            key,
            policy_matrix=matrix,
            session_state=session,
            user_facing_description="Read Gmail",
        )

        assert decision.allowed is True
        assert decision.requires_prompt is False
        broadcast.assert_not_called()

    @pytest.mark.asyncio
    async def test_never_ask_mode_field_correct(self):
        relay = AuthRelay()
        relay.set_broadcast(AsyncMock())

        matrix = _never_ask_matrix()
        key = PolicyKey(capability="workspace", action="gmail.send")
        session = SessionState(session_id="s1")

        decision = await relay.request_permission_with_policy(
            key,
            policy_matrix=matrix,
            session_state=session,
            user_facing_description="Read Gmail",
        )
        assert decision.mode == ApprovalMode.NEVER_ASK


# ---------------------------------------------------------------------------
# DENY — no broadcast, returns denied Decision
# ---------------------------------------------------------------------------


class TestDenyPolicy:
    @pytest.mark.asyncio
    async def test_deny_returns_denied_without_broadcast(self):
        broadcast = AsyncMock()
        relay = AuthRelay()
        relay.set_broadcast(broadcast)

        matrix = _deny_matrix()
        key = PolicyKey(capability="workspace", action="gmail.send")
        session = SessionState(session_id="s1")

        decision = await relay.request_permission_with_policy(
            key,
            policy_matrix=matrix,
            session_state=session,
            user_facing_description="Send Gmail",
        )

        assert decision.allowed is False
        assert decision.requires_prompt is False
        broadcast.assert_not_called()

    @pytest.mark.asyncio
    async def test_deny_mode_field_correct(self):
        relay = AuthRelay()
        relay.set_broadcast(AsyncMock())
        matrix = _deny_matrix()
        key = PolicyKey(capability="workspace", action="gmail.send")
        session = SessionState(session_id="s1")

        decision = await relay.request_permission_with_policy(
            key,
            policy_matrix=matrix,
            session_state=session,
            user_facing_description="Send Gmail",
        )
        assert decision.mode == ApprovalMode.DENY


# ---------------------------------------------------------------------------
# ALWAYS_ASK — broadcasts and waits
# ---------------------------------------------------------------------------


class TestAlwaysAskPolicy:
    @pytest.mark.asyncio
    async def test_always_ask_broadcasts_and_waits_for_approval(self):
        broadcast = AsyncMock()
        relay = AuthRelay()
        relay.set_broadcast(broadcast)

        matrix = _always_ask_matrix()
        key = PolicyKey(capability="workspace", action="gmail.send")
        session = SessionState(session_id="s1")

        async def approve_after_delay():
            await asyncio.sleep(0.05)
            call_args = broadcast.call_args[0][0]
            req_id = call_args["request_id"]
            relay.receive_response(req_id, approved=True, scope="allow_once")

        asyncio.create_task(approve_after_delay())

        decision = await relay.request_permission_with_policy(
            key,
            policy_matrix=matrix,
            session_state=session,
            user_facing_description="Send an email",
            timeout=5.0,
        )

        assert decision.allowed is True
        broadcast.assert_called_once()
        msg = broadcast.call_args[0][0]
        assert msg["type"] == "auth_request"
        assert msg["capability"] == "workspace"
        assert msg["action"] == "gmail.send"
        assert "request_id" in msg

    @pytest.mark.asyncio
    async def test_always_ask_denial_returns_denied(self):
        broadcast = AsyncMock()
        relay = AuthRelay()
        relay.set_broadcast(broadcast)

        matrix = _always_ask_matrix()
        key = PolicyKey(capability="workspace", action="gmail.send")
        session = SessionState(session_id="s1")

        async def deny_after_delay():
            await asyncio.sleep(0.05)
            call_args = broadcast.call_args[0][0]
            req_id = call_args["request_id"]
            relay.receive_response(req_id, approved=False)

        asyncio.create_task(deny_after_delay())
        decision = await relay.request_permission_with_policy(
            key,
            policy_matrix=matrix,
            session_state=session,
            user_facing_description="Send an email",
            timeout=5.0,
        )
        assert decision.allowed is False

    @pytest.mark.asyncio
    async def test_always_ask_timeout_returns_denied(self):
        broadcast = AsyncMock()
        relay = AuthRelay()
        relay.set_broadcast(broadcast)

        matrix = _always_ask_matrix()
        key = PolicyKey(capability="workspace", action="gmail.send")
        session = SessionState(session_id="s1")

        decision = await relay.request_permission_with_policy(
            key,
            policy_matrix=matrix,
            session_state=session,
            user_facing_description="Send an email",
            timeout=0.05,
        )
        assert decision.allowed is False

    @pytest.mark.asyncio
    async def test_broadcast_payload_shape(self):
        """Verify stable payload shape documented in auth_relay.py."""
        broadcast = AsyncMock()
        relay = AuthRelay()
        relay.set_broadcast(broadcast)

        matrix = _always_ask_matrix("browser", "navigate")
        key = PolicyKey(capability="browser", action="navigate", account="personal", resource="https://example.com")
        session = SessionState(session_id="s1")

        # Timeout immediately so we don't hang
        await relay.request_permission_with_policy(
            key,
            policy_matrix=matrix,
            session_state=session,
            user_facing_description="Navigate to example.com",
            tool_args={"url": "https://example.com"},
            timeout=0.05,
        )

        broadcast.assert_called_once()
        payload = broadcast.call_args[0][0]
        assert payload["type"] == "auth_request"
        assert payload["capability"] == "browser"
        assert payload["account"] == "personal"
        assert payload["action"] == "navigate"
        assert payload["resource"] == "https://example.com"
        assert payload["description"] == "Navigate to example.com"
        assert payload["args"] == {"url": "https://example.com"}
        assert "mode" in payload
        assert "request_id" in payload


# ---------------------------------------------------------------------------
# Session grant: allow_session scope updates SessionState
# ---------------------------------------------------------------------------


class TestSessionGrantScope:
    @pytest.mark.asyncio
    async def test_allow_session_updates_session_state(self):
        broadcast = AsyncMock()
        relay = AuthRelay()
        relay.set_broadcast(broadcast)

        matrix = _session_matrix()
        key = PolicyKey(capability="workspace", action="gmail.send")
        session = SessionState(session_id="s1")

        async def approve_with_session_scope():
            await asyncio.sleep(0.05)
            call_args = broadcast.call_args[0][0]
            req_id = call_args["request_id"]
            relay.receive_response(req_id, approved=True, scope="allow_session")

        asyncio.create_task(approve_with_session_scope())

        decision = await relay.request_permission_with_policy(
            key,
            policy_matrix=matrix,
            session_state=session,
            user_facing_description="Send Gmail",
            timeout=5.0,
        )

        assert decision.allowed is True
        assert key.serialize() in session.granted_this_session
        # Also check legacy _session_grants was updated
        assert key.action in relay._session_grants

    @pytest.mark.asyncio
    async def test_second_call_after_session_grant_no_prompt(self):
        broadcast = AsyncMock()
        relay = AuthRelay()
        relay.set_broadcast(broadcast)

        matrix = _session_matrix()
        key = PolicyKey(capability="workspace", action="gmail.send")
        session = SessionState(session_id="s1")
        # Pre-populate session grant
        session.granted_this_session.add(key.serialize())

        decision = await relay.request_permission_with_policy(
            key,
            policy_matrix=matrix,
            session_state=session,
            user_facing_description="Send Gmail",
        )

        assert decision.allowed is True
        assert decision.requires_prompt is False
        broadcast.assert_not_called()


# ---------------------------------------------------------------------------
# Task grant scope
# ---------------------------------------------------------------------------


class TestTaskGrantScope:
    @pytest.mark.asyncio
    async def test_allow_task_updates_task_state(self):
        broadcast = AsyncMock()
        relay = AuthRelay()
        relay.set_broadcast(broadcast)

        key = PolicyKey(capability="workspace", action="calendar.create_event")
        rule = PolicyRule(key=key, mode=ApprovalMode.FIRST_PER_TASK)
        matrix = PolicyMatrix([rule])
        session = SessionState(session_id="s1")
        task = TaskState(task_id="t1")

        async def approve_task_scope():
            await asyncio.sleep(0.05)
            call_args = broadcast.call_args[0][0]
            req_id = call_args["request_id"]
            relay.receive_response(req_id, approved=True, scope="allow_task")

        asyncio.create_task(approve_task_scope())

        decision = await relay.request_permission_with_policy(
            key,
            policy_matrix=matrix,
            session_state=session,
            task_state=task,
            user_facing_description="Create calendar event",
            timeout=5.0,
        )

        assert decision.allowed is True
        assert key.serialize() in task.granted_this_task


# ---------------------------------------------------------------------------
# No broadcast configured
# ---------------------------------------------------------------------------


class TestNoBroadcast:
    @pytest.mark.asyncio
    async def test_no_broadcast_returns_denied_on_always_ask(self):
        relay = AuthRelay()  # no broadcast set
        matrix = _always_ask_matrix()
        key = PolicyKey(capability="workspace", action="gmail.send")
        session = SessionState(session_id="s1")

        decision = await relay.request_permission_with_policy(
            key,
            policy_matrix=matrix,
            session_state=session,
            user_facing_description="Send email",
        )
        assert decision.allowed is False


# ---------------------------------------------------------------------------
# Legacy request_permission still works
# ---------------------------------------------------------------------------


class TestLegacyRequestPermission:
    @pytest.mark.asyncio
    async def test_legacy_method_still_works_approved(self):
        broadcast = AsyncMock()
        relay = AuthRelay()
        relay.set_broadcast(broadcast)

        async def approve():
            await asyncio.sleep(0.05)
            call_args = broadcast.call_args[0][0]
            req_id = call_args["request_id"]
            relay.receive_response(req_id, approved=True)

        asyncio.create_task(approve())

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            result = await relay.request_permission("edit_file", {"path": "/foo"})

        assert result is True

    @pytest.mark.asyncio
    async def test_legacy_method_still_works_denied(self):
        broadcast = AsyncMock()
        relay = AuthRelay()
        relay.set_broadcast(broadcast)

        async def deny():
            await asyncio.sleep(0.05)
            call_args = broadcast.call_args[0][0]
            req_id = call_args["request_id"]
            relay.receive_response(req_id, approved=False)

        asyncio.create_task(deny())

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            result = await relay.request_permission("edit_file", {"path": "/foo"})

        assert result is False

    @pytest.mark.asyncio
    async def test_legacy_session_grant_skips_prompt(self):
        broadcast = AsyncMock()
        relay = AuthRelay()
        relay.set_broadcast(broadcast)
        relay._session_grants.add("my_tool")

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            result = await relay.request_permission("my_tool", {})

        assert result is True
        broadcast.assert_not_called()

    @pytest.mark.asyncio
    async def test_legacy_allow_always_adds_to_session_grants(self):
        broadcast = AsyncMock()
        relay = AuthRelay()
        relay.set_broadcast(broadcast)

        async def approve_always():
            await asyncio.sleep(0.05)
            call_args = broadcast.call_args[0][0]
            req_id = call_args["request_id"]
            relay.receive_response(req_id, approved=True, scope="allow_always")

        asyncio.create_task(approve_always())

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            result = await relay.request_permission("edit_file", {"path": "/foo"})

        assert result is True
        assert "edit_file" in relay.session_grants

    @pytest.mark.asyncio
    async def test_legacy_emits_deprecation_warning(self):
        broadcast = AsyncMock()
        relay = AuthRelay()
        relay.set_broadcast(broadcast)

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            await relay.request_permission("my_tool", {}, timeout=0.01)

        dep_warnings = [x for x in w if issubclass(x.category, DeprecationWarning)]
        assert len(dep_warnings) >= 1
        assert "request_permission_with_policy" in str(dep_warnings[0].message)

    @pytest.mark.asyncio
    async def test_legacy_broadcast_includes_stable_payload_fields(self):
        """Legacy path must emit the stable auth_request shape."""
        broadcast = AsyncMock()
        relay = AuthRelay()
        relay.set_broadcast(broadcast)

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            await relay.request_permission("my_tool", {"x": 1}, timeout=0.01)

        broadcast.assert_called_once()
        payload = broadcast.call_args[0][0]
        assert payload["type"] == "auth_request"
        assert "capability" in payload
        assert "action" in payload
        assert "request_id" in payload
