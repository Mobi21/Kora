"""Life OS plan/reality domain models.

These models are intentionally storage-shaped: they describe the durable Life OS
rows that services write before core DB/DI integration is added.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class DayPlanStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    ARCHIVED = "archived"


class DayPlanEntryStatus(StrEnum):
    PLANNED = "planned"
    ACTIVE = "active"
    DONE = "done"
    PARTIAL = "partial"
    SKIPPED = "skipped"
    BLOCKED = "blocked"
    DEFERRED = "deferred"
    DROPPED = "dropped"
    RESCHEDULED = "rescheduled"


class RealityState(StrEnum):
    UNKNOWN = "unknown"
    CONFIRMED_DONE = "confirmed_done"
    CONFIRMED_PARTIAL = "confirmed_partial"
    CONFIRMED_SKIPPED = "confirmed_skipped"
    CONFIRMED_BLOCKED = "confirmed_blocked"
    INFERRED_DONE = "inferred_done"
    NEEDS_CONFIRMATION = "needs_confirmation"
    REJECTED_INFERENCE = "rejected_inference"


class LifeEventSource(StrEnum):
    USER_CONFIRMED = "user_confirmed"
    USER_CORRECTED = "user_corrected"
    ASSISTANT_INFERRED = "assistant_inferred"
    TOOL = "tool"
    ROUTINE = "routine"
    CALENDAR_SYNC = "calendar_sync"
    BACKGROUND_SCAN = "background_scan"


class ConfirmationState(StrEnum):
    CONFIRMED = "confirmed"
    INFERRED = "inferred"
    NEEDS_CONFIRMATION = "needs_confirmation"
    CORRECTED = "corrected"
    REJECTED = "rejected"


class LoadBand(StrEnum):
    LIGHT = "light"
    NORMAL = "normal"
    HIGH = "high"
    OVERLOADED = "overloaded"
    STABILIZATION = "stabilization"


class SupportMode(StrEnum):
    NORMAL = "normal"
    QUIET = "quiet"
    STABILIZATION = "stabilization"


class NudgeDecisionKind(StrEnum):
    SEND = "send"
    DEFER = "defer"
    SUPPRESS = "suppress"
    QUEUE = "queue"


class DayPlanEntry(BaseModel):
    id: str
    day_plan_id: str
    title: str
    entry_type: str
    calendar_entry_id: str | None = None
    item_id: str | None = None
    reminder_id: str | None = None
    routine_id: str | None = None
    intended_start: datetime | None = None
    intended_end: datetime | None = None
    expected_effort: str | None = None
    support_tags: list[str] = Field(default_factory=list)
    status: DayPlanEntryStatus = DayPlanEntryStatus.PLANNED
    reality_state: RealityState = RealityState.UNKNOWN
    created_at: datetime
    updated_at: datetime


class DayPlan(BaseModel):
    id: str
    plan_date: date
    revision: int = 1
    status: DayPlanStatus = DayPlanStatus.ACTIVE
    supersedes_day_plan_id: str | None = None
    generated_from: str = "conversation"
    load_assessment_id: str | None = None
    summary: str | None = None
    entries: list[DayPlanEntry] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class LifeEvent(BaseModel):
    id: str
    event_type: str
    event_time: datetime
    source: LifeEventSource
    confidence: float = 1.0
    confirmation_state: ConfirmationState = ConfirmationState.CONFIRMED
    calendar_entry_id: str | None = None
    item_id: str | None = None
    day_plan_entry_id: str | None = None
    support_module: str | None = None
    title: str | None = None
    details: str | None = None
    raw_text: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    supersedes_event_id: str | None = None
    created_at: datetime


class RecordLifeEventInput(BaseModel):
    event_type: str
    event_time: datetime | None = None
    source: LifeEventSource = LifeEventSource.USER_CONFIRMED
    confidence: float = 1.0
    confirmation_state: ConfirmationState = ConfirmationState.CONFIRMED
    calendar_entry_id: str | None = None
    item_id: str | None = None
    day_plan_entry_id: str | None = None
    support_module: str | None = None
    title: str | None = None
    details: str | None = None
    raw_text: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    supersedes_event_id: str | None = None
    correlation_id: str | None = None


class ConfirmationInput(BaseModel):
    confirmation_state: ConfirmationState = ConfirmationState.CONFIRMED
    source: LifeEventSource = LifeEventSource.USER_CONFIRMED
    confidence: float = 1.0
    details: str | None = None
    raw_text: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    correlation_id: str | None = None


class CorrectionInput(BaseModel):
    event_type: str | None = None
    source: LifeEventSource = LifeEventSource.USER_CORRECTED
    confirmation_state: ConfirmationState = ConfirmationState.CORRECTED
    confidence: float = 1.0
    title: str | None = None
    details: str | None = None
    raw_text: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    correlation_id: str | None = None


class DomainEvent(BaseModel):
    id: str
    event_type: str
    aggregate_type: str
    aggregate_id: str | None = None
    source_service: str
    correlation_id: str | None = None
    causation_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class LoadFactor(BaseModel):
    name: str
    direction: str
    weight: float
    explanation: str
    source: str | None = None


class LoadAssessment(BaseModel):
    id: str
    assessment_date: date
    score: float
    band: LoadBand
    confidence: float
    factors: list[LoadFactor] = Field(default_factory=list)
    recommended_mode: SupportMode = SupportMode.NORMAL
    generated_at: datetime
    confirmed_by_user: bool = False


class RepairAction(BaseModel):
    id: str
    day_plan_id: str
    action_type: str
    status: str = "proposed"
    title: str
    reason: str
    proposed_changes: dict[str, Any] = Field(default_factory=dict)
    requires_confirmation: bool = False
    idempotency_key: str
    created_at: datetime
    updated_at: datetime


class NudgeDecision(BaseModel):
    id: str
    decision: NudgeDecisionKind
    reason: str
    target_type: str | None = None
    target_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class SupportModeState(BaseModel):
    id: str
    mode: SupportMode
    reason: str
    started_at: datetime
    ended_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ContextPack(BaseModel):
    id: str
    pack_type: str
    title: str
    target_type: str | None = None
    target_id: str | None = None
    artifact_path: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class FutureSelfBridge(BaseModel):
    id: str
    bridge_date: date
    from_day_plan_id: str
    to_date: date
    summary: str
    carryover: list[dict[str, Any]] = Field(default_factory=list)
    first_moves: list[str] = Field(default_factory=list)
    artifact_path: str | None = None
    created_at: datetime
