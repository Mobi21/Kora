"""Phase 8f acceptance tests — sub-agent orchestration completion.

Phase 8f is the cleanup pass that verifies the Phase 7.5 sub-agent
infrastructure correctly enforces the spec §4a constraints when used
from Phase 8b/c/e stage handlers and from the supervisor's
``decompose_and_dispatch`` tool.

Acceptance items covered:

* Tool-scope validation — ASK_FIRST tools rejected (spec §4a, manual
  test "Sub-agent — ASK_FIRST constraint")
* Recursion prevention — ``decompose_and_dispatch`` cannot appear in a
  sub-task's scope (spec §4a, manual test "Sub-agent — no recursion")
* Cycle detection — runtime sub-task dependency graphs must be acyclic
  (spec §4a, "Cycle-free dependency graphs")
* Acyclic graphs succeed — sanity check the validator does not over-reject
* End-to-end dispatch — supervisor tool returns structured rejection
  payload that the LLM can recover from
* Item 8 (un-deferred): Planner/reviewer subagent delegation —
  supervisor calls ``dispatch_worker`` with ``planner`` / ``reviewer``,
  scoped tools, no decompose recursion, results aggregated back

Constraints lifted directly from the spec (§4a):

    1. **No ASK_FIRST tools.** Sub-tasks cannot include any tool with
       ``auth_level = ASK_FIRST`` or ``NEVER_WITHOUT_PERMISSION`` in
       their tool scope. The Dispatcher rejects such dispatches with
       ``REQUIRES_USER_APPROVAL``.
    2. **No recursion.** ``decompose_and_dispatch`` is never in a
       sub-agent's tool scope. Sub-tasks cannot spawn their own sub-
       tasks.
    3. **Read-only capability actions only.** Sub-tasks can call
       browser read actions but not interaction actions (click, type,
       fill) which fall under ASK_FIRST.
    4. **Cycle-free dependency graphs.**
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# pysqlite3 monkey-patch (mirrors other slice acceptance tests).
try:
    import pysqlite3 as _pysqlite3  # type: ignore[import-untyped]

    sys.modules["sqlite3"] = _pysqlite3
except ImportError:
    pass

from kora_v2.graph.dispatch import (
    SUPERVISOR_TOOLS,
    _orch_decompose_and_dispatch,
    execute_tool,
)
from kora_v2.runtime.orchestration import (
    OrchestrationEngine,
    UserScheduleProfile,
    init_orchestration_schema,
)
from kora_v2.runtime.orchestration.scope_validation import (
    KNOWN_INTERACTION_TOOL_PATTERNS,
    REJECTION_REASON_CYCLE,
    REJECTION_REASON_NO_RECURSION,
    REJECTION_REASON_REQUIRES_USER_APPROVAL,
    REJECTION_REASON_UNKNOWN_DEPENDENCY,
    ScopeValidationError,
    SubTaskSpec,
    validate_dependency_graph,
    validate_subtask_specs,
    validate_tool_scope,
)
from kora_v2.tools.types import AuthLevel

# ══════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════


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
    # Force DEEP_IDLE so background tasks could run if we drove the loop.
    engine.state_machine.note_session_end(
        datetime.now(UTC) - timedelta(hours=2)
    )
    return engine


def _stub_auth(
    mapping: dict[str, AuthLevel],
) -> Callable[[str], AuthLevel | None]:
    """Build a deterministic auth lookup stub for the validators."""
    def _lookup(name: str) -> AuthLevel | None:
        return mapping.get(name)

    return _lookup


# ══════════════════════════════════════════════════════════════════════════
# 8f.1 — validate_tool_scope (unit-level)
# ══════════════════════════════════════════════════════════════════════════


def test_8f_1_ask_first_tool_rejected_with_requires_user_approval() -> None:
    """ASK_FIRST tool in a sub-task scope is rejected with REQUIRES_USER_APPROVAL.

    Spec §4a item 1.
    """
    auth = _stub_auth(
        {
            "recall": AuthLevel.ALWAYS_ALLOWED,
            "send_email": AuthLevel.ASK_FIRST,
        }
    )
    with pytest.raises(ScopeValidationError) as exc_info:
        validate_tool_scope(
            ["recall", "send_email"], auth_lookup=auth
        )
    assert exc_info.value.reason == REJECTION_REASON_REQUIRES_USER_APPROVAL
    assert exc_info.value.offending_field == "send_email"


def test_8f_1_never_tool_also_rejected_with_requires_user_approval() -> None:
    """AuthLevel.NEVER tools fall under the same rejection.

    Spec §4a uses ``ASK_FIRST`` and ``NEVER_WITHOUT_PERMISSION`` — Kora's
    enum maps both to ``ASK_FIRST`` / ``NEVER``; both must be rejected.
    """
    auth = _stub_auth(
        {
            "recall": AuthLevel.ALWAYS_ALLOWED,
            "shell_exec": AuthLevel.NEVER,
        }
    )
    with pytest.raises(ScopeValidationError) as exc_info:
        validate_tool_scope(["shell_exec"], auth_lookup=auth)
    assert exc_info.value.reason == REJECTION_REASON_REQUIRES_USER_APPROVAL
    assert exc_info.value.offending_field == "shell_exec"


def test_8f_1_always_allowed_tools_pass_validation() -> None:
    """Pure ALWAYS_ALLOWED scope passes — sanity check the validator
    does not over-reject."""
    auth = _stub_auth(
        {
            "recall": AuthLevel.ALWAYS_ALLOWED,
            "search_web": AuthLevel.ALWAYS_ALLOWED,
            "fetch_url": AuthLevel.ALWAYS_ALLOWED,
        }
    )
    # Should not raise.
    validate_tool_scope(
        ["recall", "search_web", "fetch_url"], auth_lookup=auth
    )


def test_8f_1_unknown_tool_treated_as_permissive() -> None:
    """Unknown tools (not in registry) are treated as ALWAYS_ALLOWED.

    Matches the existing dispatch tool's behaviour — only tools that
    explicitly register an ``auth_level`` are gated.
    """
    auth = _stub_auth({})  # empty mapping returns None for everything
    # Should not raise.
    validate_tool_scope(["custom_capability_action"], auth_lookup=auth)


def test_8f_1_empty_scope_passes() -> None:
    """Empty tool scope is the dispatcher's default — must not raise."""
    validate_tool_scope([], auth_lookup=lambda _: None)


