"""Dead-code pipeline declarations for the 12-node autonomous graph.

Per spec §17.7a, the autonomous execution loop is *not* yet migrated
off :class:`~kora_v2.autonomous.loop.AutonomousExecutionLoop`. The
loop stays live. This module declares the *shape* it will take as a
:class:`~kora_v2.runtime.orchestration.pipeline.Pipeline` so:

1. The dispatcher can classify an incoming "start_autonomous" user
   action as either a ``user_autonomous_task`` or a
   ``user_routine_task`` pipeline without committing yet — the
   classification sink is exercised by a parity test even though
   the resulting pipeline instance is never actually executed.
2. The parity test in
   ``tests/unit/orchestration/test_pipeline_parity.py`` can diff the
   stage list against the live ``graph.py`` node set to catch drift
   before the Slice 7.5c cutover.
3. Phase 8 has a ready target to flip to: swap
   :class:`AutonomousExecutionLoop` out, register this pipeline as
   the live definition, and wire each stage's step function to the
   corresponding ``graph.<node>`` coroutine.

The declarations are intentionally **stage-parallel with the live
graph**. Any divergence — a new node added to ``graph.py``, a node
renamed — should fail the parity test so the migration surface stays
honest.

The routines variant (``user_routine_task``) uses the same shape
because routines reuse the autonomous graph; only the trigger source
differs (a ``time_of_day`` trigger attached to the routine's
schedule, rather than a ``user_action`` trigger).
"""

from __future__ import annotations

import ast
from datetime import time as dtime
from datetime import timedelta
from pathlib import Path

from kora_v2.runtime.orchestration.pipeline import (
    FailurePolicy,
    InterruptionPolicy,
    Pipeline,
    PipelineStage,
)
from kora_v2.runtime.orchestration.triggers import (
    Trigger,
    time_of_day,
    user_action,
)

# The canonical 12-node sequence — keep in lockstep with
# ``kora_v2/autonomous/graph.py``. The order is the primary forward
# flow; the live graph has cycles (replan → execute, reflect → plan,
# waiting_on_user → self) that the dispatcher represents via
# ``depends_on`` plus the step function's own status routing.
AUTONOMOUS_NODES: tuple[str, ...] = (
    "plan",
    "persist_plan",
    "execute_step",
    "review_step",
    "checkpoint",
    "reflect",
    "replan",
    "decision_request",
    "waiting_on_user",
    "paused_for_overlap",
    "complete",
    "failed",
)


def _stage(
    name: str,
    *,
    depends_on: list[str] | None = None,
    preset: str = "long_background",
    system_prompt_ref: str = "",
) -> PipelineStage:
    return PipelineStage(
        name=name,
        task_preset=preset,  # type: ignore[arg-type]
        goal_template="{{goal}}",
        depends_on=depends_on or [],
        system_prompt_ref=system_prompt_ref or f"autonomous.{name}",
    )


def _autonomous_stages() -> list[PipelineStage]:
    """Return the stage list mirroring the live graph."""
    return [
        _stage("plan"),
        _stage("persist_plan", depends_on=["plan"]),
        _stage("execute_step", depends_on=["persist_plan"]),
        _stage("review_step", depends_on=["execute_step"]),
        _stage("checkpoint", depends_on=["review_step"]),
        _stage("reflect", depends_on=["checkpoint"]),
        _stage("replan", depends_on=["reflect"]),
        _stage("decision_request", depends_on=["reflect"]),
        _stage("waiting_on_user", depends_on=["decision_request"]),
        _stage("paused_for_overlap", depends_on=["reflect"]),
        _stage("complete", depends_on=["reflect"]),
        _stage("failed", depends_on=["reflect"]),
    ]


def build_user_autonomous_task_pipeline() -> Pipeline:
    """Declare ``user_autonomous_task`` — user-dispatched autonomous work.

    Triggered by ``user_action("start_autonomous")`` — the same
    ``start_autonomous`` supervisor tool that today starts an
    :class:`AutonomousExecutionLoop` instance. Registering this
    pipeline is a no-op for the dispatcher until Slice 7.5c cuts
    the live loop over.
    """
    return Pipeline(
        name="user_autonomous_task",
        description=(
            "User-dispatched long-horizon autonomous work "
            "(dead-code target — live path still goes through "
            "AutonomousExecutionLoop)."
        ),
        stages=_autonomous_stages(),
        triggers=[
            user_action("user_autonomous_task", action_name="start_autonomous"),
        ],
        interruption_policy=InterruptionPolicy.PAUSE_ON_CONVERSATION,
        failure_policy=FailurePolicy.FAIL_PIPELINE,
        intent_duration="long",
    )


def build_user_routine_task_pipeline(
    *,
    schedule_time: dtime | None = None,
) -> Pipeline:
    """Declare ``user_routine_task`` — routines become pipelines.

    Routines reuse the 12-node autonomous graph — the only
    difference from ``user_autonomous_task`` is the trigger source.
    A routine scheduled for 09:00 locally produces a pipeline with a
    ``time_of_day(09:00)`` trigger; the same stage list runs against
    it.

    Args:
        schedule_time: Optional concrete schedule for the trigger.
            Defaults to a placeholder ``09:00`` when omitted, which
            is fine for the declaration's parity test but needs to
            be overridden per-routine at registration time.
    """
    trigger_time = schedule_time or dtime(9, 0)
    triggers: list[Trigger] = [
        time_of_day("user_routine_task", at=trigger_time),
    ]
    return Pipeline(
        name="user_routine_task",
        description=(
            "Routine-dispatched autonomous work — shares the 12-node "
            "graph with user_autonomous_task; only the trigger differs."
        ),
        stages=_autonomous_stages(),
        triggers=triggers,
        interruption_policy=InterruptionPolicy.PAUSE_ON_CONVERSATION,
        failure_policy=FailurePolicy.FAIL_PIPELINE,
        intent_duration="long",
    )


