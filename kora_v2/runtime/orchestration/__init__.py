"""Kora V2 — Orchestration Layer (Phase 7.5).

Public surface of the orchestration package. Everything downstream of
the dispatcher should import from here rather than from the individual
sub-modules so the file structure can evolve without breaking callers.

Slice 7.5a landed the primitives, dispatcher, triggers, limiter, and
system state machine. Slice 7.5b adds the working-doc filesystem
contract, the template registry, the notification gate, the decision
primitives (moved out of ``kora_v2.autonomous``), the open-decisions
tracker, and the core pipeline catalogue.
"""

from __future__ import annotations

from kora_v2.runtime.orchestration.autonomous_budget import (
    BudgetCheckResult,
    BudgetEnforcer,
)
from kora_v2.runtime.orchestration.checkpointing import CheckpointStore
from kora_v2.runtime.orchestration.decisions import (
    DecisionManager,
    DecisionResult,
    OpenDecision,
    OpenDecisionsTracker,
    PendingDecision,
)
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
    DeliveryChannel,
    DeliveryResult,
    GeneratedNotification,
    NotificationGate,
    PendingNotification,
)
from kora_v2.runtime.orchestration.overlap import (
    OverlapResult,
    check_topic_overlap,
)
from kora_v2.runtime.orchestration.pipeline import (
    FailurePolicy,
    InterruptionPolicy,
    Pipeline,
    PipelineInstance,
    PipelineInstanceState,
    PipelineStage,
)
from kora_v2.runtime.orchestration.profile_bootstrap import (
    DEFAULT_PROFILE_FRONTMATTER,
    BootstrapResult,
    ensure_profile_defaults,
)
from kora_v2.runtime.orchestration.registry import (
    PipelineInstanceRegistry,
    PipelineRegistry,
    TriggerStateStore,
    WorkerTaskRegistry,
    init_orchestration_schema,
)
from kora_v2.runtime.orchestration.scope_validation import (
    REJECTION_REASON_CYCLE,
    REJECTION_REASON_NO_RECURSION,
    REJECTION_REASON_REQUIRES_USER_APPROVAL,
    REJECTION_REASON_UNKNOWN_DEPENDENCY,
    REJECTION_REASONS,
    ScopeValidationError,
    SubTaskSpec,
    validate_dependency_graph,
    validate_subtask_specs,
    validate_tool_scope,
)
from kora_v2.runtime.orchestration.system_state import (
    ACTIVE_IDLE_SECONDS,
    LIGHT_IDLE_SECONDS,
    WAKE_UP_WINDOW_MINUTES,
    SystemStateMachine,
    SystemStatePhase,
    UserScheduleProfile,
)
from kora_v2.runtime.orchestration.templates import (
    DEFAULT_TEMPLATES,
    RenderedTemplate,
    Template,
    TemplatePriority,
    TemplateRegistry,
)
from kora_v2.runtime.orchestration.trigger_evaluator import TriggerEvaluator
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
from kora_v2.runtime.orchestration.working_doc import (
    PlanItem,
    TaskListDiff,
    WorkingDocHandle,
    WorkingDocStatus,
    WorkingDocStore,
    WorkingDocUpdate,
)

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
    "TriggerEvaluator",
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
    # Autonomous budget (7.5c, moved from kora_v2.autonomous.budget)
    "BudgetEnforcer",
    "BudgetCheckResult",
    # Registry
    "PipelineRegistry",
    "WorkerTaskRegistry",
    "PipelineInstanceRegistry",
    "TriggerStateStore",
    "init_orchestration_schema",
    # Working doc (7.5b)
    "WorkingDocStore",
    "WorkingDocUpdate",
    "WorkingDocHandle",
    "WorkingDocStatus",
    "PlanItem",
    "TaskListDiff",
    # Templates (7.5b)
    "TemplateRegistry",
    "Template",
    "RenderedTemplate",
    "TemplatePriority",
    "DEFAULT_TEMPLATES",
    # Notifications (7.5b)
    "NotificationGate",
    "GeneratedNotification",
    "DeliveryChannel",
    "DeliveryResult",
    "PendingNotification",
    # Decisions (7.5b, moved from kora_v2.autonomous)
    "DecisionManager",
    "PendingDecision",
    "DecisionResult",
    "OpenDecisionsTracker",
    "OpenDecision",
    # Overlap (7.5b, moved from kora_v2.autonomous)
    "check_topic_overlap",
    "OverlapResult",
    # User Model profile bootstrap (7.5b, spec §16.3)
    "ensure_profile_defaults",
    "DEFAULT_PROFILE_FRONTMATTER",
    "BootstrapResult",
    # Sub-task scope validation (Phase 8f, spec §4a)
    "ScopeValidationError",
    "SubTaskSpec",
    "validate_tool_scope",
    "validate_dependency_graph",
    "validate_subtask_specs",
    "REJECTION_REASONS",
    "REJECTION_REASON_REQUIRES_USER_APPROVAL",
    "REJECTION_REASON_NO_RECURSION",
    "REJECTION_REASON_CYCLE",
    "REJECTION_REASON_UNKNOWN_DEPENDENCY",
]
