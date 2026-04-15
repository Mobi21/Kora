"""Parity tests for the dead-code autonomous pipeline declarations.

Spec §17.7a: the autonomous execution loop stays live through Slice
7.5b. The pipeline declarations in ``kora_v2.autonomous.pipeline_factory``
exist so Phase 8 has a ready target to cut over to. These tests keep
the dead-code declarations honest by parsing the live dispatcher in
``kora_v2/autonomous/loop.py`` and asserting every dispatched node
name is represented in ``AUTONOMOUS_NODES``.
"""

from __future__ import annotations

import ast
from datetime import time as dtime
from pathlib import Path

from kora_v2.autonomous.pipeline_factory import (
    AUTONOMOUS_NODES,
    build_user_autonomous_task_pipeline,
    build_user_routine_task_pipeline,
    classify_autonomous_task,
    live_graph_node_names,
    pipeline_stage_names,
)
from kora_v2.runtime.orchestration.pipeline import (
    FailurePolicy,
    InterruptionPolicy,
)
from kora_v2.runtime.orchestration.triggers import TriggerKind

# ── AST-based live graph node extraction ─────────────────────────────────


_LOOP_PATH = (
    Path(__file__).resolve().parents[3]
    / "kora_v2"
    / "autonomous"
    / "loop.py"
)


# Dispatch-variable names and sentinels must stay in lockstep with the
# production helper — the test-side walker deliberately re-derives the
# same set so a broken helper cannot silently fix the test.
_DISPATCH_VAR_NAMES: frozenset[str] = frozenset(
    {"node_name", "next_node", "next_action"}
)
_DISPATCH_SENTINELS: frozenset[str] = frozenset({"END", "continue"})


def _extract_live_dispatch_nodes() -> set[str]:
    """Walk ``loop.py`` (entire module) and collect every node name dispatched.

    ``loop.py`` dispatches graph nodes from three methods:

    * ``_run_node`` — the primary ``if node_name == "foo": ...`` chain.
    * ``run()`` — short-circuits ``waiting_on_user`` via
      ``if next_node == "waiting_on_user":`` *before* the ``_run_node`` call.
    * ``_handle_reflect_action`` — dispatches ``decision_request`` (plus
      ``complete`` / ``paused_for_overlap`` / ``replan``) via
      ``if next_action == "...":`` branches.

    The walker therefore scans the entire module for equality
    comparisons of the form ``<dispatch_var> == "<literal>"`` (or the
    mirrored form) where *dispatch_var* is one of
    :data:`_DISPATCH_VAR_NAMES`, excluding non-node sentinels like
    ``"END"`` / ``"continue"``. We parse the file rather than importing
    it so the test does not pay the autonomous module's import cost and
    cannot be silently broken by runtime side effects.
    """
    tree = ast.parse(_LOOP_PATH.read_text(encoding="utf-8"))
    nodes: set[str] = set()

    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        # Look for `<dispatch_var> == "literal"` or `"literal" == <dispatch_var>`
        left = node.left
        comparators = node.comparators
        ops = node.ops
        if len(ops) != 1 or not isinstance(ops[0], ast.Eq):
            continue
        right = comparators[0]
        candidates: list[ast.expr] = [left, right]
        name_seen = False
        literal: str | None = None
        for candidate in candidates:
            if (
                isinstance(candidate, ast.Name)
                and candidate.id in _DISPATCH_VAR_NAMES
            ):
                name_seen = True
            elif isinstance(candidate, ast.Constant) and isinstance(
                candidate.value, str
            ):
                literal = candidate.value
        if not (name_seen and literal):
            continue
        if literal in _DISPATCH_SENTINELS:
            continue
        nodes.add(literal)

    return nodes


def test_live_graph_node_names_matches_autonomous_nodes_exactly() -> None:
    """Bidirectional drift check: the set of dispatch node literals in
    ``loop.py`` and the :data:`AUTONOMOUS_NODES` constant must be
    identical.

    * Adding a new ``if node_name == "fake_new_node":`` branch (or a
      ``next_node`` / ``next_action`` equality) without updating the
      constant fails this test with *source has nodes missing from
      constant*.
    * Removing an entry from :data:`AUTONOMOUS_NODES` while the runtime
      still dispatches it fails with *constant has nodes missing from
      source*.
    """
    dispatched = _extract_live_dispatch_nodes()
    # Sanity: parsing should yield at least the core sequence
    assert dispatched, "failed to extract any node names from loop.py"
    assert dispatched == set(AUTONOMOUS_NODES), (
        "AUTONOMOUS_NODES is out of sync with loop.py dispatch literals. "
        f"missing from constant: {sorted(dispatched - set(AUTONOMOUS_NODES))}, "
        f"missing from source: {sorted(set(AUTONOMOUS_NODES) - dispatched)}"
    )


