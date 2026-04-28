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
    Pipeline,
    PipelineStage,
    RequestClass,
    RequestLimiter,
    StepContext,
    StepResult,
    SystemStatePhase,
    UserScheduleProfile,
    WorkerTask,
    WorkerTaskState,
    get_preset,
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

    async def step_fn(_task: WorkerTask, _ctx: StepContext) -> StepResult:
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


async def test_failed_stage_cancels_blocked_siblings_and_fails_pipeline(
    bg_engine: OrchestrationEngine,
) -> None:
    pipeline = Pipeline(
        name="multi_stage_failure",
        description="multi-stage failure test",
        stages=[
            PipelineStage(
                name="first",
                task_preset="bounded_background",
                goal_template="first",
            ),
            PipelineStage(
                name="second",
                task_preset="bounded_background",
                goal_template="second",
                depends_on=["first"],
            ),
        ],
    )
    bg_engine.register_pipeline(pipeline)
    instance = await bg_engine.start_pipeline_instance(
        "multi_stage_failure",
        goal="test",
        working_doc_path="",
    )

    async def fail_step(_task: WorkerTask, _ctx: StepContext) -> StepResult:
        return StepResult(outcome="failed", error_message="boom")

    async def never_step(_task: WorkerTask, _ctx: StepContext) -> StepResult:
        raise AssertionError("dependent task should be cancelled, not run")

    first = await bg_engine.dispatch_task(
        goal="first",
        system_prompt="prompt",
        step_fn=fail_step,
        preset="bounded_background",
        stage_name="first",
        pipeline_instance_id=instance.id,
    )
    second = await bg_engine.dispatch_task(
        goal="second",
        system_prompt="prompt",
        step_fn=never_step,
        preset="bounded_background",
        stage_name="second",
        pipeline_instance_id=instance.id,
        depends_on=["first"],
    )

    await bg_engine.run_task_to_completion(first, max_ticks=3)

    reloaded_second = await bg_engine.task_registry.load(second.id)
    reloaded_instance = await bg_engine.instance_registry.load(instance.id)
    assert reloaded_second is not None
    assert reloaded_second.state is WorkerTaskState.CANCELLED
    assert reloaded_instance is not None
    assert reloaded_instance.state.value == "failed"


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


