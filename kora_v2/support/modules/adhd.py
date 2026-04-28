"""ADHD Life OS support rules."""

from __future__ import annotations

from kora_v2.support.modules.base import StaticSupportModule
from kora_v2.support.protocol import (
    FutureBridgeRule,
    LoadFactor,
    PlanningRule,
    ProactivityRule,
    RepairRule,
)


class ADHDSupportModule(StaticSupportModule):
    name = "adhd"
    display_name = "ADHD support"

    def load_factors(self, day_context=None, ledger=None) -> list[LoadFactor]:
        return [
            LoadFactor(
                rule_id="adhd.transition-tax",
                profile_key=self.name,
                effect="increase_load_for_context_switches",
                weight=0.18,
                conditions={"transition_count_gte": 4},
                parameters={"per_transition_weight": 0.04},
                reason="Frequent transitions increase executive-function load.",
            )
        ]

    def planning_rules(self, day_context=None) -> list[PlanningRule]:
        return [
            PlanningRule(
                rule_id="adhd.chunk-to-first-move",
                profile_key=self.name,
                effect="split_tasks_into_first_moves",
                weight=0.25,
                conditions={"estimated_minutes_gte": 25},
                parameters={"max_first_move_minutes": 10, "prefer_external_cue": True},
                reason="Large tasks should become short activation steps.",
            )
        ]

    def repair_rules(self, state=None) -> list[RepairRule]:
        return [
            RepairRule(
                rule_id="adhd.add-transition-buffer",
                profile_key=self.name,
                effect="add_private_transition_buffer",
                weight=0.2,
                conditions={"behind_minutes_gte": 15},
                parameters={"buffer_minutes": 10, "requires_confirmation": False},
                reason="Late days need buffers before more task pressure.",
            )
        ]

    def proactivity_rules(self, state=None) -> list[ProactivityRule]:
        return [
            ProactivityRule(
                rule_id="adhd.nudge-open-loop",
                profile_key=self.name,
                effect="prefer_one_concrete_nudge",
                weight=0.12,
                conditions={"candidate_count_gte": 2},
                parameters={"max_parallel_nudges": 1},
                reason="One concrete cue is less likely to become reminder pile-up.",
            )
        ]

    def future_bridge_rules(self, state=None) -> list[FutureBridgeRule]:
        return [
            FutureBridgeRule(
                rule_id="adhd.bridge-first-move",
                profile_key=self.name,
                effect="carry_forward_first_move_not_whole_task",
                weight=0.16,
                conditions={"carryover_status_in": ["partial", "skipped", "blocked"]},
                parameters={"include_time_guess": True},
                reason="Tomorrow needs an activation point, not a vague rollover.",
            )
        ]
