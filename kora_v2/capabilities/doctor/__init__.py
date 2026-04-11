"""Doctor capability pack — system health diagnostics (scaffolding only)."""

from kora_v2.capabilities.base import CapabilityHealth, CapabilityPack, HealthStatus, Policy
from kora_v2.capabilities.registry import ActionRegistry


class DoctorCapability(CapabilityPack):
    name = "doctor"
    description = "System health diagnostics and self-repair checks for the Kora runtime."

    async def health_check(self) -> CapabilityHealth:
        return CapabilityHealth(
            status=HealthStatus.UNIMPLEMENTED,
            summary="Doctor capability scaffolding only — implementation pending (Task 5).",
            remediation="See docs/superpowers/plans/phase-9-tooling.md Task 5.",
        )

    def register_actions(self, registry: ActionRegistry) -> None:
        return None  # Implementation in Task 5

    def get_policy(self) -> Policy:
        return Policy()