async def test_pending_cancellation_transitions_to_cancelled_immediately(
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

    reloaded = await bg_engine.task_registry.load(task.id)
    assert reloaded is not None
    assert reloaded.state is WorkerTaskState.CANCELLED


async def test_inflight_cancellation_wins_over_complete_result(
    bg_engine: OrchestrationEngine,
) -> None:
    async def step_fn(task: WorkerTask, ctx: StepContext) -> StepResult:
        await bg_engine.request_cancellation(task.id)
        return StepResult(outcome="complete", result_summary="done anyway")

    task = await bg_engine.dispatch_task(
        goal="bg",
        system_prompt="prompt",
        step_fn=step_fn,
        preset="bounded_background",
    )

    final = await bg_engine.run_task_to_completion(task, max_ticks=3)

    assert final.state is WorkerTaskState.CANCELLED
    assert final.result_summary is None


async def test_mixed_cancelled_and_completed_tasks_cancel_pipeline(
    bg_engine: OrchestrationEngine,
) -> None:
    pipeline = Pipeline(
        name="mixed_terminal",
        description="mixed terminal state test",
        stages=[
            PipelineStage(
                name="run",
                task_preset="bounded_background",
                goal_template="run",
            ),
            PipelineStage(
                name="user_added",
                task_preset="bounded_background",
                goal_template="user_added",
            ),
        ],
    )
    bg_engine.register_pipeline(pipeline)
    instance = await bg_engine.start_pipeline_instance(
        "mixed_terminal",
        goal="test",
        working_doc_path="",
    )

    async def complete_step(_task: WorkerTask, _ctx: StepContext) -> StepResult:
        return StepResult(outcome="complete", result_summary="done")

    async def continue_step(_task: WorkerTask, _ctx: StepContext) -> StepResult:
        return StepResult(outcome="continue")

    run_task = await bg_engine.dispatch_task(
        goal="run",
        system_prompt="prompt",
        step_fn=continue_step,
        preset="bounded_background",
        stage_name="run",
        pipeline_instance_id=instance.id,
    )
    user_added = await bg_engine.dispatch_task(
        goal="user_added",
        system_prompt="prompt",
        step_fn=complete_step,
        preset="bounded_background",
        stage_name="user_added",
        pipeline_instance_id=instance.id,
    )

    await bg_engine.request_cancellation(run_task.id)
    await bg_engine.run_task_to_completion(run_task, max_ticks=3)
    await bg_engine.run_task_to_completion(user_added, max_ticks=3)

    reloaded_instance = await bg_engine.instance_registry.load(instance.id)

    assert reloaded_instance is not None
    assert reloaded_instance.state.value == "cancelled"
    assert reloaded_instance.completion_reason == "task_cancelled"


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


async def test_rate_limit_rejection_pauses_and_resumes_after_window(
    tmp_path: Path,
) -> None:
    db = tmp_path / "operational.db"
    await init_orchestration_schema(db)
    engine = OrchestrationEngine(
        db,
        schedule_profile=UserScheduleProfile(timezone="UTC"),
    )
    limiter = RequestLimiter(
        db,
        capacity=1,
        conversation_reserve=0,
        notification_reserve=0,
        window_seconds=1,
    )
    await limiter.replay_from_log()
    engine.limiter = limiter
    engine.dispatcher._limiter = limiter
    engine.state_machine.note_session_end(
        datetime.now(UTC) - timedelta(hours=2)
    )

    acquired = await limiter.acquire(
        RequestClass.BACKGROUND,
        worker_task_id="saturator",
    )
    assert acquired is True

    calls = 0

    async def step_fn(_task: WorkerTask, _ctx: StepContext) -> StepResult:
        nonlocal calls
        calls += 1
        return StepResult(outcome="complete", result_summary="done")

    task = await engine.dispatch_task(
        goal="rate limited",
        system_prompt="prompt",
        step_fn=step_fn,
        preset="bounded_background",
    )

    await engine.tick_once()
    paused = await engine.task_registry.load(task.id)
    assert paused is not None
    assert paused.state is WorkerTaskState.PAUSED_FOR_RATE_LIMIT
    assert calls == 0

    await engine.tick_once()
    still_paused = await engine.task_registry.load(task.id)
    assert still_paused is not None
    assert still_paused.state is WorkerTaskState.PAUSED_FOR_RATE_LIMIT
    assert calls == 0

    import asyncio

    await asyncio.sleep(1.1)
    await engine.tick_once()
    resumed = await engine.task_registry.load(task.id)
    assert resumed is not None
    assert resumed.state is WorkerTaskState.COMPLETED
    assert calls == 1

    events = await engine.ledger.read_task_events(task.id)
    assert any(
        event.event_type == LedgerEventType.RATE_LIMIT_REJECTED
        and event.reason == "rate_limit_paused"
        for event in events
    )
    assert any(
        event.event_type == LedgerEventType.TASK_PAUSED
        and event.reason == "rate_limit_paused"
        for event in events
    )
    assert any(
        event.event_type == LedgerEventType.TASK_RESUMED
        and event.reason == "rate_limit_retry"
        for event in events
    )


async def test_list_tasks_surfaces_unacknowledged_cancelled_terminal(
    bg_engine: OrchestrationEngine,
) -> None:
    task = WorkerTask(
        id="task-unacked-cancelled",
        pipeline_instance_id=None,
        stage_name="terminal",
        config=get_preset("bounded_background"),
        goal="surface terminal result",
        system_prompt="prompt",
        state=WorkerTaskState.CANCELLED,
        completed_at=datetime.now(UTC),
        result_acknowledged_at=None,
    )
    await bg_engine.task_registry.save(task)

    matches = await bg_engine.list_tasks(relevant_to_session="different-session")
    assert task.id in {matched.id for matched in matches}

    await bg_engine.acknowledge_task(task.id)
    after_ack = await bg_engine.list_tasks(
        relevant_to_session="different-session"
    )
    assert task.id not in {matched.id for matched in after_ack}


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


def test_memory_finalization_pipelines_outrank_routine_background() -> None:
    """Conversation finalization should not starve behind routine idle jobs."""
    now = datetime.now(UTC)
    old_background = WorkerTask(
        id="task-old-background",
        pipeline_instance_id="continuity_check-abc",
        stage_name="run",
        config=replace(
            LONG_BACKGROUND,
            preset="bounded_background",
            pause_on_conversation=False,
            tool_scope=[],
        ),
        goal="routine",
        system_prompt="p",
        created_at=now - timedelta(seconds=10),
    )
    finalization = WorkerTask(
        id="task-finalization",
        pipeline_instance_id="post_session_memory-xyz",
        stage_name="extract",
        config=replace(
            LONG_BACKGROUND,
            preset="bounded_background",
            pause_on_conversation=False,
            tool_scope=[],
        ),
        goal="memory",
        system_prompt="p",
        created_at=now,
    )

    ordered = sorted(
        [old_background, finalization],
        key=lambda t: _task_priority(t, now),
    )

    assert ordered[0].id == "task-finalization"


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


async def test_paused_dependency_task_resumes_when_step_fn_resolves(
    bg_engine: OrchestrationEngine,
) -> None:
    async def step_fn(task: WorkerTask, ctx: StepContext) -> StepResult:
        return StepResult(outcome="complete", result_summary="resumed")

    task = await bg_engine.dispatch_task(
        goal="recover resolver",
        system_prompt="prompt",
        step_fn=step_fn,
        preset="bounded_background",
    )
    task.step_fn = None
    task.state = WorkerTaskState.PAUSED_FOR_DEPENDENCY
    await bg_engine.task_registry.update_state(
        task.id,
        WorkerTaskState.PAUSED_FOR_DEPENDENCY,
    )

    async def resolver(candidate: WorkerTask):
        assert candidate.id == task.id
        return step_fn

    bg_engine.dispatcher._step_fn_resolver = resolver

    await bg_engine.tick_once()

    reloaded = await bg_engine.task_registry.load(task.id)
    assert reloaded is not None
    assert reloaded.state is WorkerTaskState.COMPLETED
    assert reloaded.result_summary == "resumed"

    events = await bg_engine.ledger.read_task_events(task.id)
    assert any(
        event.event_type == LedgerEventType.TASK_RESUMED
        and event.reason == "dependency_resolved:step_fn"
        for event in events
    )