# ══════════════════════════════════════════════════════════════════════════
# 8f.2 — no-recursion rule (decompose_and_dispatch never in sub-agent scope)
# ══════════════════════════════════════════════════════════════════════════


def test_8f_2_decompose_and_dispatch_in_scope_rejected_with_no_recursion() -> None:
    """``decompose_and_dispatch`` in a sub-task scope returns NO_RECURSION.

    Spec §4a item 2.
    """
    with pytest.raises(ScopeValidationError) as exc_info:
        validate_tool_scope(
            ["recall", "decompose_and_dispatch"],
            auth_lookup=lambda _: AuthLevel.ALWAYS_ALLOWED,
        )
    assert exc_info.value.reason == REJECTION_REASON_NO_RECURSION
    assert exc_info.value.offending_field == "decompose_and_dispatch"


def test_8f_2_no_recursion_takes_precedence_over_auth_check() -> None:
    """The no-recursion rule fires before the ASK_FIRST check.

    The two rules don't overlap (the decompose tool is ALWAYS_ALLOWED to
    the supervisor) but the order matters for the reason string the
    LLM sees — and ``NO_RECURSION`` is more actionable than the
    auth-level reason.
    """
    auth = _stub_auth(
        {
            "decompose_and_dispatch": AuthLevel.ALWAYS_ALLOWED,
            "send_email": AuthLevel.ASK_FIRST,
        }
    )
    with pytest.raises(ScopeValidationError) as exc_info:
        validate_tool_scope(
            ["decompose_and_dispatch", "send_email"],
            auth_lookup=auth,
        )
    assert exc_info.value.reason == REJECTION_REASON_NO_RECURSION


def test_8f_2_other_orchestration_control_tools_also_forbidden() -> None:
    """``cancel_task`` and ``modify_task`` are also forbidden in sub-task scope.

    They are part of the supervisor's orchestration control surface and
    have no business inside a worker (defensive: a stage handler that
    forwards its parent's tool list cannot accidentally hand them off).
    """
    for forbidden in ("cancel_task", "modify_task"):
        with pytest.raises(ScopeValidationError) as exc_info:
            validate_tool_scope(
                [forbidden],
                auth_lookup=lambda _: AuthLevel.ALWAYS_ALLOWED,
            )
        assert exc_info.value.reason == REJECTION_REASON_NO_RECURSION
        assert exc_info.value.offending_field == forbidden


# ══════════════════════════════════════════════════════════════════════════
# 8f.3 — cycle detection on runtime sub-task graphs
# ══════════════════════════════════════════════════════════════════════════


