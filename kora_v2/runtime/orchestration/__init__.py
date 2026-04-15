"""Kora V2 — Orchestration Layer (Phase 7.5).

Public surface of the orchestration package. Everything downstream of
the dispatcher should import from here rather than from the individual
sub-modules so the file structure can evolve without breaking callers.

This is Slice 7.5a (Primitives + Dispatcher). Trigger evaluation loop,
working-doc filesystem contract, and notification gate arrive in 7.5b;
the template catalogue arrives in 7.5c.
"""

from __future__ import annotations

from kora_v2.runtime.orchestration.checkpointing import CheckpointStore
from kora_v2.runtime.orchestration.dispatcher import Dispatcher
from kora_v2.runtime.orchestration.engine import OrchestrationEngine
from kora_v2.runtime.orchestration.ledger import (
    LedgerEvent,
    LedgerEventType,
    WorkLedger,
)
from kora_v2.runtime.orchestration.limiter import (
    CONVERSATION_RESERVE,
    NOTIFICATION_RESERVE,
    WINDOW_CAPACITY,
    WINDOW_SECONDS,
    LimiterSnapshot,
    RequestLimiter,
)
from kora_v2.runtime.orchestration.notifications import (
    NotificationGate,
    PendingNotification,
)
from kora_v2.runtime.orchestration.pipeline import (
    FailurePolicy,
    InterruptionPolicy,
    Pipeline,
    PipelineInstance,
    PipelineInstanceState,
    PipelineStage,
)
from kora_v2.runtime.orchestration.registry import (
    PipelineInstanceRegistry,
    PipelineRegistry,
    TriggerStateStore,
    WorkerTaskRegistry,
    init_orchestration_schema,
)
from kora_v2.runtime.orchestration.system_state import (
    ACTIVE_IDLE_SECONDS,
    LIGHT_IDLE_SECONDS,
    WAKE_UP_WINDOW_MINUTES,
    SystemStateMachine,
    SystemStatePhase,
    UserScheduleProfile,
)
from kora_v2.runtime.orchestration.templates import demo_tick_pipeline
from kora_v2.runtime.orchestration.triggers import (
    ConditionFn,
    Trigger,
    TriggerContext,
    TriggerKind,
    all_of,
    any_of,
    condition,
    event,
    interval,
    sequence_complete,
    time_of_day,
    user_action,
)
from kora_v2.runtime.orchestration.worker_task import (
    BOUNDED_BACKGROUND,
    IN_TURN,
    LONG_BACKGROUND,
    PAUSED_STATES,
    PRESETS,
    TERMINAL_STATES,
    Checkpoint,
    RequestClass,
    StepContext,
    StepOutcome,
    StepResult,
    WorkerTask,
    WorkerTaskConfig,
    WorkerTaskPreset,
    WorkerTaskState,
    get_preset,
)
from kora_v2.runtime.orchestration.working_doc import WorkingDocHandle

__all__ = [
    # Engine
    "OrchestrationEngine",
    "Dispatcher",
    # Worker task
    "WorkerTask",
    "WorkerTaskConfig",
    "WorkerTaskPreset",
    "WorkerTaskState",
    "TERMINAL_STATES",
    "PAUSED_STATES",
    "RequestClass",
    "StepContext",
    "StepOutcome",
    "StepResult",
    "Checkpoint",
    "PRESETS",
    "IN_TURN",
    "BOUNDED_BACKGROUND",
    "LONG_BACKGROUND",
    "get_preset",
    # Pipeline
    "Pipeline",
    "PipelineStage",
    "PipelineInstance",
    "PipelineInstanceState",
    "InterruptionPolicy",
    "FailurePolicy",
    # Triggers
    "Trigger",
    "TriggerKind",
    "TriggerContext",
    "ConditionFn",
    "interval",
    "event",
    "condition",
    "time_of_day",
    "sequence_complete",
    "user_action",
    "any_of",
    "all_of",
    # System state
    "SystemStatePhase",
    "SystemStateMachine",
    "UserScheduleProfile",
    "ACTIVE_IDLE_SECONDS",
    "LIGHT_IDLE_SECONDS",
    "WAKE_UP_WINDOW_MINUTES",
    # Limiter
    "RequestLimiter",
    "LimiterSnapshot",
    "WINDOW_SECONDS",
    "WINDOW_CAPACITY",
    "CONVERSATION_RESERVE",
    "NOTIFICATION_RESERVE",
    # Ledger
    "WorkLedger",
    "LedgerEventType",
    "LedgerEvent",
    # Checkpoint
    "CheckpointStore",
    # Registry
    "PipelineRegistry",
    "WorkerTaskRegistry",
    "PipelineInstanceRegistry",
    "TriggerStateStore",
    "init_orchestration_schema",
    # Stubs
    "WorkingDocHandle",
    "NotificationGate",
    "PendingNotification",
    "demo_tick_pipeline",
]
