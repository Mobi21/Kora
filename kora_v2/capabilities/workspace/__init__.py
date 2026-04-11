"""Workspace capability pack — Google Workspace via MCP (scaffolding only)."""

from kora_v2.capabilities.base import CapabilityHealth, CapabilityPack, HealthStatus, Policy
from kora_v2.capabilities.registry import ActionRegistry


class WorkspaceCapability(CapabilityPack):
    name = "workspace"
    description = "Google Workspace integration (Gmail, Calendar, Drive, Docs, Tasks) via MCP."

    async def health_check(self) -> CapabilityHealth:
        return CapabilityHealth(
            status=HealthStatus.UNIMPLEMENTED,
            summary="Workspace capability scaffolding only — implementation pending (Task 6).",
            remediation="See docs/superpowers/plans/phase-9-tooling.md Task 6.",
        )

    def register_actions(self, registry: ActionRegistry) -> None:
        return None  # Implementation in Task 6

    def get_policy(self) -> Policy:
        return Policy()
