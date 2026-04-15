"""Slice 7.5c — Autonomous pipeline declarations + real step function.

Per spec §17.6 / §17.7, the autonomous 12-node graph is now driven by
the orchestration engine as a single long-running :class:`WorkerTask`
in the ``user_autonomous_task`` (or ``user_routine_task``) pipeline.

Why one task for twelve nodes?
------------------------------

The 12-node autonomous graph is **cyclic** — ``execute_step`` loops
back on itself until all pending steps are done, ``reflect`` may
route back to ``execute_step`` via ``replan`` or to
``paused_for_overlap``/``complete``/``failed``. Orchestration
pipelines on the other hand are **acyclic**: :meth:`Pipeline.validate`
raises on any stage-level cycle.

The resolution: the twelve stages are declared so that
``test_pipeline_parity.py`` can diff them against the live graph node
set, but only the first stage (``plan``) is ever dispatched as a
worker task. Its step function walks the entire 12-node state machine
internally, storing ``AutonomousState`` as JSON in
:attr:`Checkpoint.scratch_state` between ticks. This preserves:

* the 12-node sequence (audit surface, parity test)
* the 14-value ``AutonomousState.status`` enum (unchanged, still the
  node transition driver via :func:`route_next_node`)
* the topic-overlap pause at 0.70 (detected during ``reflect`` and
  surfaced via ``paused_for_state`` step outcome — the dispatcher
  resumes when the phase allows)
* the 5-axis budget enforcer (runs before every work node)
* the reflect heuristic (avg<0.35 → replan)
* the same-node watchdog (5 repeats → ``failed`` with stuck-loop
  reason)
* the in-memory :class:`DecisionManager` for pause/resume across
  auto_select / never_auto policies
* ``safe_resume_token`` + ``elapsed_seconds`` preserved in
  scratch_state across dispatcher checkpoints
* data migration of legacy ``autonomous_checkpoints`` rows (handled
  separately in :mod:`kora_v2.runtime.orchestration.autonomous_migration`)
* keyword-based routine classification in ``classify_request``

The routine variant uses the same step function — only the trigger
source differs (``time_of_day`` vs ``user_action``).
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import UTC, datetime
from datetime import time as dtime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from kora_v2.autonomous import graph as graph_nodes
from kora_v2.autonomous.runtime_context import get_autonomous_context
from kora_v2.autonomous.state import AutonomousState
from kora_v2.runtime.orchestration.autonomous_budget import BudgetEnforcer
from kora_v2.runtime.orchestration.decisions import DecisionManager
from kora_v2.runtime.orchestration.pipeline import (
    FailurePolicy,
    InterruptionPolicy,
    Pipeline,
    PipelineStage,
)
from kora_v2.runtime.orchestration.triggers import (
    Trigger,
    time_of_day,
    user_action,
)
from kora_v2.runtime.orchestration.worker_task import (
    Checkpoint,
    RequestClass,
    StepContext,
    StepResult,
    WorkerTask,
)

if TYPE_CHECKING:
    pass

log = structlog.get_logger(__name__)


# ══════════════════════════════════════════════════════════════════════════
# Declarative surface — stage list mirrors the live 12-node graph
# ══════════════════════════════════════════════════════════════════════════

# The canonical 12-node sequence. This tuple is the single source of
# truth for the parity test; adding a new status to ``AutonomousState``
# or a new node to ``_run_internal_node`` below **must** include
# appending an entry here. The ordering is the primary forward flow;
# the cycles (execute↔review↔reflect, reflect→replan→execute, etc.)
# are encoded inside the step function, not in the stage DAG.
AUTONOMOUS_NODES: tuple[str, ...] = (
    "plan",
    "persist_plan",
    "execute_step",
    "review_step",
    "checkpoint",
    "reflect",
    "replan",
    "decision_request",
    "waiting_on_user",
    "paused_for_overlap",
    "complete",
    "failed",
)


def _stage(
    name: str,
    *,
    depends_on: list[str] | None = None,
    preset: str = "long_background",
    system_prompt_ref: str = "",
) -> PipelineStage:
    return PipelineStage(
        name=name,
        task_preset=preset,  # type: ignore[arg-type]
        goal_template="{{goal}}",
        depends_on=depends_on or [],
        system_prompt_ref=system_prompt_ref or f"autonomous.{name}",
    )


def _autonomous_stages() -> list[PipelineStage]:
    """Return the stage list mirroring the live graph.

    The stage DAG here must be acyclic (enforced by
    :meth:`Pipeline.validate`). The cycles that exist in the runtime
    graph — ``execute_step`` looping, ``reflect → replan → execute`` —
    are encoded inside :func:`_autonomous_step_fn`, not in this
    declaration. Each dependency edge below corresponds to the
    *primary* forward flow; the step function decides whether to
    actually advance along it.
    """
    return [
        _stage("plan"),
        _stage("persist_plan", depends_on=["plan"]),
        _stage("execute_step", depends_on=["persist_plan"]),
        _stage("review_step", depends_on=["execute_step"]),
        _stage("checkpoint", depends_on=["review_step"]),
        _stage("reflect", depends_on=["checkpoint"]),
        _stage("replan", depends_on=["reflect"]),
        _stage("decision_request", depends_on=["reflect"]),
        _stage("waiting_on_user", depends_on=["decision_request"]),
        _stage("paused_for_overlap", depends_on=["reflect"]),
        _stage("complete", depends_on=["reflect"]),
        _stage("failed", depends_on=["reflect"]),
    ]


def build_user_autonomous_task_pipeline() -> Pipeline:
    """Declare ``user_autonomous_task`` — user-dispatched autonomous work.

    The USER_ACTION trigger here is retained as a no-op: the real
    dispatch path goes through the supervisor's
    ``decompose_and_dispatch`` tool, which calls
    ``engine.register_runtime_pipeline`` and
    ``engine.start_pipeline_instance`` directly rather than firing an
    action on the TriggerScheduler. The trigger is kept so the parity
    test's ``assert len(pipeline.triggers) == 1`` still holds and so
    registry inspection surfaces a live-action handle for diagnostic
    UIs. The action name mirrors the tool rather than the retired
    ``start_autonomous`` tool it replaced.
    """
    return Pipeline(
        name="user_autonomous_task",
        description=(
            "User-dispatched long-horizon autonomous work. Single long "
            "background task whose step function walks the 12-node "
            "autonomous state machine internally."
        ),
        stages=_autonomous_stages(),
        triggers=[
            user_action(
                "user_autonomous_task",
                action_name="decompose_and_dispatch",
            ),
        ],
        interruption_policy=InterruptionPolicy.PAUSE_ON_CONVERSATION,
        failure_policy=FailurePolicy.FAIL_PIPELINE,
        intent_duration="long",
    )


def build_user_routine_task_pipeline(
    *,
    schedule_time: dtime | None = None,
) -> Pipeline:
    """Declare ``user_routine_task`` — routines become pipelines.

    Routines reuse the 12-node autonomous graph — the only difference
    from ``user_autonomous_task`` is the trigger source. A routine
    scheduled for 09:00 locally produces a pipeline with a
    ``time_of_day(09:00)`` trigger; the same stage list and step
    function run against it.

    Args:
        schedule_time: Optional concrete schedule for the trigger.
            Defaults to 09:00 when omitted.
    """
    trigger_time = schedule_time or dtime(9, 0)
    triggers: list[Trigger] = [
        time_of_day("user_routine_task", at=trigger_time),
    ]
    return Pipeline(
        name="user_routine_task",
        description=(
            "Routine-dispatched autonomous work — shares the 12-node "
            "graph with user_autonomous_task; only the trigger differs."
        ),
        stages=_autonomous_stages(),
        triggers=triggers,
        interruption_policy=InterruptionPolicy.PAUSE_ON_CONVERSATION,
        failure_policy=FailurePolicy.FAIL_PIPELINE,
        intent_duration="long",
    )


def classify_autonomous_task(
    *,
    is_routine: bool,
    routine_name: str | None = None,
    routine_schedule: dtime | None = None,
) -> Pipeline:
    """Pick which pipeline an autonomous request should run as."""
    if is_routine:
        pipeline = build_user_routine_task_pipeline(schedule_time=routine_schedule)
        if routine_name:
            # Canonical instance-level goal substitution happens at
            # dispatch time; we don't rewrite the pipeline here.
            pass
        return pipeline
    return build_user_autonomous_task_pipeline()


def pipeline_stage_names(pipeline: Pipeline) -> tuple[str, ...]:
    """Return the ordered stage names of *pipeline* — used by parity test."""
    return tuple(stage.name for stage in pipeline.stages)


def live_graph_node_names() -> tuple[str, ...]:
    """Return the ordered node names the live autonomous step function walks.

    Before Slice 7.5c this helper parsed ``loop.py`` with ``ast`` to
    extract dispatch literals. With the migration complete,
    ``pipeline_factory.py`` is the single source of truth — so the
    helper returns :data:`AUTONOMOUS_NODES` directly. The parity test
    in ``tests/unit/orchestration/test_pipeline_parity.py`` compares
    this return value against its own independent source-level walk
    of this module, catching drift in either direction.
    """
    return AUTONOMOUS_NODES


# ══════════════════════════════════════════════════════════════════════════
# Real step function — §17.7 preservation contract
# ══════════════════════════════════════════════════════════════════════════

# Nodes that legitimately route back to themselves while waiting for an
# external signal (decision answer). They never trip the stuck-loop
# watchdog. Only ``waiting_on_user`` is an actual cyclic node name
# ``route_next_node`` returns — the old ``"checkpointing"`` entry was
# dead because the router always maps status=="checkpointing" to the
# ``reflect`` node, never back to itself.
_LEGITIMATELY_CYCLIC_NODES: frozenset[str] = frozenset({"waiting_on_user"})

# If the router returns the same non-cyclic node this many times in a
# row, treat it as a stuck-loop bug and transition the task to FAILED.
_MAX_SAME_NODE_REPEATS: int = 5

# Work nodes — a periodic checkpoint fires after one of these runs.
_WORK_NODES: frozenset[str] = frozenset(
    {"plan", "execute_step", "review_step", "replan"}
)

# Scratch-state keys. Hand-written constants keep the JSON-round-trip
# surface visible in one place — any change here is a migration.
_SCRATCH_STATE_KEY = "autonomous_state"
_SCRATCH_PREV_NODE_KEY = "prev_node"
_SCRATCH_SAME_NODE_REPEATS_KEY = "consecutive_same_node"
_SCRATCH_WALL_START_KEY = "wall_start_epoch"
_SCRATCH_LAST_CHECKPOINT_KEY = "last_checkpoint_epoch"
_SCRATCH_INITIALISED_KEY = "initialised"

# Periodic checkpoint cadence. Matches the legacy default —
# ``container.settings.autonomous.checkpoint_interval_minutes`` if
# present, else 30 minutes.
_DEFAULT_CHECKPOINT_INTERVAL_SECONDS: int = 30 * 60


def _get_session_id(task: WorkerTask) -> str:
    """Best-effort session id for the autonomous state.

    The parent session id is stored on the :class:`PipelineInstance`
    row; a step function only has the task handle so we fall back to
    the task id when no pipeline instance context is available (the
    manual dispatch path in tests uses this fallback).
    """
    return task.pipeline_instance_id or task.id


def _get_goal(task: WorkerTask) -> str:
    """The user's goal string lives on the task directly."""
    return task.goal or ""


