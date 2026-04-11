"""Tests for WorkspaceCapability pack (Task 6 — Phase 9 Tooling)."""
from __future__ import annotations

import os
import sys

import pytest

# Ensure fixtures are importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "fixtures"))

from mock_workspace_mcp import MockWorkspaceMCPManager

from kora_v2.capabilities.base import HealthStatus
from kora_v2.capabilities.registry import ActionRegistry, CapabilityRegistry
from kora_v2.capabilities.workspace import WorkspaceCapability
from kora_v2.capabilities.workspace.config import WorkspaceConfig
from kora_v2.core.settings import Settings

# ── Helpers ───────────────────────────────────────────────────────────────────

def _settings_with_workspace_server() -> Settings:
    """Return a Settings instance with a fake workspace MCP server configured."""
    from kora_v2.core.settings import MCPServerConfig, MCPSettings
    mcp = MCPSettings(servers={"workspace": MCPServerConfig(command="uvx", args=["workspace-mcp"])})
    return Settings(mcp=mcp)


def _settings_without_workspace_server() -> Settings:
    return Settings()


# ── 1. health_check() without bind → UNCONFIGURED ────────────────────────────

@pytest.mark.asyncio
async def test_health_check_without_bind_is_unconfigured() -> None:
    cap = WorkspaceCapability()
    health = await cap.health_check()
    assert health.status == HealthStatus.UNCONFIGURED
    assert "not bound" in health.summary.lower() or "unconfigured" in health.summary.lower()


# ── 2. After bind with no server configured → UNCONFIGURED ───────────────────

@pytest.mark.asyncio
async def test_health_check_with_no_mcp_server_configured() -> None:
    cap = WorkspaceCapability()
    mock = MockWorkspaceMCPManager()
    settings = _settings_without_workspace_server()
    cap.bind(settings, mock)  # type: ignore[arg-type]

    health = await cap.health_check()
    # No 'workspace' entry in settings.mcp.servers → UNCONFIGURED
    assert health.status == HealthStatus.UNCONFIGURED


# ── 3. With server configured and mock returning all tools → OK ───────────────

@pytest.mark.asyncio
async def test_health_check_with_all_tools_is_ok() -> None:
    cap = WorkspaceCapability()
    mock = MockWorkspaceMCPManager()  # includes all default tools
    settings = _settings_with_workspace_server()
    cap.bind(settings, mock)  # type: ignore[arg-type]

    health = await cap.health_check()
    assert health.status == HealthStatus.OK


@pytest.mark.asyncio
async def test_health_check_with_partial_tools_is_degraded() -> None:
    """If only some tools are present, status is DEGRADED."""
    cap = WorkspaceCapability()
    # Only expose a subset of the default tools
    partial_tools = ["search_gmail_messages", "list_calendar_events"]
    mock = MockWorkspaceMCPManager(tools=partial_tools)
    settings = _settings_with_workspace_server()
    cap.bind(settings, mock)  # type: ignore[arg-type]

    health = await cap.health_check()
    assert health.status == HealthStatus.DEGRADED


@pytest.mark.asyncio
async def test_health_check_with_no_tools_is_unhealthy() -> None:
    """If no expected tools are discovered, status is UNHEALTHY."""
    cap = WorkspaceCapability()
    mock = MockWorkspaceMCPManager(tools=[])  # empty — no tools at all
    settings = _settings_with_workspace_server()
    cap.bind(settings, mock)  # type: ignore[arg-type]

    health = await cap.health_check()
    assert health.status == HealthStatus.UNHEALTHY


# ── 4. register_actions populates ≥15 actions ─────────────────────────────────

def test_register_actions_populates_at_least_15_actions() -> None:
    cap = WorkspaceCapability()
    registry = ActionRegistry()
    cap.register_actions(registry)
    actions = registry.get_all()
    assert len(actions) >= 15, (
        f"Expected ≥15 actions registered, got {len(actions)}: {[a.name for a in actions]}"
    )


def test_register_actions_all_belong_to_workspace_capability() -> None:
    cap = WorkspaceCapability()
    registry = ActionRegistry()
    cap.register_actions(registry)
    for action in registry.get_all():
        assert action.capability == "workspace", (
            f"Action {action.name!r} has capability {action.capability!r}, expected 'workspace'"
        )


def test_register_actions_reads_are_not_requires_approval() -> None:
    """Read actions should have requires_approval=False."""
    cap = WorkspaceCapability()
    registry = ActionRegistry()
    cap.register_actions(registry)
    read_names = {
        "workspace.gmail.search",
        "workspace.gmail.get_message",
        "workspace.calendar.list",
        "workspace.calendar.get_event",
        "workspace.drive.search",
        "workspace.drive.get_file",
        "workspace.docs.read",
        "workspace.tasks.list",
    }
    for action in registry.get_all():
        if action.name in read_names:
            assert not action.requires_approval, (
                f"Read action {action.name!r} should not require approval"
            )
            assert action.read_only


# ── 5. Registering WorkspaceCapability again replaces correctly ───────────────

def test_register_capability_replaces_workspace_correctly() -> None:
    """register_capability() with a new WorkspaceCapability replaces the old one."""
    local_registry = CapabilityRegistry()

    cap1 = WorkspaceCapability(config=WorkspaceConfig(account="personal"))
    cap2 = WorkspaceCapability(config=WorkspaceConfig(account="work"))

    local_registry.register(cap1)
    assert local_registry.get("workspace") is cap1

    local_registry.register(cap2)
    assert local_registry.get("workspace") is cap2

    # Only one workspace entry
    workspace_packs = [p for p in local_registry.get_all() if p.name == "workspace"]
    assert len(workspace_packs) == 1
    assert workspace_packs[0]._config.account == "work"


# ── 6. get_policy() returns the PolicyMatrix ──────────────────────────────────

def test_get_policy_returns_policy_matrix() -> None:
    from kora_v2.capabilities.policy import PolicyMatrix
    cap = WorkspaceCapability()
    policy = cap.get_policy()
    assert isinstance(policy, PolicyMatrix)


# ── 7. make_context raises if not bound ──────────────────────────────────────

def test_make_context_raises_if_not_bound() -> None:
    from kora_v2.capabilities.policy import SessionState
    cap = WorkspaceCapability()
    with pytest.raises(RuntimeError, match="not bound"):
        cap.make_context(session=SessionState(session_id="x"))
