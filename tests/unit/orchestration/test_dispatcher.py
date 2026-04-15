"""Dispatcher tick-level unit tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from kora_v2.core.events import EventEmitter, EventType
from kora_v2.runtime.orchestration import (
    LONG_BACKGROUND,
    LedgerEventType,
    OrchestrationEngine,
    StepContext,
    StepResult,
    SystemStatePhase,
    UserScheduleProfile,
    WorkerTask,
    WorkerTaskState,
    init_orchestration_schema,
)
from kora_v2.runtime.orchestration.dispatcher import (
    FAIRNESS_THRESHOLD_BACKGROUND_SECONDS,
    _task_priority,
)


@pytest.fixture
async def bg_engine(tmp_path: Path) -> OrchestrationEngine:
    db = tmp_path / "bg.db"
    await init_orchestration_schema(db)
    # Force the state machine to report DEEP_IDLE so background tasks
    # are eligible.
    profile = UserScheduleProfile(timezone="UTC")
    engine = OrchestrationEngine(db, schedule_profile=profile)
    # Rehydrate limiter without starting the loop.
    await engine.limiter.replay_from_log()
    # Force DEEP_IDLE by pretending the last session ended 2 hours ago.
    engine.state_machine.note_session_end(
        datetime.now(UTC) - timedelta(hours=2)
    )
    return engine


async def test_tick_runs_a_background_task_to_completion(bg_engine: OrchestrationEngine) -> None:
    call_count = 0

    async def step_fn(task: WorkerTask, ctx: StepContext) -> StepResult:
        nonlocal call_count
        call_count += 1
        if call_count >= 3:
            return StepResult(outcome="complete", result_summary="done")
        return StepResult(outcome="continue", request_count_delta=1)

    task = await bg_engine.dispatch_task(
        goal="test-goal",
        system_prompt="prompt",
        step_fn=step_fn,
        preset="bounded_background",
    )
    final = await bg_engine.run_task_to_completion(task)
    assert final.state is WorkerTaskState.COMPLETED
    assert call_count >= 3


async def test_phase_gating_blocks_background_during_conversation(
    bg_engine: OrchestrationEngine,
) -> None:
    # Force CONVERSATION phase
    bg_engine.state_machine.note_session_start(datetime.now(UTC))

    stepped = False

    async def step_fn(task: WorkerTask, ctx: StepContext) -> StepResult:
        nonlocal stepped
        stepped = True
        return StepResult(outcome="complete")

    await bg_engine.dispatch_task(
        goal="bg",
        system_prompt="prompt",
        step_fn=step_fn,
        preset="bounded_background",
    )
    await bg_engine.tick_once()
    # The task must not have been stepped: its allowed_states do not
    # include CONVERSATION.
    assert stepped is False


async def test_failing_step_transitions_to_failed(
    bg_engine: OrchestrationEngine,
) -> None:
    async def step_fn(task: WorkerTask, ctx: StepContext) -> StepResult:
        raise RuntimeError("boom")

    task = await bg_engine.dispatch_task(
        goal="boom",
        system_prompt="prompt",
        step_fn=step_fn,
        preset="bounded_background",
    )
    final = await bg_engine.run_task_to_completion(task, max_ticks=5)
    assert final.state is WorkerTaskState.FAILED
    assert final.error_message == "boom"


async def test_cancellation_request_transitions_to_cancelled(
    bg_engine: OrchestrationEngine,
) -> None:
    async def step_fn(task: WorkerTask, ctx: StepContext) -> StepResult:
        return StepResult(outcome="continue")

    task = await bg_engine.dispatch_task(
        goal="bg",
        system_prompt="prompt",
        step_fn=step_fn,
        preset="bounded_background",
    )
    await bg_engine.request_cancellation(task.id)
    final = await bg_engine.run_task_to_completion(task, max_ticks=3)
    assert final.state is WorkerTaskState.CANCELLED


async def test_max_requests_budget_enforced(
    bg_engine: OrchestrationEngine,
) -> None:
    async def step_fn(task: WorkerTask, ctx: StepContext) -> StepResult:
        return StepResult(outcome="continue", request_count_delta=10)

    task = await bg_engine.dispatch_task(
        goal="bg",
        system_prompt="prompt",
        step_fn=step_fn,
        preset="bounded_background",
        config_overrides={"max_requests": 20},
    )
    final = await bg_engine.run_task_to_completion(task, max_ticks=5)
    assert final.state is WorkerTaskState.FAILED
    assert final.error_message == "max_requests_exceeded"


# ── H1: SYSTEM_STATE_CHANGED is published on each tick ────────────────────


async def test_dispatcher_emits_system_state_changed(tmp_path: Path) -> None:
    """Each dispatcher tick that crosses a phase boundary publishes
    ``SYSTEM_STATE_CHANGED`` on the event bus AND writes a row to
    ``system_state_log`` (spec §6.3 + §16.1).
    """
    import aiosqlite

    db = tmp_path / "operational.db"
    await init_orchestration_schema(db)
    emitter = EventEmitter()
    engine = OrchestrationEngine(
        db,
        schedule_profile=UserScheduleProfile(timezone="UTC"),
        event_emitter=emitter,
    )
    await engine.limiter.replay_from_log()

    received: list[tuple[str | None, str]] = []

    async def handler(payload: dict) -> None:
        received.append((payload["previous_phase"], payload["new_phase"]))

    emitter.on(EventType.SYSTEM_STATE_CHANGED, handler)

    # Drive transitions: initial → CONVERSATION → ACTIVE_IDLE → DEEP_IDLE.
    # Each transition is observed via a dispatcher tick.
    engine.state_machine.note_session_start(datetime.now(UTC))
    await engine.tick_once()  # → CONVERSATION

    engine.state_machine.note_session_end(datetime.now(UTC))
    await engine.tick_once()  # → ACTIVE_IDLE

    engine.state_machine.note_session_end(
        datetime.now(UTC) - timedelta(hours=2)
    )
    await engine.tick_once()  # → DEEP_IDLE

    # Three transitions on the bus.
    assert len(received) >= 3
    new_phases = [new for _, new in received]
    assert SystemStatePhase.CONVERSATION.value in new_phases
    assert SystemStatePhase.ACTIVE_IDLE.value in new_phases
    assert SystemStatePhase.DEEP_IDLE.value in new_phases

    # system_state_log mirror check.
    async with aiosqlite.connect(str(db)) as conn:
        cursor = await conn.execute(
            "SELECT new_phase FROM system_state_log ORDER BY id ASC"
        )
        rows = [row[0] for row in await cursor.fetchall()]
    assert SystemStatePhase.CONVERSATION.value in rows
    assert SystemStatePhase.ACTIVE_IDLE.value in rows
    assert SystemStatePhase.DEEP_IDLE.value in rows


# ── H3: pause_on_conversation pauses running tasks ────────────────────────


async def test_long_background_task_pauses_on_conversation_phase_transition(
    tmp_path: Path,
) -> None:
    """A LONG_BACKGROUND task with ``pause_on_conversation=True`` that
    is mid-flight when the phase moves to CONVERSATION must transition
    to ``paused_for_state`` without the step function being called
    again (spec §7.5).
    """
    db = tmp_path / "operational.db"
    await init_orchestration_schema(db)
    emitter = EventEmitter()
    engine = OrchestrationEngine(
        db,
        schedule_profile=UserScheduleProfile(timezone="UTC"),
        event_emitter=emitter,
    )
    await engine.limiter.replay_from_log()
    # Start in DEEP_IDLE so the task is initially eligible.
    engine.state_machine.note_session_end(
        datetime.now(UTC) - timedelta(hours=2)
    )

    step_calls = 0

    async def step_fn(task: WorkerTask, ctx: StepContext) -> StepResult:
        nonlocal step_calls
        step_calls += 1
        return StepResult(outcome="continue", request_count_delta=1)

    # LONG_BACKGROUND has pause_on_conversation=True per spec §3.2.
    task = await engine.dispatch_task(
        goal="long-running",
        system_prompt="prompt",
        step_fn=step_fn,
        preset="long_background",
    )

    # First tick: task runs, transitions PENDING → RUNNING, and step_fn fires.
    await engine.tick_once()
    assert step_calls == 1
    assert task.state is WorkerTaskState.RUNNING

    # Phase transitions to CONVERSATION (e.g. user opens the app).
    engine.state_machine.note_session_start(datetime.now(UTC))

    # Second tick: the dispatch pass observes the phase change and
    # parks the task. step_fn must NOT be called.
    await engine.tick_once()
    assert step_calls == 1, "step_fn should not run during CONVERSATION"
    assert task.state is WorkerTaskState.PAUSED_FOR_STATE

    # The ledger has a paused-for-state transition.
    events = await engine.ledger.read_task_events(task.id)
    paused_events = [
        e for e in events
        if e.event_type == LedgerEventType.TASK_PAUSED
        and (e.reason or "").startswith("conversation")
    ]
    assert paused_events, f"expected conversation_began pause; got {[e.event_type for e in events]}"


# ── M3: starvation protection / fairness boost ─────────────────────────────


def test_dispatcher_starvation_protection() -> None:
    """A LONG_BACKGROUND task that has been waiting longer than the
    fairness threshold must sort ahead of freshly-arrived
    BOUNDED_BACKGROUND siblings (spec §7.3 rule 3).
    """
    base_time = datetime(2026, 4, 14, 12, 0, 0, tzinfo=UTC)

    long_task = WorkerTask(
        id="task-long",
        pipeline_instance_id=None,
        stage_name="long-stage",
        config=replace(LONG_BACKGROUND, tool_scope=list(LONG_BACKGROUND.tool_scope)),
        goal="long",
        system_prompt="p",
        created_at=base_time,
    )

    # Ten BOUNDED tasks arriving steadily over the next 120 seconds.
    bounded_tasks: list[WorkerTask] = []
    for i in range(10):
        bounded = WorkerTask(
            id=f"task-bounded-{i}",
            pipeline_instance_id=None,
            stage_name="bounded-stage",
            config=replace(
                LONG_BACKGROUND,  # share the same request_class for fair compare
                preset="bounded_background",
                pause_on_conversation=False,
                tool_scope=[],
            ),
            goal=f"bounded-{i}",
            system_prompt="p",
            created_at=base_time + timedelta(seconds=10 + i * 12),
        )
        bounded_tasks.append(bounded)

    # Fast-forward beyond the fairness threshold relative to long_task.
    now = base_time + timedelta(
        seconds=FAIRNESS_THRESHOLD_BACKGROUND_SECONDS + 30
    )

    all_tasks = [*bounded_tasks, long_task]
    all_tasks.sort(key=lambda t: _task_priority(t, now))

    # The starved long task must be at the front.
    assert all_tasks[0].id == "task-long", (
        f"starvation protection failed: ordering={[t.id for t in all_tasks]}"
    )


# ── M4: start/stop lifecycle + crash recovery rehydration ────────────────


async def test_engine_start_stop_lifecycle_recovers_running_tasks(
    tmp_path: Path,
) -> None:
    """``engine.start()`` rehydrates non-terminal tasks from the DB and
    transitions any RUNNING/CHECKPOINTING rows to ``paused_for_state``
    with reason ``crash_recovery`` (spec §7.6 step 4). A subsequent
    ``engine.stop(graceful=True)`` shuts the dispatcher loop down
    cleanly.
    """
    db = tmp_path / "operational.db"
    await init_orchestration_schema(db)

    # Pre-seed a task that was "running" when the previous process
    # died — bypass the dispatcher by writing through the registry.
    pre_engine = OrchestrationEngine(
        db,
        schedule_profile=UserScheduleProfile(timezone="UTC"),
    )
    await pre_engine.limiter.replay_from_log()

    crashed = WorkerTask(
        id="crashed-task",
        pipeline_instance_id=None,
        stage_name="adhoc",
        config=replace(LONG_BACKGROUND, tool_scope=[]),
        goal="resume me",
        system_prompt="prompt",
        state=WorkerTaskState.RUNNING,
    )
    await pre_engine.task_registry.save(crashed)

    # Now spin up a fresh engine (mimics process restart) and call start().
    engine = OrchestrationEngine(
        db,
        schedule_profile=UserScheduleProfile(timezone="UTC"),
        tick_interval=10.0,  # large interval — tests don't drive the loop
    )
    await engine.start()
    try:
        # The crashed row was loaded into the live set.
        live = engine.dispatcher.live_task("crashed-task")
        assert live is not None
        # And was transitioned to paused_for_state with the right reason.
        assert live.state is WorkerTaskState.PAUSED_FOR_STATE

        # Persisted reflection of the recovery.
        reloaded = await engine.task_registry.load("crashed-task")
        assert reloaded is not None
        assert reloaded.state is WorkerTaskState.PAUSED_FOR_STATE

        events = await engine.ledger.read_task_events("crashed-task")
        recovery = [
            e for e in events
            if e.event_type == LedgerEventType.TASK_PAUSED
            and (e.reason or "") == "crash_recovery"
        ]
        assert recovery, (
            "expected a crash_recovery ledger row; "
            f"got {[(e.event_type, e.reason) for e in events]}"
        )
    finally:
        await engine.stop(graceful=True)
    assert engine._started is False
