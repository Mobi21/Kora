"""Slice 7.5c — regression tests for review findings H1–H4 / M2.

Each test in this file pins a specific fix from the Slice 7.5c code
review. They are orthogonal to the preservation contract
(``test_preservation_contract.py``) — that file validates behavioural
parity with the legacy autonomous loop, this file validates the
review findings were actually fixed and do not silently regress.

Covered:

* **H1** — :class:`OrchestrationEngine` plumbs ``container`` through to
  :func:`set_autonomous_context` when it starts, so
  :func:`_autonomous_step_fn` can resolve its DI container on the
  first tick.
* **H2** — ``/inspect/autonomous`` returns real per-task step progress
  by reading the orchestration ``CheckpointStore`` rather than an
  in-memory ``_autonomous_loops`` dict (which was retired in 7.5c).
* **H3** — The autonomous step function honours the orchestration
  :class:`RequestLimiter` before dispatching LLM-bearing nodes; a
  saturated limiter yields ``paused_for_rate_limit`` instead of
  burning a call.
* **M2** — Wall-clock bookkeeping uses :func:`time.time` (epoch) so
  ``elapsed_seconds`` survives a daemon restart.
"""

from __future__ import annotations

import time as _time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from kora_v2.autonomous.pipeline_factory import (
    _SCRATCH_INITIALISED_KEY,
    _SCRATCH_LAST_CHECKPOINT_KEY,
    _SCRATCH_PREV_NODE_KEY,
    _SCRATCH_SAME_NODE_REPEATS_KEY,
    _SCRATCH_STATE_KEY,
    _SCRATCH_WALL_START_KEY,
    _autonomous_step_fn,
)
from kora_v2.autonomous.runtime_context import (
    get_autonomous_context,
    set_autonomous_context,
)
from kora_v2.autonomous.state import AutonomousState
from kora_v2.core.db import init_operational_db
from kora_v2.core.settings import AutonomousSettings, LLMSettings, get_settings
from kora_v2.runtime.orchestration import (
    OrchestrationEngine,
    UserScheduleProfile,
    init_orchestration_schema,
)
from kora_v2.runtime.orchestration.limiter import RequestLimiter
from kora_v2.runtime.orchestration.worker_task import (
    Checkpoint,
    RequestClass,
    StepContext,
    WorkerTask,
    WorkerTaskState,
    get_preset,
)

# ── Shared fakes (mirror test_preservation_contract.py) ─────────────────


@dataclass
class _FakeSettings:
    autonomous: AutonomousSettings
    llm: LLMSettings


class _FakeContainer:
    """Minimal DI container surface the step function touches."""

    def __init__(self) -> None:
        base = get_settings()
        self.settings = _FakeSettings(
            autonomous=AutonomousSettings(
                enabled=True,
                max_session_hours=1.0,
                checkpoint_interval_minutes=30,
                per_session_cost_limit=1.0,
                request_warning_threshold=0.85,
                request_hard_stop_threshold=1.0,
            ),
            llm=base.llm,
        )


def _make_task(goal: str = "Review fix regression test") -> WorkerTask:
    return WorkerTask(
        id="task-review-fix",
        pipeline_instance_id=None,
        stage_name="plan",
        config=get_preset("long_background"),
        goal=goal,
        system_prompt="",
        state=WorkerTaskState.RUNNING,
        created_at=datetime.now(UTC),
    )


def _make_ctx(
    task: WorkerTask,
    *,
    limiter: RequestLimiter | None = None,
) -> StepContext:
    stored: dict[str, Any] = {}

    async def _checkpoint_callback(scratch: dict[str, Any]) -> None:
        stored.clear()
        stored.update(scratch)
        task.checkpoint_blob = Checkpoint(
            task_id=task.id,
            created_at=datetime.now(UTC),
            state=task.state,
            current_step_index=0,
            scratch_state=dict(scratch),
        )

    return StepContext(
        task=task,
        limiter=limiter,  # type: ignore[arg-type]
        cancellation_flag=lambda: False,
        now=lambda: datetime.now(UTC),
        checkpoint_callback=_checkpoint_callback,
    )


