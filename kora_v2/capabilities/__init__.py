"""Capability-pack system for Kora's internal integration boundary."""

from kora_v2.capabilities.base import (
    Action,
    CapabilityHealth,
    CapabilityPack,
    HealthStatus,
    Policy,
    StructuredFailure,
)
from kora_v2.capabilities.browser import BrowserCapability
from kora_v2.capabilities.doctor import DoctorCapability
from kora_v2.capabilities.registry import (
    ActionRegistry,
    CapabilityRegistry,
    get_all_capabilities,
    get_default_registry,
    register_capability,
)
from kora_v2.capabilities.vault import VaultCapability
from kora_v2.capabilities.workspace import WorkspaceCapability

# Register the stubs so later tasks can replace them in place.
register_capability(WorkspaceCapability())
register_capability(BrowserCapability())
register_capability(VaultCapability())
register_capability(DoctorCapability())

__all__ = [
    "Action",
    "ActionRegistry",
    "CapabilityHealth",
    "CapabilityPack",
    "CapabilityRegistry",
    "HealthStatus",
    "Policy",
    "StructuredFailure",
    "get_all_capabilities",
    "get_default_registry",
    "register_capability",
    "WorkspaceCapability",
    "BrowserCapability",
    "VaultCapability",
    "DoctorCapability",
]
