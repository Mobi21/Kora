"""WebSocket-based tool authorization relay.

When a tool with ``AuthLevel.ASK_FIRST`` is about to execute, the relay
sends an ``auth_request`` to all connected WebSocket clients and blocks
until one responds or the timeout expires.

Three-layer permission check (checked in order):
1. settings.security.auth_mode == "trust_all" -> auto-approve (NEVER still blocks)
2. Persistent DB grant (tool_permissions table) -> auto-approve
3. Session grant (in-memory set) -> auto-approve
4. Prompt user via WebSocket relay
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable, Awaitable
from typing import Any

import structlog

log = structlog.get_logger(__name__)


class AuthRelay:
    """Async relay between tool execution and WebSocket client.

    Usage::

        relay = AuthRelay()
        relay.set_broadcast(server_broadcast_fn)

        # In dispatch.py (blocks until user responds or timeout):
        approved = await relay.request_permission("edit_file", {"path": "/foo"})

        # In server.py WebSocket handler (unblocks the above):
        relay.receive_response(request_id, approved=True, scope="allow_always")
    """

    def __init__(self) -> None:
        self._pending: dict[str, asyncio.Event] = {}
        self._decisions: dict[str, bool] = {}
        self._request_tools: dict[str, str] = {}  # req_id -> tool_name
        self._session_grants: set[str] = set()
        self._broadcast: Callable[[dict], Awaitable[None]] | None = None

    def set_broadcast(self, fn: Callable[[dict], Awaitable[None]]) -> None:
        """Set the broadcast function for sending auth_request messages."""
        self._broadcast = fn

    async def request_permission(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        timeout: float = 30.0,
        *,
        session_id: str | None = None,
        risk_level: str = "unknown",
    ) -> bool:
        """Request user permission for a tool call.

        Sends ``auth_request`` to all WebSocket clients and waits for
        a response.  Returns True if approved, False if denied or timed out.

        Args:
            tool_name: Name of the tool requesting permission.
            tool_args: Arguments the tool will be called with.
            timeout: Seconds to wait before auto-denying.
            session_id: Active session ID for context.
            risk_level: Risk assessment of the tool call.

        Returns:
            True if user approved, False otherwise.
        """
        # Check session grant first
        if tool_name in self._session_grants:
            log.debug("auth_session_grant", tool=tool_name)
            return True

        if self._broadcast is None:
            log.warning("auth_relay_no_broadcast", tool=tool_name)
            return False

        req_id = uuid.uuid4().hex[:12]
        event = asyncio.Event()
        self._pending[req_id] = event
        self._request_tools[req_id] = tool_name

        await self._broadcast({
            "type": "auth_request",
            "request_id": req_id,
            "tool": tool_name,
            "args": tool_args,
        })

        log.info("auth_request_sent", request_id=req_id, tool=tool_name)

        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
            approved = self._decisions.pop(req_id, False)
            log.info("auth_response_received", request_id=req_id, approved=approved)
            return approved
        except asyncio.TimeoutError:
            log.warning("auth_request_timeout", request_id=req_id, tool=tool_name)
            return False
        finally:
            self._pending.pop(req_id, None)
            self._request_tools.pop(req_id, None)
            self._decisions.pop(req_id, None)

    def receive_response(
        self,
        request_id: str,
        approved: bool,
        scope: str = "allow_once",
    ) -> None:
        """Receive a user's auth response from the WebSocket handler.

        Args:
            request_id: The request ID from the auth_request message.
            approved: Whether the user approved the action.
            scope: "allow_once", "allow_always" (session), or "allow_forever" (persistent DB).
        """
        self._decisions[request_id] = approved

        if approved and scope == "allow_always":
            tool_name = self._request_tools.get(request_id)
            if tool_name:
                self._session_grants.add(tool_name)
                log.info("auth_session_grant_added", tool=tool_name)

        event = self._pending.get(request_id)
        if event:
            event.set()
        else:
            log.warning("auth_response_no_pending", request_id=request_id)

    def clear_session_grants(self) -> None:
        """Clear all session-scoped permission grants."""
        self._session_grants.clear()

    @property
    def session_grants(self) -> frozenset[str]:
        """Return current session-granted tool names."""
        return frozenset(self._session_grants)
