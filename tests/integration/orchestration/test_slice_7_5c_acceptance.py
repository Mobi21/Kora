"""Slice 7.5c acceptance tests — items 36, 38, 39, 40, 41, 42, 43.

Spec §18.7 maps acceptance items to their earliest-green slice. Slice
7.5c's headline target is **item 36 — multi-concurrent autonomous
goals** (§18.2). Items 38–43 are mapped to ``DEFERRED_TO_PHASE_8*`` in
§18.7; the contracts they will eventually assert are documented here
with skip-marked tests so the §18.2 coverage table is complete and the
phase 8 hand-off has a concrete landing point.

Per §18.7 the running coverage tracker should advance from 37 → 38
active items at slice 7.5c landing — the only newly-green item is 36.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from kora_v2.runtime.orchestration import (
    OrchestrationEngine,
    StepContext,
    StepResult,
    UserScheduleProfile,
    WorkerTask,
    WorkerTaskState,
    init_orchestration_schema,
)
from kora_v2.runtime.orchestration.ledger import LedgerEventType
from kora_v2.runtime.orchestration.pipeline import (
    FailurePolicy,
    InterruptionPolicy,
    Pipeline,
    PipelineInstanceState,
    PipelineStage,
)
from kora_v2.runtime.orchestration.triggers import user_action

# ── Helpers ──────────────────────────────────────────────────────────────


async def _make_engine(tmp_path: Path) -> OrchestrationEngine:
    """Build an engine pinned to DEEP_IDLE so background tasks can run."""
    db = tmp_path / "operational.db"
    await init_orchestration_schema(db)
    engine = OrchestrationEngine(
        db,
        schedule_profile=UserScheduleProfile(timezone="UTC"),
        memory_root=tmp_path / "_KoraMemory",
        tick_interval=0.005,
    )
    engine.working_docs.ensure_inbox()
    engine.templates.ensure_defaults()
    engine.templates.reload_if_changed()
    await engine.limiter.replay_from_log()
    # Force DEEP_IDLE so long_background tasks are allowed to run.
    engine.state_machine.note_session_end(
        datetime.now(UTC) - timedelta(hours=2)
    )
    return engine


def _make_simulated_autonomous_pipeline(name: str) -> Pipeline:
    """Build a long_background pipeline whose stage step counts ticks.

    Stands in for ``user_autonomous_task`` without bringing the full
    12-node graph (which needs ``set_autonomous_context`` and a fake
    container). The dispatcher contract we are checking — fair
    interleaving across two concurrent long_background tasks — is the
    same.
    """
    return Pipeline(
        name=name,
        description=f"Slice 7.5c acceptance fake autonomous pipeline ({name})",
        stages=[
            PipelineStage(
                name="run",
                task_preset="long_background",  # type: ignore[arg-type]
                goal_template="{{goal}}",
            )
        ],
        triggers=[user_action(name, action_name="start_autonomous")],
        interruption_policy=InterruptionPolicy.PAUSE_ON_CONVERSATION,
        failure_policy=FailurePolicy.FAIL_PIPELINE,
        intent_duration="long",
    )


# ══════════════════════════════════════════════════════════════════════════
# Item 36 — Multi-concurrent autonomous goals (slice 7.5c headline)
# ══════════════════════════════════════════════════════════════════════════


async def test_item_36_two_autonomous_tasks_dispatch_concurrently(
    tmp_path: Path,
) -> None:
    """Two ``user_autonomous_task``-class instances run side by side.

    Spec §18.2 item 36: dispatch two autonomous tasks simultaneously;
    verify they interleave correctly; both complete independently.

    The slice 7.5c contract this test pins:

    1. Both tasks reach ``COMPLETED`` (no deadlock on the dispatcher
       lock or shared mutable state corruption).
    2. Each pipeline instance's ledger trail is independent — neither
       task ends up bleeding events into the other instance's audit
       trail.
    3. Both tasks make forward progress within the same dispatch pass
       sequence (the dispatcher does not single-thread one task to
       completion before starting the other — fair interleaving).
    """
    engine = await _make_engine(tmp_path)
    engine.register_pipeline(_make_simulated_autonomous_pipeline("auto_a"))
    engine.register_pipeline(_make_simulated_autonomous_pipeline("auto_b"))

    # Per-task interleave log so we can assert fair scheduling.
    interleave: list[str] = []
    # Per-task tick counters used to drive each task to completion in
    # exactly N ticks. Independent so the two tasks cannot share state.
    counters: dict[str, int] = {"auto_a": 0, "auto_b": 0}
    target_steps = 5

    def make_step_fn(label: str):
        async def step_fn(task: WorkerTask, ctx: StepContext) -> StepResult:
            counters[label] += 1
            interleave.append(label)
            if counters[label] >= target_steps:
                return StepResult(
                    outcome="complete",
                    result_summary=f"{label}-done",
                )
            return StepResult(outcome="continue")

        return step_fn

    instance_a = await engine.start_pipeline_instance(
        "auto_a",
        goal="Goal A — concurrent autonomous task A",
        working_doc_path="",
        parent_session_id="sess-A",
    )
    instance_b = await engine.start_pipeline_instance(
        "auto_b",
        goal="Goal B — concurrent autonomous task B",
        working_doc_path="",
        parent_session_id="sess-B",
    )

    task_a = await engine.dispatch_task(
        goal=instance_a.goal,
        system_prompt="",
        step_fn=make_step_fn("auto_a"),
        preset="long_background",
        stage_name="run",
        pipeline_instance_id=instance_a.id,
    )
    task_b = await engine.dispatch_task(
        goal=instance_b.goal,
        system_prompt="",
        step_fn=make_step_fn("auto_b"),
        preset="long_background",
        stage_name="run",
        pipeline_instance_id=instance_b.id,
    )

    # Drive both tasks via shared dispatch passes. Each tick_once is a
    # fair pass: both ready tasks are stepped in priority order before
    # the next pass begins. This is the contract the slice promises.
    for _ in range(20):
        await engine.tick_once()
        if (
            counters["auto_a"] >= target_steps
            and counters["auto_b"] >= target_steps
        ):
            break

    # ── 1. Both tasks completed ──────────────────────────────────────
    final_a = await engine.task_registry.load(task_a.id)
    final_b = await engine.task_registry.load(task_b.id)
    assert final_a is not None
    assert final_b is not None
    assert final_a.state is WorkerTaskState.COMPLETED, (
        f"task A did not complete — final state={final_a.state}"
    )
    assert final_b.state is WorkerTaskState.COMPLETED, (
        f"task B did not complete — final state={final_b.state}"
    )

    # ── 2. Independent ledger trails ─────────────────────────────────
    rows_a = await engine.ledger.read_pipeline_events(instance_a.id)
    rows_b = await engine.ledger.read_pipeline_events(instance_b.id)
    types_a = {row.event_type for row in rows_a}
    types_b = {row.event_type for row in rows_b}
    # PIPELINE_STARTED was recorded for each instance.
    assert LedgerEventType.PIPELINE_STARTED in types_a
    assert LedgerEventType.PIPELINE_STARTED in types_b
    # Cross-leakage check: every row's instance id matches its bucket.
    for row in rows_a:
        assert row.pipeline_instance_id == instance_a.id
    for row in rows_b:
        assert row.pipeline_instance_id == instance_b.id

    # ── 3. Fair interleaving — both tasks were stepped while the
    #     other was still live. We do not require strict round-robin
    #     (the dispatcher is allowed to step priority winners first
    #     within a single pass), but neither task may have run to
    #     completion before the other started.
    first_a = interleave.index("auto_a")
    first_b = interleave.index("auto_b")
    last_a = max(i for i, x in enumerate(interleave) if x == "auto_a")
    last_b = max(i for i, x in enumerate(interleave) if x == "auto_b")
    assert first_b < last_a, (
        "task B never started before task A finished — single-threaded"
    )
    assert first_a < last_b, (
        "task A never started before task B finished — single-threaded"
    )
    assert counters["auto_a"] == target_steps
    assert counters["auto_b"] == target_steps


async def test_item_36_concurrent_autonomous_no_deadlock_under_pressure(
    tmp_path: Path,
) -> None:
    """Three concurrent long_background tasks make progress without deadlock.

    Spec §18.2 item 36 (deadlock variant): the dispatcher's single
    lock + shared registries must not serialise concurrent autonomous
    work. This test scales to three tasks to exercise the lock
    contention under realistic load.
    """
    engine = await _make_engine(tmp_path)
    for label in ("auto_x", "auto_y", "auto_z"):
        engine.register_pipeline(_make_simulated_autonomous_pipeline(label))

    completion: dict[str, bool] = {}

    def make_step_fn(label: str):
        ticks = {"n": 0}

        async def step_fn(task: WorkerTask, ctx: StepContext) -> StepResult:
            ticks["n"] += 1
            # Yield to the event loop so other tasks get a turn — this
            # is the realistic shape of the autonomous step function.
            await asyncio.sleep(0)
            if ticks["n"] >= 3:
                completion[label] = True
                return StepResult(outcome="complete", result_summary="ok")
            return StepResult(outcome="continue")

        return step_fn

    tasks: list[WorkerTask] = []
    for label in ("auto_x", "auto_y", "auto_z"):
        instance = await engine.start_pipeline_instance(
            label,
            goal=f"Goal {label}",
            working_doc_path="",
            parent_session_id=f"sess-{label}",
        )
        tasks.append(
            await engine.dispatch_task(
                goal=instance.goal,
                system_prompt="",
                step_fn=make_step_fn(label),
                preset="long_background",
                stage_name="run",
                pipeline_instance_id=instance.id,
            )
        )

    # Bound the number of ticks so a regression that re-introduces
    # serialisation still terminates the test (it just fails the
    # completion check below).
    for _ in range(30):
        await engine.tick_once()
        if len(completion) == 3:
            break

    assert len(completion) == 3
    for task in tasks:
        loaded = await engine.task_registry.load(task.id)
        assert loaded is not None
        assert loaded.state is WorkerTaskState.COMPLETED


async def test_item_36_two_autonomous_pipeline_instances_remain_distinct(
    tmp_path: Path,
) -> None:
    """Per spec §17.7 row 1: each instance has its own pipeline_instance row.

    Slice 7.5c promises that two concurrent autonomous tasks have
    independent ``PipelineInstance`` rows that walk through the
    ``RUNNING → COMPLETED`` lifecycle without overwriting each other.
    """
    engine = await _make_engine(tmp_path)
    engine.register_pipeline(_make_simulated_autonomous_pipeline("auto_p"))
    engine.register_pipeline(_make_simulated_autonomous_pipeline("auto_q"))

    instance_p = await engine.start_pipeline_instance(
        "auto_p", goal="Goal P", working_doc_path="", parent_session_id="sess-P"
    )
    instance_q = await engine.start_pipeline_instance(
        "auto_q", goal="Goal Q", working_doc_path="", parent_session_id="sess-Q"
    )

    assert instance_p.id != instance_q.id
    assert instance_p.parent_session_id != instance_q.parent_session_id
    assert instance_p.state is PipelineInstanceState.RUNNING
    assert instance_q.state is PipelineInstanceState.RUNNING

    # Reload from the registry — each id must round-trip back to the
    # same row contents (no cross-write).
    loaded_p = await engine.instance_registry.load(instance_p.id)
    loaded_q = await engine.instance_registry.load(instance_q.id)
    assert loaded_p is not None and loaded_q is not None
    assert loaded_p.goal == "Goal P"
    assert loaded_q.goal == "Goal Q"
    assert loaded_p.parent_session_id == "sess-P"
    assert loaded_q.parent_session_id == "sess-Q"


# ══════════════════════════════════════════════════════════════════════════
# Items 38–43 — DEFERRED_TO_PHASE_8 contract markers
# ══════════════════════════════════════════════════════════════════════════
#
# Per spec §18.7 these items are mapped to phase 8 because their stage
# handlers (continuity_check, post_session_memory, wake_up_preparation,
# contextual_engagement, INSIGHT_AVAILABLE wiring, open-decisions
# tracker handlers) do not exist until phase 8 lands. We document the
# eventual contracts here so the §18.2 coverage table has concrete
# tests to point at, but the assertions are skipped until the phase 8
# work that backs them is done.


# Item 38 — Continuity check during conversation -------------------------


@pytest.mark.skip(
    reason=(
        "DEFERRED_TO_PHASE_8C — continuity_check pipeline handler "
        "ships in phase 8c life-management; spec §18.7."
    )
)
async def test_item_38_continuity_check_fires_for_medication_window(
    tmp_path: Path,
) -> None:
    """Spec §18.2 item 38: a medication-window continuity check fires
    inside an active conversation and the supervisor surfaces it
    inline. Requires Phase 8c's life-management pipeline handlers.
    """


# Item 39 — Pipeline completion events triggers downstream pipeline ------


@pytest.mark.skip(
    reason=(
        "DEFERRED_TO_PHASE_8B — post_session_memory + post_memory_vault "
        "stage handlers live in phase 8b memory steward; spec §18.7."
    )
)
async def test_item_39_sequence_complete_chains_post_memory_vault(
    tmp_path: Path,
) -> None:
    """Spec §18.2 item 39: ``post_session_memory`` completion fires
    ``post_memory_vault`` via ``sequence_complete`` trigger, verified
    via ledger. Requires phase 8b stage handlers.
    """


# Item 40 — WAKE_UP_WINDOW wake_up_preparation pipeline ------------------


@pytest.mark.skip(
    reason=(
        "DEFERRED_TO_PHASE_8C — wake_up_preparation pipeline handler "
        "ships in phase 8c life-management; spec §18.7."
    )
)
async def test_item_40_wake_up_preparation_runs_before_wake_window(
    tmp_path: Path,
) -> None:
    """Spec §18.2 item 40: before a simulated wake time the
    ``wake_up_preparation`` pipeline runs and the briefing is
    available at wake. Requires phase 8c handlers.
    """


# Item 41 — Contextual engagement on emotion shift -----------------------


@pytest.mark.skip(
    reason=(
        "DEFERRED_TO_PHASE_8A — contextual_engagement ProactiveAgent "
        "handler ships in phase 8a; spec §18.7."
    )
)
async def test_item_41_contextual_engagement_fires_on_fatigue_shift(
    tmp_path: Path,
) -> None:
    """Spec §18.2 item 41: emotion assessor detects a fatigue shift
    mid-session, ``contextual_engagement`` pipeline fires, Kora offers
    a context-appropriate nudge. Requires phase 8a ProactiveAgent
    pipeline handlers.
    """


# Item 42 — Proactive pattern scan on insight ----------------------------


@pytest.mark.skip(
    reason=(
        "DEFERRED_TO_PHASE_8D — INSIGHT_AVAILABLE emission from "
        "ContextEngine ships in phase 8d; spec §18.7."
    )
)
async def test_item_42_proactive_pattern_scan_fires_on_insight(
    tmp_path: Path,
) -> None:
    """Spec §18.2 item 42: a mock ContextEngine insight triggers
    ``proactive_pattern_scan``, nudge appears in notifications.
    Requires phase 8d ContextEngine wiring.
    """


# Item 43 — Open decisions tracker fires after 3 days --------------------


@pytest.mark.skip(
    reason=(
        "DEFERRED_TO_PHASE_8A — open-decision tracker handlers ship "
        "in phase 8a; spec §18.7. Tracker primitive landed in 7.5b "
        "but the DECISION_PENDING_3D timer handler is 8a's."
    )
)
async def test_item_43_open_decisions_tracker_fires_after_three_days(
    tmp_path: Path,
) -> None:
    """Spec §18.2 item 43: Jordan mentions a decision, supervisor
    records it, after 3 simulated days the ``DECISION_PENDING_3D``
    notification fires. Requires phase 8a tracker handler.
    """
