"""Slice 7.5b acceptance tests — items 25-46 from spec §18.7.

Covers:
  25. Long-running task dispatch (working-doc created, pipeline registered)
  26. Working document visible in Inbox
  27. Adaptive task list mutation (plan items appended at runtime)
  28. Kora-judged completion (status → done in frontmatter)
  29. Mid-flight task query (get_task_progress)
  30. Task cancellation
  31. User edit to working document (reconcile_plan surfaces new items)
  34. Templated fallback — zero-request delivery
  35. Crash-recovery plumbing — working-doc persists across engine restart
  37. Merge on re-engagement — list_tasks with relevant_to_session filter
  44. Runtime pipeline via routine creation — persists to ``runtime_pipelines``
  46. Supervisor dispatch-and-end — templated ack without LLM provider request
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from datetime import time as dtime
from pathlib import Path

import aiosqlite

from kora_v2.runtime.orchestration import (
    OrchestrationEngine,
    StepContext,
    StepResult,
    UserScheduleProfile,
    WorkerTask,
    WorkerTaskState,
    init_orchestration_schema,
)
from kora_v2.runtime.orchestration.notifications import DeliveryChannel
from kora_v2.runtime.orchestration.pipeline import (
    FailurePolicy,
    InterruptionPolicy,
    Pipeline,
    PipelineStage,
)
from kora_v2.runtime.orchestration.triggers import time_of_day
from kora_v2.runtime.orchestration.working_doc import (
    WorkingDocStatus,
    WorkingDocUpdate,
)

# ── Helpers ──────────────────────────────────────────────────────────────


async def _make_engine(tmp_path: Path) -> OrchestrationEngine:
    db = tmp_path / "operational.db"
    await init_orchestration_schema(db)
    engine = OrchestrationEngine(
        db,
        schedule_profile=UserScheduleProfile(timezone="UTC"),
        memory_root=tmp_path / "_KoraMemory",
        tick_interval=0.01,
    )
    engine.working_docs.ensure_inbox()
    engine.templates.ensure_defaults()
    engine.templates.reload_if_changed()
    await engine.limiter.replay_from_log()
    # Force DEEP_IDLE so background tasks can run.
    engine.state_machine.note_session_end(
        datetime.now(UTC) - timedelta(hours=2)
    )
    return engine


def _make_pipeline(name: str) -> Pipeline:
    return Pipeline(
        name=name,
        description="ad-hoc research pipeline",
        stages=[
            PipelineStage(
                name="run",
                task_preset="long_background",  # type: ignore[arg-type]
                goal_template="{goal}",
            )
        ],
        triggers=[],
        interruption_policy=InterruptionPolicy.PAUSE_ON_CONVERSATION,
        failure_policy=FailurePolicy.FAIL_PIPELINE,
        intent_duration="long",
    )


async def _spawn_research_pipeline(
    engine: OrchestrationEngine,
    *,
    goal: str,
    seed_plan_items: list[str] | None = None,
    parent_session_id: str | None = None,
) -> tuple[Path, object]:
    """Register + start a research pipeline with an attached working doc.

    Returns ``(doc_path, instance)``. Mirrors the two-phase pattern in
    the production supervisor tool ``_orch_decompose_and_dispatch``:
    start the instance first, derive the doc path from its real id,
    then create the working doc.
    """
    instance = await engine.start_pipeline_instance(
        "research",
        goal=goal,
        working_doc_path="",
        parent_session_id=parent_session_id,
    )
    doc_path = engine.working_docs.doc_path(
        pipeline_name="research",
        instance_id=instance.id,
        goal=goal,
    )
    await engine.working_docs.create(
        instance_id=instance.id,
        task_id=instance.id,
        pipeline_name="research",
        goal=goal,
        seed_plan_items=seed_plan_items,
    )
    return doc_path, instance


# ── Item 25 + 26: long-running dispatch + working doc in Inbox ──────────


async def test_item_25_26_dispatch_creates_working_doc_in_inbox(
    tmp_path: Path,
) -> None:
    engine = await _make_engine(tmp_path)
    engine.register_pipeline(_make_pipeline("research"))

    # Start the pipeline instance first so we can derive the working
    # doc path from the real instance id (the dispatch wrapper in
    # graph/dispatch.py uses the same two-phase sequence: start the
    # instance, then create the doc with the instance's real id).
    instance = await engine.start_pipeline_instance(
        "research",
        goal="Study ADHD research papers",
        working_doc_path="",  # placeholder; real path computed below
    )

    await engine.working_docs.create(
        instance_id=instance.id,
        task_id=instance.id,
        pipeline_name="research",
        goal="Study ADHD research papers",
        seed_plan_items=["survey the literature", "extract themes"],
    )

    # Working doc is inside the per-test _KoraMemory/Inbox/
    inbox = tmp_path / "_KoraMemory" / "Inbox"
    docs = list(inbox.glob("*.md"))
    assert len(docs) == 1
    body = docs[0].read_text()
    assert "status: in_progress" in body
    assert "Study ADHD research papers" in body


# ── Item 27: adaptive task list mutation ────────────────────────────────


async def test_item_27_adaptive_plan_mutation(tmp_path: Path) -> None:
    engine = await _make_engine(tmp_path)
    engine.register_pipeline(_make_pipeline("research"))
    doc_path, instance = await _spawn_research_pipeline(
        engine, goal="dig into topic", seed_plan_items=["initial scan"]
    )

    handle_before = await engine.working_docs.read(doc_path)
    assert handle_before is not None
    before = [i.text for i in handle_before.parse_current_plan()]
    assert before == ["initial scan"]

    # Stage step_fn asks to append a new plan item mid-flight.
    await engine.working_docs.apply_update(
        instance_id=instance.id,
        path=doc_path,
        update=WorkingDocUpdate(
            section_patches={
                "Current Plan": "- [ ] initial scan\n- [ ] deep dive on lead A\n"
            }
        ),
    )

    handle_after = await engine.working_docs.read(doc_path)
    assert handle_after is not None
    after = [i.text for i in handle_after.parse_current_plan()]
    assert "deep dive on lead A" in after


# ── Item 28: Kora-judged completion ─────────────────────────────────────


async def test_item_28_kora_judged_completion(tmp_path: Path) -> None:
    engine = await _make_engine(tmp_path)
    engine.register_pipeline(_make_pipeline("research"))
    doc_path, instance = await _spawn_research_pipeline(
        engine, goal="finalize"
    )

    await engine.working_docs.mark_status(
        instance_id=instance.id,
        path=doc_path,
        status=WorkingDocStatus.DONE,
        reason="llm_judgement",
        completion_text="findings documented",
    )

    handle = await engine.working_docs.read(doc_path)
    assert handle is not None
    assert handle.status == WorkingDocStatus.DONE.value


# ── Item 29: mid-flight task query via get_task_progress ───────────────


async def test_item_29_mid_flight_task_query(tmp_path: Path) -> None:
    engine = await _make_engine(tmp_path)
    engine.register_pipeline(_make_pipeline("research"))
    doc_path, instance = await _spawn_research_pipeline(
        engine,
        goal="check in",
        seed_plan_items=["step A", "step B"],
    )
    # Update the instance's working_doc_path so the engine's progress
    # reader can resolve it.
    instance.working_doc_path = str(doc_path)
    await engine.instance_registry.save(instance)

    async def step_fn(task: WorkerTask, ctx: StepContext) -> StepResult:
        return StepResult(outcome="running")

    task = await engine.dispatch_task(
        goal="check in",
        system_prompt="p",
        step_fn=step_fn,
        preset="bounded_background",
        stage_name="run",
        pipeline_instance_id=instance.id,
    )

    progress = await engine.get_task_progress(task.id)
    assert progress["found"] is True
    assert progress["task_id"] == task.id
    assert progress["goal"] == "check in"
    assert progress["working_doc_path"] == str(doc_path)
    assert {p["text"] for p in progress["plan_items"]} == {"step A", "step B"}


# ── Item 30: task cancellation ──────────────────────────────────────────


async def test_item_30_task_cancellation(tmp_path: Path) -> None:
    engine = await _make_engine(tmp_path)
    engine.register_pipeline(_make_pipeline("research"))
    doc_path, instance = await _spawn_research_pipeline(
        engine,
        goal="cancel-probe partial preservation",
        seed_plan_items=["draft option A", "draft option B"],
    )
    instance.working_doc_path = str(doc_path)
    await engine.instance_registry.save(instance)

    async def step_fn(task: WorkerTask, ctx: StepContext) -> StepResult:
        if task.cancellation_requested:
            return StepResult(outcome="cancelled", result_summary="stopped")
        return StepResult(outcome="running")

    task = await engine.dispatch_task(
        goal="infinite",
        system_prompt="p",
        step_fn=step_fn,
        preset="bounded_background",
        stage_name="run",
        pipeline_instance_id=instance.id,
    )
    await engine.tick_once()  # one running tick
    ok = await engine.cancel_task(task.id, reason="supervisor_request")
    assert ok is True

    await engine.run_task_to_completion(task, max_ticks=5)
    reloaded = await engine.task_registry.load(task.id)
    assert reloaded is not None
    assert reloaded.state in {
        WorkerTaskState.CANCELLED,
        WorkerTaskState.COMPLETED,
        WorkerTaskState.FAILED,
    }
    doc_text = doc_path.read_text(encoding="utf-8")
    assert "status: cancelled" in doc_text
    assert "Pipeline cancelled" in doc_text
    assert "existing working doc content preserved" in doc_text
    assert "draft option A" in doc_text


# ── Item 31: user edit to working document picked up by reconcile ──────


async def test_item_31_user_edit_reconcile_plan(tmp_path: Path) -> None:
    engine = await _make_engine(tmp_path)
    engine.register_pipeline(_make_pipeline("research"))
    doc_path, instance = await _spawn_research_pipeline(
        engine, goal="edited", seed_plan_items=["original item"]
    )

    # Simulate a direct user edit by appending a plan item to the
    # Current Plan section via a raw file write.
    raw = doc_path.read_text()
    raw = raw.replace(
        "- [ ] original item\n",
        "- [ ] original item\n- [ ] user-added item\n",
    )
    doc_path.write_text(raw)

    # reconcile_plan surfaces new items the writer did not add.
    handle = await engine.working_docs.read(doc_path)
    assert handle is not None
    diff = engine.working_docs.reconcile_plan(
        handle,
        known_task_descriptions=["original item"],
    )
    added_texts = [item.text for item in diff.added]
    assert "user-added item" in added_texts


# ── Item 34: templated fallback (zero provider requests) ───────────────


async def test_item_34_templated_fallback_rate_limit(tmp_path: Path) -> None:
    engine = await _make_engine(tmp_path)

    # Ship the template and deliver through the gate directly — no
    # provider capacity is consumed because send_templated never calls
    # the LLM.
    result = await engine.notify(
        template_id="rate_limit_paused",
        via=DeliveryChannel.TURN_RESPONSE,
        template_vars={"minutes": 7},
    )
    assert result.delivered is True
    assert result.tier == "templated"
    assert result.template_id == "rate_limit_paused"
    assert "7" in result.text
    # Limiter is untouched — the templated path did not acquire anything.
    snap = await engine.limiter_snapshot()
    assert snap["total"] == 0


# ── Item 35: crash recovery — working doc survives engine restart ──────


async def test_item_35_working_doc_survives_restart(tmp_path: Path) -> None:
    engine = await _make_engine(tmp_path)
    engine.register_pipeline(_make_pipeline("research"))
    doc_path, _ = await _spawn_research_pipeline(
        engine, goal="crash-recover", seed_plan_items=["pre-crash step"]
    )

    # "Crash" — drop the engine reference and reopen.
    db = engine._db_path
    del engine

    engine2 = OrchestrationEngine(
        db,
        schedule_profile=UserScheduleProfile(timezone="UTC"),
        memory_root=tmp_path / "_KoraMemory",
        tick_interval=0.01,
    )
    handle = await engine2.working_docs.read(doc_path)
    assert handle is not None
    plan = [i.text for i in handle.parse_current_plan()]
    assert "pre-crash step" in plan


# ── Item 37: merge on re-engagement — list_tasks(relevant_to_session) ─


async def test_item_37_list_tasks_by_session(tmp_path: Path) -> None:
    engine = await _make_engine(tmp_path)
    engine.register_pipeline(_make_pipeline("research"))
    doc_path, instance = await _spawn_research_pipeline(
        engine, goal="re-engage", parent_session_id="sess-42"
    )

    async def step_fn(task: WorkerTask, ctx: StepContext) -> StepResult:
        return StepResult(outcome="running")

    task = await engine.dispatch_task(
        goal="re-engage",
        system_prompt="p",
        step_fn=step_fn,
        preset="bounded_background",
        stage_name="run",
        pipeline_instance_id=instance.id,
    )
    await engine.tick_once()

    matches = await engine.list_tasks(relevant_to_session="sess-42")
    matched_ids = {t.id for t in matches}
    assert task.id in matched_ids

    # Unknown session returns no match for this task.
    other = await engine.list_tasks(relevant_to_session="sess-99")
    assert task.id not in {t.id for t in other}


# ── §13.1 cases 2, 3, 4: list_tasks four-condition OR ────────────────────


async def test_list_tasks_case_2_system_pipeline_in_window(
    tmp_path: Path,
) -> None:
    """Case 2: a task on a system pipeline (parent_session_id IS NULL)
    that is currently running (or recently completed) is surfaced
    regardless of the session filter — system output is everyone's
    concern.
    """
    engine = await _make_engine(tmp_path)
    engine.register_pipeline(_make_pipeline("research"))
    # Spawn a research pipeline with no parent_session_id → system pipeline
    _, instance = await _spawn_research_pipeline(
        engine, goal="system-tier work", parent_session_id=None
    )

    async def step_fn(task: WorkerTask, ctx: StepContext) -> StepResult:
        return StepResult(outcome="running")

    task = await engine.dispatch_task(
        goal="system-tier work",
        system_prompt="p",
        step_fn=step_fn,
        preset="bounded_background",
        stage_name="run",
        pipeline_instance_id=instance.id,
    )
    await engine.tick_once()

    # Even though the session id is unrelated, the system-pipeline task
    # should surface because the instance is running.
    matches = await engine.list_tasks(relevant_to_session="sess-someone-else")
    assert task.id in {t.id for t in matches}


async def test_list_tasks_case_3_unacked_terminal_state(
    tmp_path: Path,
) -> None:
    """Case 3: tasks in COMPLETED/FAILED with no acknowledgement timestamp
    still surface so the supervisor can deliver the result.
    """
    engine = await _make_engine(tmp_path)
    engine.register_pipeline(_make_pipeline("research"))
    _, instance = await _spawn_research_pipeline(
        engine, goal="acked work", parent_session_id="sess-A"
    )

    async def step_fn(task: WorkerTask, ctx: StepContext) -> StepResult:
        return StepResult(outcome="completed", summary="done")

    task = await engine.dispatch_task(
        goal="acked work",
        system_prompt="p",
        step_fn=step_fn,
        preset="bounded_background",
        stage_name="run",
        pipeline_instance_id=instance.id,
    )
    # Force terminal state directly so the test does not race on the
    # dispatcher tick — list_tasks pulls from the registry, so we mark
    # it complete and persist.
    task.state = WorkerTaskState.COMPLETED
    task.result_acknowledged_at = None
    await engine.task_registry.save(task)

    # An unrelated session — case 1 fails. The unacked terminal state
    # should still cause it to appear via case 3.
    matches = await engine.list_tasks(relevant_to_session="sess-other")
    assert task.id in {t.id for t in matches}


async def test_list_tasks_case_4_overlap_ambiguous_band(
    tmp_path: Path,
) -> None:
    """Case 4: a task whose goal overlaps the user message in the
    ambiguous 0.45 ≤ score ≤ 0.70 band is surfaced.
    Patch ``check_topic_overlap`` to return a controlled score so the
    test does not depend on the embedding service.
    """
    from kora_v2.runtime.orchestration import overlap as overlap_mod

    engine = await _make_engine(tmp_path)
    engine.register_pipeline(_make_pipeline("research"))
    _, instance = await _spawn_research_pipeline(
        engine, goal="ambiguous topic", parent_session_id="sess-A"
    )

    async def step_fn(task: WorkerTask, ctx: StepContext) -> StepResult:
        return StepResult(outcome="running")

    task = await engine.dispatch_task(
        goal="ambiguous topic",
        system_prompt="p",
        step_fn=step_fn,
        preset="bounded_background",
        stage_name="run",
        pipeline_instance_id=instance.id,
    )
    await engine.tick_once()

    async def fake_overlap(user_msg, goal, step, container):
        # 0.55 lands squarely in the 0.45..0.70 ambiguous band.
        return overlap_mod.OverlapResult(
            score=0.55, action="ambiguous", message=None
        )

    import kora_v2.runtime.orchestration.engine as engine_mod

    real = engine_mod.check_topic_overlap
    engine_mod.check_topic_overlap = fake_overlap  # type: ignore[assignment]
    try:
        matches = await engine.list_tasks(
            relevant_to_session="sess-other-session",
            user_message="something kinda related",
        )
    finally:
        engine_mod.check_topic_overlap = real  # type: ignore[assignment]

    assert task.id in {t.id for t in matches}


async def test_list_tasks_case_4_below_threshold_excluded(
    tmp_path: Path,
) -> None:
    """Scores below 0.45 must NOT surface — they are fully unrelated."""
    from kora_v2.runtime.orchestration import overlap as overlap_mod

    engine = await _make_engine(tmp_path)
    engine.register_pipeline(_make_pipeline("research"))
    _, instance = await _spawn_research_pipeline(
        engine, goal="unrelated work", parent_session_id="sess-A"
    )

    async def step_fn(task: WorkerTask, ctx: StepContext) -> StepResult:
        return StepResult(outcome="running")

    task = await engine.dispatch_task(
        goal="unrelated work",
        system_prompt="p",
        step_fn=step_fn,
        preset="bounded_background",
        stage_name="run",
        pipeline_instance_id=instance.id,
    )
    await engine.tick_once()

    async def fake_overlap(user_msg, goal, step, container):
        return overlap_mod.OverlapResult(
            score=0.10, action="continue", message=None
        )

    import kora_v2.runtime.orchestration.engine as engine_mod

    real = engine_mod.check_topic_overlap
    engine_mod.check_topic_overlap = fake_overlap  # type: ignore[assignment]
    try:
        matches = await engine.list_tasks(
            relevant_to_session="sess-other-session",
            user_message="totally unrelated",
        )
    finally:
        engine_mod.check_topic_overlap = real  # type: ignore[assignment]

    assert task.id not in {t.id for t in matches}


# ── Item 44: runtime pipeline persists via routine creation ────────────


async def test_item_44_runtime_pipeline_persists(tmp_path: Path) -> None:
    engine = await _make_engine(tmp_path)
    routine = Pipeline(
        name="morning_routine",
        description="User routine",
        stages=[
            PipelineStage(
                name="run",
                task_preset="long_background",  # type: ignore[arg-type]
                goal_template="Run morning routine",
            )
        ],
        triggers=[time_of_day("morning_routine", at=dtime(7, 0))],
        interruption_policy=InterruptionPolicy.PAUSE_ON_CONVERSATION,
        failure_policy=FailurePolicy.FAIL_PIPELINE,
        intent_duration="long",
    )
    await engine.register_runtime_pipeline(
        routine, created_by_session="sess-42"
    )

    # Persisted to runtime_pipelines table
    async with aiosqlite.connect(str(engine._db_path)) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT name, declaration_json, created_by_session, enabled "
            "FROM runtime_pipelines WHERE name=?",
            ("morning_routine",),
        )
        row = await cursor.fetchone()
    assert row is not None
    assert row["name"] == "morning_routine"
    assert row["enabled"] == 1
    assert row["created_by_session"] == "sess-42"
    decl = json.loads(row["declaration_json"])
    assert decl["name"] == "morning_routine"
    assert decl["stages"][0]["name"] == "run"


async def test_runtime_pipeline_declarations_load_on_engine_start(
    tmp_path: Path,
) -> None:
    engine = await _make_engine(tmp_path)
    routine = Pipeline(
        name="loaded_routine",
        description="Runtime routine restored at boot",
        stages=[
            PipelineStage(
                name="research",
                task_preset="bounded_background",  # type: ignore[arg-type]
                goal_template="Research something",
                tool_scope=["search_web"],
            ),
            PipelineStage(
                name="write_note",
                task_preset="bounded_background",  # type: ignore[arg-type]
                goal_template="Write the note",
                depends_on=["research"],
                tool_scope=["write_file"],
            ),
        ],
        triggers=[],
        interruption_policy=InterruptionPolicy.PAUSE_ON_CONVERSATION,
        failure_policy=FailurePolicy.FAIL_PIPELINE,
        intent_duration="short",
    )
    await engine.register_runtime_pipeline(
        routine, created_by_session="sess-42"
    )

    restarted = OrchestrationEngine(
        engine._db_path,
        schedule_profile=UserScheduleProfile(timezone="UTC"),
        memory_root=tmp_path / "_KoraMemory",
        tick_interval=10.0,
    )
    await restarted.start()
    try:
        loaded = restarted.pipelines.get("loaded_routine")
        assert loaded.intent_duration == "short"
        assert [stage.name for stage in loaded.stages] == [
            "research",
            "write_note",
        ]
        assert loaded.stages[0].tool_scope == ["search_web"]
        assert loaded.stages[1].depends_on == ["research"]
        assert loaded.stages[1].tool_scope == ["write_file"]
    finally:
        await restarted.stop(graceful=True)


# ── Item 46: supervisor dispatch-and-end (templated ack, no LLM) ───────


async def test_item_46_dispatch_and_end_uses_templated_ack(
    tmp_path: Path,
) -> None:
    engine = await _make_engine(tmp_path)

    # Kora dispatches the task (does not drive it to completion here)
    # and sends the templated ack. The caller can then return that
    # ack as the turn response without a second provider round-trip.
    async def step_fn(task: WorkerTask, ctx: StepContext) -> StepResult:
        return StepResult(outcome="running")

    await engine.dispatch_task(
        goal="overnight research",
        system_prompt="do research",
        step_fn=step_fn,
        preset="long_background",
    )
    result = await engine.notify(
        template_id="task_started",
        via=DeliveryChannel.TURN_RESPONSE,
        template_vars={"goal": "overnight research"},
    )
    assert result.delivered
    assert result.tier == "templated"
    assert "overnight research" in result.text
    # Zero provider requests for the ack.
    snap = await engine.limiter_snapshot()
    assert snap["total"] == 0
