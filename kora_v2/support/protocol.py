"""Runtime contracts for Life OS support modules."""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, Field


class SupportRule(BaseModel):
    """A concrete rule consumed by Life OS engines."""

    rule_id: str
    profile_key: str
    decision_surface: str
    effect: str
    weight: float = 0.0
    conditions: dict[str, Any] = Field(default_factory=dict)
    parameters: dict[str, Any] = Field(default_factory=dict)
    reason: str


class LoadFactor(SupportRule):
    decision_surface: str = "load"


class PlanningRule(SupportRule):
    decision_surface: str = "planning"


class RepairRule(SupportRule):
    decision_surface: str = "repair"


class ProactivityRule(SupportRule):
    decision_surface: str = "proactivity"


class StabilizationRule(SupportRule):
    decision_surface: str = "stabilization"


class ContextPackRule(SupportRule):
    decision_surface: str = "context_pack"


class FutureBridgeRule(SupportRule):
    decision_surface: str = "future_bridge"


class SupportModule(Protocol):
    """Contract for user-needs modules loaded from active profiles."""

    name: str
    display_name: str

    def load_factors(
        self,
        day_context: Any | None = None,
        ledger: list[Any] | None = None,
    ) -> list[LoadFactor]: ...

    def planning_rules(self, day_context: Any | None = None) -> list[PlanningRule]: ...

    def repair_rules(self, state: Any | None = None) -> list[RepairRule]: ...

    def proactivity_rules(self, state: Any | None = None) -> list[ProactivityRule]: ...

    def stabilization_rules(self, state: Any | None = None) -> list[StabilizationRule]: ...

    def context_pack_rules(self, state: Any | None = None) -> list[ContextPackRule]: ...

    def future_bridge_rules(self, state: Any | None = None) -> list[FutureBridgeRule]: ...

    def output_guidance(self) -> list[str]: ...

    def supervisor_context(self) -> dict[str, Any]: ...
