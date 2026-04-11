"""Vault capability pack — secrets and credential management (scaffolding only)."""

from kora_v2.capabilities.base import CapabilityHealth, CapabilityPack, HealthStatus, Policy
from kora_v2.capabilities.registry import ActionRegistry


class VaultCapability(CapabilityPack):
    name = "vault"
    description = "Secrets and credential vault for secure storage and retrieval."

    async def health_check(self) -> CapabilityHealth:
        return CapabilityHealth(
            status=HealthStatus.UNIMPLEMENTED,
            summary="Vault capability scaffolding only — implementation pending (Task 8).",
            remediation="See docs/superpowers/plans/phase-9-tooling.md Task 8.",
        )

    def register_actions(self, registry: ActionRegistry) -> None:
        return None  # Implementation in Task 8

    def get_policy(self) -> Policy:
        return Policy()
