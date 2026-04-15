"""Slice 7.5a acceptance tests — items 12, 24, 32, 33, 45 from spec §18.7.

Item 12: task state transitions are persisted and emitted.
Item 24: dispatcher gates background tasks on SystemStatePhase.
Item 32: request limiter enforces per-class reserves.
Item 33: request limiter rolls over after the sliding window.
Item 45: work ledger records every dispatcher transition.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from kora_v2.core.events import EventEmitter, EventType
from kora_v2.runtime.orchestration import (
    LedgerEventType,
    OrchestrationEngine,
    RequestClass,
    RequestLimiter,
    StepContext,
    StepResult,
    UserScheduleProfile,
    WorkerTask,
    WorkerTaskState,
    demo_tick_pipeline,
    init_orchestration_schema,
)

# ── Helpers ───────────────────────────────────────────────────────────────


async def _make_engine(tmp_path: Path) -> OrchestrationEngine:
    db = tmp_path / "operational.db"
    await init_orchestration_schema(db)
    emitter = EventEmitter()
    engine = OrchestrationEngine(
        db,
        schedule_profile=UserScheduleProfile(timezone="UTC"),
        event_emitter=emitter,
    )
    await engine.limiter.replay_from_log()
    # Ensure we're not in a session and force DEEP_IDLE by pretending
    # the last session ended well past LIGHT_IDLE_SECONDS ago.
    engine.state_machine.note_session_end(
        datetime.now(UTC) - timedelta(hours=2)
    )
    return engine


# ── Item 12: task state transitions persist and fire events ──────────────


async def test_item_12_task_state_transitions_persist_and_emit(
    tmp_path: Path,
) -> None:
    engine = await _make_engine(tmp_path)
    received: list[EventType] = []

    async def handler(payload: dict) -> None:
        received.append(payload["event_type"])

    engine._emitter.on(EventType.TASK_COMPLETED, handler)  # type: ignore[union-attr]

    async def step_fn(task: WorkerTask, ctx: StepContext) -> StepResult:
        return StepResult(outcome="complete", result_summary="ok")

    task = await engine.dispatch_task(
        goal="persist test",
        system_prompt="prompt",
        step_fn=step_fn,
    )
    final = await engine.run_task_to_completion(task)

    # In-memory: terminal state
    assert final.state is WorkerTaskState.COMPLETED

    # Persistence: SQL row reflects the transition
    reloaded = await engine.task_registry.load(task.id)
    assert reloaded is not None
    assert reloaded.state is WorkerTaskState.COMPLETED
    assert reloaded.result_summary == "ok"

    # Events: TASK_COMPLETED was emitted
    assert EventType.TASK_COMPLETED in received


# ── Item 24: dispatcher gates on SystemStatePhase ────────────────────────


async def test_item_24_dispatcher_respects_system_state_phase(
    tmp_path: Path,
) -> None:
    engine = await _make_engine(tmp_path)
    executions = 0

    async def step_fn(task: WorkerTask, ctx: StepContext) -> StepResult:
        nonlocal executions
        executions += 1
        return StepResult(outcome="complete")

    # Enter CONVERSATION phase → background task must not run
    engine.state_machine.note_session_start(datetime.now(UTC))

    await engine.dispatch_task(
        goal="bg",
        system_prompt="prompt",
        step_fn=step_fn,
        preset="bounded_background",
    )
    await engine.tick_once()
    assert executions == 0

    # Leave the session → DEEP_IDLE → task runs. Backdate the session
    # end by 2 hours so the state machine lands in DEEP_IDLE rather
    # than ACTIVE_IDLE.
    engine.state_machine.note_session_end(
        datetime.now(UTC) - timedelta(hours=2)
    )
    await engine.tick_once()
    assert executions == 1


# ── Item 32: per-class reserves enforced ─────────────────────────────────


async def test_item_32_request_limiter_reserves_enforced(tmp_path: Path) -> None:
    db = tmp_path / "lim.db"
    await init_orchestration_schema(db)
    lim = RequestLimiter(
        db,
        capacity=100,
        conversation_reserve=20,
        notification_reserve=10,
    )
    await lim.replay_from_log()

    # Burn 70 on background (cap=100, conv=20, notif=10 → bg budget=70).
    for _ in range(70):
        assert await lim.acquire(RequestClass.BACKGROUND)
    assert await lim.acquire(RequestClass.BACKGROUND) is False

    # Conversation must still succeed — the reserve exists *for* it.
    for _ in range(5):
        assert await lim.acquire(RequestClass.CONVERSATION)


# ── Item 33: sliding window rollover ─────────────────────────────────────


async def test_item_33_request_limiter_window_rollover(tmp_path: Path) -> None:
    db = tmp_path / "lim.db"
    await init_orchestration_schema(db)
    lim = RequestLimiter(
        db,
        capacity=10,
        conversation_reserve=2,
        notification_reserve=1,
        window_seconds=2,
    )
    await lim.replay_from_log()
    for _ in range(7):
        assert await lim.acquire(RequestClass.BACKGROUND)
    assert await lim.acquire(RequestClass.BACKGROUND) is False

    await asyncio.sleep(2.2)
    # Window has rolled; background is eligible again.
    assert await lim.acquire(RequestClass.BACKGROUND)


# ── Item 45: WorkLedger records transitions ──────────────────────────────


async def test_item_45_work_ledger_records_transitions(tmp_path: Path) -> None:
    engine = await _make_engine(tmp_path)

    async def step_fn(task: WorkerTask, ctx: StepContext) -> StepResult:
        return StepResult(outcome="complete", result_summary="done")

    task = await engine.dispatch_task(
        goal="g",
        system_prompt="p",
        step_fn=step_fn,
    )
    await engine.run_task_to_completion(task)
    events = await engine.ledger.read_task_events(task.id)
    event_types = [e.event_type for e in events]
    # At minimum: creation → running → completed
    assert LedgerEventType.TASK_CREATED in event_types
    assert LedgerEventType.TASK_STARTED in event_types  # running transition
    assert LedgerEventType.TASK_COMPLETED in event_types


# ── Integration: demo_tick pipeline end-to-end ────────────────────────────


async def test_demo_tick_pipeline_runs_end_to_end(tmp_path: Path) -> None:
    engine = await _make_engine(tmp_path)
    engine.register_pipeline(demo_tick_pipeline())

    instance = await engine.start_pipeline_instance(
        "demo_tick",
        goal="heartbeat",
        working_doc_path="/tmp/demo_tick.md",
    )
    assert instance.pipeline_name == "demo_tick"

    # Attach a stage task matching the single-stage pipeline.
    async def step_fn(task: WorkerTask, ctx: StepContext) -> StepResult:
        return StepResult(outcome="complete", result_summary="tick")

    task = await engine.dispatch_task(
        goal="heartbeat",
        system_prompt="prompt",
        step_fn=step_fn,
        stage_name="tick",
        pipeline_instance_id=instance.id,
    )
    final = await engine.run_task_to_completion(task)
    assert final.state is WorkerTaskState.COMPLETED

    # Ledger captures both the pipeline start and the task lifecycle.
    events = await engine.ledger.read_pipeline_events(instance.id)
    event_types = [e.event_type for e in events]
    assert LedgerEventType.PIPELINE_STARTED in event_types
    assert LedgerEventType.TASK_CREATED in event_types
    assert LedgerEventType.TASK_COMPLETED in event_types
