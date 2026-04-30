"""Pydantic contracts for the Kora desktop UI.

These models are intentionally UI-shaped. They hide raw SQLite table layout and
filesystem memory details behind stable desktop view models.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

RuntimeState = Literal["starting", "connected", "degraded", "disconnected", "needs_setup"]
LoadBand = Literal["light", "normal", "high", "overloaded", "stabilization", "unknown"]
ArtifactKind = Literal[
    "today_plan",
    "repair_preview",
    "calendar_slice",
    "calendar_edit_preview",
    "medication_status",
    "medication_log_preview",
    "routine_status",
    "vault_memory",
    "context_pack",
    "future_bridge",
    "autonomous_progress",
    "settings_control",
    "permission_prompt",
    "doctor_report",
]
HealthState = Literal["ok", "degraded", "unavailable", "unconfigured"]


class VaultState(BaseModel):
    enabled: bool
    configured: bool
    path: str | None = None
    memory_root: str
    obsidian_facing: bool = True
    health: Literal["ok", "unconfigured", "missing", "degraded"] = "ok"
    message: str


class DesktopStatusView(BaseModel):
    status: RuntimeState
    version: str
    host: str = "127.0.0.1"
    port: int
    session_active: bool = False
    session_id: str | None = None
    turn_count: int = 0
    failed_subsystems: list[str] = Field(default_factory=list)
    orchestration_pipelines: int = 0
    vault: VaultState
    support_mode: str = "normal"
    generated_at: datetime


class TimelineItem(BaseModel):
    id: str
    title: str
    item_type: str
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    status: str = "planned"
    reality_state: str = "unknown"
    support_tags: list[str] = Field(default_factory=list)
    provenance: list[str] = Field(default_factory=list)
    risk: Literal["none", "watch", "repair"] = "none"


class TodayBlock(BaseModel):
    title: str
    subtitle: str | None = None
    items: list[TimelineItem] = Field(default_factory=list)
    empty_label: str


class LoadState(BaseModel):
    band: LoadBand = "unknown"
    score: float | None = None
    recommended_mode: str = "normal"
    factors: list[str] = Field(default_factory=list)
    confidence: float | None = None


class TodayViewModel(BaseModel):
    date: date
    plan_id: str | None = None
    revision: int | None = None
    summary: str | None = None
    now: TodayBlock
    next: TodayBlock
    later: TodayBlock
    timeline: list[TimelineItem] = Field(default_factory=list)
    load: LoadState = Field(default_factory=LoadState)
    support_mode: str = "normal"
    repair_available: bool = False
    generated_at: datetime


class CalendarLayerState(BaseModel):
    id: str
    label: str
    enabled: bool = True
    color: str
    description: str


class CalendarEventView(BaseModel):
    id: str
    title: str
    kind: str
    starts_at: datetime
    ends_at: datetime | None = None
    all_day: bool = False
    source: str = "kora"
    status: str = "active"
    layer_ids: list[str] = Field(default_factory=list)
    provenance: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CalendarRangeView(BaseModel):
    start: datetime
    end: datetime
    default_view: Literal["day", "week", "month", "agenda"] = "week"
    layers: list[CalendarLayerState]
    events: list[CalendarEventView]
    quiet_hours: dict[str, str | None] = Field(default_factory=dict)
    working_hours: dict[str, str | None] = Field(default_factory=dict)
    generated_at: datetime


class RepairActionPreview(BaseModel):
    id: str
    action_type: str
    title: str
    reason: str
    severity: float = 0.0
    target_day_plan_entry_id: str | None = None
    target_calendar_entry_id: str | None = None
    target_item_id: str | None = None
    before: str | None = None
    after: str | None = None
    requires_confirmation: bool = True


class RepairStateView(BaseModel):
    date: date
    day_plan_id: str | None = None
    mode: Literal["guided", "board"] = "guided"
    what_changed_options: list[str]
    broken_or_at_risk: list[TimelineItem] = Field(default_factory=list)
    suggested_repairs: list[RepairActionPreview] = Field(default_factory=list)
    protected_commitments: list[TimelineItem] = Field(default_factory=list)
    flexible_items: list[TimelineItem] = Field(default_factory=list)
    move_to_tomorrow: list[TimelineItem] = Field(default_factory=list)
    preview_required: bool = True
    generated_at: datetime


class RepairPreviewRequest(BaseModel):
    date: date
    change_type: str = "make_smaller"
    note: str | None = None
    selected_entry_ids: list[str] = Field(default_factory=list)


class RepairApplyRequest(BaseModel):
    date: date
    preview_action_ids: list[str] = Field(default_factory=list)
    user_confirmed: bool = True


class RepairPreview(BaseModel):
    date: date
    day_plan_id: str | None = None
    summary: str
    actions: list[RepairActionPreview]
    mutates_state: bool = False
    generated_at: datetime


class RepairApplyResult(BaseModel):
    status: Literal["applied", "skipped", "unavailable"]
    applied_action_ids: list[str] = Field(default_factory=list)
    skipped_action_ids: list[str] = Field(default_factory=list)
    new_day_plan_id: str | None = None
    message: str


class VaultMemoryItem(BaseModel):
    id: str
    title: str
    body_preview: str
    memory_type: str
    certainty: Literal["confirmed", "guess", "correction", "stale", "unknown"]
    tags: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    provenance: list[str] = Field(default_factory=list)
    vault_note_path: str | None = None
    updated_at: str | None = None


class ContextPackSummary(BaseModel):
    id: str
    title: str
    pack_type: str
    artifact_path: str | None = None
    created_at: str | None = None


class FutureBridgeSummary(BaseModel):
    id: str
    summary: str
    to_date: str | None = None
    artifact_path: str | None = None


class VaultContextView(BaseModel):
    vault: VaultState
    recent_memories: list[VaultMemoryItem]
    corrections: list[VaultMemoryItem]
    uncertain_or_stale: list[VaultMemoryItem]
    context_packs: list[ContextPackSummary]
    future_bridges: list[FutureBridgeSummary]
    generated_at: datetime


class VaultSearchView(BaseModel):
    query: str
    results: list[VaultMemoryItem]
    vault: VaultState
    generated_at: datetime


class DesktopSettings(BaseModel):
    theme_family: str = "warm-neutral"
    accent_color: str = "terracotta"
    density: str = "cozy"
    motion: str = "normal"
    support_mode_visuals: bool = True
    command_bar_behavior: str = "screen-aware"
    chat_panel_default_open: bool = True
    chat_panel_width: int = 380
    calendar_default_view: str = "week"
    calendar_layers: dict[str, bool] = Field(default_factory=dict)
    today_module_order: list[str] = Field(default_factory=lambda: ["now", "next", "later", "timeline"])
    timeline_position: str = "right"
    updated_at: datetime | None = None


class KoraArtifact(BaseModel):
    id: str
    kind: ArtifactKind
    title: str
    summary: str
    payload: dict[str, Any]
    created_at: datetime


# ── Calendar mutations ────────────────────────────────────────────────────


class CalendarEditRequest(BaseModel):
    """Preview/apply payload for moving, resizing, or canceling a calendar item."""

    operation: Literal["move", "resize", "cancel", "create"]
    event_id: str | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    title: str | None = None
    note: str | None = None


class CalendarEditPreview(BaseModel):
    operation: str
    event_id: str | None = None
    before: CalendarEventView | None = None
    after: CalendarEventView | None = None
    conflicts: list[CalendarEventView] = Field(default_factory=list)
    summary: str
    mutates_state: bool = False
    requires_confirmation: bool = True
    generated_at: datetime


class CalendarEditResult(BaseModel):
    status: Literal["applied", "skipped", "unavailable"]
    event_id: str | None = None
    message: str


# ── Medication ────────────────────────────────────────────────────────────


class MedicationDose(BaseModel):
    id: str
    medication_id: str
    name: str
    dose_label: str
    scheduled_at: datetime | None = None
    window_start: datetime | None = None
    window_end: datetime | None = None
    status: Literal["pending", "taken", "skipped", "missed", "unknown"] = "pending"
    pair_with: list[str] = Field(default_factory=list)  # e.g. ["water", "meal"]
    notes: str | None = None


class MedicationDayView(BaseModel):
    date: date
    enabled: bool
    doses: list[MedicationDose] = Field(default_factory=list)
    history_summary: dict[str, int] = Field(default_factory=dict)
    last_taken_at: datetime | None = None
    health_signals: list[str] = Field(default_factory=list)
    health: HealthState = "ok"
    message: str | None = None
    generated_at: datetime


class MedicationLogRequest(BaseModel):
    dose_id: str
    status: Literal["taken", "skipped", "missed"]
    note: str | None = None
    occurred_at: datetime | None = None


class MedicationLogPreview(BaseModel):
    dose_id: str
    before: MedicationDose
    after: MedicationDose
    summary: str
    mutates_state: bool = False
    generated_at: datetime


class MedicationLogResult(BaseModel):
    status: Literal["applied", "skipped", "unavailable"]
    dose_id: str
    message: str


# ── Routines ──────────────────────────────────────────────────────────────


class RoutineStepView(BaseModel):
    index: int
    title: str
    description: str = ""
    estimated_minutes: int = 5
    energy_required: Literal["low", "medium", "high"] = "medium"
    cue: str = ""
    completed: bool = False


class RoutineRunView(BaseModel):
    id: str
    routine_id: str
    name: str
    description: str = ""
    variant: Literal["standard", "low_energy"] = "standard"
    status: Literal["pending", "active", "paused", "completed", "skipped"] = "pending"
    started_at: datetime | None = None
    estimated_total_minutes: int = 0
    steps: list[RoutineStepView] = Field(default_factory=list)
    next_step_index: int | None = None


class RoutineDayView(BaseModel):
    date: date
    runs: list[RoutineRunView] = Field(default_factory=list)
    upcoming: list[RoutineRunView] = Field(default_factory=list)
    health: HealthState = "ok"
    message: str | None = None
    generated_at: datetime


class RoutineActionRequest(BaseModel):
    action: Literal["complete_step", "skip_step", "pause", "resume", "cancel", "start"]
    run_id: str | None = None
    routine_id: str | None = None
    step_index: int | None = None
    note: str | None = None


class RoutineActionResult(BaseModel):
    status: Literal["applied", "skipped", "unavailable"]
    run_id: str | None = None
    message: str


# ── Vault corrections ─────────────────────────────────────────────────────


class VaultCorrectionRequest(BaseModel):
    memory_id: str
    operation: Literal["correct", "merge", "delete", "confirm", "mark_stale"]
    new_text: str | None = None
    merge_target_id: str | None = None
    note: str | None = None


class VaultCorrectionPreview(BaseModel):
    memory_id: str
    operation: str
    before: VaultMemoryItem
    after: VaultMemoryItem | None = None
    summary: str
    mutates_state: bool = False
    generated_at: datetime


class VaultCorrectionResult(BaseModel):
    status: Literal["applied", "skipped", "unavailable"]
    memory_id: str
    message: str


# ── Autonomous work ──────────────────────────────────────────────────────


class AutonomousCheckpointView(BaseModel):
    id: str
    label: str
    status: Literal["passed", "pending", "failed"] = "pending"
    occurred_at: datetime | None = None
    summary: str | None = None


class AutonomousDecisionView(BaseModel):
    id: str
    prompt: str
    options: list[str]
    deadline_at: datetime | None = None
    pipeline_id: str | None = None


class AutonomousPlanView(BaseModel):
    id: str
    pipeline_id: str
    title: str
    goal: str
    status: Literal["queued", "running", "paused", "completed", "failed", "cancelled"] = "running"
    started_at: datetime | None = None
    progress: float = 0.0
    completed_steps: int = 0
    total_steps: int = 0
    current_step: str | None = None
    checkpoints: list[AutonomousCheckpointView] = Field(default_factory=list)
    open_decisions: list[AutonomousDecisionView] = Field(default_factory=list)
    last_activity_at: datetime | None = None


class AutonomousView(BaseModel):
    enabled: bool
    active: list[AutonomousPlanView] = Field(default_factory=list)
    queued: list[AutonomousPlanView] = Field(default_factory=list)
    recently_completed: list[AutonomousPlanView] = Field(default_factory=list)
    open_decisions: list[AutonomousDecisionView] = Field(default_factory=list)
    health: HealthState = "ok"
    message: str | None = None
    generated_at: datetime


# ── Integrations & tools ──────────────────────────────────────────────────


class IntegrationStatusView(BaseModel):
    id: str
    label: str
    kind: Literal["mcp", "workspace", "browser", "vault", "claude_code"]
    enabled: bool
    health: HealthState = "ok"
    detail: str | None = None
    last_check_at: datetime | None = None
    tools_available: int = 0
    tools_failing: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class IntegrationToolView(BaseModel):
    integration_id: str
    name: str
    description: str | None = None
    status: Literal["available", "failing", "untested"] = "available"
    last_error: str | None = None


class IntegrationsView(BaseModel):
    integrations: list[IntegrationStatusView]
    tools: list[IntegrationToolView] = Field(default_factory=list)
    generated_at: datetime


# ── Settings (full surface) ───────────────────────────────────────────────


class SettingsValidationIssue(BaseModel):
    path: str
    severity: Literal["error", "warning", "info"] = "warning"
    message: str
    requires_restart: bool = False


class SettingsValidationView(BaseModel):
    valid: bool
    issues: list[SettingsValidationIssue] = Field(default_factory=list)
    generated_at: datetime
