"""Core dataclasses and protocols for the capability-pack system."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from kora_v2.capabilities.registry import ActionRegistry


class HealthStatus(StrEnum):
    OK = "ok"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNCONFIGURED = "unconfigured"
    UNIMPLEMENTED = "unimplemented"


@dataclass
class CapabilityHealth:
    status: HealthStatus
    summary: str
    details: dict[str, Any] = field(default_factory=dict)
    remediation: str | None = None  # Actionable hint for fixing


@dataclass
class StructuredFailure:
    capability: str  # e.g., "workspace"
    action: str  # e.g., "gmail.search"
    path: str  # e.g., "mcp.google-workspace.search_messages"
    reason: str  # machine-readable: "auth_required", "server_unreachable", "policy_denied", ...
    user_message: str  # plain-language line Kora can say
    recoverable: bool  # whether the model should consider retry/alternate path
    machine_details: dict[str, Any] = field(default_factory=dict)


@dataclass
class Action:
    name: str  # e.g., "workspace.gmail.search"
    description: str
    capability: str  # e.g., "workspace"
    input_schema: dict[str, Any]  # JSON schema for tool args
    requires_approval: bool = False
    read_only: bool = True
    handler: Callable[..., Awaitable[Any]] | None = None  # set by implementation tasks


@dataclass
class Policy:
    """Base policy — concrete capability policies subclass or compose this."""

    default_approval_mode: str = "never_ask"  # actual PolicyMatrix comes in Task 4
    deny_list: list[str] = field(default_factory=list)  # action names that are hard-disabled


class CapabilityPack:
    """
    Protocol/base class for capability packs.

    Each pack represents one integration domain (workspace, browser, vault, doctor).
    Concrete packs implement health_check(), register_actions(), and get_policy().
    """

    name: str
    description: str

    async def health_check(self) -> CapabilityHealth:
        raise NotImplementedError

    def register_actions(self, registry: ActionRegistry) -> None:
        raise NotImplementedError

    def get_policy(self) -> Policy:
        raise NotImplementedError
