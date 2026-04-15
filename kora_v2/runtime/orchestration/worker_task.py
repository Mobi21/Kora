"""WorkerTask primitive — the orchestration layer's unit of work.

Everything that runs inside the engine is a :class:`WorkerTask`: in-turn
sub-agents, bounded-background maintenance pipelines, and long-running
autonomous jobs all share the same lifecycle, budget, and checkpoint
plumbing. Configuration presets (:data:`IN_TURN`, :data:`BOUNDED_BACKGROUND`,
:data:`LONG_BACKGROUND`) parameterise the three default profiles so callers
do not recreate 20 knobs at every dispatch site.

The dataclass naming matches spec §3.1 — the ``state`` field (not
``lifecycle_state`` as the §17.7 row 2 table mentions) is what the
dispatcher reads and writes. See the "Spec Decisions" section of the
7.5a PR for the note on this choice.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal

from kora_v2.runtime.orchestration.system_state import SystemStatePhase

if TYPE_CHECKING:
    from kora_v2.runtime.orchestration.limiter import RequestLimiter


class WorkerTaskState(StrEnum):
    """The 11 lifecycle states a :class:`WorkerTask` can occupy.

    The dispatcher owns every transition between these states. Tasks
    themselves return ``StepResult`` from their step function and the
    dispatcher decides which state to move them into.
    """

    PENDING = "pending"
    PLANNING = "planning"
    RUNNING = "running"
    CHECKPOINTING = "checkpointing"
    PAUSED_FOR_STATE = "paused_for_state"
    PAUSED_FOR_RATE_LIMIT = "paused_for_rate_limit"
    PAUSED_FOR_DECISION = "paused_for_decision"
    PAUSED_FOR_DEPENDENCY = "paused_for_dependency"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATES: frozenset[WorkerTaskState] = frozenset(
    {
        WorkerTaskState.COMPLETED,
        WorkerTaskState.FAILED,
        WorkerTaskState.CANCELLED,
    }
)

PAUSED_STATES: frozenset[WorkerTaskState] = frozenset(
    {
        WorkerTaskState.PAUSED_FOR_STATE,
        WorkerTaskState.PAUSED_FOR_RATE_LIMIT,
        WorkerTaskState.PAUSED_FOR_DECISION,
        WorkerTaskState.PAUSED_FOR_DEPENDENCY,
    }
)


class RequestClass(StrEnum):
    """Request classes for the sliding-window :class:`RequestLimiter`."""

    CONVERSATION = "conversation"
    NOTIFICATION = "notification"
    BACKGROUND = "background"


WorkerTaskPreset = Literal["in_turn", "bounded_background", "long_background"]


@dataclass
class WorkerTaskConfig:
    """Static per-task configuration — durability, budget, gating."""

    preset: WorkerTaskPreset
    max_duration_seconds: int
    checkpoint_every_seconds: int | None
    request_class: RequestClass
    allowed_states: frozenset[SystemStatePhase]
    tool_scope: list[str]
    pause_on_conversation: bool
    pause_on_topic_overlap: bool
    report_via: frozenset[str]
    blocks_parent: bool
    max_requests_per_hour: int | None = None
    max_requests: int | None = None
    max_context_tokens: int | None = None
    max_cost: float | None = None
    soft_warning_fraction: float = 0.85
    can_be_cancelled: bool = True


# ── Presets ───────────────────────────────────────────────────────────────

IN_TURN = WorkerTaskConfig(
    preset="in_turn",
    max_duration_seconds=300,
    checkpoint_every_seconds=None,
    request_class=RequestClass.CONVERSATION,
    allowed_states=frozenset({SystemStatePhase.CONVERSATION}),
    tool_scope=[],
    pause_on_conversation=False,
    pause_on_topic_overlap=False,
    report_via=frozenset({"return"}),
    blocks_parent=True,
    max_requests_per_hour=None,
    max_requests=None,
    max_context_tokens=20_000,
    max_cost=None,
)

BOUNDED_BACKGROUND = WorkerTaskConfig(
    preset="bounded_background",
    max_duration_seconds=1800,
    checkpoint_every_seconds=300,
    request_class=RequestClass.BACKGROUND,
    allowed_states=frozenset(
        {
            SystemStatePhase.LIGHT_IDLE,
            SystemStatePhase.DEEP_IDLE,
            SystemStatePhase.WAKE_UP_WINDOW,
        }
    ),
    tool_scope=[],
    pause_on_conversation=False,
    pause_on_topic_overlap=False,
    report_via=frozenset({"notification"}),
    blocks_parent=False,
    max_requests_per_hour=20,
    max_requests=60,
    max_context_tokens=120_000,
    max_cost=0.25,
)

LONG_BACKGROUND = WorkerTaskConfig(
    preset="long_background",
    max_duration_seconds=0,
    checkpoint_every_seconds=60,
    request_class=RequestClass.BACKGROUND,
    allowed_states=frozenset(
        {
            SystemStatePhase.LIGHT_IDLE,
            SystemStatePhase.DEEP_IDLE,
            SystemStatePhase.WAKE_UP_WINDOW,
        }
    ),
    tool_scope=[],
    pause_on_conversation=True,
    pause_on_topic_overlap=True,
    report_via=frozenset({"notification", "working_doc"}),
    blocks_parent=False,
    max_requests_per_hour=200,
    max_requests=500,
    max_context_tokens=800_000,
    max_cost=5.00,
)

PRESETS: dict[str, WorkerTaskConfig] = {
    "in_turn": IN_TURN,
    "bounded_background": BOUNDED_BACKGROUND,
    "long_background": LONG_BACKGROUND,
}


def get_preset(name: WorkerTaskPreset) -> WorkerTaskConfig:
    """Return a fresh copy of the named preset.

    A copy (not the module-level singleton) is returned so callers can
    override individual axes via ``dataclasses.replace`` without
    mutating the shared default.
    """
    if name not in PRESETS:
        raise ValueError(f"Unknown worker task preset: {name!r}")
    base = PRESETS[name]
    from dataclasses import replace
    return replace(base, tool_scope=list(base.tool_scope))


# ── Checkpoint ────────────────────────────────────────────────────────────


@dataclass
class Checkpoint:
    """Snapshot of a task's position for durable resume.

    Written to ``worker_tasks.checkpoint_blob`` as JSON. Kept small
    (typical <10KB) so writes are cheap.
    """

    task_id: str
    created_at: datetime
    state: WorkerTaskState
    current_step_index: int
    plan: Any | None = None
    accumulated_artifacts: list[str] = field(default_factory=list)
    working_doc_mtime: float = 0.0
    scratch_state: dict[str, Any] = field(default_factory=dict)
    request_count: int = 0
    agent_turn_count: int = 0


# ── WorkerTask dataclass ──────────────────────────────────────────────────


@dataclass
class WorkerTask:
    """A single unit of work the dispatcher can step forward."""

    id: str
    pipeline_instance_id: str | None
    stage_name: str
    config: WorkerTaskConfig
    goal: str
    system_prompt: str
    parent_task_id: str | None = None
    context_snapshot: str = ""
    depends_on: list[str] = field(default_factory=list)
    state: WorkerTaskState = WorkerTaskState.PENDING
    checkpoint_blob: Checkpoint | None = None
    request_count: int = 0
    agent_turn_count: int = 0
    cancellation_requested: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_step_at: datetime | None = None
    last_checkpoint_at: datetime | None = None
    completed_at: datetime | None = None
    result_summary: str | None = None
    error_message: str | None = None
    result_acknowledged_at: datetime | None = None

    # In-memory, not persisted. Populated by the dispatcher when the
    # task is first picked up so the step function can invoke it.
    step_fn: Callable[[WorkerTask, StepContext], Awaitable[StepResult]] | None = None

    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    def is_paused(self) -> bool:
        return self.state in PAUSED_STATES

    def request_cancellation(self) -> None:
        if self.config.can_be_cancelled:
            self.cancellation_requested = True

    def apply_step_result(self, result: StepResult) -> None:
        self.request_count += result.request_count_delta
        self.agent_turn_count += result.agent_turn_count_delta


# ── Step contract ─────────────────────────────────────────────────────────


StepOutcome = Literal[
    "continue",
    "complete",
    "paused_for_state",
    "paused_for_rate_limit",
    "paused_for_decision",
    "paused_for_dependency",
    "failed",
]


@dataclass
class StepResult:
    """What a step function returns to the dispatcher."""

    outcome: StepOutcome
    artifacts: list[str] = field(default_factory=list)
    progress_marker: str | None = None
    proposed_new_tasks: list[dict[str, Any]] = field(default_factory=list)
    request_count_delta: int = 0
    agent_turn_count_delta: int = 0
    result_summary: str | None = None
    error_message: str | None = None


@dataclass
class StepContext:
    """Per-step environment handed to a step function.

    A fresh instance is constructed by the dispatcher before every
    invocation so step functions never cache references that could
    outlive the surrounding step.
    """

    task: WorkerTask
    limiter: RequestLimiter
    cancellation_flag: Callable[[], bool]
    now: Callable[[], datetime]
    working_doc: Any | None = None
    tool_registry: Any | None = None
    checkpoint_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None
    progress_marker_callback: Callable[[str], Awaitable[None]] | None = None
    extras: dict[str, Any] = field(default_factory=dict)
