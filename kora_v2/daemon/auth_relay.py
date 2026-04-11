"""WebSocket-based tool authorization relay.

When a tool with ``AuthLevel.ASK_FIRST`` is about to execute, the relay
sends an ``auth_request`` to all connected WebSocket clients and blocks
until one responds or the timeout expires.

Three-layer permission check (checked in order):
1. settings.security.auth_mode == "trust_all" -> auto-approve (NEVER still blocks)
2. Persistent DB grant (tool_permissions table) -> auto-approve
3. Session grant (in-memory set) -> auto-approve
4. Prompt user via WebSocket relay

------------------------------------------------------------------------
WebSocket ``auth_request`` payload shape (stable contract):

    {
        "type":        "auth_request",
        "request_id":  str,           # opaque hex id
        "capability":  str,           # e.g. "workspace"  (legacy: "legacy")
        "account":     str | None,    # e.g. "personal" / None
        "action":      str,           # e.g. "gmail.send" (legacy: tool_name)
        "resource":    str | None,    # optional narrowing id/path/pattern
        "description": str,           # human-readable description
        "args":        dict | None,   # raw tool args (may be None)
        "mode":        str,           # ApprovalMode.value used to decide
    }

Legacy ``auth_request`` (from ``request_permission``) uses the same shape
with ``capability="legacy"`` and ``action=tool_name`` so the frontend
handles both paths identically.
------------------------------------------------------------------------
"""

from __future__ import annotations

import asyncio
import uuid
import warnings
from collections.abc import Awaitable, Callable
from typing import Any

import structlog

from kora_v2.capabilities.policy import (
    ApprovalMode,
    Decision,
    PolicyKey,
    PolicyMatrix,
    SessionState,
    TaskState,
)

log = structlog.get_logger(__name__)