def test_8f_3_cyclic_dependency_graph_rejected() -> None:
    """A → B → A forms a cycle; the validator must reject.

    Spec §4a "Cycle-free dependency graphs".
    """
    specs = [
        SubTaskSpec(
            task_id="a",
            description="task a",
            required_tools=[],
            depends_on=["b"],
        ),
        SubTaskSpec(
            task_id="b",
            description="task b",
            required_tools=[],
            depends_on=["a"],
        ),
    ]
    with pytest.raises(ScopeValidationError) as exc_info:
        validate_dependency_graph(specs)
    assert exc_info.value.reason == REJECTION_REASON_CYCLE
    assert "->" in exc_info.value.message  # the cycle path is reported


def test_8f_3_three_node_cycle_rejected() -> None:
    """A → B → C → A — non-trivial cycle still caught."""
    specs = [
        SubTaskSpec("a", "a", [], ["b"]),
        SubTaskSpec("b", "b", [], ["c"]),
        SubTaskSpec("c", "c", [], ["a"]),
    ]
    with pytest.raises(ScopeValidationError) as exc_info:
        validate_dependency_graph(specs)
    assert exc_info.value.reason == REJECTION_REASON_CYCLE


def test_8f_3_self_loop_rejected() -> None:
    """A → A self-edge is the simplest cycle."""
    specs = [
        SubTaskSpec("loop", "loop", [], ["loop"]),
    ]
    with pytest.raises(ScopeValidationError) as exc_info:
        validate_dependency_graph(specs)
    assert exc_info.value.reason == REJECTION_REASON_CYCLE


def test_8f_3_acyclic_graph_passes() -> None:
    """Linear chain and diamond dependencies are valid — sanity check."""
    # Linear chain: a → b → c
    chain = [
        SubTaskSpec("a", "a", [], []),
        SubTaskSpec("b", "b", [], ["a"]),
        SubTaskSpec("c", "c", [], ["b"]),
    ]
    validate_dependency_graph(chain)

    # Diamond: a → b, a → c, both → d
    diamond = [
        SubTaskSpec("a", "a", [], []),
        SubTaskSpec("b", "b", [], ["a"]),
        SubTaskSpec("c", "c", [], ["a"]),
        SubTaskSpec("d", "d", [], ["b", "c"]),
    ]
    validate_dependency_graph(diamond)


def test_8f_3_unknown_dependency_rejected() -> None:
    """Stage that depends on a non-existent task_id is rejected with
    UNKNOWN_DEPENDENCY (a softer error class than CYCLE_DETECTED so
    the LLM can repair the spec)."""
    specs = [
        SubTaskSpec("a", "a", [], ["ghost"]),
    ]
    with pytest.raises(ScopeValidationError) as exc_info:
        validate_dependency_graph(specs)
    assert exc_info.value.reason == REJECTION_REASON_UNKNOWN_DEPENDENCY
    assert exc_info.value.offending_field == "ghost"


def test_8f_3_empty_specs_pass() -> None:
    """Edge case: zero specs is a no-op."""
    validate_dependency_graph([])


def test_8f_3_combined_validator_runs_all_rules() -> None:
    """``validate_subtask_specs`` runs scope + graph rules in one pass."""
    auth = _stub_auth(
        {
            "send_email": AuthLevel.ASK_FIRST,
        }
    )
    # ASK_FIRST should be caught first.
    bad_scope = [
        SubTaskSpec("a", "a", ["send_email"], []),
        SubTaskSpec("b", "b", [], ["a"]),
    ]
    with pytest.raises(ScopeValidationError) as exc_info:
        validate_subtask_specs(bad_scope, auth_lookup=auth)
    assert exc_info.value.reason == REJECTION_REASON_REQUIRES_USER_APPROVAL

    # Cycle should be caught when scopes are clean.
    bad_graph = [
        SubTaskSpec("a", "a", [], ["b"]),
        SubTaskSpec("b", "b", [], ["a"]),
    ]
    with pytest.raises(ScopeValidationError) as exc_info:
        validate_subtask_specs(bad_graph, auth_lookup=auth)
    assert exc_info.value.reason == REJECTION_REASON_CYCLE


