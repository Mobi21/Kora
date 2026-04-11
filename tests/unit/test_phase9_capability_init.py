"""Phase 9 capability init regression tests.

Verifies that get_all_capabilities() returns the 4 expected packs
with the correct interface, that each can be bound, and that after
binding their register_actions() populates the ActionRegistry with
at least the documented minimum counts.
"""
from __future__ import annotations

import inspect

import pytest

from kora_v2.capabilities import (
    BrowserCapability,
    VaultCapability,
    WorkspaceCapability,
    get_all_capabilities,
)
from kora_v2.capabilities.registry import ActionRegistry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings_for_workspace() -> object:
    """Minimal settings that satisfy WorkspaceCapability.bind()."""

    class _MCPSettings:
        servers: dict = {}

    class _Settings:
        mcp = _MCPSettings()

    return _Settings()


def _make_settings_for_browser() -> object:
    """Minimal settings that satisfy BrowserCapability.bind()."""

    class _Browser:
        enabled = False
        binary_path = ""
        default_profile = ""
        clip_target = "vault"
        max_session_duration_seconds = 3600
        command_timeout_seconds = 10

    class _Settings:
        browser = _Browser()

    return _Settings()


def _make_settings_for_vault() -> object:
    """Minimal settings that satisfy VaultCapability.bind()."""

    class _Vault:
        enabled = False
        path = ""
        clips_subdir = "Clips"
        notes_subdir = "Notes"

    class _Settings:
        vault = _Vault()

    return _Settings()


# ---------------------------------------------------------------------------
# 1. get_all_capabilities returns exactly 4 packs with the right names
# ---------------------------------------------------------------------------


def test_get_all_capabilities_returns_exactly_four_packs() -> None:
    packs = get_all_capabilities()
    names = {p.name for p in packs}
    assert names == {"workspace", "browser", "vault", "doctor"}, (
        f"Expected exactly workspace/browser/vault/doctor, got {names}"
    )


def test_get_all_capabilities_returns_four_items() -> None:
    packs = get_all_capabilities()
    assert len(packs) == 4, f"Expected 4 packs, got {len(packs)}"


# ---------------------------------------------------------------------------
# 2. Each pack exposes the required interface
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("pack_name", ["workspace", "browser", "vault", "doctor"])
def test_pack_has_name(pack_name: str) -> None:
    packs = {p.name: p for p in get_all_capabilities()}
    pack = packs[pack_name]
    assert isinstance(pack.name, str) and pack.name == pack_name


@pytest.mark.parametrize("pack_name", ["workspace", "browser", "vault", "doctor"])
def test_pack_has_description(pack_name: str) -> None:
    packs = {p.name: p for p in get_all_capabilities()}
    pack = packs[pack_name]
    assert isinstance(pack.description, str) and pack.description.strip()


@pytest.mark.parametrize("pack_name", ["workspace", "browser", "vault", "doctor"])
def test_pack_has_health_check_awaitable(pack_name: str) -> None:
    packs = {p.name: p for p in get_all_capabilities()}
    pack = packs[pack_name]
    assert hasattr(pack, "health_check"), f"{pack_name} missing health_check"
    # health_check must be a coroutine function
    assert inspect.iscoroutinefunction(pack.health_check), (
        f"{pack_name}.health_check must be a coroutine function"
    )


@pytest.mark.parametrize("pack_name", ["workspace", "browser", "vault", "doctor"])
def test_pack_has_register_actions(pack_name: str) -> None:
    packs = {p.name: p for p in get_all_capabilities()}
    pack = packs[pack_name]
    assert hasattr(pack, "register_actions"), f"{pack_name} missing register_actions"
    assert callable(pack.register_actions)


@pytest.mark.parametrize("pack_name", ["workspace", "browser", "vault", "doctor"])
def test_pack_has_get_policy(pack_name: str) -> None:
    packs = {p.name: p for p in get_all_capabilities()}
    pack = packs[pack_name]
    assert hasattr(pack, "get_policy"), f"{pack_name} missing get_policy"
    assert callable(pack.get_policy)