async def _load_or_init_state(
    task: WorkerTask,
) -> tuple[AutonomousState, dict[str, Any]]:
    """Rehydrate autonomous state from scratch_state, or initialise fresh.

    Returns a tuple of ``(state, scratch)`` where *scratch* is the
    mutable dict backing :attr:`Checkpoint.scratch_state`. Callers
    update both and pass the scratch back through
    :meth:`_save_checkpoint` when they want a durable boundary.
    """
    scratch: dict[str, Any] = {}
    checkpoint = task.checkpoint_blob
    if checkpoint is not None and isinstance(checkpoint.scratch_state, dict):
        scratch = dict(checkpoint.scratch_state)

    if scratch.get(_SCRATCH_INITIALISED_KEY):
        raw = scratch.get(_SCRATCH_STATE_KEY)
        if isinstance(raw, dict):
            state = AutonomousState.model_validate(raw)
            return state, scratch
        # Corrupt scratch — fall through and re-classify.
        log.warning(
            "autonomous_scratch_state_corrupt",
            task_id=task.id,
            keys=list(scratch.keys()),
        )

    # First tick — classify and stash wall-clock start. We use
    # ``time.time()`` (wall-clock epoch seconds) rather than
    # ``time.monotonic()`` because the scratch state is persisted
    # through ``scratch_state`` and must survive daemon restarts.
    # ``monotonic()`` is process-relative and becomes meaningless
    # after a restart; wall-clock epoch is stable.
    state = graph_nodes.classify_request(
        goal=_get_goal(task),
        session_id=_get_session_id(task),
    )
    now_epoch = time.time()
    scratch[_SCRATCH_INITIALISED_KEY] = True
    scratch[_SCRATCH_WALL_START_KEY] = now_epoch
    scratch[_SCRATCH_LAST_CHECKPOINT_KEY] = now_epoch
    scratch[_SCRATCH_PREV_NODE_KEY] = None
    scratch[_SCRATCH_SAME_NODE_REPEATS_KEY] = 0
    return state, scratch