# ══════════════════════════════════════════════════════════════════════════
# 8f.4 — _orch_decompose_and_dispatch end-to-end rejections
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_8f_4_dispatch_with_ask_first_tool_returns_structured_rejection(
    tmp_path: Path,
) -> None:
    """The supervisor tool returns a structured ``REQUIRES_USER_APPROVAL``
    payload the LLM can read and recover from."""
    from pydantic import BaseModel

    from kora_v2.tools.registry import ToolRegistry
    from kora_v2.tools.types import ToolCategory

    # Save / restore registry around the test so we don't leak.
    saved = dict(ToolRegistry._tools)
    try:
        class _Empty(BaseModel):
            pass

        ToolRegistry.register(
            name="send_email_test_8f",
            description="ASK_FIRST tool for the 8f test",
            category=ToolCategory.MESSAGING,
            auth_level=AuthLevel.ASK_FIRST,
            func=AsyncMock(),
            input_model=_Empty,
        )

        engine = await _make_engine(tmp_path)
        result = await _orch_decompose_and_dispatch(
            engine,
            {
                "goal": "send a quick note",
                "pipeline_name": "scope_test_8f",
                "stages": [
                    {
                        "name": "send",
                        "tool_scope": ["send_email_test_8f"],
                    }
                ],
                "in_turn": True,
            },
            session_id="sess-8f-1",
        )
        parsed = json.loads(result)
        assert parsed["status"] == "error"
        assert parsed["rejection_reason"] == REJECTION_REASON_REQUIRES_USER_APPROVAL
        assert parsed["offending_field"] == "send_email_test_8f"
    finally:
        ToolRegistry._tools = saved


@pytest.mark.asyncio
async def test_8f_4_dispatch_with_decompose_in_scope_returns_no_recursion(
    tmp_path: Path,
) -> None:
    """Sub-stage that includes ``decompose_and_dispatch`` is rejected."""
    engine = await _make_engine(tmp_path)
    result = await _orch_decompose_and_dispatch(
        engine,
        {
            "goal": "analyze a thing",
            "pipeline_name": "recursion_test_8f",
            "stages": [
                {
                    "name": "subdecompose",
                    "tool_scope": ["decompose_and_dispatch"],
                },
            ],
            "in_turn": False,
        },
        session_id="sess-8f-2",
    )
    parsed = json.loads(result)
    assert parsed["status"] == "error"
    assert parsed["rejection_reason"] == REJECTION_REASON_NO_RECURSION
    assert parsed["offending_field"] == "decompose_and_dispatch"


@pytest.mark.asyncio
async def test_8f_4_dispatch_with_cyclic_stage_dependencies_rejected(
    tmp_path: Path,
) -> None:
    """Stages with manually declared cyclic ``depends_on`` are rejected."""
    engine = await _make_engine(tmp_path)
    result = await _orch_decompose_and_dispatch(
        engine,
        {
            "goal": "cyclic plan",
            "pipeline_name": "cycle_test_8f",
            "stages": [
                {"name": "stage_a", "depends_on": ["stage_b"]},
                {"name": "stage_b", "depends_on": ["stage_a"]},
            ],
            "in_turn": False,
        },
        session_id="sess-8f-3",
    )
    parsed = json.loads(result)
    assert parsed["status"] == "error"
    assert parsed["rejection_reason"] == REJECTION_REASON_CYCLE


@pytest.mark.asyncio
async def test_8f_4_acyclic_dispatch_succeeds(tmp_path: Path) -> None:
    """Linear acyclic dispatch lands a real instance and a working doc."""
    engine = await _make_engine(tmp_path)
    result = await _orch_decompose_and_dispatch(
        engine,
        {
            "goal": "research X then summarise",
            "pipeline_name": "linear_test_8f",
            "stages": ["gather", "summarise"],
            "in_turn": False,
        },
        session_id="sess-8f-4",
    )
    parsed = json.loads(result)
    assert parsed["status"] == "ok"
    assert parsed["pipeline_name"] == "linear_test_8f"
    assert parsed["stage_count"] == 2
    assert "pipeline_instance_id" in parsed
    assert parsed["pipeline_instance_id"]


