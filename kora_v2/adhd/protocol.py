"""Neurodivergent support protocol + shared data models.

Any module that provides neurodivergent-specific behaviour (ADHD first,
anxiety/depression/etc. in later phases) implements
``NeurodivergentModule`` so the ``ContextEngine`` can treat them
uniformly.
"""

from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field


class EnergySignal(BaseModel):
    """A single input to ``ContextEngine._estimate_energy``.

    ``level_adjustment`` moves the latent score in ``[-1.0, 1.0]``;
    ``confidence`` is the per-signal confidence used for noisy-OR
    aggregation in the engine.
    """

    source: str
    level_adjustment: float = Field(ge=-1.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    description: str
    is_guess: bool = True


class OutputRule(BaseModel):
    """Regex-based output filter used by the RSD filter.

    Behavioural/semantic guidance (not regex-checkable) lives in
    ``NeurodivergentModule.output_guidance()`` instead and is injected
    as prose into the frozen prefix.
    """

    name: str
    pattern: str
    description: str
    replacement_guidance: str


class FocusState(BaseModel):
    """Current focus state as computed by ``focus_detection``."""

    level: Literal["scattered", "normal", "focused", "locked_in"]
    turns_at_level: int
    session_minutes: int
    hyperfocus_mode: bool = False


class PlanningConfig(BaseModel):
    """Per-user planning adjustments returned by a module."""

    time_correction_factor: float = 1.5
    max_steps_per_plan: int = 7
    first_step_max_minutes: int = 10
    require_micro_step_first: bool = True
    energy_matching: bool = True


class CheckInTrigger(BaseModel):
    """A template for a scheduled check-in the agent may surface."""

    name: str
    interval_minutes: int
    condition: str | None = None
    message_template: str


class NeurodivergentModule(Protocol):
    """Protocol for neurodivergent support modules.

    The ``ContextEngine`` consumes a list of modules (currently just
    ``ADHDModule``); each contributes energy signals, output rules,
    focus detection, planning adjustments, and check-in triggers.
    """

    name: str

    def energy_signals(self, day_context: Any) -> list[EnergySignal]: ...
    def output_rules(self) -> list[OutputRule]: ...
    def output_guidance(self) -> list[str]: ...
    def supervisor_context(self) -> dict[str, Any]: ...
    def focus_detection(
        self, turns_in_topic: int, session_minutes: int
    ) -> FocusState: ...
    def planning_adjustments(self) -> PlanningConfig: ...
    def check_in_triggers(self) -> list[CheckInTrigger]: ...
    def profile_schema(self) -> dict[str, Any]: ...
