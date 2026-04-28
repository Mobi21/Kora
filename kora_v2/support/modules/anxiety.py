"""Anxiety and avoidance Life OS support rules."""

from __future__ import annotations

from kora_v2.support.modules.base import StaticSupportModule
from kora_v2.support.protocol import ContextPackRule, LoadFactor, PlanningRule, ProactivityRule


class AnxietySupportModule(StaticSupportModule):
    name = "anxiety"
    display_name = "Anxiety support"

    def load_factors(self, day_context=None, ledger=None) -> list[LoadFactor]:
        return [
            LoadFactor(
                rule_id="anxiety.uncertainty-load",
                profile_key=self.name,
                effect="increase_load_for_uncertain_admin",
                weight=0.2,
                conditions={"tags_any": ["admin", "call", "appointment", "form"]},
                parameters={"uncertainty_weight": 0.1},
                reason="Unclear admin tasks can be heavier than their duration.",
            )
        ]

    def planning_rules(self, day_context=None) -> list[PlanningRule]:
        return [
            PlanningRule(
                rule_id="anxiety.prep-before-admin",
                profile_key=self.name,
                effect="schedule_uncertainty_reduction_step",
                weight=0.18,
                conditions={"event_tags_any": ["admin", "healthcare", "call"]},
                parameters={"prep_minutes": 10, "same_day_ok": True},
                reason="Small prep lowers avoidance pressure before stressful tasks.",
            )
        ]

    def proactivity_rules(self, state=None) -> list[ProactivityRule]:
        return [
            ProactivityRule(
                rule_id="anxiety.limit-reassurance-loop",
                profile_key=self.name,
                effect="suppress_repetitive_reassurance_nudges",
                weight=0.22,
                conditions={"similar_nudges_recent_gte": 2},
                parameters={"cooldown_minutes": 120},
                reason="Repeated reassurance can strengthen checking loops.",
            )
        ]

    def context_pack_rules(self, state=None) -> list[ContextPackRule]:
        return [
            ContextPackRule(
                rule_id="anxiety.admin-context-pack",
                profile_key=self.name,
                effect="create_context_pack_for_anxiety_admin",
                weight=0.3,
                conditions={"target_tags_any": ["form", "call", "appointment"]},
                parameters={
                    "sections": [
                        "purpose",
                        "materials",
                        "script",
                        "uncertainty_list",
                        "first_step",
                        "fallback_plan",
                    ]
                },
                reason="A context pack reduces ambiguity before avoidance grows.",
            )
        ]