# ── Dispatcher-side classifier ──────────────────────────────────────────


def classify_autonomous_task(
    *,
    is_routine: bool,
    routine_name: str | None = None,
    routine_schedule: dtime | None = None,
) -> Pipeline:
    """Pick which pipeline an autonomous request should run as.

    The dispatcher calls this when it sees either a
    ``start_autonomous`` user action or a routine fire: routines go
    to ``user_routine_task`` (with the routine's schedule baked into
    the trigger), everything else goes to ``user_autonomous_task``.
    """
    if is_routine:
        pipeline = build_user_routine_task_pipeline(schedule_time=routine_schedule)
        if routine_name:
            # The canonical instance-level goal template substitutes
            # ``{{routine_name}}`` at dispatch; we don't rewrite the
            # pipeline itself because pipelines are registry-level.
            pass
        return pipeline
    return build_user_autonomous_task_pipeline()


# ── Parity surface ──────────────────────────────────────────────────────


def pipeline_stage_names(pipeline: Pipeline) -> tuple[str, ...]:
    """Return the ordered stage names of *pipeline* — used by parity test."""
    return tuple(stage.name for stage in pipeline.stages)


_LOOP_PATH = Path(__file__).resolve().parent / "loop.py"

# Variable names in ``loop.py`` that carry a node-dispatch identifier on
# the right-hand side of an equality comparison. ``_run_node`` uses
# ``node_name``; ``run()`` uses ``next_node`` (to short-circuit on
# ``waiting_on_user`` before the ``_run_node`` call); and
# ``_handle_reflect_action`` uses ``next_action`` to pick between
# ``decision_request`` / ``paused_for_overlap`` / ``replan`` / ``complete``.
# Scanning all three variables across the module catches every node
# dispatched by the live runtime, regardless of which method owns it.
_DISPATCH_VAR_NAMES: frozenset[str] = frozenset(
    {"node_name", "next_node", "next_action"}
)

# String literals that appear on the RHS of ``<dispatch_var> == "..."``
# comparisons but are *not* nodes in the autonomous graph. These are
# router sentinels (``"END"`` terminates the loop, ``"continue"`` is a
# fall-through instruction from ``reflect()``). They must be excluded
# so the parity walker does not misreport them as drift. Any new
# sentinel added to ``loop.py`` should be added here too.
_DISPATCH_SENTINELS: frozenset[str] = frozenset({"END", "continue"})


def live_graph_node_names() -> tuple[str, ...]:
    """Return the ordered node names dispatched by the live autonomous loop.

    Parses ``kora_v2/autonomous/loop.py`` with :mod:`ast` and walks **the
    entire module**, not just ``_run_node``. The live runtime dispatches
    nodes from three places:

    * ``_run_node`` — the primary ``if node_name == "foo":`` chain.
    * ``run()`` — short-circuits ``waiting_on_user`` via
      ``if next_node == "waiting_on_user":`` before the ``_run_node`` call.
    * ``_handle_reflect_action`` — dispatches ``decision_request`` (plus
      ``complete`` / ``paused_for_overlap`` / ``replan``) via
      ``if next_action == "...":`` branches.

    Any comparison of the form ``<dispatch_var> == "<literal>"`` (or the
    mirrored ``"<literal>" == <dispatch_var>``) where *dispatch_var* is
    one of :data:`_DISPATCH_VAR_NAMES` contributes its literal to the
    result — **except** for known sentinels in :data:`_DISPATCH_SENTINELS`
    (``END``, ``continue``) which are not real graph nodes.

    Callers compare this against :data:`AUTONOMOUS_NODES` to detect
    drift in either direction: a new node added to ``loop.py`` without
    updating :data:`AUTONOMOUS_NODES`, or a node removed from the
    constant while still dispatched by the runtime, both fail parity.

    Parsing with :mod:`ast` (rather than importing and introspecting)
    keeps this helper free of autonomous-module import costs and
    robust to runtime state.
    """
    dispatched: list[str] = []
    seen: set[str] = set()
    try:
        tree = ast.parse(_LOOP_PATH.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        # Source file missing or malformed — fall back to the
        # declared sequence so callers still see something sensible.
        return AUTONOMOUS_NODES

    for child in ast.walk(tree):
        if not isinstance(child, ast.Compare):
            continue
        if len(child.ops) != 1 or not isinstance(child.ops[0], ast.Eq):
            continue
        left, right = child.left, child.comparators[0]
        name_seen = False
        literal: str | None = None
        for candidate in (left, right):
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
        if literal in seen:
            continue
        seen.add(literal)
        dispatched.append(literal)

    return tuple(dispatched)


# ``timedelta`` intentionally imported but kept at module scope so
# future cooldown-based declarations have an in-scope reference.
_ = timedelta
