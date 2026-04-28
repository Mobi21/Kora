"""Shared helpers for deterministic support modules."""

from __future__ import annotations

from typing import Any

from kora_v2.support.protocol import (
    ContextPackRule,
    FutureBridgeRule,
    LoadFactor,
    PlanningRule,
    ProactivityRule,
    RepairRule,
    StabilizationRule,
)


class StaticSupportModule:
    """Base class for support modules that expose durable decision rules."""

    name: str
    display_name: str

    def __init__(self, settings: dict[str, Any] | None = None) -> None:
        self.settings = settings or {}

    def load_factors(
        self,
        day_context: Any | None = None,
        ledger: list[Any] | None = None,
    ) -> list[LoadFactor]:
        return []

    def planning_rules(self, day_context: Any | None = None) -> list[PlanningRule]:
        return []

    def repair_rules(self, state: Any | None = None) -> list[RepairRule]:
        return []

    def proactivity_rules(self, state: Any | None = None) -> list[ProactivityRule]:
        return []

    def stabilization_rules(self, state: Any | None = None) -> list[StabilizationRule]:
        return []

    def context_pack_rules(self, state: Any | None = None) -> list[ContextPackRule]:
        return []

    def future_bridge_rules(self, state: Any | None = None) -> list[FutureBridgeRule]:
        return []

    def output_guidance(self) -> list[str]:
        return []

    def supervisor_context(self) -> dict[str, Any]:
        return {
            "profile_key": self.name,
            "display_name": self.display_name,
            "settings": self.settings,
            "runtime_surfaces": sorted(
                {
                    rule.decision_surface
                    for rule in [
                        *self.load_factors(),
                        *self.planning_rules(),
                        *self.repair_rules(),
                        *self.proactivity_rules(),
                        *self.stabilization_rules(),
                        *self.context_pack_rules(),
                        *self.future_bridge_rules(),
                    ]
                }
            ),
        }