class AuthRelay:
    """Async relay between tool execution and WebSocket client.

    Usage::

        relay = AuthRelay()
        relay.set_broadcast(server_broadcast_fn)

        # Legacy path (blocks until user responds or timeout):
        approved = await relay.request_permission("edit_file", {"path": "/foo"})

        # Policy-aware path:
        key = PolicyKey(capability="workspace", action="gmail.send")
        decision = await relay.request_permission_with_policy(
            key,
            policy_matrix=matrix,
            session_state=session,
            user_facing_description="Send an email via Gmail",
        )

        # In server.py WebSocket handler (unblocks the above):
        relay.receive_response(request_id, approved=True, scope="allow_always")
    """

    def __init__(self) -> None:
        self._pending: dict[str, asyncio.Event] = {}
        self._decisions: dict[str, bool] = {}
        self._request_tools: dict[str, str] = {}  # req_id -> tool_name / action label
        self._request_policy_keys: dict[str, PolicyKey] = {}  # req_id -> PolicyKey (policy path)
        self._session_grants: set[str] = set()
        self._broadcast: Callable[[dict], Awaitable[None]] | None = None
        self._last_scope: dict[str, str] = {}

    def set_broadcast(self, fn: Callable[[dict], Awaitable[None]]) -> None:
        """Set the broadcast function for sending auth_request messages."""
        self._broadcast = fn

    # ------------------------------------------------------------------
    # Legacy tool-name-scoped path (kept exactly as-is)
    # ------------------------------------------------------------------

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

        .. deprecated::
            Prefer ``request_permission_with_policy`` for new callers.
            This method constructs an implicit ``PolicyKey(capability="legacy",
            action=tool_name)`` internally and logs a deprecation warning.

        Args:
            tool_name: Name of the tool requesting permission.
            tool_args: Arguments the tool will be called with.
            timeout: Seconds to wait before auto-denying.
            session_id: Active session ID for context.
            risk_level: Risk assessment of the tool call.

        Returns:
            True if user approved, False otherwise.
        """
        warnings.warn(
            f"request_permission('{tool_name}', ...) is deprecated; "
            "use request_permission_with_policy() with an explicit PolicyKey.",
            DeprecationWarning,
            stacklevel=2,
        )
        log.debug(
            "auth_legacy_tool_call",
            tool=tool_name,
            note="implicit PolicyKey(capability='legacy', action=tool_name)",
        )

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
            # Stable payload shape — capability/action fields included even on legacy path
            "capability": "legacy",
            "account": None,
            "action": tool_name,
            "resource": None,
            "description": f"Tool call: {tool_name}",
            "args": tool_args,
            "mode": ApprovalMode.ALWAYS_ASK.value,
            # Kept for backwards compat with older frontend consumers
            "tool": tool_name,
        })

        log.info("auth_request_sent", request_id=req_id, tool=tool_name)

        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
            approved = self._decisions.pop(req_id, False)
            log.info("auth_response_received", request_id=req_id, approved=approved)
            return approved
        except TimeoutError:
            log.warning("auth_request_timeout", request_id=req_id, tool=tool_name)
            return False
        finally:
            self._pending.pop(req_id, None)
            self._request_tools.pop(req_id, None)
            self._decisions.pop(req_id, None)

    # ------------------------------------------------------------------
    # Policy-aware path
    # ------------------------------------------------------------------

    async def request_permission_with_policy(
        self,
        policy_key: PolicyKey,
        *,
        policy_matrix: PolicyMatrix,
        session_state: SessionState,
        task_state: TaskState | None = None,
        user_facing_description: str,
        tool_args: dict[str, Any] | None = None,
        timeout: float = 30.0,
    ) -> Decision:
        """Evaluate policy, maybe prompt, return final Decision.

        Decision logic:
        - Matrix denies → return denied Decision immediately (no prompt).
        - Matrix allows + no prompt needed → return allowed Decision (no prompt).
        - Otherwise → broadcast ``auth_request`` to all WebSocket clients, wait
          for response, update session/task grant state, return Decision.

        On ``allow_session`` approval the serialized key is added to
        ``session_state.granted_this_session`` AND to ``_session_grants`` so
        legacy tool-name lookups still work when the action matches.

        On ``allow_task`` approval the serialized key is added to
        ``task_state.granted_this_task`` (if task_state is provided).

        Args:
            policy_key:             Identifies the capability/account/action/resource.
            policy_matrix:          Rules to evaluate.
            session_state:          Mutable session grant state (modified in place).
            task_state:             Mutable task grant state (modified in place if provided).
            user_facing_description: Human-readable description shown to the user.
            tool_args:              Raw tool arguments for display (may be None).
            timeout:                Seconds to wait for user response.

        Returns:
            A ``Decision`` with ``allowed``, ``requires_prompt``, ``mode``, and
            ``reason`` fields.
        """
        decision = policy_matrix.evaluate(policy_key, session=session_state, task=task_state)

        if not decision.requires_prompt:
            log.debug(
                "auth_policy_no_prompt",
                capability=policy_key.capability,
                action=policy_key.action,
                allowed=decision.allowed,
                mode=decision.mode,
            )
            return decision

        if self._broadcast is None:
            log.warning(
                "auth_relay_no_broadcast",
                capability=policy_key.capability,
                action=policy_key.action,
            )
            return Decision(
                allowed=False,
                requires_prompt=False,
                mode=decision.mode,
                reason="no broadcast function configured",
            )

        req_id = uuid.uuid4().hex[:12]
        event = asyncio.Event()
        self._pending[req_id] = event
        self._request_policy_keys[req_id] = policy_key
        # Also store a human-readable label for legacy _request_tools tracking
        self._request_tools[req_id] = f"{policy_key.capability}.{policy_key.action}"

        await self._broadcast({
            "type": "auth_request",
            "request_id": req_id,
            "capability": policy_key.capability,
            "account": policy_key.account,
            "action": policy_key.action,
            "resource": policy_key.resource,
            "description": user_facing_description,
            "args": tool_args,
            "mode": decision.mode.value,
        })

        log.info(
            "auth_policy_request_sent",
            request_id=req_id,
            capability=policy_key.capability,
            action=policy_key.action,
        )

        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
            approved = self._decisions.pop(req_id, False)
            log.info(
                "auth_policy_response_received",
                request_id=req_id,
                approved=approved,
            )

            if not approved:
                return Decision(
                    allowed=False,
                    requires_prompt=False,
                    mode=decision.mode,
                    reason="user denied",
                )

            # Retrieve the scope recorded by receive_response
            scope = self._last_scope.get(req_id, "allow_once")
            serial = policy_key.serialize()

            if scope == "allow_session":
                session_state.granted_this_session.add(serial)
                # Also update legacy _session_grants so tool-name checks still hit
                self._session_grants.add(policy_key.action)
                log.info("auth_policy_session_grant_added", key=serial)
            elif scope == "allow_task" and task_state is not None:
                task_state.granted_this_task.add(serial)
                log.info("auth_policy_task_grant_added", key=serial)

            return Decision(
                allowed=True,
                requires_prompt=False,
                mode=decision.mode,
                reason="user approved",
            )

        except TimeoutError:
            log.warning(
                "auth_policy_request_timeout",
                request_id=req_id,
                capability=policy_key.capability,
                action=policy_key.action,
            )
            return Decision(
                allowed=False,
                requires_prompt=False,
                mode=decision.mode,
                reason="timed out waiting for user",
            )
        finally:
            self._pending.pop(req_id, None)
            self._request_tools.pop(req_id, None)
            self._request_policy_keys.pop(req_id, None)
            self._decisions.pop(req_id, None)
            self._last_scope.pop(req_id, None)

    # ------------------------------------------------------------------
    # Response handler (shared by both paths)
    # ------------------------------------------------------------------

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
            scope: One of:
                - ``"allow_once"``    — approved for this call only (default)
                - ``"allow_always"``  — session-level grant (legacy name)
                - ``"allow_forever"`` — persistent DB grant (handled externally)
                - ``"allow_session"`` — session-level grant (new policy path name)
                - ``"allow_task"``    — task-level grant (new policy path name)
        """
        self._decisions[request_id] = approved

        if approved:
            # Legacy path: allow_always adds to _session_grants by tool name
            if scope == "allow_always":
                tool_name = self._request_tools.get(request_id)
                if tool_name:
                    self._session_grants.add(tool_name)
                    log.info("auth_session_grant_added", tool=tool_name)

            # Policy path: stash scope so request_permission_with_policy can read it
            elif scope in ("allow_session", "allow_task"):
                self._last_scope[request_id] = scope
                log.info("auth_policy_scope_stashed", request_id=request_id, scope=scope)

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
