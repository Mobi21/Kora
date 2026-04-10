"""Kora V2 — Autonomous runtime state models.

Defines the Pydantic models that carry all mutable runtime state for an
autonomous session: the session-level AutonomousState, per-step
AutonomousStepState, and the serialisable AutonomousCheckpoint that is
written to the database at regular intervals.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

# ── Step status ───────────────────────────────────────────────────────────

StepStatus = Literal[
    "planned",
    "dispatched",
    "waiting_on_user",
    "blocked",
    "accepted",
    "dropped",
]

# ── Per-step state ────────────────────────────────────────────────────────


class AutonomousStepState(BaseModel):
    """Runtime snapshot for a single plan step."""

    id: str
    title: str
    description: str
    status: StepStatus = "planned"
    worker: str = ""
    started_at: datetime | None = None
    completed_at: datetime | None = None
    artifacts: list[str] = Field(default_factory=list)
    error: str | None = None


# ── Session-level state ───────────────────────────────────────────────────


class AutonomousState(BaseModel):
    """Full mutable runtime state for one autonomous session."""

    session_id: str
    plan_id: str
    root_item_id: str | None = None
    mode: Literal["task", "routine"] = "task"
    status: Literal[
        "idle",
        "planned",
        "executing",
        "waiting_on_user",
        "checkpointing",
        "reflecting",
        "replanning",
        "paused_for_overlap",
        "reviewing",
        "completed",
        "cancelled",
        "failed",
    ] = "idle"

    current_step_id: str | None = None
    current_step_index: int = 0
    completed_step_ids: list[str] = Field(default_factory=list)
    pending_step_ids: list[str] = Field(default_factory=list)
    produced_artifact_ids: list[str] = Field(default_factory=list)
    granted_tools: list[str] = Field(default_factory=list)
    decision_queue: list[str] = Field(default_factory=list)

    latest_reflection: str | None = None
    overlap_score: float = 0.0
    checkpoint_due_at: datetime | None = None

    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_checkpoint_at: datetime | None = None
    elapsed_seconds: int = 0

    iteration_count: int = 0
    request_count: int = 0
    token_estimate: int = 0
    cost_estimate: float = 0.0
    request_window_1h: int = 0
    request_window_5h: int = 0

    interruption_pending: bool = False
    safe_resume_token: str | None = None

    quality_summary: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


# ── Checkpoint ────────────────────────────────────────────────────────────


class AutonomousCheckpoint(BaseModel):
    """Serialisable snapshot written to the database at checkpoint time.

    The ``state`` field carries the full ``AutonomousState`` so that a
    session can be resumed exactly from where it left off.
    """

    checkpoint_id: str
    session_id: str
    plan_id: str
    root_item_id: str | None = None
    mode: Literal["task", "routine"] = "task"

    state: AutonomousState
    active_step_snapshot: dict[str, Any] = Field(default_factory=dict)
    completed_step_ids: list[str] = Field(default_factory=list)
    pending_step_ids: list[str] = Field(default_factory=list)
    produced_artifact_ids: list[str] = Field(default_factory=list)
    granted_tools: list[str] = Field(default_factory=list)
    quality_results: list[dict[str, Any]] = Field(default_factory=list)
    decision_queue: list[str] = Field(default_factory=list)

    latest_reflection: str | None = None
    overlap_score: float = 0.0

    resume_token: str
    elapsed_seconds: int = 0
    request_count: int = 0
    token_estimate: int = 0
    cost_estimate: float = 0.0

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    reason: str = "periodic"  # periodic | overlap | budget | replan | termination