def test_live_graph_node_names_helper_matches_ast_extraction() -> None:
    """``live_graph_node_names`` is the production source of truth used by
    callers. It must return *exactly* the set of dispatch literals the
    test-side AST walker finds — any drift means the helper is lying
    about what the live loop runs.
    """
    dispatched = _extract_live_dispatch_nodes()
    helper = set(live_graph_node_names())
    assert helper == dispatched, (
        f"live_graph_node_names() drifted from loop.py dispatch literals: "
        f"helper={sorted(helper)} ast={sorted(dispatched)}"
    )


def test_live_graph_node_names_finds_twelve_nodes() -> None:
    """The live loop dispatches all 12 nodes in :data:`AUTONOMOUS_NODES`.

    This pins the current node count so a regression that silently
    drops a branch is caught even if the same regression drops it from
    :data:`AUTONOMOUS_NODES` too.
    """
    assert len(live_graph_node_names()) == 12
    assert set(live_graph_node_names()) == set(AUTONOMOUS_NODES)


def test_autonomous_nodes_has_twelve_entries() -> None:
    assert len(AUTONOMOUS_NODES) == 12
    assert len(set(AUTONOMOUS_NODES)) == 12  # all unique


def test_user_autonomous_task_stage_names_match_graph() -> None:
    pipeline = build_user_autonomous_task_pipeline()
    assert pipeline_stage_names(pipeline) == AUTONOMOUS_NODES


def test_user_routine_task_stage_names_match_graph() -> None:
    pipeline = build_user_routine_task_pipeline()
    assert pipeline_stage_names(pipeline) == AUTONOMOUS_NODES


def test_user_autonomous_task_has_user_action_trigger() -> None:
    pipeline = build_user_autonomous_task_pipeline()
    assert pipeline.name == "user_autonomous_task"
    assert pipeline.intent_duration == "long"
    assert len(pipeline.triggers) == 1
    assert pipeline.triggers[0].kind is TriggerKind.USER_ACTION


def test_user_routine_task_has_time_of_day_trigger() -> None:
    pipeline = build_user_routine_task_pipeline(schedule_time=dtime(7, 30))
    assert pipeline.name == "user_routine_task"
    assert pipeline.intent_duration == "long"
    assert len(pipeline.triggers) == 1
    assert pipeline.triggers[0].kind is TriggerKind.TIME_OF_DAY


def test_classify_routine_returns_user_routine_task() -> None:
    pipeline = classify_autonomous_task(is_routine=True)
    assert pipeline.name == "user_routine_task"


def test_classify_non_routine_returns_user_autonomous_task() -> None:
    pipeline = classify_autonomous_task(is_routine=False)
    assert pipeline.name == "user_autonomous_task"


def test_pipelines_share_interruption_and_failure_policy() -> None:
    auto = build_user_autonomous_task_pipeline()
    routine = build_user_routine_task_pipeline()
    assert auto.interruption_policy is InterruptionPolicy.PAUSE_ON_CONVERSATION
    assert routine.interruption_policy is InterruptionPolicy.PAUSE_ON_CONVERSATION
    assert auto.failure_policy is FailurePolicy.FAIL_PIPELINE
    assert routine.failure_policy is FailurePolicy.FAIL_PIPELINE


def test_autonomous_stage_dependencies_form_expected_edges() -> None:
    pipeline = build_user_autonomous_task_pipeline()
    stages_by_name = {s.name: s for s in pipeline.stages}
    # Keep a tiny edge-set check so renaming a stage fails loudly.
    assert stages_by_name["persist_plan"].depends_on == ["plan"]
    assert stages_by_name["execute_step"].depends_on == ["persist_plan"]
    assert stages_by_name["reflect"].depends_on == ["checkpoint"]
    # Plan has no predecessor
    assert stages_by_name["plan"].depends_on == []
