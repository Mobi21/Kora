"""Burnout Life OS support rules."""

from __future__ import annotations

from kora_v2.support.modules.base import StaticSupportModule
from kora_v2.support.protocol import LoadFactor, PlanningRule, ProactivityRule, StabilizationRule


class BurnoutSupportModule(StaticSupportModule):
    name = "burnout"
    display_name = "Burnout support"

    def load_factors(self, day_context=None, ledger=None) -> list[LoadFactor]:
        return [
            LoadFactor(
                rule_id="burnout.overcommitment-load",
                profile_key=self.name,
                effect="increase_load_for_overcommitment",
                weight=0.26,
                conditions={"planned_effort_minutes_gte": 240},
                parameters={"load_cap_band": "high"},
                reason="Burnout support treats overcommitment as a load risk.",
            )
        ]

    def planning_rules(self, day_context=None) -> list[PlanningRule]:
        return [
            PlanningRule(
                rule_id="burnout.priority-thinning",
                profile_key=self.name,
                effect="thin_plan_to_essentials_and_recovery",
                weight=0.32,
                conditions={"load_band_in": ["high", "overloaded", "stabilization"]},
                parameters={"protect_recovery_blocks": True, "max_priority_items": 3},
                reason="Burnout recovery requires fewer active commitments.",
            )
        ]

    def proactivity_rules(self, state=None) -> list[ProactivityRule]:
        return [
            ProactivityRule(
                rule_id="burnout.suppress-productivity-push",
                profile_key=self.name,
                effect="suppress_optional_productivity_nudges",
                weight=0.3,
                conditions={"candidate_tags_any": ["optional", "productivity"]},
                parameters={"allow_health_and_fixed_commitments": True},
                reason="Optional productivity nudges can worsen burnout load.",
            )
        ]

    def stabilization_rules(self, state=None) -> list[StabilizationRule]:
        return [
            StabilizationRule(
                rule_id="burnout.recovery-mode",
                profile_key=self.name,
                effect="prefer_recovery_mode_before_more_repair",
                weight=0.28,
                conditions={"load_band": "overloaded"},
                parameters={"mode": "recovery", "exit_requires_user_confirmed_capacity": True},
                reason="Overloaded burnout days should bias toward recovery.",
            )
        ]
