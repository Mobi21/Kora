"""Capability and action registries for the capability-pack system."""

from __future__ import annotations

from kora_v2.capabilities.base import Action, CapabilityPack


class ActionRegistry:
    """Stores Actions registered by capability packs, keyed by name."""

    def __init__(self) -> None:
        self._actions: dict[str, Action] = {}

    def register(self, action: Action) -> None:
        """Register an action; replaces any existing action with the same name."""
        self._actions[action.name] = action

    def get(self, name: str) -> Action | None:
        """Return the action with the given name, or None if not found."""
        return self._actions.get(name)

    def get_all(self) -> list[Action]:
        """Return all registered actions."""
        return list(self._actions.values())

    def get_by_capability(self, capability: str) -> list[Action]:
        """Return all actions belonging to the given capability name."""
        return [a for a in self._actions.values() if a.capability == capability]


class CapabilityRegistry:
    """Singleton-style registry of all CapabilityPacks."""

    def __init__(self) -> None:
        self._packs: dict[str, CapabilityPack] = {}
        self._action_registry = ActionRegistry()

    def register(self, pack: CapabilityPack) -> None:
        """Register a capability pack; replaces any existing pack with the same name."""
        self._packs[pack.name] = pack

    def get(self, name: str) -> CapabilityPack | None:
        """Return the capability pack with the given name, or None if not found."""
        return self._packs.get(name)

    def get_all(self) -> list[CapabilityPack]:
        """Return all registered capability packs."""
        return list(self._packs.values())

    @property
    def actions(self) -> ActionRegistry:
        """Return the shared ActionRegistry."""
        return self._action_registry


# Module-level singleton
_default_registry = CapabilityRegistry()


def get_default_registry() -> CapabilityRegistry:
    """Return the process-wide default CapabilityRegistry."""
    return _default_registry


def register_capability(pack: CapabilityPack) -> None:
    """Register a capability pack in the default registry."""
    _default_registry.register(pack)


def get_all_capabilities() -> list[CapabilityPack]:
    """Return all capability packs registered in the default registry."""
    return _default_registry.get_all()