@pytest.mark.parametrize("pack_name", ["workspace", "browser", "vault"])
def test_real_pack_has_make_context(pack_name: str) -> None:
    packs = {p.name: p for p in get_all_capabilities()}
    pack = packs[pack_name]
    assert hasattr(pack, "make_context"), (
        f"{pack_name} must have make_context()"
    )
    assert callable(pack.make_context)


# ---------------------------------------------------------------------------
# 3. Each real pack can be bound without crashing
# ---------------------------------------------------------------------------


def test_workspace_bind_does_not_crash() -> None:
    pack = WorkspaceCapability()
    pack.bind(settings=_make_settings_for_workspace(), mcp_manager=None)
    # No exception is the assertion


def test_browser_bind_does_not_crash() -> None:
    pack = BrowserCapability()
    pack.bind(settings=_make_settings_for_browser())
    # No exception


def test_browser_bind_accepts_mcp_manager_kwarg() -> None:
    """bind() must accept mcp_manager= without raising (DI wiring pattern)."""
    pack = BrowserCapability()
    pack.bind(settings=_make_settings_for_browser(), mcp_manager=None)


def test_vault_bind_does_not_crash() -> None:
    pack = VaultCapability()
    pack.bind(settings=_make_settings_for_vault())
    # No exception


# ---------------------------------------------------------------------------
# 4. After binding, register_actions populates with minimum counts
# ---------------------------------------------------------------------------


def test_workspace_register_actions_min_15() -> None:
    pack = WorkspaceCapability()
    pack.bind(settings=_make_settings_for_workspace(), mcp_manager=None)
    registry = ActionRegistry()
    pack.register_actions(registry)
    actions = registry.get_by_capability("workspace")
    assert len(actions) >= 15, (
        f"workspace must register at least 15 actions, got {len(actions)}: "
        f"{[a.name for a in actions]}"
    )


def test_browser_register_actions_min_9() -> None:
    pack = BrowserCapability()
    pack.bind(settings=_make_settings_for_browser())
    registry = ActionRegistry()
    pack.register_actions(registry)
    actions = registry.get_by_capability("browser")
    assert len(actions) >= 9, (
        f"browser must register at least 9 actions, got {len(actions)}: "
        f"{[a.name for a in actions]}"
    )


def test_vault_register_actions_min_3() -> None:
    pack = VaultCapability()
    pack.bind(settings=_make_settings_for_vault())
    registry = ActionRegistry()
    pack.register_actions(registry)
    actions = registry.get_by_capability("vault")
    assert len(actions) >= 3, (
        f"vault must register at least 3 actions, got {len(actions)}: "
        f"{[a.name for a in actions]}"
    )


def test_workspace_register_actions_includes_known_names() -> None:
    pack = WorkspaceCapability()
    pack.bind(settings=_make_settings_for_workspace(), mcp_manager=None)
    registry = ActionRegistry()
    pack.register_actions(registry)
    all_names = {a.name for a in registry.get_all()}
    expected_real = {
        "workspace.gmail.search",
        "workspace.gmail.send",
        "workspace.calendar.list",
        "workspace.calendar.create_event",
        "workspace.calendar.delete_event",
        "workspace.drive.search",
        "workspace.drive.upload",
    }
    assert expected_real.issubset(all_names), (
        f"Missing workspace actions: {expected_real - all_names}"
    )


def test_browser_register_actions_includes_known_names() -> None:
    pack = BrowserCapability()
    pack.bind(settings=_make_settings_for_browser())
    registry = ActionRegistry()
    pack.register_actions(registry)
    all_names = {a.name for a in registry.get_all()}
    expected = {
        "browser.open",
        "browser.click",
        "browser.snapshot",
        "browser.screenshot",
    }
    assert expected.issubset(all_names), (
        f"Missing browser actions: {expected - all_names}"
    )


def test_vault_register_actions_includes_known_names() -> None:
    pack = VaultCapability()
    pack.bind(settings=_make_settings_for_vault())
    registry = ActionRegistry()
    pack.register_actions(registry)
    all_names = {a.name for a in registry.get_all()}
    expected = {"vault.write_note", "vault.write_clip", "vault.read_note"}
    assert expected.issubset(all_names), (
        f"Missing vault actions: {expected - all_names}"
    )