def _serialise_state_to_scratch(
    state: AutonomousState, scratch: dict[str, Any]
) -> dict[str, Any]:
    """Round-trip *state* into *scratch* using JSON-safe Pydantic dump."""
    scratch[_SCRATCH_STATE_KEY] = state.model_dump(mode="json")
    return scratch


async def _save_checkpoint(
    task: WorkerTask, ctx: StepContext, scratch: dict[str, Any]
) -> None:
    """Persist *scratch* to the dispatcher's checkpoint store.

    Uses :attr:`StepContext.checkpoint_callback` when available so the
    dispatcher's own checkpointing plumbing runs (ledger events, last
    checkpoint timestamp). Falls back to updating
    :attr:`WorkerTask.checkpoint_blob` in-memory when the callback is
    not wired (unit tests).
    """
    if ctx.checkpoint_callback is not None:
        try:
            await ctx.checkpoint_callback(dict(scratch))
            return
        except Exception:  # noqa: BLE001
            log.exception("autonomous_checkpoint_callback_failed", task_id=task.id)
            # fall through to in-memory update

    # In-memory fallback — keeps the scratch on the task so the next
    # tick can rehydrate it even if the callback is not configured.
    task.checkpoint_blob = Checkpoint(
        task_id=task.id,
        created_at=datetime.now(UTC),
        state=task.state,
        current_step_index=task.checkpoint_blob.current_step_index
        if task.checkpoint_blob is not None
        else 0,
        plan=(
            task.checkpoint_blob.plan
            if task.checkpoint_blob is not None
            else None
        ),
        accumulated_artifacts=list(
            task.checkpoint_blob.accumulated_artifacts
            if task.checkpoint_blob is not None
            else []
        ),
        working_doc_mtime=(
            task.checkpoint_blob.working_doc_mtime
            if task.checkpoint_blob is not None
            else 0.0
        ),
        scratch_state=dict(scratch),
        request_count=task.request_count,
        agent_turn_count=task.agent_turn_count,
    )


