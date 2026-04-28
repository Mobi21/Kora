"""Autism and sensory-load Life OS support rules."""

from __future__ import annotations

from kora_v2.support.modules.base import StaticSupportModule
from kora_v2.support.protocol import ContextPackRule, LoadFactor, PlanningRule, RepairRule


class AutismSensorySupportModule(StaticSupportModule):
    name = "autism_sensory"
    display_name = "Autism and sensory support"

    def load_factors(self, day_context=None, ledger=None) -> list[LoadFactor]:
        return [
            LoadFactor(
                rule_id="autism_sensory.social-sensory-load",
                profile_key=self.name,
                effect="increase_load_for_social_or_sensory_events",
                weight=0.24,
                conditions={"social_or_sensory_events_gte": 2},
                parameters={"social_weight": 0.1, "sensory_weight": 0.12},
                reason="Social and sensory load can make the whole day heavier.",
            )
        ]

    def planning_rules(self, day_context=None) -> list[PlanningRule]:
        return [
            PlanningRule(
                rule_id="autism_sensory.protect-decompression",
                profile_key=self.name,
                effect="insert_decompression_after_heavy_event",
                weight=0.25,
                conditions={"event_tags_any": ["social", "sensory", "transition-heavy"]},
                parameters={"decompression_minutes": 20},
                reason="Recovery blocks protect against overload after demanding events.",
            )
        ]

    def repair_rules(self, state=None) -> list[RepairRule]:
        return [
            RepairRule(
                rule_id="autism_sensory.reduce-context-switching",
                profile_key=self.name,
                effect="cluster_or_defer_flexible_context_switches",
                weight=0.2,
                conditions={"transition_count_gte": 3},
                parameters={"preserve_fixed_commitments": True},
                reason="Repair should reduce surprise and switching, not add it.",
            )
        ]

    def context_pack_rules(self, state=None) -> list[ContextPackRule]:
        return [
            ContextPackRule(
                rule_id="autism_sensory.event-expectations-pack",
                profile_key=self.name,
                effect="create_expectations_and_script_pack",
                weight=0.2,
                conditions={"target_tags_any": ["appointment", "social", "sensory"]},
                parameters={"include_exit_plan": True, "include_communication_script": True},
                reason="Clear expectations and scripts reduce sensory/social uncertainty.",
            )
        ]
