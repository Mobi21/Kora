"""Browser capability pack — headless browser automation (scaffolding only)."""

from kora_v2.capabilities.base import CapabilityHealth, CapabilityPack, HealthStatus, Policy
from kora_v2.capabilities.registry import ActionRegistry


class BrowserCapability(CapabilityPack):
    name = "browser"
    description = "Headless browser automation for web research and interaction."

    async def health_check(self) -> CapabilityHealth:
        return CapabilityHealth(
            status=HealthStatus.UNIMPLEMENTED,
            summary="Browser capability scaffolding only — implementation pending (Task 7).",
            remediation="See docs/superpowers/plans/phase-9-tooling.md Task 7.",
        )

    def register_actions(self, registry: ActionRegistry) -> None:
        return None  # Implementation in Task 7

    def get_policy(self) -> Policy:
        return Policy()