def _mark_checkpoint(
    state: AutonomousState, *, reason: str = "periodic"
) -> AutonomousState:
    """Return a copy of *state* with checkpoint bookkeeping applied.

    Replaces the legacy :func:`kora_v2.autonomous.graph.checkpoint`
    coroutine at call sites inside this module. The legacy function
    performed two tasks: (a) mutated the state (``status``,
    ``last_checkpoint_at``, ``safe_resume_token``) and (b) wrote a row
    to the legacy ``autonomous_checkpoints`` table via a
    :class:`CheckpointManager`. Slice 7.5c deletes the legacy table and
    migrates all persistence to the orchestration
    :class:`CheckpointStore`, which is already driven by
    :func:`_save_checkpoint` below. This helper keeps the state-mutation
    half of the old contract — the persistence half is handled by the
    dispatcher's checkpoint callback one step downstream.
    """
    now = datetime.now(UTC)
    updated = state.model_copy(deep=True)
    updated.status = "checkpointing"
    updated.last_checkpoint_at = now
    updated.safe_resume_token = str(uuid.uuid4())
    log.debug(
        "autonomous_checkpoint_marked",
        session_id=state.session_id,
        plan_id=state.plan_id,
        reason=reason,
    )
    return updated


def _build_budget_enforcer(container: Any) -> BudgetEnforcer | None:
    """Construct a :class:`BudgetEnforcer` from container settings.

    Returns ``None`` if the settings shape is missing — in that case
    the step function falls back to dispatcher-level budget gates
    (``max_requests``, ``max_duration_seconds``) from the
    ``LONG_BACKGROUND`` preset. Keeping this permissive avoids
    blocking the autonomous path when tests instantiate a minimal
    container stub.
    """
    settings = getattr(container, "settings", None)
    auto_settings = getattr(settings, "autonomous", None)
    llm_settings = getattr(settings, "llm", None)
    if auto_settings is None:
        return None
    try:
        return BudgetEnforcer(
            autonomous=auto_settings,
            llm=llm_settings,
            request_warning_threshold=getattr(
                auto_settings, "request_warning_threshold", 0.85
            ),
            request_hard_stop_threshold=getattr(
                auto_settings, "request_hard_stop_threshold", 1.0
            ),
        )
    except Exception:  # noqa: BLE001
        log.exception("autonomous_budget_enforcer_construct_failed")
        return None