# ══════════════════════════════════════════════════════════════════════════
# H1 — Engine.start() plumbs container through to autonomous context
# ══════════════════════════════════════════════════════════════════════════


async def test_h1_engine_start_populates_autonomous_runtime_context(
    tmp_path: Path,
) -> None:
    """``OrchestrationEngine.start()`` wires the DI container into the
    process-level autonomous runtime context.

    Regression: prior to the H1 fix the engine constructor accepted
    ``container`` but ``start()`` called
    :func:`set_autonomous_context` with ``container=None``. The
    symptom was :func:`_autonomous_step_fn` failing at first tick with
    ``autonomous_runtime_context_not_set`` despite the engine being up
    — because it ran against a context whose ``container`` attribute
    was ``None``, which downstream code treated as "no context".
    """
    db = tmp_path / "operational.db"
    await init_orchestration_schema(db)
    await init_operational_db(db)

    fake_container = _FakeContainer()
    engine = OrchestrationEngine(
        db,
        schedule_profile=UserScheduleProfile(timezone="UTC"),
        memory_root=tmp_path / "_KoraMemory",
        tick_interval=0.01,
        container=fake_container,
    )
    try:
        await engine.start()
        runtime_ctx = get_autonomous_context()
        assert runtime_ctx is not None, "runtime context was never set"
        assert runtime_ctx.container is fake_container
        assert runtime_ctx.db_path == db
    finally:
        await engine.stop(graceful=False)


# ══════════════════════════════════════════════════════════════════════════
# H2 — /inspect/autonomous derives progress from CheckpointStore
# ══════════════════════════════════════════════════════════════════════════


