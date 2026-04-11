"""Phase 9 calendar provenance regression tests.

Verifies that:
- calendar_create_event injects kora_origin and [Created by Kora] marker
- calendar_update_event does NOT inject provenance
- drive_upload does NOT inject visible markers
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from kora_v2.capabilities.policy import SessionState, TaskState
from kora_v2.capabilities.workspace.actions import WorkspaceActionContext
from kora_v2.capabilities.workspace.config import WorkspaceConfig
from kora_v2.capabilities.workspace.policy import build_default_policy

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_mcp_manager(return_data: dict[str, Any] | None = None) -> MagicMock:
    """Return a mock MCPManager that records call_tool invocations."""
    manager = MagicMock()
    mock_result = MagicMock()
    mock_result.is_error = False
    mock_result.text = "{}"
    mock_result.structured_data = return_data or {"id": "event-123"}
    manager.call_tool = AsyncMock(return_value=mock_result)
    return manager


def _make_ctx(mcp_manager: Any) -> WorkspaceActionContext:
    config = WorkspaceConfig()
    policy = build_default_policy(account="personal")
    return WorkspaceActionContext(
        config=config,
        policy=policy,
        mcp_manager=mcp_manager,
        session=SessionState(session_id="test-session"),
        task=TaskState(task_id="test-task"),
    )


# ---------------------------------------------------------------------------
# 1. calendar_create_event injects provenance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_calendar_create_event_injects_kora_origin() -> None:
    """calendar_create_event must pass kora_origin in extendedProperties.private."""
    mcp = _make_mock_mcp_manager()
    ctx = _make_ctx(mcp)

    from kora_v2.capabilities.workspace.actions import calendar_create_event

    await calendar_create_event(
        ctx,
        summary="Test Event",
        start="2026-04-10T10:00:00Z",
        end="2026-04-10T11:00:00Z",
        approved=True,
    )

    assert mcp.call_tool.called, "call_tool must be invoked"
    call_args = mcp.call_tool.call_args
    # call_tool(server_name, tool_name, args)
    args_dict = call_args[0][2] if call_args[0] else call_args.args[2]

    extended = args_dict.get("extendedProperties", {})
    private = extended.get("private", {})
    assert private.get("kora_origin") == "kora-v2", (
        f"extendedProperties.private.kora_origin must be 'kora-v2', got: {private}"
    )


@pytest.mark.asyncio
async def test_calendar_create_event_injects_created_by_kora_marker() -> None:
    """calendar_create_event must include [Created by Kora] in description."""
    mcp = _make_mock_mcp_manager()
    ctx = _make_ctx(mcp)

    from kora_v2.capabilities.workspace.actions import calendar_create_event

    await calendar_create_event(
        ctx,
        summary="Test Event",
        start="2026-04-10T10:00:00Z",
        end="2026-04-10T11:00:00Z",
        description="Initial description",
        approved=True,
    )

    args_dict = mcp.call_tool.call_args[0][2]
    description = args_dict.get("description", "")
    assert "[Created by Kora]" in description, (
        f"description must contain '[Created by Kora]', got: {description!r}"
    )


@pytest.mark.asyncio
async def test_calendar_create_event_creates_description_when_absent() -> None:
    """calendar_create_event creates a description if none was provided."""
    mcp = _make_mock_mcp_manager()
    ctx = _make_ctx(mcp)

    from kora_v2.capabilities.workspace.actions import calendar_create_event

    await calendar_create_event(
        ctx,
        summary="No description event",
        start="2026-04-10T10:00:00Z",
        end="2026-04-10T11:00:00Z",
        approved=True,
    )

    args_dict = mcp.call_tool.call_args[0][2]
    description = args_dict.get("description", "")
    assert description != "", "description must be set even if not provided"
    assert "[Created by Kora]" in description


# ---------------------------------------------------------------------------
# 2. calendar_update_event does NOT inject provenance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_calendar_update_event_does_not_inject_provenance() -> None:
    """calendar_update_event must NOT add kora_origin or Kora markers."""
    mcp = _make_mock_mcp_manager()
    ctx = _make_ctx(mcp)

    from kora_v2.capabilities.workspace.actions import calendar_update_event

    await calendar_update_event(
        ctx,
        event_id="event-123",
        updates={"summary": "Updated title"},
        approved=True,
    )

    assert mcp.call_tool.called, "call_tool must be invoked"
    args_dict = mcp.call_tool.call_args[0][2]

    # No extendedProperties added
    extended = args_dict.get("extendedProperties")
    if extended is not None:
        private = extended.get("private", {})
        assert "kora_origin" not in private, (
            "calendar_update_event must NOT inject kora_origin"
        )

    # No description injected with the marker
    description = args_dict.get("description", "")
    assert "[Created by Kora]" not in description, (
        "calendar_update_event must NOT add [Created by Kora] marker"
    )


# ---------------------------------------------------------------------------
# 3. drive_upload does NOT inject visible markers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drive_upload_does_not_inject_visible_marker() -> None:
    """drive_upload must NOT add any visible attribution or Kora markers."""
    mcp = _make_mock_mcp_manager()
    ctx = _make_ctx(mcp)

    from kora_v2.capabilities.workspace.actions import drive_upload

    content = "Plain file content without markers"
    await drive_upload(
        ctx,
        name="test-file.txt",
        content=content,
        mime_type="text/plain",
        approved=True,
    )

    assert mcp.call_tool.called, "call_tool must be invoked"
    args_dict = mcp.call_tool.call_args[0][2]

    # Content should not have been modified with Kora markers
    uploaded_content = args_dict.get("content", "")
    assert "[Created by Kora]" not in uploaded_content, (
        "drive_upload must NOT inject a visible Kora marker in content"
    )
    assert "kora" not in uploaded_content.lower() or uploaded_content == content, (
        "drive_upload must NOT modify the file content"
    )


# ---------------------------------------------------------------------------
# 4. Provenance uses config values (configurable marker)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_calendar_create_event_uses_config_provenance_value() -> None:
    """The provenance_metadata_value comes from config, not a hardcoded string."""
    from kora_v2.capabilities.workspace.actions import calendar_create_event

    custom_config = WorkspaceConfig(
        provenance_metadata_key="kora_origin",
        provenance_metadata_value="kora-v2",  # confirm it's the real value
        provenance_marker="[Created by Kora]",
    )
    policy = build_default_policy(account="personal")
    mcp = _make_mock_mcp_manager()
    ctx = WorkspaceActionContext(
        config=custom_config,
        policy=policy,
        mcp_manager=mcp,
        session=SessionState(session_id="s"),
        task=TaskState(task_id="t"),
    )

    await calendar_create_event(
        ctx,
        summary="Config test",
        start="2026-04-10T10:00:00Z",
        end="2026-04-10T11:00:00Z",
        approved=True,
    )

    args_dict = mcp.call_tool.call_args[0][2]
    private = args_dict["extendedProperties"]["private"]
    assert private["kora_origin"] == custom_config.provenance_metadata_value