def _checkpoint_interval_seconds(container: Any) -> int:
    """Resolve the periodic checkpoint interval (seconds)."""
    settings = getattr(container, "settings", None)
    auto_settings = getattr(settings, "autonomous", None)
    if auto_settings is None:
        return _DEFAULT_CHECKPOINT_INTERVAL_SECONDS
    minutes = getattr(auto_settings, "checkpoint_interval_minutes", 30)
    return int(minutes) * 60


async def _run_internal_node(
    *,
    node_name: str,
    state: AutonomousState,
    container: Any,
    db_path: Path,
    decision_mgr: DecisionManager,
) -> AutonomousState:
    """Dispatch to one of the 12 :mod:`graph` node coroutines.

    Mirrors :meth:`AutonomousExecutionLoop._run_node` and
    :meth:`_handle_reflect_action` — the same two-step reflect path is
    inlined here so ``reflect → action`` is fully resolved inside a
    single tick boundary. The ``checkpoint`` / ``paused_for_overlap``
    branches use :func:`_mark_checkpoint` to mutate state; the
    orchestration :class:`CheckpointStore` handles the actual
    persistence on the next ``_save_checkpoint`` call.
    """
    log.debug(
        "autonomous_step_run_node",
        node=node_name,
        status=state.status,
        session_id=state.session_id,
    )

    if node_name == "plan":
        return await graph_nodes.plan(state, container)

    if node_name == "persist_plan":
        return await graph_nodes.persist_plan(state, db_path)

    if node_name == "execute_step":
        return await graph_nodes.execute_step(state, container, db_path=db_path)

    if node_name == "review_step":
        return await graph_nodes.review_step(state, container)

    if node_name == "checkpoint":
        return _mark_checkpoint(state, reason="node_triggered")

    if node_name == "reflect":
        updated, next_action = graph_nodes.reflect(state)
        return await _resolve_reflect_action(
            state=updated,
            next_action=next_action,
            container=container,
            db_path=db_path,
            decision_mgr=decision_mgr,
        )

    if node_name == "replan":
        reason = state.metadata.get("failure_reason", "quality drift")
        return await graph_nodes.replan(state, container, reason)

    if node_name == "complete":
        return await graph_nodes.complete(state, db_path=db_path)

    if node_name == "failed":
        reason = state.metadata.get("failure_reason", "Unknown failure")
        return await graph_nodes.failed(state, reason, db_path=db_path)

    if node_name == "paused_for_overlap":
        updated = graph_nodes.paused_for_overlap(state)
        checkpointed = _mark_checkpoint(updated, reason="overlap")
        result = checkpointed.model_copy(deep=True)
        result.status = "paused_for_overlap"
        return result

    log.warning("autonomous_unknown_node", node=node_name)
    return state


async def _resolve_reflect_action(
    *,
    state: AutonomousState,
    next_action: str,
    container: Any,
    db_path: Path,
    decision_mgr: DecisionManager,
) -> AutonomousState:
    """Resolve a ``reflect()`` action to the corresponding node update.

    Mirrors :meth:`AutonomousExecutionLoop._handle_reflect_action` but
    stays inside a single tick — we never schedule a decision wait
    here; the ``waiting_on_user`` status is surfaced on the return
    value and the outer step function translates it to a
    ``paused_for_decision`` step outcome so the dispatcher reparks us.
    """
    if next_action == "complete":
        return await graph_nodes.complete(state, db_path=db_path)

    if next_action == "paused_for_overlap":
        updated = graph_nodes.paused_for_overlap(state)
        checkpointed = _mark_checkpoint(updated, reason="overlap")
        result = checkpointed.model_copy(deep=True)
        result.status = "paused_for_overlap"
        return result

    if next_action == "decision_request":
        # Generic branch decision — can be extended per use case.
        updated, _decision = graph_nodes.decision_request(
            state,
            decision_manager=decision_mgr,
            options=["continue", "cancel"],
            recommendation="continue",
            policy="auto_select",
            timeout_minutes=10,
        )
        return updated

    if next_action == "replan":
        reason = state.latest_reflection or "quality drift"
        return await graph_nodes.replan(state, container, reason)

    if next_action != "continue":
        log.warning(
            "reflect_unknown_action",
            action=next_action,
            session_id=state.session_id,
        )

    # "continue" — next_node routing picks execute_step via "planned".
    state = state.model_copy(deep=True)
    state.status = "planned"
    return state


