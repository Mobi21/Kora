"""Workspace action implementations — Gmail, Calendar, Drive, Docs, Tasks via MCP."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog

from kora_v2.capabilities.base import StructuredFailure
from kora_v2.capabilities.policy import (
    ApprovalMode,
    PolicyKey,
    PolicyMatrix,
    SessionState,
    TaskState,
)
from kora_v2.capabilities.workspace.config import WorkspaceConfig
from kora_v2.capabilities.workspace.provenance import inject_calendar_create_provenance
from kora_v2.mcp.manager import MCPManager

log = structlog.get_logger(__name__)

_CAP = "workspace"
_CURRENT_CALENDAR_TOOLS = {"get_events", "manage_event", "list_calendars", "query_freebusy"}


@dataclass
class WorkspaceActionContext:
    """Runtime context passed to every workspace action."""

    config: WorkspaceConfig
    policy: PolicyMatrix
    mcp_manager: MCPManager
    session: SessionState
    task: TaskState | None = None


# ── Core dispatch helper ──────────────────────────────────────────────────────


async def _call_action(
    ctx: WorkspaceActionContext,
    action_name: str,
    args: dict[str, Any],
    *,
    approved: bool = False,
    resource: str | None = None,
) -> dict[str, Any] | StructuredFailure:
    """Evaluate policy, optionally call MCP, and return structured result.

    Steps:
    1. Build a PolicyKey from the action name + account + resource.
    2. Evaluate against the policy matrix.
    3. If DENY → return StructuredFailure(reason="policy_denied") without touching MCP.
    4. If requires_prompt and not approved → return StructuredFailure(reason="approval_required").
    5. Resolve MCP tool name from config.tool_map.
    6. Call the MCP manager.
    7. On isError → StructuredFailure(reason="mcp_error").
    8. Return structured_data, falling back to {"text": result.text}.
    """
    key = PolicyKey(
        capability=_CAP,
        action=action_name,
        account=ctx.config.account,
        resource=resource,
    )
    decision = ctx.policy.evaluate(key, session=ctx.session, task=ctx.task)

    if not decision.allowed:
        log.debug(
            "workspace.action.policy_denied",
            action=action_name,
            reason=decision.reason,
        )
        return StructuredFailure(
            capability=_CAP,
            action=action_name,
            path=f"mcp.{ctx.config.mcp_server_name}.{action_name}",
            reason="policy_denied",
            user_message=(
                f"The action '{action_name}' is not permitted for the "
                f"'{ctx.config.account}' account by the current policy. "
                f"Reason: {decision.reason}"
            ),
            recoverable=False,
            machine_details={
                "policy_mode": decision.mode,
                "policy_reason": decision.reason,
            },
        )

    if decision.requires_prompt and not approved:
        log.debug(
            "workspace.action.approval_required",
            action=action_name,
            mode=decision.mode,
        )
        return StructuredFailure(
            capability=_CAP,
            action=action_name,
            path=f"mcp.{ctx.config.mcp_server_name}.{action_name}",
            reason="approval_required",
            user_message=(
                f"The action '{action_name}' requires user approval "
                f"(mode: {decision.mode}). Re-invoke with approved=True after the user confirms."
            ),
            recoverable=True,
            machine_details={
                "policy_mode": decision.mode,
                "policy_reason": decision.reason,
            },
        )

    # Resolve tool name
    tool_name = ctx.config.tool_map.get(action_name)
    if tool_name is None:
        return StructuredFailure(
            capability=_CAP,
            action=action_name,
            path=f"mcp.{ctx.config.mcp_server_name}.{action_name}",
            reason="tool_not_mapped",
            user_message=(
                f"Action '{action_name}' has no MCP tool mapping in the current config."
            ),
            recoverable=False,
        )

    # Mark approval in session/task grants if FIRST_PER_* mode
    if decision.requires_prompt and approved:
        serial = key.serialize()
        if decision.mode == ApprovalMode.FIRST_PER_SESSION:
            ctx.session.granted_this_session.add(serial)
        elif decision.mode == ApprovalMode.FIRST_PER_TASK and ctx.task is not None:
            ctx.task.granted_this_task.add(serial)

    log.debug(
        "workspace.action.calling_mcp",
        action=action_name,
        tool=tool_name,
        server=ctx.config.mcp_server_name,
    )

    try:
        result = await ctx.mcp_manager.call_tool(
            ctx.config.mcp_server_name, tool_name, args
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "workspace.action.mcp_exception",
            action=action_name,
            tool=tool_name,
            error=str(exc),
        )
        return StructuredFailure(
            capability=_CAP,
            action=action_name,
            path=f"mcp.{ctx.config.mcp_server_name}.{tool_name}",
            reason="mcp_error",
            user_message=f"MCP call for '{action_name}' raised an exception: {exc}",
            recoverable=False,
            machine_details={"error": str(exc)},
        )

    if result.is_error:
        log.warning(
            "workspace.action.mcp_is_error",
            action=action_name,
            tool=tool_name,
            text=result.text[:200],
        )
        return StructuredFailure(
            capability=_CAP,
            action=action_name,
            path=f"mcp.{ctx.config.mcp_server_name}.{tool_name}",
            reason="mcp_error",
            user_message=f"MCP tool '{tool_name}' returned an error: {result.text}",
            recoverable=False,
            machine_details={"mcp_text": result.text},
        )

    # Build stable return shape
    data = result.structured_data
    if data is not None:
        return data
    return {"text": result.text}


def _workspace_user_email(ctx: WorkspaceActionContext) -> str:
    """Return the configured Google account email for current workspace-mcp."""
    configured = (ctx.config.user_google_email or "").strip()
    if configured:
        return configured
    import os

    return os.environ.get("USER_GOOGLE_EMAIL", "").strip()


def _missing_workspace_email(action_name: str) -> StructuredFailure:
    return StructuredFailure(
        capability=_CAP,
        action=action_name,
        path=f"mcp.workspace.{action_name}",
        reason="missing_user_google_email",
        user_message=(
            "Google Calendar via workspace-mcp requires a configured Google "
            "account email. Set workspace.user_google_email in ~/.kora/settings.toml "
            "or export USER_GOOGLE_EMAIL."
        ),
        recoverable=True,
    )


# ── Gmail ─────────────────────────────────────────────────────────────────────


async def gmail_search(
    ctx: WorkspaceActionContext,
    query: str,
    max_results: int = 10,
    *,
    approved: bool = False,
) -> dict[str, Any] | StructuredFailure:
    """Search Gmail messages matching query."""
    return await _call_action(
        ctx,
        "gmail.search",
        {"query": query, "max_results": max_results},
        approved=approved,
    )


async def gmail_get_message(
    ctx: WorkspaceActionContext,
    message_id: str,
    *,
    approved: bool = False,
) -> dict[str, Any] | StructuredFailure:
    """Get a single Gmail message by ID."""
    return await _call_action(
        ctx,
        "gmail.get_message",
        {"message_id": message_id},
        approved=approved,
    )


async def gmail_draft(
    ctx: WorkspaceActionContext,
    to: str,
    subject: str,
    body: str,
    *,
    approved: bool = False,
) -> dict[str, Any] | StructuredFailure:
    """Create a Gmail draft message."""
    return await _call_action(
        ctx,
        "gmail.draft",
        {"to": to, "subject": subject, "body": body},
        approved=approved,
    )


async def gmail_send(
    ctx: WorkspaceActionContext,
    to: str,
    subject: str,
    body: str,
    *,
    approved: bool = False,
) -> dict[str, Any] | StructuredFailure:
    """Send a Gmail message (denied by default on personal account)."""
    return await _call_action(
        ctx,
        "gmail.send",
        {"to": to, "subject": subject, "body": body},
        approved=approved,
    )


# ── Calendar ──────────────────────────────────────────────────────────────────


async def calendar_list(
    ctx: WorkspaceActionContext,
    time_min: str | None = None,
    time_max: str | None = None,
    calendar_id: str | None = None,
    *,
    approved: bool = False,
) -> dict[str, Any] | StructuredFailure:
    """List calendar events."""
    action_name = "calendar.list"
    tool_name = ctx.config.tool_map.get(action_name)
    args: dict[str, Any] = {"calendar_id": calendar_id or ctx.config.default_calendar_id}
    if tool_name == "get_events":
        user_email = _workspace_user_email(ctx)
        if not user_email:
            return _missing_workspace_email(action_name)
        args["user_google_email"] = user_email
    if time_min is not None:
        args["time_min"] = time_min
    if time_max is not None:
        args["time_max"] = time_max
    return await _call_action(ctx, action_name, args, approved=approved)


async def calendar_get_event(
    ctx: WorkspaceActionContext,
    event_id: str,
    calendar_id: str | None = None,
    *,
    approved: bool = False,
) -> dict[str, Any] | StructuredFailure:
    """Get a single calendar event."""
    action_name = "calendar.get_event"
    tool_name = ctx.config.tool_map.get(action_name)
    if tool_name == "get_events":
        user_email = _workspace_user_email(ctx)
        if not user_email:
            return _missing_workspace_email(action_name)
        return await _call_action(
            ctx,
            action_name,
            {
                "event_id": event_id,
                "calendar_id": calendar_id or ctx.config.default_calendar_id,
                "user_google_email": user_email,
            },
            approved=approved,
        )
    return await _call_action(
        ctx,
        action_name,
        {
            "event_id": event_id,
            "calendar_id": calendar_id or ctx.config.default_calendar_id,
        },
        approved=approved,
    )


async def calendar_create_event(
    ctx: WorkspaceActionContext,
    summary: str,
    start: str,
    end: str,
    description: str | None = None,
    attendees: list[str] | None = None,
    calendar_id: str | None = None,
    *,
    approved: bool = False,
) -> dict[str, Any] | StructuredFailure:
    """Create a calendar event with Kora provenance markers injected."""
    action_name = "calendar.create_event"
    raw_args: dict[str, Any] = {
        "summary": summary,
        "start": start,
        "end": end,
        "calendar_id": calendar_id or ctx.config.default_calendar_id,
    }
    if description is not None:
        raw_args["description"] = description
    if attendees is not None:
        raw_args["attendees"] = attendees

    # Inject provenance before the MCP call
    args_with_provenance = inject_calendar_create_provenance(raw_args, ctx.config)

    if ctx.config.tool_map.get(action_name) == "manage_event":
        user_email = _workspace_user_email(ctx)
        if not user_email:
            return _missing_workspace_email(action_name)
        current_args: dict[str, Any] = {
            "action": "create",
            "user_google_email": user_email,
            "calendar_id": args_with_provenance["calendar_id"],
            "summary": args_with_provenance["summary"],
            "start_time": args_with_provenance["start"],
            "end_time": args_with_provenance["end"],
        }
        if args_with_provenance.get("description") is not None:
            current_args["description"] = args_with_provenance["description"]
        if attendees is not None:
            current_args["attendees"] = attendees
        return await _call_action(ctx, action_name, current_args, approved=approved)

    return await _call_action(ctx, action_name, args_with_provenance, approved=approved)


async def calendar_update_event(
    ctx: WorkspaceActionContext,
    event_id: str,
    updates: dict[str, Any],
    calendar_id: str | None = None,
    *,
    approved: bool = False,
) -> dict[str, Any] | StructuredFailure:
    """Update a calendar event (no provenance — spec: calendar edits need no extra marker)."""
    action_name = "calendar.update_event"
    if ctx.config.tool_map.get(action_name) == "manage_event":
        user_email = _workspace_user_email(ctx)
        if not user_email:
            return _missing_workspace_email(action_name)
        current_updates = dict(updates)
        if "start" in current_updates and "start_time" not in current_updates:
            current_updates["start_time"] = current_updates.pop("start")
        if "end" in current_updates and "end_time" not in current_updates:
            current_updates["end_time"] = current_updates.pop("end")
        current_updates.update(
            {
                "action": "update",
                "event_id": event_id,
                "calendar_id": calendar_id or ctx.config.default_calendar_id,
                "user_google_email": user_email,
            }
        )
        return await _call_action(ctx, action_name, current_updates, approved=approved)
    return await _call_action(
        ctx,
        action_name,
        {
            "event_id": event_id,
            "updates": updates,
            "calendar_id": calendar_id or ctx.config.default_calendar_id,
        },
        approved=approved,
    )


async def calendar_delete_event(
    ctx: WorkspaceActionContext,
    event_id: str,
    calendar_id: str | None = None,
    *,
    approved: bool = False,
) -> dict[str, Any] | StructuredFailure:
    """Delete a calendar event (ALWAYS_ASK policy)."""
    action_name = "calendar.delete_event"
    if ctx.config.tool_map.get(action_name) == "manage_event":
        user_email = _workspace_user_email(ctx)
        if not user_email:
            return _missing_workspace_email(action_name)
        return await _call_action(
            ctx,
            action_name,
            {
                "action": "delete",
                "event_id": event_id,
                "calendar_id": calendar_id or ctx.config.default_calendar_id,
                "user_google_email": user_email,
            },
            approved=approved,
        )
    return await _call_action(
        ctx,
        action_name,
        {
            "event_id": event_id,
            "calendar_id": calendar_id or ctx.config.default_calendar_id,
        },
        approved=approved,
    )


# ── Drive ─────────────────────────────────────────────────────────────────────


async def drive_search(
    ctx: WorkspaceActionContext,
    query: str,
    max_results: int = 10,
    *,
    approved: bool = False,
) -> dict[str, Any] | StructuredFailure:
    """Search Google Drive files."""
    return await _call_action(
        ctx,
        "drive.search",
        {"query": query, "max_results": max_results},
        approved=approved,
    )


async def drive_get_file(
    ctx: WorkspaceActionContext,
    file_id: str,
    *,
    approved: bool = False,
) -> dict[str, Any] | StructuredFailure:
    """Get Drive file content."""
    return await _call_action(
        ctx,
        "drive.get_file",
        {"file_id": file_id},
        approved=approved,
    )


async def drive_upload(
    ctx: WorkspaceActionContext,
    name: str,
    content: str,
    mime_type: str = "text/plain",
    *,
    approved: bool = False,
) -> dict[str, Any] | StructuredFailure:
    """Upload a file to Google Drive (no visible attribution by default)."""
    return await _call_action(
        ctx,
        "drive.upload",
        {"name": name, "content": content, "mime_type": mime_type},
        approved=approved,
    )


# ── Docs ──────────────────────────────────────────────────────────────────────


async def docs_read(
    ctx: WorkspaceActionContext,
    document_id: str,
    *,
    approved: bool = False,
) -> dict[str, Any] | StructuredFailure:
    """Read a Google Doc."""
    return await _call_action(
        ctx,
        "docs.read",
        {"document_id": document_id},
        approved=approved,
    )


async def docs_create(
    ctx: WorkspaceActionContext,
    title: str,
    content: str | None = None,
    *,
    approved: bool = False,
) -> dict[str, Any] | StructuredFailure:
    """Create a Google Doc (no visible attribution by default)."""
    args: dict[str, Any] = {"title": title}
    if content is not None:
        args["content"] = content
    return await _call_action(ctx, "docs.create", args, approved=approved)


async def docs_update(
    ctx: WorkspaceActionContext,
    document_id: str,
    updates: dict[str, Any],
    *,
    approved: bool = False,
) -> dict[str, Any] | StructuredFailure:
    """Update a Google Doc."""
    return await _call_action(
        ctx,
        "docs.update",
        {"document_id": document_id, "updates": updates},
        approved=approved,
    )


# ── Tasks ─────────────────────────────────────────────────────────────────────


async def tasks_list(
    ctx: WorkspaceActionContext,
    list_id: str | None = None,
    *,
    approved: bool = False,
) -> dict[str, Any] | StructuredFailure:
    """List Google Tasks."""
    args: dict[str, Any] = {}
    if list_id is not None:
        args["list_id"] = list_id
    return await _call_action(ctx, "tasks.list", args, approved=approved)


async def tasks_create(
    ctx: WorkspaceActionContext,
    title: str,
    list_id: str | None = None,
    notes: str | None = None,
    *,
    approved: bool = False,
) -> dict[str, Any] | StructuredFailure:
    """Create a Google Task."""
    args: dict[str, Any] = {"title": title}
    if list_id is not None:
        args["list_id"] = list_id
    if notes is not None:
        args["notes"] = notes
    return await _call_action(ctx, "tasks.create", args, approved=approved)
