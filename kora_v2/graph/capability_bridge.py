"""Capability-pack bridge for the supervisor graph.

This module provides two public functions:

* ``collect_capability_tools(container)`` -- walks all registered capability
  packs and converts their actions into the same dict shape used by
  ``SUPERVISOR_TOOLS``, so the LLM can discover them.

* ``execute_capability_action(tool_name, tool_args, container)`` -- looks up an
  action by name across all packs, builds a minimal session/task context, runs
  the handler, and returns a JSON string (success *or* structured failure).

Session / task plumbing note:
  Full session threading is deferred to Task 11.  For now, ``execute_capability_action``
  tries to pull the active session_id from the container; if it cannot, it falls
  back to a sensible default.  ``TaskState`` is always ``None`` (no autonomous
  task context required for interactive turns).
"""

from __future__ import annotations

import json
from typing import Any

import structlog

from kora_v2.capabilities import StructuredFailure, get_all_capabilities
from kora_v2.capabilities.policy import SessionState
from kora_v2.capabilities.registry import ActionRegistry

log = structlog.get_logger(__name__)

# Known capability prefixes used for quick routing checks.
_KNOWN_CAPABILITY_PREFIXES = {"workspace", "browser", "vault", "doctor"}


# ── Process-wide action registry ─────────────────────────────────────────────
# Built lazily on first call so that import-time side-effects are avoided.
_action_registry: ActionRegistry | None = None


def _get_action_registry() -> ActionRegistry:
    """Return (and lazily build) the shared capability action registry."""
    global _action_registry  # noqa: PLW0603
    if _action_registry is not None:
        return _action_registry

    registry = ActionRegistry()
    for pack in get_all_capabilities():
        try:
            pack.register_actions(registry)
        except Exception:  # noqa: BLE001
            log.warning("capability_register_actions_failed", pack=getattr(pack, "name", "?"))
    _action_registry = registry
    return _action_registry


def _reset_action_registry() -> None:
    """Reset the cached registry (used in tests)."""
    global _action_registry  # noqa: PLW0603
    _action_registry = None


# ── Public API ────────────────────────────────────────────────────────────────


def collect_capability_tools(container: Any | None = None) -> list[dict[str, Any]]:  # noqa: ARG001
    """Return tool-definition dicts for all registered capability actions.

    Each dict matches the shape of a ``SUPERVISOR_TOOLS`` entry so the LLM
    sees them as first-class tools.  Internal metadata fields are prefixed
    with ``_`` so the LLM provider strips them before sending to the API.

    Args:
        container: Service container (currently unused; reserved for future
                   runtime-aware filtering).

    Returns:
        List of tool dicts, one per registered capability action.
    """
    registry = _get_action_registry()
    tools: list[dict[str, Any]] = []
    for action in registry.get_all():
        tool: dict[str, Any] = {
            "name": action.name,
            "description": action.description,
            "input_schema": action.input_schema if action.input_schema else {"type": "object", "properties": {}},
            # Internal metadata — stripped by LLM provider layers
            "_capability": action.capability,
            "_requires_approval": action.requires_approval,
            "_read_only": action.read_only,
        }
        tools.append(tool)
    return tools


def _active_session_id(container: Any | None) -> str:
    """Extract the active session id from the container, with a safe default."""
    if container is None:
        return "default"
    session_mgr = getattr(container, "session_manager", None)
    active_session = getattr(session_mgr, "active_session", None)
    session_id = getattr(active_session, "session_id", None)
    return str(session_id) if session_id else "default"


def _serialize_structured_failure(failure: StructuredFailure) -> str:
    """Convert a StructuredFailure into the stable JSON error shape."""
    return json.dumps({
        "error": True,
        "capability": failure.capability,
        "action": failure.action,
        "reason": failure.reason,
        "user_message": failure.user_message,
        "recoverable": failure.recoverable,
        "failed_path": failure.path,
    })


async def execute_capability_action(
    tool_name: str,
    tool_args: dict[str, Any],
    container: Any | None,
) -> str | None:
    """Dispatch a tool call to the matching capability action handler.

    Returns:
        JSON string (success or structured failure) if the tool name matches
        a registered capability action, or ``None`` if no match was found
        (so that the caller can continue with its own routing).
    """
    registry = _get_action_registry()
    action = registry.get(tool_name)
    if action is None:
        # Check whether the name starts with a known capability prefix to
        # give a better error message than the generic "unknown tool".
        prefix = tool_name.split(".")[0] if "." in tool_name else ""
        if prefix in _KNOWN_CAPABILITY_PREFIXES:
            log.warning("capability_action_not_found", tool=tool_name, prefix=prefix)
            return json.dumps({
                "error": True,
                "capability": prefix,
                "action": tool_name,
                "reason": "action_not_registered",
                "user_message": f"Capability action '{tool_name}' is not registered.",
                "recoverable": False,
                "failed_path": f"capability.{tool_name}",
            })
        return None

    if action.handler is None:
        log.warning("capability_action_no_handler", tool=tool_name)
        return json.dumps({
            "error": True,
            "capability": action.capability,
            "action": tool_name,
            "reason": "not_implemented",
            "user_message": f"Action '{tool_name}' is not yet implemented.",
            "recoverable": False,
            "failed_path": f"capability.{tool_name}",
        })

    session_id = _active_session_id(container)
    session = SessionState(session_id=session_id, granted_this_session=set())

    log.info("capability_action_dispatch", tool=tool_name, session_id=session_id)

    try:
        result = await action.handler(session=session, task=None, **tool_args)
    except Exception as exc:  # noqa: BLE001
        log.error("capability_action_error", tool=tool_name, error=str(exc))
        return json.dumps({
            "error": True,
            "capability": action.capability,
            "action": tool_name,
            "reason": "handler_exception",
            "user_message": f"Action '{tool_name}' raised an unexpected error: {exc}",
            "recoverable": True,
            "failed_path": f"capability.{tool_name}",
        })

    if isinstance(result, StructuredFailure):
        return _serialize_structured_failure(result)

    # Successful result: serialize dict/list as JSON, pass strings through.
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result)
    except TypeError:
        return json.dumps({"result": str(result)})