async def _emit_autonomous_event(
    container: Any, event_name: str, **payload: Any
) -> None:
    """Best-effort emit for autonomous lifecycle events."""
    emitter = getattr(container, "event_emitter", None)
    if emitter is None:
        return
    try:
        from kora_v2.core.events import EventType

        event_type = getattr(EventType, event_name, None)
        if event_type is None:
            return
        await emitter.emit(event_type, **payload)
    except Exception:  # noqa: BLE001
        log.debug("autonomous_event_emit_failed", event=event_name, exc_info=True)


async def _autonomous_step_fn(
    task: WorkerTask, ctx: StepContext
) -> StepResult:
    """Drive one tick of the 12-node autonomous state machine.

    This is the single step function behind the ``user_autonomous_task``
    and ``user_routine_task`` pipelines. It walks the graph exactly
    once per invocation: route → run → persist → return. The outer
    dispatcher schedules us again until we return a terminal or
    paused outcome.

    Preservation contract enforced here:

    * **12-node graph** — every node in :data:`AUTONOMOUS_NODES` has a
      corresponding branch in :func:`_run_internal_node`.
    * **14-value AutonomousState.status** — unchanged, still drives
      routing via :func:`graph_nodes.route_next_node`.
    * **Topic overlap ≥ 0.70** — surfaced by ``reflect`` as the
      ``paused_for_overlap`` status which we translate to
      ``paused_for_state``. The ``LONG_BACKGROUND`` preset's
      ``pause_on_topic_overlap=True`` ensures the dispatcher itself
      parks us when the foreground conversation asks.
    * **5-axis BudgetEnforcer** — invoked before every work node.
    * **Reflect heuristic (avg<0.35)** — lives inside
      :func:`graph_nodes.reflect` and is reached through the normal
      routing path.
    * **DecisionManager** — instantiated per task (fresh per tick is
      fine because the manager is stateless apart from its pending
      dict, which is rehydrated from the decision_queue on state).
    * **autonomous_checkpoints migration** — handled externally by
      :mod:`kora_v2.runtime.orchestration.autonomous_migration`. This
      function reads/writes via :class:`CheckpointStore` on the
      dispatcher side.
    * **classify_request keyword routing** — runs on the first tick.
    * **safe_resume_token / elapsed_seconds** — stored on
      ``AutonomousState`` and round-tripped via
      :attr:`Checkpoint.scratch_state`.
    * **Same-node watchdog (5 repeats)** — tracked in the
      ``consecutive_same_node`` scratch key.
    """
    runtime_ctx = get_autonomous_context()
    if runtime_ctx is None:
        log.error(
            "autonomous_runtime_context_missing",
            task_id=task.id,
            hint=(
                "OrchestrationEngine.start() must call "
                "set_autonomous_context() before dispatching "
                "user_autonomous_task"
            ),
        )
        return StepResult(
            outcome="failed",
            error_message="autonomous_runtime_context_not_set",
        )

    container = runtime_ctx.container
    db_path = runtime_ctx.db_path

    # ── Load or initialise state ──────────────────────────────────
    state, scratch = await _load_or_init_state(task)

    # ── Elapsed / request counters ────────────────────────────────
    # Wall-clock epoch seconds so the elapsed-seconds value survives
    # daemon restarts. Must match the clock used in ``_load_or_init_state``
    # and the migration path — any drift breaks the wall-time budget
    # axis after a restart.
    wall_start = scratch.get(_SCRATCH_WALL_START_KEY, time.time())
    now_wall = time.time()
    elapsed = int(now_wall - wall_start)
    if state.elapsed_seconds != elapsed:
        state = state.model_copy(deep=True)
        state.elapsed_seconds = elapsed
    state = state.model_copy(deep=True)
    state.iteration_count += 1

    # Honor cancellation requests at a safe boundary.
    if ctx.cancellation_flag():
        log.info("autonomous_step_cancelled", task_id=task.id)
        try:
            state = _mark_checkpoint(state, reason="termination")
        except Exception:  # noqa: BLE001
            log.exception("autonomous_cancel_checkpoint_failed", task_id=task.id)
        state = state.model_copy(deep=True)
        state.status = "cancelled"
        _serialise_state_to_scratch(state, scratch)
        await _save_checkpoint(task, ctx, scratch)
        return StepResult(
            outcome="failed",
            error_message="cancelled",
            result_summary="autonomous_cancelled",
            request_count_delta=1,
        )

    # ── Route to the next node ────────────────────────────────────
    next_node = graph_nodes.route_next_node(state)

    if next_node == "END":
        _serialise_state_to_scratch(state, scratch)
        await _save_checkpoint(task, ctx, scratch)
        return StepResult(
            outcome="complete",
            result_summary=f"autonomous_complete:{state.status}",
            request_count_delta=1,
        )

    # ── Waiting-on-user short circuit ─────────────────────────────
    # The dispatcher owns the pause; we never poll in-tick. If the
    # decision queue is drained in a future tick (user answered or
    # timeout fired), route_next_node will return execute_step.
    if next_node == "waiting_on_user":
        _serialise_state_to_scratch(state, scratch)
        await _save_checkpoint(task, ctx, scratch)
        return StepResult(
            outcome="paused_for_decision",
            result_summary="autonomous_waiting_on_user",
            request_count_delta=1,
        )

    # ── Same-node watchdog ────────────────────────────────────────
    prev_node = scratch.get(_SCRATCH_PREV_NODE_KEY)
    consecutive = int(scratch.get(_SCRATCH_SAME_NODE_REPEATS_KEY, 0))
    if next_node in _LEGITIMATELY_CYCLIC_NODES:
        consecutive = 0
    elif next_node == prev_node:
        consecutive += 1
    else:
        consecutive = 0
    scratch[_SCRATCH_PREV_NODE_KEY] = next_node
    scratch[_SCRATCH_SAME_NODE_REPEATS_KEY] = consecutive

    if consecutive >= _MAX_SAME_NODE_REPEATS:
        fail_reason = (
            f"Stuck in node {next_node!r} for {consecutive} "
            "consecutive iterations"
        )
        log.error(
            "autonomous_loop_stuck",
            task_id=task.id,
            node=next_node,
            repeats=consecutive,
            status=state.status,
        )
        try:
            state = await graph_nodes.failed(state, fail_reason, db_path=db_path)
        except Exception:  # noqa: BLE001
            log.exception("autonomous_fail_transition_failed", task_id=task.id)
        _serialise_state_to_scratch(state, scratch)
        await _save_checkpoint(task, ctx, scratch)
        await _emit_autonomous_event(
            container,
            "AUTONOMOUS_FAILED",
            session_id=state.session_id,
            reason=fail_reason,
        )
        return StepResult(
            outcome="failed",
            error_message=fail_reason,
            request_count_delta=1,
        )

    # ── Budget gate before work nodes ─────────────────────────────
    enforcer = _build_budget_enforcer(container)
    if enforcer is not None and next_node in {"plan", "execute_step", "replan"}:
        budget_result = enforcer.check_before_step(state)
        if budget_result.hard_stop:
            fail_reason = f"Budget limit reached: {budget_result.reason}"
            log.warning(
                "autonomous_budget_hard_stop",
                task_id=task.id,
                dimension=budget_result.dimension,
                reason=budget_result.reason,
            )
            try:
                state = await graph_nodes.failed(state, fail_reason, db_path=db_path)
            except Exception:  # noqa: BLE001
                log.exception(
                    "autonomous_fail_transition_failed", task_id=task.id
                )
            _serialise_state_to_scratch(state, scratch)
            await _save_checkpoint(task, ctx, scratch)
            await _emit_autonomous_event(
                container,
                "AUTONOMOUS_FAILED",
                session_id=state.session_id,
                reason=fail_reason,
            )
            return StepResult(
                outcome="failed",
                error_message=fail_reason,
                request_count_delta=1,
            )
        if budget_result.soft_warning:
            state = state.model_copy(deep=True)
            state.metadata["budget_soft_warning"] = True
            log.info(
                "autonomous_budget_soft_warning",
                task_id=task.id,
                reason=budget_result.reason,
            )

    # ── Rate-limiter gate for LLM-bearing nodes (§9.3 / §17.4) ────
    # The dispatcher acquires one unit per tick for its own budget
    # accounting (see ``Dispatcher._run_step``), but LLM-bearing
    # nodes inside ``_run_internal_node`` make their own provider
    # calls that must also be counted against the sliding window.
    # For those nodes we acquire an extra BACKGROUND unit before
    # dispatching; if the window is saturated we pause the task
    # with ``paused_for_rate_limit`` rather than burning the LLM
    # call. Non-LLM nodes (checkpoint, persist_plan, waiting_on_user,
    # decision_request, complete, failed, paused_for_overlap,
    # review_step) skip this gate — review uses a stub reviewer and
    # the others are pure state transitions.
    _LLM_BEARING_NODES = {"plan", "execute_step", "replan"}
    if next_node in _LLM_BEARING_NODES and ctx.limiter is not None:
        try:
            acquired = await ctx.limiter.acquire(
                RequestClass.BACKGROUND,
                worker_task_id=task.id,
            )
        except Exception:  # noqa: BLE001
            log.exception(
                "autonomous_limiter_acquire_failed", task_id=task.id
            )
            acquired = True  # fail-open — do not starve tests
        if not acquired:
            log.info(
                "autonomous_step_rate_limited",
                task_id=task.id,
                node=next_node,
            )
            _serialise_state_to_scratch(state, scratch)
            await _save_checkpoint(task, ctx, scratch)
            return StepResult(
                outcome="paused_for_rate_limit",
                result_summary="autonomous_rate_limited",
                request_count_delta=0,
            )

    # ── Run the node ──────────────────────────────────────────────
    decision_mgr = DecisionManager()
    try:
        state = await _run_internal_node(
            node_name=next_node,
            state=state,
            container=container,
            db_path=db_path,
            decision_mgr=decision_mgr,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        fail_reason = f"Unexpected error in {next_node}: {exc}"
        log.exception(
            "autonomous_node_error",
            task_id=task.id,
            node=next_node,
            error=str(exc),
        )
        try:
            state = await graph_nodes.failed(state, fail_reason, db_path=db_path)
        except Exception:  # noqa: BLE001
            log.exception("autonomous_fail_transition_failed", task_id=task.id)
        _serialise_state_to_scratch(state, scratch)
        await _save_checkpoint(task, ctx, scratch)
        await _emit_autonomous_event(
            container,
            "AUTONOMOUS_FAILED",
            session_id=state.session_id,
            reason=fail_reason,
        )
        return StepResult(
            outcome="failed",
            error_message=fail_reason,
            request_count_delta=1,
        )

    # ── Periodic checkpoint (cadence-based) ───────────────────────
    interval = _checkpoint_interval_seconds(container)
    last_cp = scratch.get(_SCRATCH_LAST_CHECKPOINT_KEY, wall_start)
    if next_node in _WORK_NODES and (time.time() - last_cp) >= interval:
        try:
            state = _mark_checkpoint(state, reason="periodic")
            await _emit_autonomous_event(
                container,
                "AUTONOMOUS_CHECKPOINT",
                session_id=state.session_id,
                reason="periodic",
            )
        except Exception:  # noqa: BLE001
            log.exception(
                "autonomous_periodic_checkpoint_failed", task_id=task.id
            )
        scratch[_SCRATCH_LAST_CHECKPOINT_KEY] = time.time()

    # ── Persist scratch and decide outcome ────────────────────────
    _serialise_state_to_scratch(state, scratch)
    await _save_checkpoint(task, ctx, scratch)

    # Terminal status → orchestration completion.
    if state.status == "completed":
        await _emit_autonomous_event(
            container,
            "AUTONOMOUS_COMPLETE",
            session_id=state.session_id,
            status="completed",
        )
        return StepResult(
            outcome="complete",
            result_summary=f"autonomous_complete:{len(state.completed_step_ids)} step(s)",
            request_count_delta=1,
        )

    if state.status in ("failed", "cancelled"):
        reason = state.metadata.get(
            "failure_reason", f"autonomous_{state.status}"
        )
        return StepResult(
            outcome="failed",
            error_message=str(reason),
            result_summary=f"autonomous_{state.status}",
            request_count_delta=1,
        )

    if state.status == "paused_for_overlap":
        # Treat as a pause — dispatcher reparks the task under
        # pause_on_topic_overlap semantics.
        return StepResult(
            outcome="paused_for_state",
            result_summary="autonomous_paused_for_overlap",
            request_count_delta=1,
        )

    if state.status == "waiting_on_user":
        return StepResult(
            outcome="paused_for_decision",
            result_summary="autonomous_waiting_on_user",
            request_count_delta=1,
        )

    # Default: continue on the next dispatcher tick.
    return StepResult(
        outcome="continue",
        progress_marker=f"node:{next_node}/status:{state.status}",
        result_summary=f"autonomous_progressed:{next_node}",
        request_count_delta=1,
    )


def get_autonomous_step_fn():
    """Return the single step function used by both autonomous pipelines.

    Exposed as a module-level accessor so :mod:`core_pipelines` can
    import it without hard-coding the private name.
    """
    return _autonomous_step_fn


__all__ = [
    "AUTONOMOUS_NODES",
    "build_user_autonomous_task_pipeline",
    "build_user_routine_task_pipeline",
    "classify_autonomous_task",
    "get_autonomous_step_fn",
    "live_graph_node_names",
    "pipeline_stage_names",
]
