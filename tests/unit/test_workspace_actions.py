"""Tests for workspace action layer (Task 6 — Phase 9 Tooling)."""
from __future__ import annotations

import os
import sys

import pytest

# Ensure fixtures are importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "fixtures"))

from mock_workspace_mcp import MockWorkspaceMCPManager

from kora_v2.capabilities.base import StructuredFailure
from kora_v2.capabilities.policy import SessionState, TaskState
from kora_v2.capabilities.workspace.actions import (
    WorkspaceActionContext,
    calendar_create_event,
    calendar_delete_event,
    drive_upload,
    gmail_search,
    gmail_send,
)
from kora_v2.capabilities.workspace.config import WorkspaceConfig
from kora_v2.capabilities.workspace.policy import build_default_policy

# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_ctx(
    account: str = "personal",
    read_only: bool = False,
    mock: MockWorkspaceMCPManager | None = None,
) -> tuple[WorkspaceActionContext, MockWorkspaceMCPManager]:
    mock = mock or MockWorkspaceMCPManager()
    config = WorkspaceConfig(account=account, read_only=read_only)
    policy = build_default_policy(account=account, read_only=read_only)
    session = SessionState(session_id="test-session")
    task = TaskState(task_id="test-task")
    ctx = WorkspaceActionContext(
        config=config,
        policy=policy,
        mcp_manager=mock,  # type: ignore[arg-type]
        session=session,
        task=task,
    )
    return ctx, mock


# ── 1. gmail_search with approved=False calls MCP (read action) ──────────────

@pytest.mark.asyncio
async def test_gmail_search_calls_mcp() -> None:
    ctx, mock = _make_ctx()
    mock.set_response("search_gmail_messages", {"messages": [{"id": "123"}]})

    result = await gmail_search(ctx, query="from:boss", approved=False)

    assert not isinstance(result, StructuredFailure), f"Expected success, got: {result}"
    assert "messages" in result
    # MCP was called
    assert len(mock.calls) == 1
    server, tool, args = mock.calls[0]
    assert tool == "search_gmail_messages"
    assert args["query"] == "from:boss"


# ── 2. gmail_send with approved=False returns policy_denied, MCP untouched ───

@pytest.mark.asyncio
async def test_gmail_send_returns_policy_denied_without_mcp() -> None:
    ctx, mock = _make_ctx(account="personal")

    result = await gmail_send(ctx, to="x@y.com", subject="Hi", body="Hello", approved=False)

    assert isinstance(result, StructuredFailure)
    assert result.reason == "policy_denied"
    # MCP must NOT have been called
    assert mock.calls == [], f"MCP was called unexpectedly: {mock.calls}"


# ── 3. calendar_create_event with approved=False returns approval_required ────

@pytest.mark.asyncio
async def test_calendar_create_event_without_approval() -> None:
    ctx, mock = _make_ctx()

    result = await calendar_create_event(
        ctx,
        summary="Team sync",
        start="2026-04-10T09:00:00Z",
        end="2026-04-10T10:00:00Z",
        approved=False,
    )

    assert isinstance(result, StructuredFailure)
    assert result.reason == "approval_required"
    assert result.recoverable is True
    # MCP must not have been called
    assert mock.calls == []


# ── 4. calendar_create_event with approved=True calls MCP with provenance ─────

@pytest.mark.asyncio
async def test_calendar_create_event_with_approval_injects_provenance() -> None:
    ctx, mock = _make_ctx()
    mock.set_response("create_calendar_event", {"id": "evt-001", "status": "confirmed"})

    result = await calendar_create_event(
        ctx,
        summary="Team sync",
        start="2026-04-10T09:00:00Z",
        end="2026-04-10T10:00:00Z",
        description="Weekly standup",
        approved=True,
    )

    assert not isinstance(result, StructuredFailure), f"Expected success, got: {result}"
    assert len(mock.calls) == 1
    _server, _tool, call_args = mock.calls[0]

    # Provenance marker should be in the description
    assert "[Created by Kora]" in call_args.get("description", ""), (
        f"Provenance marker missing from description: {call_args.get('description')}"
    )
    # extendedProperties.private.kora_origin should be set
    ext = call_args.get("extendedProperties", {})
    private = ext.get("private", {})
    assert private.get("kora_origin") == "kora-v2"


# ── 5. calendar_delete_event with approved=False returns approval_required ────

@pytest.mark.asyncio
async def test_calendar_delete_event_without_approval() -> None:
    ctx, mock = _make_ctx()

    result = await calendar_delete_event(ctx, event_id="evt-999", approved=False)

    assert isinstance(result, StructuredFailure)
    assert result.reason == "approval_required"
    assert mock.calls == []


@pytest.mark.asyncio
async def test_calendar_delete_event_always_asks_even_after_prior_approval() -> None:
    """ALWAYS_ASK means every call requires approval, not just the first."""
    ctx, mock = _make_ctx()
    mock.set_response("delete_calendar_event", {"status": "deleted"})

    # First call with approval succeeds
    result1 = await calendar_delete_event(ctx, event_id="evt-001", approved=True)
    assert not isinstance(result1, StructuredFailure)

    # Second call WITHOUT re-approval still requires approval
    result2 = await calendar_delete_event(ctx, event_id="evt-002", approved=False)
    assert isinstance(result2, StructuredFailure)
    assert result2.reason == "approval_required"


# ── 6. MCP isError response returns StructuredFailure ────────────────────────

@pytest.mark.asyncio
async def test_mcp_error_response_returns_structured_failure() -> None:
    ctx, mock = _make_ctx()
    mock.set_error("search_gmail_messages", "Auth token expired")

    result = await gmail_search(ctx, query="test", approved=False)

    assert isinstance(result, StructuredFailure)
    assert result.reason == "mcp_error"
    assert "Auth token expired" in result.user_message


# ── 7. read_only with approved=True still returns policy_denied for writes ────

@pytest.mark.asyncio
async def test_read_only_drive_upload_denied_even_with_approval() -> None:
    ctx, mock = _make_ctx(read_only=True)

    result = await drive_upload(
        ctx,
        name="report.txt",
        content="hello",
        approved=True,  # caller thinks it's approved — policy should still deny
    )

    assert isinstance(result, StructuredFailure)
    assert result.reason == "policy_denied"
    assert mock.calls == []
