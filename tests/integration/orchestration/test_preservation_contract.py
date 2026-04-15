"""Slice 7.5c §17.7 — Autonomous preservation contract (10 rows).

This file is the behavioural gate for the slice. Each test corresponds
to exactly one row of the ``#17.7 Autonomous preservation contract``
table in the Phase 7.5 spec, and together they assert that the 12-node
autonomous runtime still behaves identically after being lifted onto
the orchestration engine as a ``user_autonomous_task`` pipeline.

Every row must stay green for the slice to land. Any regression here
is a preservation violation, not a simplification — follow the spec's
directive verbatim.

The preserved behaviours are:

    1.  12-node LangGraph declared as a ``Pipeline`` stage list
    2.  12-value ``AutonomousState.status`` literal drives
        :func:`route_next_node`
    3.  Topic overlap pause fires at score ``>= 0.70``
    4.  5-axis :class:`BudgetEnforcer` hard-stops before ``execute_step``
        when any axis is saturated
    5.  ``reflect()`` heuristic routes to ``replan`` when average
        confidence falls below 0.35 with at least one completed step
    6.  :class:`DecisionManager` honours ``auto_select`` vs ``never_auto``
        plus timeout-based auto-resolve
    7.  Legacy ``autonomous_checkpoints`` table rows migrate into the
        orchestration ``worker_tasks`` + ``pipeline_instances`` tables
    8.  ``classify_request`` keyword routing still chooses ``task`` vs
        ``routine`` mode from the goal text alone
    9.  ``safe_resume_token`` + ``elapsed_seconds`` are preserved across
        a dispatcher-level scratch checkpoint round-trip
    10. Same-node watchdog fires at 5 consecutive repeats of a
        non-cyclic node and transitions the task to ``failed``

The fake container / fake worker harness used here is deliberately
minimal — real worker harnesses do LLM calls, which would make these
tests slow and non-deterministic. The fake only supports the three
entry points the 12-node graph actually touches (``planner``,
``executor``, ``reviewer``) and returns pre-baked model outputs.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import aiosqlite
import pytest

from kora_v2.autonomous import graph as graph_nodes
from kora_v2.autonomous.pipeline_factory import (
    _MAX_SAME_NODE_REPEATS,
    _SCRATCH_INITIALISED_KEY,
    _SCRATCH_PREV_NODE_KEY,
    _SCRATCH_SAME_NODE_REPEATS_KEY,
    _SCRATCH_STATE_KEY,
    _SCRATCH_WALL_START_KEY,
    AUTONOMOUS_NODES,
    _autonomous_step_fn,
    _build_budget_enforcer,
    build_user_autonomous_task_pipeline,
    build_user_routine_task_pipeline,
)
from kora_v2.autonomous.runtime_context import (
    clear_autonomous_context,
    set_autonomous_context,
)
from kora_v2.autonomous.state import AutonomousCheckpoint, AutonomousState
from kora_v2.core.db import init_operational_db
from kora_v2.core.models import Plan, PlanOutput, PlanStep
from kora_v2.core.settings import AutonomousSettings, LLMSettings, get_settings
from kora_v2.runtime.orchestration.autonomous_migration import (
    migrate_legacy_autonomous_checkpoints,
)
from kora_v2.runtime.orchestration.checkpointing import CheckpointStore
from kora_v2.runtime.orchestration.decisions import DecisionManager
from kora_v2.runtime.orchestration.ledger import WorkLedger
from kora_v2.runtime.orchestration.pipeline import (
    PipelineInstanceState,
)
from kora_v2.runtime.orchestration.registry import (
    PipelineInstanceRegistry,
    WorkerTaskRegistry,
    init_orchestration_schema,
)
from kora_v2.runtime.orchestration.worker_task import (
    Checkpoint,
    StepContext,
    WorkerTask,
    WorkerTaskState,
    get_preset,
)

# ══════════════════════════════════════════════════════════════════════════
# Fakes — a minimal container the step function can actually drive
# ══════════════════════════════════════════════════════════════════════════


@dataclass
class _FakeSettings:
    autonomous: AutonomousSettings
    llm: LLMSettings


class _FakePlannerWorker:
    """Planner stub that returns a deterministic single-step plan."""

    def __init__(self, *, step_count: int = 1) -> None:
        self._step_count = step_count

    async def execute(self, plan_input: Any) -> PlanOutput:
        steps = [
            PlanStep(
                id=f"step-{i}",
                title=f"Step {i}",
                description="Fake step for preservation contract test",
                depends_on=[],
                estimated_minutes=1,
                worker="executor",
                tools_needed=[],
                energy_level="low",
                needs_review=False,
                review_criteria=[],
            )
            for i in range(self._step_count)
        ]
        plan = Plan(
            id=str(uuid.uuid4()),
            goal=plan_input.goal,
            steps=steps,
            estimated_total_minutes=self._step_count,
            confidence=0.9,
        )
        return PlanOutput(
            plan=plan,
            steps=steps,
            estimated_effort="quick",
            confidence=0.9,
            adhd_notes="fake",
        )


class _FakeContainer:
    """Minimal DI surface the 12-node graph touches."""

    def __init__(
        self,
        *,
        settings: _FakeSettings | None = None,
        planner: Any | None = None,
    ) -> None:
        if settings is None:
            base = get_settings()
            settings = _FakeSettings(
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
        self.settings = settings
        self._planner = planner or _FakePlannerWorker()

    def resolve_worker(self, name: str) -> Any:
        if name == "planner":
            return self._planner
        raise ValueError(f"Unknown worker: {name}")


# ══════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════


async def _prepare_db(tmp_path: Path) -> Path:
    db = tmp_path / "operational.db"
    # Orchestration schema + the legacy operational tables the graph
    # nodes touch (items, autonomous_plans, item_state_history, …).
    await init_orchestration_schema(db)
    await init_operational_db(db)
    return db


def _make_task(
    *,
    task_id: str = "task-preservation",
    goal: str = "Research climate adaptation strategies",
    scratch: dict[str, Any] | None = None,
    state: WorkerTaskState = WorkerTaskState.RUNNING,
) -> WorkerTask:
    """Build a WorkerTask suitable for direct step-fn invocation."""
    config = get_preset("long_background")
    task = WorkerTask(
        id=task_id,
        pipeline_instance_id=None,
        stage_name="plan",
        config=config,
        goal=goal,
        system_prompt="",
        state=state,
        created_at=datetime.now(UTC),
    )
    if scratch is not None:
        task.checkpoint_blob = Checkpoint(
            task_id=task_id,
            created_at=datetime.now(UTC),
            state=state,
            current_step_index=0,
            scratch_state=dict(scratch),
        )
    return task


def _make_ctx(task: WorkerTask, *, cancelled: bool = False) -> StepContext:
    """Build a StepContext whose checkpoint callback updates task.scratch.

    The callback mirrors what the real dispatcher does inside
    :meth:`Dispatcher._capture_checkpoint` — we persist the new
    scratch blob back onto the task so the next tick can rehydrate
    it. That keeps the test self-contained without wiring up the
    full dispatcher loop.
    """
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

    ctx = StepContext(
        task=task,
        limiter=None,  # type: ignore[arg-type]
        cancellation_flag=lambda: cancelled,
        now=lambda: datetime.now(UTC),
        checkpoint_callback=_checkpoint_callback,
    )
    return ctx


@pytest.fixture
def fake_container() -> _FakeContainer:
    return _FakeContainer()


@pytest.fixture
async def autonomous_context(
    tmp_path: Path, fake_container: _FakeContainer
) -> Path:
    """Install the process-level autonomous runtime context for a test."""
    db = await _prepare_db(tmp_path)
    set_autonomous_context(container=fake_container, db_path=db)
    try:
        yield db
    finally:
        clear_autonomous_context()


# ══════════════════════════════════════════════════════════════════════════
# Row 1 — 12-node LangGraph declared as a Pipeline
# ══════════════════════════════════════════════════════════════════════════


def test_row_01_pipeline_declares_all_twelve_autonomous_nodes() -> None:
    """The ``user_autonomous_task`` pipeline exposes every graph node.

    Preservation: the audit surface — the ordered stage list — must
    name every node the live step function can reach. ``AUTONOMOUS_NODES``
    is the single source of truth; the test cross-checks it against
    both pipeline declarations so drift is caught in either direction.
    """
    autonomous = build_user_autonomous_task_pipeline()
    routine = build_user_routine_task_pipeline()

    # The exact 12 nodes from the spec's 12-node graph.
    expected = {
        "plan", "persist_plan", "execute_step", "review_step",
        "checkpoint", "reflect", "replan", "decision_request",
        "waiting_on_user", "paused_for_overlap", "complete", "failed",
    }
    assert set(AUTONOMOUS_NODES) == expected
    assert len(AUTONOMOUS_NODES) == 12

    # Both pipelines share the identical stage list — only the trigger
    # differs (spec §17.6 routine variant).
    autonomous_stages = {stage.name for stage in autonomous.stages}
    routine_stages = {stage.name for stage in routine.stages}
    assert autonomous_stages == expected
    assert routine_stages == expected
    assert autonomous.name == "user_autonomous_task"
    assert routine.name == "user_routine_task"


# ══════════════════════════════════════════════════════════════════════════
# Row 2 — AutonomousState.status literal drives route_next_node
# ══════════════════════════════════════════════════════════════════════════


def test_row_02_state_status_literal_drives_routing() -> None:
    """Every status in the literal has a deterministic routing rule.

    Preservation: ``AutonomousState.status`` is the driver of
    :func:`route_next_node`. Every value appearing in the literal
    must resolve to a node the step function understands (or ``END``,
    for terminal and dispatcher-level pause states). This test catches
    the regression where a status is added but the router does not
    learn about it, or where a live status silently falls off into the
    ``# Fallback`` branch.
    """
    # Grab the literal values directly from the type annotation.
    status_field = AutonomousState.model_fields["status"]
    status_values = list(status_field.annotation.__args__)  # type: ignore[attr-defined]
    # Sanity: the literal has 12 values matching spec code reality.
    assert len(status_values) == 12
    assert "idle" in status_values

    valid_node_names = {
        "plan", "persist_plan", "execute_step", "review_step",
        "checkpoint", "reflect", "replan", "decision_request",
        "waiting_on_user", "paused_for_overlap", "complete",
        "failed", "END",
    }
    terminal = {"completed", "cancelled", "failed"}
    # ``idle`` is the pre-classify initial value; a live task never
    # holds it. ``paused_for_overlap`` is a dispatcher-pause status
    # that routes to END so the outer loop can exit cleanly.
    end_by_design = {"idle", "paused_for_overlap"}

    for status in status_values:
        state = AutonomousState(
            session_id="s",
            plan_id="p",
            status=status,
            pending_step_ids=["x"],  # prevent plan→plan fallback
        )
        next_node = graph_nodes.route_next_node(state)
        assert next_node in valid_node_names, (
            f"unknown node name {next_node!r} for status {status!r}"
        )
        if status in terminal or status in end_by_design:
            assert next_node == "END", (
                f"status {status!r} must route to END — got {next_node!r}"
            )
        else:
            assert next_node != "END", (
                f"live status {status!r} routed to END — missing routing rule"
            )


# ══════════════════════════════════════════════════════════════════════════
# Row 3 — Topic overlap pause fires at >= 0.70
# ══════════════════════════════════════════════════════════════════════════


async def test_row_03_overlap_score_above_threshold_pauses_task(
    autonomous_context: Path,
) -> None:
    """A reflect pass with ``overlap_score >= 0.70`` pauses the task.

    Preservation: the 0.70 threshold is the pause gate the supervisor
    uses to decide "this conversation is on my topic — step aside".
    The scoring function itself is unit-tested elsewhere; here we
    confirm the step function honours the state and returns the
    ``paused_for_state`` outcome the dispatcher knows to repark on.
    """
    task = _make_task(goal="Build weekly sleep improvement plan")

    state = AutonomousState(
        session_id=task.id,
        plan_id="plan-overlap",
        status="reviewing",  # route_next_node → checkpoint → reflect
        completed_step_ids=["step-0"],
        pending_step_ids=["step-1"],
        current_step_id="step-1",
        overlap_score=0.78,
        iteration_count=1,
        metadata={"goal": task.goal, "steps": {"step-1": {"id": "step-1"}}},
    )
    scratch = {
        _SCRATCH_INITIALISED_KEY: True,
        _SCRATCH_STATE_KEY: state.model_dump(mode="json"),
        _SCRATCH_PREV_NODE_KEY: "review_step",
        _SCRATCH_SAME_NODE_REPEATS_KEY: 0,
    }
    task.checkpoint_blob = Checkpoint(
        task_id=task.id,
        created_at=datetime.now(UTC),
        state=WorkerTaskState.RUNNING,
        current_step_index=1,
        scratch_state=scratch,
    )

    ctx = _make_ctx(task)
    # First tick runs checkpoint → status="checkpointing"
    first = await _autonomous_step_fn(task, ctx)
    assert first.outcome == "continue"

    # Second tick runs reflect → sees overlap_score=0.78 → pauses.
    second = await _autonomous_step_fn(task, ctx)
    assert second.outcome == "paused_for_state"
    assert "paused_for_overlap" in (second.result_summary or "")


def test_row_03_overlap_band_thresholds_are_preserved() -> None:
    """The >=0.70 pause threshold is not smuggled in via config.

    The magic number itself lives in
    :func:`kora_v2.runtime.orchestration.overlap._classify`; this test
    locks it down by asserting both the pause and the ambiguous-band
    classifications via direct calls.
    """
    from kora_v2.runtime.orchestration.overlap import _classify

    assert _classify(0.70).action == "pause"
    assert _classify(0.85).action == "pause"
    assert _classify(0.69).action == "ambiguous"
    assert _classify(0.45).action == "ambiguous"
    assert _classify(0.44).action == "continue"


# ══════════════════════════════════════════════════════════════════════════
# Row 4 — 5-axis BudgetEnforcer hard-stops at saturation
# ══════════════════════════════════════════════════════════════════════════


async def test_row_04_budget_hard_stop_transitions_to_failed(
    autonomous_context: Path,
) -> None:
    """Exhausting the wall-clock axis before a work node fails the task.

    Preservation: the step function must call ``check_before_step``
    before running any of ``plan``/``execute_step``/``replan``, and a
    hard stop must transition the task to ``failed`` with the
    dimension preserved in the error message. Tests the full 5-axis
    enforcer chain via the ``time`` axis (simplest to saturate
    deterministically).
    """
    import time as _time

    task = _make_task(goal="Budget saturation test")

    # State positioned right before plan: status="planned" with no
    # pending steps so route_next_node → "plan". We back-date
    # ``_SCRATCH_WALL_START_KEY`` so the step function computes an
    # elapsed of ~2h against the 1h max_session_hours limit — the
    # step function overwrites ``state.elapsed_seconds`` on entry
    # from the wall-clock diff, so we cannot just seed the field
    # directly on the state. Slice 7.5c switched the wall clock from
    # ``monotonic()`` to ``time.time()`` (epoch) so restart durability
    # is preserved; the back-date must use the same clock.
    state = AutonomousState(
        session_id=task.id,
        plan_id="plan-budget",
        status="planned",
        pending_step_ids=[],
        metadata={"goal": task.goal},
    )
    scratch = {
        _SCRATCH_INITIALISED_KEY: True,
        _SCRATCH_STATE_KEY: state.model_dump(mode="json"),
        _SCRATCH_PREV_NODE_KEY: None,
        _SCRATCH_SAME_NODE_REPEATS_KEY: 0,
        _SCRATCH_WALL_START_KEY: _time.time() - (60 * 60 * 2),
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

    assert result.outcome == "failed"
    assert result.error_message is not None
    assert "Budget limit reached" in result.error_message
    # The enforcer covers all 5 axes; we specifically exercised "time".
    # (quota / request / cost / token are unit-tested in
    # ``tests/unit/test_phase6_budget.py``.)
    assert "elapsed" in result.error_message.lower()


def test_row_04_budget_enforcer_has_all_five_axes() -> None:
    """The enforcer wires all 5 dimensions the preservation contract names."""
    settings = AutonomousSettings(
        enabled=True,
        max_session_hours=1.0,
        per_session_cost_limit=1.0,
        request_limit_per_hour=100,
        max_request_count=50,
    )
    enforcer = _build_budget_enforcer(
        _FakeContainer(
            settings=_FakeSettings(autonomous=settings, llm=get_settings().llm)
        )
    )
    assert enforcer is not None

    # Saturate each axis one at a time and confirm the dimension label
    # in the result. All 5 must be recognised.
    dimensions_seen: set[str] = set()

    state = AutonomousState(session_id="s", plan_id="p", status="planned")
    state.request_window_1h = 100
    dimensions_seen.add(enforcer._check_quota_window(state).dimension)
    state.request_window_1h = 0

    state.request_count = 200
    dimensions_seen.add(enforcer._check_request_count(state).dimension)
    state.request_count = 0

    state.elapsed_seconds = 60 * 60 * 2
    dimensions_seen.add(enforcer._check_wall_time(state).dimension)
    state.elapsed_seconds = 0

    state.cost_estimate = 10.0
    dimensions_seen.add(enforcer._check_cost(state).dimension)
    state.cost_estimate = 0.0

    state.token_estimate = 10_000_000
    dimensions_seen.add(enforcer._check_tokens(state).dimension)

    assert dimensions_seen == {"quota", "request", "time", "cost", "token"}


# ══════════════════════════════════════════════════════════════════════════
# Row 5 — reflect() heuristic triggers replan at avg confidence < 0.35
# ══════════════════════════════════════════════════════════════════════════


def test_row_05_reflect_triggers_replan_on_low_confidence() -> None:
    """Average step confidence < 0.35 with one completed step → replan."""
    state = AutonomousState(
        session_id="s",
        plan_id="p",
        status="checkpointing",
        pending_step_ids=["step-2", "step-3"],
        completed_step_ids=["step-1"],
        quality_summary={
            "step-1": {"confidence": 0.25},
        },
    )
    updated, action = graph_nodes.reflect(state)
    assert action == "replan"
    assert "below threshold" in (updated.latest_reflection or "")


def test_row_05_reflect_continues_on_high_confidence() -> None:
    """Average step confidence >= 0.35 → continue."""
    state = AutonomousState(
        session_id="s",
        plan_id="p",
        status="checkpointing",
        pending_step_ids=["step-2"],
        completed_step_ids=["step-1"],
        quality_summary={"step-1": {"confidence": 0.9}},
    )
    _, action = graph_nodes.reflect(state)
    assert action == "continue"


def test_row_05_reflect_ignores_low_confidence_without_completed_steps() -> None:
    """Preservation: the heuristic requires >=1 completed step.

    With no completed steps the planner has not produced any evidence
    worth replanning on, so we continue. This guards the
    ``len(state.completed_step_ids) > 0`` clause.
    """
    state = AutonomousState(
        session_id="s",
        plan_id="p",
        status="checkpointing",
        pending_step_ids=["step-1"],
        completed_step_ids=[],
        quality_summary={"step-1": {"confidence": 0.1}},
    )
    _, action = graph_nodes.reflect(state)
    assert action == "continue"


# ══════════════════════════════════════════════════════════════════════════
# Row 6 — DecisionManager auto_select / never_auto + timeout
# ══════════════════════════════════════════════════════════════════════════


def test_row_06_decision_manager_auto_select_and_never_auto() -> None:
    """Both policies are preserved with the same behaviour contract.

    Preservation: ``auto_select`` decisions auto-resolve to their
    recommendation on timeout; ``never_auto`` decisions never do and
    must wait for a real user answer. The lifted DecisionManager
    module still ships both.
    """
    manager = DecisionManager()

    # Auto-select with immediate timeout resolves to the recommendation.
    auto = manager.create_decision(
        options=["proceed", "pause"],
        recommendation="proceed",
        policy="auto_select",
        timeout_minutes=0,  # already expired
    )
    # The decision was created with expires_at = now + 0 minutes. Wait
    # a few milliseconds so is_expired() returns True deterministically.
    auto.expires_at = auto.expires_at - timedelta(seconds=1)
    result = manager.check_timeout(auto)
    assert result is not None
    assert result.chosen == "proceed"
    assert result.method == "timeout"

    # never_auto never auto-resolves even when expired.
    never = manager.create_decision(
        options=["accept", "reject"],
        recommendation="accept",
        policy="never_auto",
        timeout_minutes=0,
    )
    never.expires_at = never.expires_at - timedelta(seconds=1)
    assert manager.check_timeout(never) is None

    # But a real user answer still resolves it.
    user_result = manager.submit_answer(never.decision_id, chosen="reject")
    assert user_result.chosen == "reject"
    assert user_result.method == "user"


# ══════════════════════════════════════════════════════════════════════════
# Row 7 — Legacy autonomous_checkpoints migration
# ══════════════════════════════════════════════════════════════════════════


async def test_row_07_legacy_checkpoint_migration_creates_orchestration_rows(
    tmp_path: Path,
) -> None:
    """A legacy in-flight checkpoint becomes a pipeline instance + task.

    Preservation: on engine start the idempotent migration walks every
    unique ``(session_id, plan_id)`` pair in the legacy table and
    emits one matching ``pipeline_instances`` row, one
    ``worker_tasks`` row, and one scratch-state ``Checkpoint`` so the
    normal dispatcher loop can resume where the legacy runtime left
    off.
    """
    db = await _prepare_db(tmp_path)

    # Seed the legacy table with one in-flight checkpoint.
    state = AutonomousState(
        session_id="sess-legacy",
        plan_id="plan-legacy",
        status="executing",
        pending_step_ids=["step-a", "step-b"],
        completed_step_ids=["step-0"],
        elapsed_seconds=120,
        request_count=5,
        metadata={"goal": "Finish the research draft"},
    )
    legacy_checkpoint = AutonomousCheckpoint(
        checkpoint_id="cp-1",
        session_id=state.session_id,
        plan_id=state.plan_id,
        state=state,
        resume_token="rt-legacy",
        elapsed_seconds=state.elapsed_seconds,
        request_count=state.request_count,
    )
    # ``init_operational_db`` has already created the legacy
    # ``autonomous_checkpoints`` table with its historical column
    # layout (see ``core/db.py`` lines 61-72). The migration only
    # reads ``plan_json``, so we match that schema exactly.
    async with aiosqlite.connect(str(db)) as conn:
        await conn.execute(
            "INSERT INTO autonomous_checkpoints "
            "(id, plan_id, plan_json, completed_steps, current_step, "
            " accumulated_context, artifacts, elapsed_minutes, "
            " reflection, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                legacy_checkpoint.checkpoint_id,
                legacy_checkpoint.plan_id,
                legacy_checkpoint.model_dump_json(),
                "[]",
                None,
                "",
                "[]",
                2,
                None,
                datetime.now(UTC).isoformat(),
            ),
        )
        await conn.commit()

    # Run the migration.
    task_registry = WorkerTaskRegistry(db)
    instance_registry = PipelineInstanceRegistry(db)
    checkpoint_store = CheckpointStore(db)
    ledger = WorkLedger(db)
    migrated = await migrate_legacy_autonomous_checkpoints(
        db_path=db,
        ledger=ledger,
        task_registry=task_registry,
        instance_registry=instance_registry,
        checkpoint_store=checkpoint_store,
    )
    assert migrated == 1

    # The new orchestration rows are in place.
    active_tasks = await task_registry.load_all_non_terminal()
    matching = [
        task for task in active_tasks
        if task.goal == "Finish the research draft"
    ]
    assert len(matching) == 1
    new_task = matching[0]
    assert new_task.state is WorkerTaskState.PAUSED_FOR_STATE

    instance = await instance_registry.load(new_task.pipeline_instance_id)
    assert instance is not None
    assert instance.pipeline_name == "user_autonomous_task"
    assert instance.state is PipelineInstanceState.PAUSED
    assert instance.parent_session_id == "sess-legacy"

    # The scratch state carries the legacy state through unchanged.
    checkpoint = await checkpoint_store.load(new_task.id)
    assert checkpoint is not None
    assert checkpoint.scratch_state[_SCRATCH_INITIALISED_KEY] is True
    restored = AutonomousState.model_validate(
        checkpoint.scratch_state[_SCRATCH_STATE_KEY]
    )
    assert restored.status == "executing"
    assert restored.elapsed_seconds == 120
    assert restored.request_count == 5
    assert restored.metadata["goal"] == "Finish the research draft"

    # Re-running the migration must be a no-op (idempotent marker).
    migrated_again = await migrate_legacy_autonomous_checkpoints(
        db_path=db,
        ledger=ledger,
        task_registry=task_registry,
        instance_registry=instance_registry,
        checkpoint_store=checkpoint_store,
    )
    assert migrated_again == 0


# ══════════════════════════════════════════════════════════════════════════
# Row 8 — classify_request keyword routing (task vs routine)
# ══════════════════════════════════════════════════════════════════════════


def test_row_08_classify_request_keyword_routing() -> None:
    """Routine keywords → mode="routine"; everything else → mode="task".

    Preservation: the keyword set must still pick out the canonical
    routine words and everything else must stay ``task``. The
    returned state's ``metadata["goal"]`` is how downstream nodes
    access the user's goal — it must also be preserved.
    """
    routine_goals = (
        "Run my morning routine",
        "evening walkthrough before bed",
        "Daily habit tracker check",
        "Walkthrough the workspace ritual",
    )
    for goal in routine_goals:
        state = graph_nodes.classify_request(goal=goal, session_id="s")
        assert state.mode == "routine", f"expected routine for goal={goal!r}"
        assert state.status == "planned"
        assert state.metadata["goal"] == goal

    task_goals = (
        "Research battery chemistry papers",
        "Debug the failing CI pipeline",
        "Write a status report for the board",
    )
    for goal in task_goals:
        state = graph_nodes.classify_request(goal=goal, session_id="s")
        assert state.mode == "task", f"expected task for goal={goal!r}"
        assert state.metadata["goal"] == goal


# ══════════════════════════════════════════════════════════════════════════
# Row 9 — safe_resume_token + elapsed_seconds preserved across resume
# ══════════════════════════════════════════════════════════════════════════


async def test_row_09_resume_preserves_token_and_elapsed_time(
    autonomous_context: Path,
) -> None:
    """A task that checkpoints and is re-ticked keeps its identity.

    Preservation: at every checkpoint the graph sets a fresh
    ``safe_resume_token`` and the scratch-state round-trip must not
    lose ``elapsed_seconds`` — both are the fields the supervisor
    uses to reconcile a resumed task with its pre-pause history. The
    legacy runtime rewrites ``safe_resume_token`` at every
    :func:`graph.checkpoint` call, so the preservation contract here
    is "after a checkpoint pass the token is populated and the
    elapsed time is the back-dated wall-clock value, not reset".
    """
    import time as _time

    task = _make_task(goal="Long-running research task")

    # Back-date wall_start so the step function's elapsed computation
    # returns ~30 minutes (1800s). Slice 7.5c switched the wall clock
    # from ``monotonic()`` to ``time.time()`` (epoch) so restart
    # durability is preserved; the back-date must use the same clock.
    wall_start = _time.time() - 1800

    # Seed a state with a pending step, positioned to run ``checkpoint``
    # on the next tick (status="reviewing" → route_next_node →
    # "checkpoint").
    state = AutonomousState(
        session_id=task.id,
        plan_id="plan-resume",
        status="reviewing",
        pending_step_ids=["step-1"],
        completed_step_ids=["step-0"],
        current_step_id="step-1",
        safe_resume_token=None,  # checkpoint will populate this
        metadata={"goal": task.goal},
    )
    scratch = {
        _SCRATCH_INITIALISED_KEY: True,
        _SCRATCH_STATE_KEY: state.model_dump(mode="json"),
        _SCRATCH_PREV_NODE_KEY: "review_step",
        _SCRATCH_SAME_NODE_REPEATS_KEY: 0,
        _SCRATCH_WALL_START_KEY: wall_start,
    }
    task.checkpoint_blob = Checkpoint(
        task_id=task.id,
        created_at=datetime.now(UTC),
        state=WorkerTaskState.RUNNING,
        current_step_index=1,
        scratch_state=scratch,
    )

    ctx = _make_ctx(task)
    result = await _autonomous_step_fn(task, ctx)
    assert result.outcome == "continue"

    # After the tick, the scratch carries the new state round-tripped
    # through JSON. Pull it back out and verify the two fields.
    new_scratch = task.checkpoint_blob.scratch_state  # type: ignore[union-attr]
    new_state = AutonomousState.model_validate(new_scratch[_SCRATCH_STATE_KEY])
    # (a) Fresh resume token was issued on the checkpoint pass.
    assert new_state.safe_resume_token is not None
    assert len(new_state.safe_resume_token) > 0
    # (b) elapsed_seconds was preserved through the wall-clock gate —
    # must be >= our 1800s seed, not reset to 0.
    assert new_state.elapsed_seconds >= 1800


async def test_row_09_safe_resume_token_round_trips_through_scratch(
    autonomous_context: Path,
) -> None:
    """The token survives a full scratch-state JSON round-trip.

    Preservation: the legacy checkpoint path serialised the token
    into the DB blob; the new path serialises it into
    ``Checkpoint.scratch_state``. Verify the Pydantic round-trip
    preserves the value exactly.
    """
    state = AutonomousState(
        session_id="sess-roundtrip",
        plan_id="plan-roundtrip",
        status="executing",
        safe_resume_token="token-xyz-789",
        elapsed_seconds=3600,
    )
    serialised = state.model_dump(mode="json")
    # Push through a JSON boundary (what scratch_state → SQLite does).
    reloaded_json = json.loads(json.dumps(serialised))
    reloaded = AutonomousState.model_validate(reloaded_json)
    assert reloaded.safe_resume_token == "token-xyz-789"
    assert reloaded.elapsed_seconds == 3600


# ══════════════════════════════════════════════════════════════════════════
# Row 10 — Same-node watchdog (5 repeats → failed)
# ══════════════════════════════════════════════════════════════════════════


async def test_row_10_same_node_watchdog_fails_after_max_repeats(
    autonomous_context: Path,
) -> None:
    """The watchdog transitions the task to failed at 5 consecutive hits.

    Preservation: the ``_MAX_SAME_NODE_REPEATS`` constant is 5 and the
    watchdog excludes ``waiting_on_user`` (legitimately cyclic). We
    simulate the pathological case by priming the scratch counter to
    ``_MAX_SAME_NODE_REPEATS - 1`` on a non-cyclic node (``plan``),
    then verifying the next tick trips the watchdog and fails the
    task without running the node.
    """
    task = _make_task(goal="Watchdog target")

    # Prime a state that will route to "plan" on every tick: status
    # "planned" with empty pending_step_ids.
    state = AutonomousState(
        session_id=task.id,
        plan_id="plan-watchdog",
        status="planned",
        pending_step_ids=[],
        elapsed_seconds=10,
        metadata={"goal": task.goal},
    )
    scratch = {
        _SCRATCH_INITIALISED_KEY: True,
        _SCRATCH_STATE_KEY: state.model_dump(mode="json"),
        _SCRATCH_PREV_NODE_KEY: "plan",
        _SCRATCH_SAME_NODE_REPEATS_KEY: _MAX_SAME_NODE_REPEATS - 1,
        _SCRATCH_WALL_START_KEY: 0.0,
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
    assert result.outcome == "failed"
    assert result.error_message is not None
    assert "Stuck in node 'plan'" in result.error_message


def test_row_10_watchdog_constant_is_five_and_cyclic_set_is_preserved() -> None:
    """The watchdog's magic numbers match the preservation contract.

    Preservation: the spec pins ``_MAX_SAME_NODE_REPEATS = 5`` and the
    cyclic carveout set to exactly ``{"waiting_on_user"}``. Any drift
    is a behavioural regression — e.g. raising the repeat count would
    silently hide a real stuck-loop bug. The set previously held a
    dead ``"checkpointing"`` entry; ``route_next_node`` never actually
    returns that literal (status=="checkpointing" routes to reflect),
    so dropping it tightens the watchdog without changing behaviour.
    """
    from kora_v2.autonomous.pipeline_factory import _LEGITIMATELY_CYCLIC_NODES

    assert _MAX_SAME_NODE_REPEATS == 5
    assert _LEGITIMATELY_CYCLIC_NODES == frozenset({"waiting_on_user"})


async def test_row_10_watchdog_does_not_trip_under_threshold(
    autonomous_context: Path,
) -> None:
    """Four consecutive repeats of a non-cyclic node do not trip the watchdog.

    Preservation: the watchdog only fires when ``consecutive >= 5``.
    A task that is legitimately re-entering the same node four times
    (for instance during a tight replan→execute loop) must survive
    without being killed.
    """
    import time as _time

    task = _make_task(goal="Under-threshold watchdog test")

    state = AutonomousState(
        session_id=task.id,
        plan_id="plan-underthreshold",
        status="planned",
        pending_step_ids=[],
        metadata={"goal": task.goal},
    )
    # Prime the counter to MAX-2 (=3) — the next tick will see
    # consecutive=4 which is still under the trip count. Wall start
    # must be the current wall-clock epoch (not ``0.0``) so the budget
    # enforcer's elapsed-time axis stays well under the 1h hard stop.
    # Slice 7.5c switched the wall clock from ``monotonic()`` to
    # ``time.time()`` for restart durability; the seed must match.
    scratch = {
        _SCRATCH_INITIALISED_KEY: True,
        _SCRATCH_STATE_KEY: state.model_dump(mode="json"),
        _SCRATCH_PREV_NODE_KEY: "plan",
        _SCRATCH_SAME_NODE_REPEATS_KEY: _MAX_SAME_NODE_REPEATS - 2,
        _SCRATCH_WALL_START_KEY: _time.time(),
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
    # The planner (fake) produced a step plan → status transitioned
    # back to "planned" with pending steps → outcome is "continue".
    assert result.outcome == "continue"
    # Counter is now MAX-1 (=4) on the "plan" node.
    new_scratch = task.checkpoint_blob.scratch_state  # type: ignore[union-attr]
    assert (
        new_scratch[_SCRATCH_SAME_NODE_REPEATS_KEY]
        == _MAX_SAME_NODE_REPEATS - 1
    )
    assert new_scratch[_SCRATCH_PREV_NODE_KEY] == "plan"