async def test_h2_inspect_autonomous_reports_scratch_state_progress(
    tmp_path: Path,
) -> None:
    """The inspection endpoint reflects scratch-state progress fields.

    Regression: H2 replaced an in-memory ``_autonomous_loops`` dict
    (deleted in 7.5c alongside the legacy loop) with a query over the
    :class:`CheckpointStore`. The endpoint must unpack the
    ``_SCRATCH_STATE_KEY`` blob into a real ``AutonomousState`` and
    surface ``steps_completed`` / ``steps_pending`` / ``elapsed_seconds``
    on the result. Any regression here ships zeros to the UI even
    when the autonomous task has made progress.
    """
    from kora_v2.autonomous.pipeline_factory import _SCRATCH_STATE_KEY
    from kora_v2.runtime.orchestration.checkpointing import CheckpointStore
    from kora_v2.runtime.orchestration.pipeline import (
        PipelineInstance,
        PipelineInstanceState,
    )

    db = tmp_path / "operational.db"
    await init_orchestration_schema(db)
    await init_operational_db(db)

    engine = OrchestrationEngine(
        db,
        schedule_profile=UserScheduleProfile(timezone="UTC"),
        memory_root=tmp_path / "_KoraMemory",
        tick_interval=0.01,
    )

    # Seed a PipelineInstance + WorkerTask + Checkpoint by hand.
    instance = PipelineInstance(
        id="inst-h2",
        pipeline_name="user_autonomous_task",
        working_doc_path="",
        goal="Research adaptive clothing",
        state=PipelineInstanceState.RUNNING,
        parent_session_id="sess-h2",
        parent_task_id=None,
        intent_duration="long",
        started_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    await engine.instance_registry.save(instance)

    task = WorkerTask(
        id="task-h2",
        pipeline_instance_id="inst-h2",
        stage_name="plan",
        config=get_preset("long_background"),
        goal="Research adaptive clothing",
        system_prompt="",
        state=WorkerTaskState.RUNNING,
        request_count=7,
        created_at=datetime.now(UTC),
    )
    await engine.task_registry.save(task)

    state = AutonomousState(
        session_id="sess-h2",
        plan_id="plan-h2",
        status="executing",
        completed_step_ids=["step-0", "step-1"],
        pending_step_ids=["step-2", "step-3", "step-4"],
        elapsed_seconds=4321,
    )
    checkpoint_store = CheckpointStore(db)
    await checkpoint_store.save(
        Checkpoint(
            task_id=task.id,
            created_at=datetime.now(UTC),
            state=WorkerTaskState.RUNNING,
            current_step_index=2,
            scratch_state={
                _SCRATCH_INITIALISED_KEY: True,
                _SCRATCH_STATE_KEY: state.model_dump(mode="json"),
            },
            request_count=7,
            agent_turn_count=0,
        )
    )

    # Drive the inspect_autonomous handler directly by (1) patching
    # the server module's ``_container`` global to a minimal shim that
    # exposes the engine and (2) invoking ``_build_router`` to compile
    # the route, then pulling the endpoint callable off the router. We
    # avoid ``create_app`` entirely because it wires CORS / auth /
    # websocket code we are not testing here.
    from kora_v2.daemon import server as server_module

    class _ContainerShim:
        _orchestration_engine = engine

    shim: Any = _ContainerShim()

    saved_container = server_module._container
    server_module._container = shim
    try:
        router = server_module._build_router()
        handler = None
        for route in router.routes:
            if getattr(route, "path", None) == "/api/v1/inspect/autonomous":
                handler = route.endpoint  # type: ignore[attr-defined]
                break
        assert handler is not None, "inspect_autonomous route not registered"
        payload = await handler()
    finally:
        server_module._container = saved_container

    assert payload["count"] == 1
    loops = payload["loops"]
    assert "sess-h2" in loops
    row = loops["sess-h2"]
    assert row["goal"] == "Research adaptive clothing"
    assert row["steps_completed"] == 2
    assert row["steps_pending"] == 3
    assert row["elapsed_seconds"] == 4321
    assert row["request_count"] == 7


# ══════════════════════════════════════════════════════════════════════════
# H3 — Rate-limiter gate for LLM-bearing nodes
# ══════════════════════════════════════════════════════════════════════════


async def test_h3_saturated_limiter_pauses_autonomous_step(
    tmp_path: Path,
) -> None:
    """A saturated :class:`RequestLimiter` pauses the step via
    ``paused_for_rate_limit`` before the LLM-bearing node runs.

    Regression: prior to H3 the autonomous step function bypassed the
    sliding-window limiter entirely and would happily burn LLM calls
    even when the provider was saturated. The fix acquires one extra
    BACKGROUND unit before dispatching LLM-bearing nodes
    (``plan``/``execute_step``/``replan``) and returns
    ``paused_for_rate_limit`` when the window cannot absorb the call.
    """
    db = tmp_path / "operational.db"
    await init_orchestration_schema(db)
    await init_operational_db(db)

    container = _FakeContainer()
    set_autonomous_context(container=container, db_path=db)
    try:
        # Build a limiter saturated in the BACKGROUND class. The
        # acquire() API returns False when no units are available for
        # the requested class after the window count is consumed.
        limiter = RequestLimiter(db)
        await limiter.replay_from_log()

        # Saturate the window to the conversation+notification reserve
        # floor so BACKGROUND has zero remaining capacity.
        from kora_v2.runtime.orchestration.limiter import (
            CONVERSATION_RESERVE,
            NOTIFICATION_RESERVE,
            WINDOW_CAPACITY,
        )

        background_budget = (
            WINDOW_CAPACITY - CONVERSATION_RESERVE - NOTIFICATION_RESERVE
        )
        # Pre-fill the window with BACKGROUND requests until none
        # remain. We bypass the public API and push rows into the
        # ledger directly to avoid edge cases in the accounting path.
        for _ in range(background_budget):
            acquired = await limiter.acquire(
                RequestClass.BACKGROUND, worker_task_id="saturator"
            )
            assert acquired is True

        # Now a fresh BACKGROUND acquire should fail.
        probe = await limiter.acquire(
            RequestClass.BACKGROUND, worker_task_id="probe"
        )
        assert probe is False, "fixture precondition: limiter must be saturated"

        # Seed a task that will route to "plan" (LLM-bearing node).
        task = _make_task()
        state = AutonomousState(
            session_id=task.id,
            plan_id="plan-h3",
            status="planned",
            pending_step_ids=[],
            metadata={"goal": task.goal},
        )
        scratch = {
            _SCRATCH_INITIALISED_KEY: True,
            _SCRATCH_STATE_KEY: state.model_dump(mode="json"),
            _SCRATCH_PREV_NODE_KEY: None,
            _SCRATCH_SAME_NODE_REPEATS_KEY: 0,
            _SCRATCH_WALL_START_KEY: _time.time(),
            _SCRATCH_LAST_CHECKPOINT_KEY: _time.time(),
        }
        task.checkpoint_blob = Checkpoint(
            task_id=task.id,
            created_at=datetime.now(UTC),
            state=WorkerTaskState.RUNNING,
            current_step_index=0,
            scratch_state=scratch,
        )

        ctx = _make_ctx(task, limiter=limiter)
        result = await _autonomous_step_fn(task, ctx)

        assert result.outcome == "paused_for_rate_limit"
        assert result.result_summary == "autonomous_rate_limited"
        # Paused ticks must not be counted against the task's request
        # budget — zero delta is the preservation contract so the
        # outer budget enforcer does not bill the call.
        assert result.request_count_delta == 0
    finally:
        from kora_v2.autonomous.runtime_context import clear_autonomous_context

        clear_autonomous_context()


# ══════════════════════════════════════════════════════════════════════════
# M2 — Wall-clock bookkeeping uses time.time() (restart-durable)
# ══════════════════════════════════════════════════════════════════════════


async def test_m2_elapsed_seconds_uses_wall_clock_epoch(
    tmp_path: Path,
) -> None:
    """Backdating ``_SCRATCH_WALL_START_KEY`` with ``time.time()-1800``
    yields an elapsed value >= 1800 on the next tick.

    Regression: M2 switched the wall clock from ``time.monotonic()``
    (process-relative, resets on daemon restart) to ``time.time()``
    (absolute epoch). If this silently reverts, a daemon restart
    causes the elapsed-seconds budget axis to reset to zero and every
    long-running autonomous session over-runs its wall-time budget.
    """
    db = tmp_path / "operational.db"
    await init_orchestration_schema(db)
    await init_operational_db(db)

    container = _FakeContainer()
    set_autonomous_context(container=container, db_path=db)
    try:
        task = _make_task(goal="M2 wall-clock regression test")

        # Back-date by exactly 1800 seconds on the wall-clock epoch.
        wall_start = _time.time() - 1800
        state = AutonomousState(
            session_id=task.id,
            plan_id="plan-m2",
            status="planned",
            pending_step_ids=[],
            metadata={"goal": task.goal},
        )
        scratch = {
            _SCRATCH_INITIALISED_KEY: True,
            _SCRATCH_STATE_KEY: state.model_dump(mode="json"),
            _SCRATCH_PREV_NODE_KEY: None,
            _SCRATCH_SAME_NODE_REPEATS_KEY: 0,
            _SCRATCH_WALL_START_KEY: wall_start,
            _SCRATCH_LAST_CHECKPOINT_KEY: wall_start,
        }
        task.checkpoint_blob = Checkpoint(
            task_id=task.id,
            created_at=datetime.now(UTC),
            state=WorkerTaskState.RUNNING,
            current_step_index=0,
            scratch_state=scratch,
        )

        ctx = _make_ctx(task)
        result = await _autonomous_step_fn(task, ctx)
        # We do not care whether the planner succeeded — only that
        # elapsed_seconds was computed from the wall clock and made
        # it into the round-tripped scratch state.
        assert result is not None

        new_scratch = task.checkpoint_blob.scratch_state  # type: ignore[union-attr]
        new_state = AutonomousState.model_validate(new_scratch[_SCRATCH_STATE_KEY])
        assert new_state.elapsed_seconds >= 1800, (
            f"expected elapsed_seconds >= 1800, got {new_state.elapsed_seconds}"
        )
    finally:
        from kora_v2.autonomous.runtime_context import clear_autonomous_context

        clear_autonomous_context()
