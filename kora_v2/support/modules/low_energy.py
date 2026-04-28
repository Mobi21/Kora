"""Low-energy and depression-like day Life OS support rules."""

from __future__ import annotations

from kora_v2.support.modules.base import StaticSupportModule
from kora_v2.support.protocol import FutureBridgeRule, LoadFactor, PlanningRule, StabilizationRule


class LowEnergySupportModule(StaticSupportModule):
    name = "low_energy"
    display_name = "Low-energy support"

    def load_factors(self, day_context=None, ledger=None) -> list[LoadFactor]:
        return [
            LoadFactor(
                rule_id="low_energy.capacity-drop",
                profile_key=self.name,
                effect="increase_load_when_energy_low",
                weight=0.28,
                conditions={"energy_level": "low"},
                parameters={"capacity_multiplier": 0.45},
                reason="A low-energy day has less usable capacity.",
            )
        ]

    def planning_rules(self, day_context=None) -> list[PlanningRule]:
        return [
            PlanningRule(
                rule_id="low_energy.maintenance-first",
                profile_key=self.name,
                effect="prioritize_maintenance_and_one_obligation",
                weight=0.3,
                conditions={"energy_level": "low"},
                parameters={
                    "protected_tags": ["medication", "food", "hydration", "hygiene"],
                    "max_nonessential_items": 1,
                },
                reason="Low-energy plans should protect basics before productivity.",
            )
        ]

    def stabilization_rules(self, state=None) -> list[StabilizationRule]:
        return [
            StabilizationRule(
                rule_id="low_energy.enter-stabilization",
                profile_key=self.name,
                effect="enter_stabilization_on_very_low_energy",
                weight=0.35,
                conditions={"energy_level": "very_low"},
                parameters={"suppress_optional_nudges": True, "recovery_first": True},
                reason="Very low energy should reduce the day, not intensify it.",
            )
        ]

    def future_bridge_rules(self, state=None) -> list[FutureBridgeRule]:
        return [
            FutureBridgeRule(
                rule_id="low_energy.shame-safe-carryover",
                profile_key=self.name,
                effect="carry_reason_without_guilt_language",
                weight=0.18,
                conditions={"carryover_status_in": ["skipped", "dropped", "blocked"]},
                parameters={"include_energy_context": True, "avoid_streaks": True},
                reason="Tomorrow needs useful context, not guilt.",
            )
        ]