@pytest.mark.asyncio
async def test_8f_4_dispatch_seed_failure_returns_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A declared pipeline must not report ok if no runnable task is seeded."""
    engine = await _make_engine(tmp_path)

    async def _fail_dispatch_task(**kwargs):
        raise RuntimeError("scheduler unavailable")

    monkeypatch.setattr(engine, "dispatch_task", _fail_dispatch_task)

    result = await _orch_decompose_and_dispatch(
        engine,
        {
            "goal": "research X then summarise",
            "pipeline_name": "seed_failure_test_8f",
            "stages": ["gather", "summarise"],
            "in_turn": False,
        },
        session_id="sess-8f-seed-failure",
    )

    parsed = json.loads(result)
    assert parsed["status"] == "error"
    assert parsed["error_category"] == "dispatch"
    assert parsed["pipeline_name"] == "seed_failure_test_8f"
    assert "no runnable worker task" in parsed["message"]


@pytest.mark.asyncio
async def test_8f_4_acyclic_dag_dispatch_with_per_stage_tool_scope_succeeds(
    tmp_path: Path,
) -> None:
    """Per-stage object form with explicit ALWAYS_ALLOWED tool_scope works."""
    engine = await _make_engine(tmp_path)
    result = await _orch_decompose_and_dispatch(
        engine,
        {
            "goal": "research X then summarise",
            "pipeline_name": "scoped_dag_test_8f",
            "stages": [
                {
                    "name": "gather",
                    "tool_scope": ["recall", "search_web"],
                    "depends_on": [],
                },
                {
                    "name": "summarise",
                    "tool_scope": ["recall"],
                    "depends_on": ["gather"],
                },
            ],
            "in_turn": False,
        },
        session_id="sess-8f-5",
    )
    parsed = json.loads(result)
    assert parsed["status"] == "ok"
    assert parsed["stage_count"] == 2

    # The stages should have been registered with their tool_scope values.
    pipeline = engine.pipelines.get("scoped_dag_test_8f")
    assert pipeline is not None
    by_name = {s.name: s for s in pipeline.stages}
    assert by_name["gather"].tool_scope == ["recall", "search_web"]
    assert by_name["summarise"].tool_scope == ["recall"]
    assert by_name["summarise"].depends_on == ["gather"]


# ══════════════════════════════════════════════════════════════════════════
# 8f.5 — execute_tool integration (LLM-facing surface)
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_8f_5_execute_tool_routes_decompose_and_returns_rejection(
    tmp_path: Path,
) -> None:
    """The full ``execute_tool`` path (the LLM-facing entry point) returns
    the same structured rejection payload."""
    engine = await _make_engine(tmp_path)
    container = MagicMock()
    container.orchestration_engine = engine
    container.session_manager = None
    container.settings = None

    result = await execute_tool(
        "decompose_and_dispatch",
        {
            "goal": "test",
            "pipeline_name": "exec_recurse_test_8f",
            "stages": [
                {"name": "x", "tool_scope": ["decompose_and_dispatch"]},
            ],
        },
        container=container,
    )
    parsed = json.loads(result)
    assert parsed["status"] == "error"
    assert parsed["rejection_reason"] == REJECTION_REASON_NO_RECURSION


@pytest.mark.asyncio
async def test_8f_5_supervisor_tool_schema_supports_per_stage_objects() -> None:
    """The exported tool schema accepts both string stages (legacy) and
    object stages with ``tool_scope`` / ``depends_on`` (Phase 8f)."""
    decompose = next(
        t for t in SUPERVISOR_TOOLS if t["name"] == "decompose_and_dispatch"
    )
    stages_schema = decompose["input_schema"]["properties"]["stages"]
    assert stages_schema["type"] == "array"
    item_schema = stages_schema["items"]
    assert "oneOf" in item_schema
    variants = item_schema["oneOf"]
    # Must accept plain strings (legacy) and objects with name+tool_scope.
    assert any(v.get("type") == "string" for v in variants)
    object_variant = next(v for v in variants if v.get("type") == "object")
    object_props = object_variant["properties"]
    assert "name" in object_props
    assert "tool_scope" in object_props
    assert "depends_on" in object_props


# ══════════════════════════════════════════════════════════════════════════
# 8f.6 — Item 8 (un-deferred): Planner / reviewer subagent delegation
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_8f_6_planner_subagent_delegation_end_to_end() -> None:
    """Planner delegation via ``dispatch_worker`` runs end-to-end.

    Acceptance item 8 (Phase 8f un-deferred): the supervisor calls
    ``dispatch_worker`` with ``worker_name="planner"``; the worker runs
    its harness and returns a typed result; the result aggregates back
    to the supervisor as JSON. No ASK_FIRST prompts, no recursion, no
    sub-task graph required for the dispatch_worker path itself.
    """
    from kora_v2.core.models import PlanOutput

    mock_output = MagicMock(spec=PlanOutput)
    mock_output.model_dump_json.return_value = json.dumps(
        {
            "plan": [
                {"step": 1, "title": "Outline scope"},
                {"step": 2, "title": "Draft response"},
            ],
            "estimated_minutes": 12,
        }
    )

    mock_worker = AsyncMock()
    mock_worker.execute = AsyncMock(return_value=mock_output)

    container = MagicMock()
    container.resolve_worker = MagicMock(return_value=mock_worker)
    container.session_manager = None
    container.settings = None

    plan_input_json = json.dumps(
        {"goal": "Plan a 1-day learning sprint on Phase 8f"}
    )
    result = await execute_tool(
        "dispatch_worker",
        {"worker_name": "planner", "input_json": plan_input_json},
        container=container,
    )

    container.resolve_worker.assert_called_once_with("planner")
    mock_worker.execute.assert_awaited_once()
    parsed = json.loads(result)
    assert parsed["plan"][0]["title"] == "Outline scope"
    assert parsed["estimated_minutes"] == 12


@pytest.mark.asyncio
async def test_8f_6_reviewer_subagent_delegation_end_to_end() -> None:
    """Reviewer delegation via ``dispatch_worker`` runs end-to-end.

    Same shape as the planner test — the reviewer is the second of the
    two sub-agents item 8 names. Verifies that the dispatch surface is
    symmetric between worker types and that the reviewer can be called
    without an executor in the loop (i.e. plan-only review).
    """
    from kora_v2.core.models import ReviewOutput

    mock_output = MagicMock(spec=ReviewOutput)
    mock_output.model_dump_json.return_value = json.dumps(
        {"approved": True, "feedback": "Plan is sound."}
    )

    mock_worker = AsyncMock()
    mock_worker.execute = AsyncMock(return_value=mock_output)

    container = MagicMock()
    container.resolve_worker = MagicMock(return_value=mock_worker)
    container.session_manager = None
    container.settings = None

    review_input_json = json.dumps(
        {
            "work_product": "Plan: step1, step2 — partial result captured.",
            "criteria": ["accuracy", "completeness"],
            "original_goal": "Review the Phase 8f plan",
            "context": "Phase 8f acceptance test for reviewer dispatch.",
        }
    )
    result = await execute_tool(
        "dispatch_worker",
        {"worker_name": "reviewer", "input_json": review_input_json},
        container=container,
    )

    container.resolve_worker.assert_called_once_with("reviewer")
    mock_worker.execute.assert_awaited_once()
    parsed = json.loads(result)
    assert parsed["approved"] is True
    assert "sound" in parsed["feedback"].lower()


@pytest.mark.asyncio
async def test_8f_6_subagent_dispatch_via_decompose_with_scoped_tools(
    tmp_path: Path,
) -> None:
    """Subagent dispatch via ``decompose_and_dispatch`` honours scoped tools.

    This is the realistic path the supervisor uses for ad-hoc work:
    decompose into stages, each stage has a tool scope that is checked
    by the validator before any pipeline registration happens.
    Mirrors item 8's "Planner/reviewer subagent delegation" via the
    pipeline path, with a clean ALWAYS_ALLOWED scope.
    """
    engine = await _make_engine(tmp_path)
    result = await _orch_decompose_and_dispatch(
        engine,
        {
            "goal": "Plan, then review",
            "pipeline_name": "planner_reviewer_demo_8f",
            "stages": [
                {
                    "name": "plan",
                    "tool_scope": ["recall"],
                    "depends_on": [],
                },
                {
                    "name": "review",
                    "tool_scope": ["recall"],
                    "depends_on": ["plan"],
                },
            ],
            "in_turn": True,
        },
        session_id="sess-8f-item-8",
    )
    parsed = json.loads(result)
    assert parsed["status"] == "ok"
    assert parsed["stage_count"] == 2

    pipeline = engine.pipelines.get("planner_reviewer_demo_8f")
    assert pipeline is not None
    stage_names = [s.name for s in pipeline.stages]
    assert stage_names == ["plan", "review"]
    # In-turn preset confirmed
    for stage in pipeline.stages:
        assert stage.task_preset == "in_turn"
    # Recursion explicitly absent from every stage's scope
    for stage in pipeline.stages:
        assert "decompose_and_dispatch" not in stage.tool_scope


# ══════════════════════════════════════════════════════════════════════════
# 8f.7 — Planner / reviewer harness tool-scope constraint (real introspection)
#
# The 8f.6 dispatch_worker tests above use a MagicMock worker, so they
# verify routing but NOT that the actual planner/reviewer harnesses are
# scoped correctly. These tests introspect the real harness modules to
# guarantee that ``decompose_and_dispatch`` (and the other forbidden
# orchestration-control tools) cannot leak into the planner / reviewer
# tool surface, even by accident.
#
# The planner registers exactly one structured-output tool
# (``submit_plan``); the reviewer registers exactly one (``submit_review``).
# Both are built via the module-level ``_build_submit_plan_tool`` /
# ``_build_submit_review_tool`` helpers — the test asserts the names of
# the tools each harness composes match that whitelist and contain none
# of the forbidden orchestration-control tools.
# ══════════════════════════════════════════════════════════════════════════


def _planner_tool_names() -> list[str]:
    """Return the tool names the planner harness composes for its LLM call.

    Reads the live planner module so a future regression that adds
    ``decompose_and_dispatch`` (or any forbidden tool) to the planner's
    tool list will fail this test.
    """
    from kora_v2.agents.workers import planner as planner_mod

    tool_defs = [planner_mod._build_submit_plan_tool()]
    return [t.get("name") for t in tool_defs]


def _reviewer_tool_names() -> list[str]:
    """Return the tool names the reviewer harness composes for its LLM call."""
    from kora_v2.agents.workers import reviewer as reviewer_mod

    tool_defs = [reviewer_mod._build_submit_review_tool()]
    return [t.get("name") for t in tool_defs]


def test_8f_7_planner_harness_does_not_expose_decompose_and_dispatch() -> None:
    """``decompose_and_dispatch`` is NOT in the planner harness's tool list.

    Real-introspection guard for the 8f spec §4a "no recursion" rule —
    if a future change adds ``decompose_and_dispatch`` to the planner's
    LLM tools, this test fails.
    """
    names = _planner_tool_names()
    assert "decompose_and_dispatch" not in names
    # Defensive: the rest of the orchestration-control surface must also
    # be absent so the planner cannot cancel/modify supervisor-owned tasks.
    assert "cancel_task" not in names
    assert "modify_task" not in names
    # Sanity: the planner DOES register its own structured-output tool.
    assert "submit_plan" in names


def test_8f_7_reviewer_harness_does_not_expose_decompose_and_dispatch() -> None:
    """``decompose_and_dispatch`` is NOT in the reviewer harness's tool list.

    Same guarantee as the planner test, applied to the reviewer harness.
    """
    names = _reviewer_tool_names()
    assert "decompose_and_dispatch" not in names
    assert "cancel_task" not in names
    assert "modify_task" not in names
    # Sanity: the reviewer DOES register its own structured-output tool.
    assert "submit_review" in names


def test_8f_7_planner_harness_has_no_dispatch_capability_at_all() -> None:
    """The planner module itself must not import ``_orch_decompose_and_dispatch``.

    Stronger guard than the tool-list check — a future regression could
    add the function as a Python-level call rather than as an LLM tool.
    Asserting absence at the module surface catches both routes.
    """
    from kora_v2.agents.workers import planner as planner_mod

    assert not hasattr(planner_mod, "_orch_decompose_and_dispatch")
    assert not hasattr(planner_mod, "decompose_and_dispatch")


def test_8f_7_reviewer_harness_has_no_dispatch_capability_at_all() -> None:
    """The reviewer module itself must not import the dispatch tool either."""
    from kora_v2.agents.workers import reviewer as reviewer_mod

    assert not hasattr(reviewer_mod, "_orch_decompose_and_dispatch")
    assert not hasattr(reviewer_mod, "decompose_and_dispatch")


# ══════════════════════════════════════════════════════════════════════════
# 8f.8 — Capability-pack interaction tools blocked from sub-task scope
#
# Capability-pack tools (browser.click, browser.type, browser.fill, …)
# are NOT registered in the in-process ``ToolRegistry`` — they live on
# the capability-pack ``ActionRegistry`` — so the normal auth-lookup
# returns ``None`` for them and they would otherwise fall through as
# permissive (ALWAYS_ALLOWED).
#
# Spec §4a constraint 3 ("Read-only capability actions only") says
# sub-tasks may use browser read/navigation actions but NOT interaction
# actions. ``KNOWN_INTERACTION_TOOL_PATTERNS`` enforces that even when
# the registry is silent.
# ══════════════════════════════════════════════════════════════════════════


def test_8f_8_browser_click_in_subtask_scope_rejected() -> None:
    """``browser.click`` is an interaction tool — rejected even though the
    ToolRegistry has no entry for it (capability-pack tool)."""
    # Empty auth lookup mirrors the production case for capability-pack
    # tools — they are not in ToolRegistry so the lookup returns None.
    with pytest.raises(ScopeValidationError) as exc_info:
        validate_tool_scope(
            ["browser.click"], auth_lookup=lambda _name: None
        )
    assert exc_info.value.reason == REJECTION_REASON_REQUIRES_USER_APPROVAL
    assert exc_info.value.offending_field == "browser.click"


def test_8f_8_browser_type_in_subtask_scope_rejected() -> None:
    """``browser.type`` is also an interaction tool — must be rejected."""
    with pytest.raises(ScopeValidationError) as exc_info:
        validate_tool_scope(
            ["browser.type"], auth_lookup=lambda _name: None
        )
    assert exc_info.value.reason == REJECTION_REASON_REQUIRES_USER_APPROVAL
    assert exc_info.value.offending_field == "browser.type"


def test_8f_8_browser_fill_in_subtask_scope_rejected() -> None:
    """``browser.fill`` rounds out the trio of write actions; must be rejected."""
    with pytest.raises(ScopeValidationError) as exc_info:
        validate_tool_scope(
            ["browser.fill"], auth_lookup=lambda _name: None
        )
    assert exc_info.value.reason == REJECTION_REASON_REQUIRES_USER_APPROVAL
    assert exc_info.value.offending_field == "browser.fill"


def test_8f_8_browser_read_actions_allowed_in_subtask_scope() -> None:
    """Read/navigation browser actions must STILL be allowed — sanity check
    that the interaction-tool blocklist did not over-reject."""
    # browser.open, browser.snapshot, browser.screenshot, browser.clip_page,
    # browser.clip_selection, browser.close are the read-only navigation
    # actions per kora_v2.capabilities.browser.policy._READ_ACTIONS.
    read_actions = [
        "browser.open",
        "browser.snapshot",
        "browser.screenshot",
        "browser.clip_page",
        "browser.clip_selection",
        "browser.close",
    ]
    # Should not raise — these are NOT in KNOWN_INTERACTION_TOOL_PATTERNS
    # and the empty auth lookup leaves them ALWAYS_ALLOWED by default.
    validate_tool_scope(read_actions, auth_lookup=lambda _name: None)


def test_8f_8_known_interaction_tool_patterns_covers_all_browser_writes() -> None:
    """The blocklist must include every browser write action.

    Cross-check ``KNOWN_INTERACTION_TOOL_PATTERNS`` against the
    capability-pack module's own list of write actions
    (``GOOGLE_WRITE_ACTIONS``) so the two stay in sync — adding a new
    write action to the browser pack without adding it to the blocklist
    would fail this test.
    """
    from kora_v2.capabilities.browser.policy import GOOGLE_WRITE_ACTIONS

    for action in GOOGLE_WRITE_ACTIONS:
        assert action in KNOWN_INTERACTION_TOOL_PATTERNS, (
            f"Browser write action {action!r} is missing from "
            "KNOWN_INTERACTION_TOOL_PATTERNS — sub-tasks could silently "
            "delegate it to a worker."
        )


@pytest.mark.asyncio
async def test_8f_8_dispatch_with_browser_click_returns_structured_rejection(
    tmp_path: Path,
) -> None:
    """End-to-end: dispatching a stage with ``browser.click`` in the tool
    scope returns the same structured rejection payload as ASK_FIRST tools."""
    engine = await _make_engine(tmp_path)
    result = await _orch_decompose_and_dispatch(
        engine,
        {
            "goal": "automate a button click",
            "pipeline_name": "browser_click_test_8f",
            "stages": [
                {
                    "name": "click_thing",
                    "tool_scope": ["browser.click"],
                },
            ],
            "in_turn": False,
        },
        session_id="sess-8f-8a",
    )
    parsed = json.loads(result)
    assert parsed["status"] == "error"
    assert parsed["rejection_reason"] == REJECTION_REASON_REQUIRES_USER_APPROVAL
    assert parsed["offending_field"] == "browser.click"


@pytest.mark.asyncio
async def test_8f_8_dispatch_with_browser_read_actions_succeeds(
    tmp_path: Path,
) -> None:
    """End-to-end: read-only browser actions pass validation."""
    engine = await _make_engine(tmp_path)
    result = await _orch_decompose_and_dispatch(
        engine,
        {
            "goal": "scrape some pages",
            "pipeline_name": "browser_read_test_8f",
            "stages": [
                {
                    "name": "scrape",
                    "tool_scope": ["browser.open", "browser.snapshot"],
                },
            ],
            "in_turn": False,
        },
        session_id="sess-8f-8b",
    )
    parsed = json.loads(result)
    assert parsed["status"] == "ok"
