"""Tests for the capability-pack scaffolding (Task 2 of Phase 9 Tooling)."""

from kora_v2.capabilities import (
    get_all_capabilities,
    get_default_registry,
)
from kora_v2.capabilities.base import (
    Action,
    CapabilityHealth,
    CapabilityPack,
    HealthStatus,
    Policy,
)
from kora_v2.capabilities.registry import (
    ActionRegistry,
    CapabilityRegistry,
)

# ---------------------------------------------------------------------------
# 1. get_all_capabilities() returns all 4 stubs
# ---------------------------------------------------------------------------


def test_get_all_capabilities_returns_four_packs() -> None:
    packs = get_all_capabilities()
    names = {p.name for p in packs}
    assert names == {"workspace", "browser", "vault", "doctor"}


def test_get_all_capabilities_returns_correct_names() -> None:
    packs = get_all_capabilities()
    names = [p.name for p in packs]
    assert "workspace" in names
    assert "browser" in names
    assert "vault" in names
    assert "doctor" in names


# ---------------------------------------------------------------------------
# 2. Each stub returns HealthStatus.UNIMPLEMENTED with a non-empty summary
# ---------------------------------------------------------------------------


async def test_workspace_health_check_is_unimplemented() -> None:
    registry = get_default_registry()
    pack = registry.get("workspace")
    assert pack is not None
    health = await pack.health_check()
    assert health.status == HealthStatus.UNIMPLEMENTED
    assert health.summary.strip() != ""


async def test_browser_health_check_is_unimplemented() -> None:
    registry = get_default_registry()
    pack = registry.get("browser")
    assert pack is not None
    health = await pack.health_check()
    assert health.status == HealthStatus.UNIMPLEMENTED
    assert health.summary.strip() != ""


async def test_vault_health_check_is_unimplemented() -> None:
    registry = get_default_registry()
    pack = registry.get("vault")
    assert pack is not None
    health = await pack.health_check()
    assert health.status == HealthStatus.UNIMPLEMENTED
    assert health.summary.strip() != ""


async def test_doctor_health_check_is_unimplemented() -> None:
    registry = get_default_registry()
    pack = registry.get("doctor")
    assert pack is not None
    health = await pack.health_check()
    assert health.status == HealthStatus.UNIMPLEMENTED
    assert health.summary.strip() != ""


async def test_all_stubs_have_unimplemented_health() -> None:
    """Verify every registered stub returns UNIMPLEMENTED health."""
    packs = get_all_capabilities()
    for pack in packs:
        health = await pack.health_check()
        assert health.status == HealthStatus.UNIMPLEMENTED, (
            f"{pack.name!r} should return UNIMPLEMENTED, got {health.status!r}"
        )
        assert health.summary.strip(), f"{pack.name!r} health summary must not be empty"


# ---------------------------------------------------------------------------
# 3. ActionRegistry register / get / get_all / get_by_capability
# ---------------------------------------------------------------------------


def _make_action(name: str, capability: str) -> Action:
    return Action(
        name=name,
        description=f"Test action {name}",
        capability=capability,
        input_schema={"type": "object", "properties": {}},
    )


def test_action_registry_register_and_get() -> None:
    ar = ActionRegistry()
    action = _make_action("workspace.gmail.search", "workspace")
    ar.register(action)
    retrieved = ar.get("workspace.gmail.search")
    assert retrieved is action


def test_action_registry_get_missing_returns_none() -> None:
    ar = ActionRegistry()
    assert ar.get("nonexistent.action") is None


def test_action_registry_get_all() -> None:
    ar = ActionRegistry()
    a1 = _make_action("workspace.gmail.search", "workspace")
    a2 = _make_action("browser.navigate", "browser")
    ar.register(a1)
    ar.register(a2)
    all_actions = ar.get_all()
    assert len(all_actions) == 2
    names = {a.name for a in all_actions}
    assert names == {"workspace.gmail.search", "browser.navigate"}


def test_action_registry_get_by_capability() -> None:
    ar = ActionRegistry()
    a1 = _make_action("workspace.gmail.search", "workspace")
    a2 = _make_action("workspace.calendar.list", "workspace")
    a3 = _make_action("browser.navigate", "browser")
    ar.register(a1)
    ar.register(a2)
    ar.register(a3)

    workspace_actions = ar.get_by_capability("workspace")
    assert len(workspace_actions) == 2
    assert all(a.capability == "workspace" for a in workspace_actions)

    browser_actions = ar.get_by_capability("browser")
    assert len(browser_actions) == 1
    assert browser_actions[0].name == "browser.navigate"

    assert ar.get_by_capability("vault") == []


# ---------------------------------------------------------------------------
# 4. register_capability replaces rather than appends on duplicate name
# ---------------------------------------------------------------------------


def test_register_capability_replaces_on_duplicate_name() -> None:
    """Registering a second pack with the same name replaces the first."""
    local_registry = CapabilityRegistry()

    class FirstPack(CapabilityPack):
        name = "test-cap"
        description = "First version"

        async def health_check(self) -> CapabilityHealth:
            return CapabilityHealth(status=HealthStatus.UNCONFIGURED, summary="first")

        def register_actions(self, registry: ActionRegistry) -> None:
            return None

        def get_policy(self) -> Policy:
            return Policy()

    class SecondPack(CapabilityPack):
        name = "test-cap"
        description = "Second version"

        async def health_check(self) -> CapabilityHealth:
            return CapabilityHealth(status=HealthStatus.OK, summary="second")

        def register_actions(self, registry: ActionRegistry) -> None:
            return None

        def get_policy(self) -> Policy:
            return Policy()

    first = FirstPack()
    second = SecondPack()

    local_registry.register(first)
    assert local_registry.get("test-cap") is first

    local_registry.register(second)
    assert local_registry.get("test-cap") is second

    # Only one pack with that name should exist
    all_packs = local_registry.get_all()
    test_packs = [p for p in all_packs if p.name == "test-cap"]
    assert len(test_packs) == 1


# ---------------------------------------------------------------------------
# 5. get_default_registry() returns a singleton
# ---------------------------------------------------------------------------


def test_get_default_registry_returns_singleton() -> None:
    r1 = get_default_registry()
    r2 = get_default_registry()
    assert r1 is r2


def test_default_registry_is_capability_registry_instance() -> None:
    registry = get_default_registry()
    assert isinstance(registry, CapabilityRegistry)


def test_default_registry_has_action_registry() -> None:
    registry = get_default_registry()
    assert isinstance(registry.actions, ActionRegistry)
