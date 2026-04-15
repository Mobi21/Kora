"""Unit tests for the 20 core pipelines (spec §4.3 / Slice 7.5b)."""

from __future__ import annotations

from pathlib import Path

from kora_v2.runtime.orchestration import (
    OrchestrationEngine,
    UserScheduleProfile,
    init_orchestration_schema,
)
from kora_v2.runtime.orchestration.core_pipelines import (
    _session_bridge_pruning_step,
    _skill_refinement_step,
    _stub_step,
    build_core_pipelines,
    core_step_fns,
    register_core_pipelines,
)

EXPECTED_PIPELINE_NAMES = {
    "post_session_memory",
    "post_memory_vault",
    "weekly_adhd_profile",
    "user_autonomous_task",
    "in_turn_subagent",
    "wake_up_preparation",
    "continuity_check",
    "proactive_pattern_scan",
    "anticipatory_prep",
    "proactive_research",
    "article_digest",
    "follow_through_draft",
    "contextual_engagement",
    "commitment_tracking",
    "stuck_detection",
    "weekly_triage",
    "draft_on_observation",
    "connection_making",
    "session_bridge_pruning",
    "skill_refinement",
}


def test_build_core_pipelines_returns_twenty() -> None:
    pipelines = build_core_pipelines()
    assert len(pipelines) == 20
    names = {p.name for p in pipelines}
    assert names == EXPECTED_PIPELINE_NAMES


def test_real_step_functions_wired_for_bgworker_replacements() -> None:
    build_core_pipelines()
    fns = core_step_fns()
    assert fns["session_bridge_pruning"] is _session_bridge_pruning_step
    assert fns["skill_refinement"] is _skill_refinement_step


def test_user_autonomous_task_wired_to_pipeline_factory_step_fn() -> None:
    """Slice 7.5c: user_autonomous_task uses the real autonomous step fn
    from :mod:`kora_v2.autonomous.pipeline_factory`, not the stub.
    """
    from kora_v2.autonomous.pipeline_factory import get_autonomous_step_fn

    build_core_pipelines()
    fns = core_step_fns()
    assert fns["user_autonomous_task"] is get_autonomous_step_fn()


def test_other_pipelines_use_stub_step() -> None:
    build_core_pipelines()
    fns = core_step_fns()
    stub_names = EXPECTED_PIPELINE_NAMES - {
        "session_bridge_pruning",
        "skill_refinement",
        "user_autonomous_task",
    }
    for name in stub_names:
        assert fns[name] is _stub_step


def test_each_pipeline_has_at_least_one_trigger() -> None:
    pipelines = build_core_pipelines()
    for p in pipelines:
        assert p.triggers, f"{p.name} has no triggers"


def test_each_pipeline_has_single_stage() -> None:
    pipelines = build_core_pipelines()
    for p in pipelines:
        assert len(p.stages) == 1
        assert p.stages[0].name == "run"


async def test_register_core_pipelines_populates_engine(tmp_path: Path) -> None:
    db_path = tmp_path / "operational.db"
    await init_orchestration_schema(db_path)
    engine = OrchestrationEngine(
        db_path,
        schedule_profile=UserScheduleProfile(timezone="UTC"),
        tick_interval=0.01,
    )
    register_core_pipelines(engine)
    names = {p.name for p in engine.pipelines.all()}
    for expected in EXPECTED_PIPELINE_NAMES:
        assert expected in names


def test_post_memory_vault_uses_sequence_complete_trigger() -> None:
    """Spec §4.3: post_memory_vault fires on sequence_complete(post_session_memory)
    OR a deep-idle interval. The any_of must contain a SEQUENCE_COMPLETE
    trigger bound to the post_session_memory sequence — not a generic
    PIPELINE_COMPLETE event.
    """
    from kora_v2.runtime.orchestration.triggers import TriggerKind

    pipelines = build_core_pipelines()
    by_name = {p.name: p for p in pipelines}
    vault = by_name["post_memory_vault"]
    assert len(vault.triggers) == 1
    composite = vault.triggers[0]
    # any_of trigger exposes its children via `children`.
    children = getattr(composite, "children", None) or []
    sequence_kinds = [
        c
        for c in children
        if getattr(c, "kind", None) is TriggerKind.SEQUENCE_COMPLETE
    ]
    assert len(sequence_kinds) == 1, (
        f"post_memory_vault must depend on a SEQUENCE_COMPLETE trigger; "
        f"saw children kinds={[getattr(c, 'kind', None) for c in children]}"
    )
    seq_trigger = sequence_kinds[0]
    assert seq_trigger.sequence_name == "post_session_memory"
