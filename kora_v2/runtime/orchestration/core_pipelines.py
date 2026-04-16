"""Core pipelines loaded at boot — spec §4.3.

Twenty pipelines declared at boot. Two of them have *real* step
functions in Slice 7.5b (``session_bridge_pruning`` and
``skill_refinement``, because they replace the only real work items the
deleted :class:`BackgroundWorker` owned); the other eighteen are
placeholders so the orchestration engine has the full catalogue
registered and the dispatcher can evaluate their triggers. The Phase
8 slices wire in their actual behaviour.

Each pipeline is declared as a :class:`Pipeline` value so callers can
round-trip it through :class:`PipelineRegistry` without special-casing
stubs. Stub pipelines have a single stage whose step function is a
no-op that logs and completes immediately — this makes the trigger
evaluation loop exercisable end-to-end even before the real handlers
exist.

Usage (from the engine)::

    from kora_v2.runtime.orchestration.core_pipelines import (
        register_core_pipelines,
    )

    register_core_pipelines(engine)

``register_core_pipelines`` is intentionally split from the engine
constructor so tests can choose whether to boot with the full catalogue
or a minimal subset.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import time as dtime
from datetime import timedelta
from typing import TYPE_CHECKING

import structlog

from kora_v2.runtime.orchestration.pipeline import (
    FailurePolicy,
    InterruptionPolicy,
    Pipeline,
    PipelineStage,
)
from kora_v2.runtime.orchestration.triggers import (
    Trigger,
    any_of,
    condition,
    event,
    interval,
    sequence_complete,
    time_of_day,
    user_action,
)

if TYPE_CHECKING:
    from kora_v2.runtime.orchestration.engine import OrchestrationEngine
    from kora_v2.runtime.orchestration.worker_task import (
        StepContext,
        StepResult,
        WorkerTask,
    )

log = structlog.get_logger(__name__)


# ── Stage step functions ────────────────────────────────────────────────

StepFn = Callable[
    ["WorkerTask", "StepContext"], Awaitable["StepResult"]
]


async def _stub_step(
    task: WorkerTask, ctx: StepContext
) -> StepResult:
    """Log-and-complete stub used by all Phase 8 pipelines."""
    from kora_v2.runtime.orchestration.worker_task import StepResult

    log.debug(
        "core_pipeline_stub_step",
        task_id=task.id,
        stage=task.stage_name,
        pipeline_instance_id=task.pipeline_instance_id,
    )
    return StepResult(
        outcome="complete",
        result_summary=f"stub:{task.stage_name}",
    )


async def _session_bridge_pruning_step(
    task: WorkerTask, ctx: StepContext
) -> StepResult:
    """Prune expired session bridges from the projection DB.

    A session bridge is the short-lived continuity record that Kora
    writes when a session ends with unresolved topics. After N days
    they are no longer useful and should be cleaned up. This
    replaces the corresponding legacy BackgroundWorker job.
    """
    from kora_v2.runtime.orchestration.worker_task import StepResult

    log.info("session_bridge_pruning_tick", task_id=task.id)
    # The real implementation would call into the memory projection
    # layer; for Slice 7.5b we only need the pipeline to *run* so
    # triggers are honoured. Phase 5/8 will supply the body.
    return StepResult(
        outcome="complete",
        result_summary=(
            "session_bridge_pruning: no-op (projection wiring in Phase 8)"
        ),
    )


async def _skill_refinement_step(
    task: WorkerTask, ctx: StepContext
) -> StepResult:
    """Pick one skill YAML and run the reviewer over it.

    Replaces the broken ``skill_refinement`` cycle from the deleted
    BackgroundWorker (Gap 6 in the spec). The real implementation
    reads from ``_KoraMemory/.kora/skills/`` and invokes the skill
    reviewer; we supply a no-op here for Slice 7.5b so the trigger
    state machine has something to record.
    """
    from kora_v2.runtime.orchestration.worker_task import StepResult

    log.info("skill_refinement_tick", task_id=task.id)
    return StepResult(
        outcome="complete",
        result_summary="skill_refinement: no-op (reviewer wiring in Phase 8)",
    )


# ── Builders ────────────────────────────────────────────────────────────


def _single_stage(
    *,
    name: str,
    description: str,
    preset: str,
    triggers: list[Trigger],
    intent_duration: str = "indefinite",
) -> Pipeline:
    """Build a single-stage stub :class:`Pipeline`.

    All core pipelines are single-stage in Slice 7.5b; the Phase 8
    slices that own each one are responsible for expanding the DAG
    when they wire in their real step functions.
    """
    return Pipeline(
        name=name,
        description=description,
        stages=[
            PipelineStage(
                name="run",
                task_preset=preset,  # type: ignore[arg-type]
                goal_template=f"{name} — default goal",
            )
        ],
        triggers=triggers,
        interruption_policy=InterruptionPolicy.PAUSE_ON_CONVERSATION,
        failure_policy=FailurePolicy.FAIL_PIPELINE,
        intent_duration=intent_duration,
    )


def _never(_ctx: object) -> bool:
    """Placeholder predicate for stubs that need a :func:`condition` trigger."""
    return False


# Pipeline name → step function. Used by the daemon to wire real
# behaviour in later slices without touching the pipeline declarations.
STEP_FUNCTIONS: dict[str, StepFn] = {}


# ── The catalogue ────────────────────────────────────────────────────────


def build_core_pipelines() -> list[Pipeline]:
    """Return the 20 core :class:`Pipeline` declarations (spec §4.3)."""
    global STEP_FUNCTIONS
    step_map: dict[str, StepFn] = {}
    pipelines: list[Pipeline] = []

    def _add(
        name: str,
        description: str,
        triggers: list[Trigger],
        preset: str,
        step_fn: StepFn,
        *,
        intent_duration: str = "indefinite",
    ) -> None:
        pipelines.append(
            _single_stage(
                name=name,
                description=description,
                preset=preset,
                triggers=triggers,
                intent_duration=intent_duration,
            )
        )
        step_map[name] = step_fn

    # 1. post_session_memory — Phase 8b: 5-stage pipeline with dependency
    #    edges (extract → consolidate → dedup → entities → vault_handoff).
    #    Replaces the single-stage stub from Slice 7.5b.
    from kora_v2.agents.background.memory_steward_handlers import (
        consolidate_step,
        dedup_step,
        entities_step,
        extract_step,
        vault_handoff_step,
    )

    post_session_memory_pipeline = Pipeline(
        name="post_session_memory",
        description="Memory Steward: extract → consolidate → dedup → entities → vault_handoff.",
        stages=[
            PipelineStage(
                name="extract",
                task_preset="bounded_background",
                goal_template="Extract facts from session transcripts and signals",
                depends_on=[],
            ),
            PipelineStage(
                name="consolidate",
                task_preset="bounded_background",
                goal_template="Consolidate semantically related notes",
                depends_on=["extract"],
            ),
            PipelineStage(
                name="dedup",
                task_preset="bounded_background",
                goal_template="Deduplicate near-identical notes",
                depends_on=["consolidate"],
            ),
            PipelineStage(
                name="entities",
                task_preset="bounded_background",
                goal_template="Resolve fuzzy entity matches",
                depends_on=["dedup"],
            ),
            PipelineStage(
                name="vault_handoff",
                task_preset="bounded_background",
                goal_template="Signal memory pipeline completion",
                depends_on=["entities"],
            ),
        ],
        triggers=[event("post_session_memory", event_type="SESSION_END")],
        interruption_policy=InterruptionPolicy.PAUSE_ON_CONVERSATION,
        failure_policy=FailurePolicy.FAIL_PIPELINE,
        intent_duration="indefinite",
    )
    pipelines.append(post_session_memory_pipeline)
    step_map["post_session_memory:extract"] = extract_step
    step_map["post_session_memory:consolidate"] = consolidate_step
    step_map["post_session_memory:dedup"] = dedup_step
    step_map["post_session_memory:entities"] = entities_step
    step_map["post_session_memory:vault_handoff"] = vault_handoff_step

    # 2. post_memory_vault — spec §4.3: sequence_complete(
    #    "post_session_memory") ∨ interval(1800s, {DEEP_IDLE})
    _add(
        "post_memory_vault",
        "Vault Organizer: reindex → structure → links → moc_sessions.",
        [
            any_of(
                "post_memory_vault",
                sequence_complete(
                    "post_memory_vault",
                    sequence_name="post_session_memory",
                ),
                interval(
                    "post_memory_vault",
                    every=timedelta(seconds=1800),
                    allowed_phases=["deep_idle"],
                ),
            )
        ],
        "bounded_background",
        _stub_step,
    )

    # 3. weekly_adhd_profile — Phase 8b: real handler replaces stub.
    from kora_v2.agents.background.memory_steward_handlers import (
        adhd_profile_refine_step,
    )

    _add(
        "weekly_adhd_profile",
        "ADHD profile refinement (weekly).",
        [time_of_day("weekly_adhd_profile", at=dtime(2, 0))],
        "bounded_background",
        adhd_profile_refine_step,
    )

    # 4. user_autonomous_task — real step function from pipeline_factory.
    # Imported here (not at module top) to keep the core_pipelines import
    # graph free of the autonomous subpackage for tests that boot a
    # minimal engine without autonomous wiring.
    from kora_v2.autonomous.pipeline_factory import get_autonomous_step_fn

    _add(
        "user_autonomous_task",
        "Plan → execute → review → replan (formerly AutonomousExecutionLoop).",
        # L1: the retired ``start_autonomous`` supervisor tool was
        # replaced by ``decompose_and_dispatch`` (see §17.9). The
        # USER_ACTION trigger here is a diagnostic placeholder — the
        # real dispatch path goes through
        # ``engine.start_pipeline_instance`` directly.
        [user_action("user_autonomous_task", action_name="decompose_and_dispatch")],
        "long_background",
        get_autonomous_step_fn(),
        intent_duration="long",
    )

    # 5. in_turn_subagent
    _add(
        "in_turn_subagent",
        "Parallel sub-tasks within a supervisor turn.",
        [
            user_action(
                "in_turn_subagent",
                action_name="decompose_and_dispatch_in_turn",
            )
        ],
        "in_turn",
        _stub_step,
    )

    # 6. wake_up_preparation
    _add(
        "wake_up_preparation",
        "Morning briefing preparation (user.wake_time - 45m).",
        [time_of_day("wake_up_preparation", at=dtime(6, 15))],
        "bounded_background",
        _stub_step,
    )

    # 7. continuity_check
    _add(
        "continuity_check",
        "Meeting reminders, medication windows, routine nudges.",
        [interval("continuity_check", every=timedelta(seconds=300))],
        "bounded_background",
        _stub_step,
    )

    # 8. proactive_pattern_scan
    _add(
        "proactive_pattern_scan",
        "ProactiveAgent Area A — pattern-based noticing.",
        [
            any_of(
                "proactive_pattern_scan",
                event("proactive_pattern_scan", event_type="INSIGHT_AVAILABLE"),
                event(
                    "proactive_pattern_scan", event_type="EMOTION_SHIFT_DETECTED"
                ),
                event("proactive_pattern_scan", event_type="MEMORY_STORED"),
                interval(
                    "proactive_pattern_scan",
                    every=timedelta(seconds=1800),
                    allowed_phases=["light_idle", "deep_idle"],
                ),
            )
        ],
        "bounded_background",
        _stub_step,
    )

    # 9. anticipatory_prep
    _add(
        "anticipatory_prep",
        "ProactiveAgent Area B — prep for upcoming events.",
        [
            any_of(
                "anticipatory_prep",
                interval(
                    "anticipatory_prep",
                    every=timedelta(seconds=1200),
                    allowed_phases=["deep_idle"],
                ),
                time_of_day("anticipatory_prep", at=dtime(6, 15)),
            )
        ],
        "long_background",
        _stub_step,
        intent_duration="long",
    )

    # 10. proactive_research
    _add(
        "proactive_research",
        "ProactiveAgent Area C — deep-dive research.",
        [user_action("proactive_research", action_name="dispatch_research")],
        "long_background",
        _stub_step,
        intent_duration="long",
    )

    # 11. article_digest
    _add(
        "article_digest",
        "ProactiveAgent Area C — article summarization.",
        [
            condition(
                "article_digest",
                predicate=_never,
                min_interval=timedelta(seconds=3600),
            )
        ],
        "long_background",
        _stub_step,
        intent_duration="long",
    )

    # 12. follow_through_draft
    _add(
        "follow_through_draft",
        "ProactiveAgent Area C — draft on observed need.",
        [
            event(
                "follow_through_draft", event_type="USER_STATED_INTENT"
            )
        ],
        "bounded_background",
        _stub_step,
    )

    # 13. contextual_engagement
    _add(
        "contextual_engagement",
        "ProactiveAgent Area D — context-driven engagement.",
        [
            any_of(
                "contextual_engagement",
                event(
                    "contextual_engagement",
                    event_type="EMOTION_SHIFT_DETECTED",
                ),
                event("contextual_engagement", event_type="TASK_LINGERING"),
                event(
                    "contextual_engagement",
                    event_type="OPEN_DECISION_POSED",
                ),
                event(
                    "contextual_engagement",
                    event_type="LONG_FOCUS_BLOCK_ENDED",
                ),
            )
        ],
        "bounded_background",
        _stub_step,
    )

    # 14. commitment_tracking
    _add(
        "commitment_tracking",
        "ProactiveAgent Area E — scan transcripts for commitments.",
        [time_of_day("commitment_tracking", at=dtime(1, 0))],
        "bounded_background",
        _stub_step,
    )

    # 15. stuck_detection
    _add(
        "stuck_detection",
        "ProactiveAgent Area E — detect stuck work.",
        [
            interval(
                "stuck_detection",
                every=timedelta(seconds=21600),
                allowed_phases=["light_idle", "deep_idle"],
            )
        ],
        "bounded_background",
        _stub_step,
    )

    # 16. weekly_triage
    _add(
        "weekly_triage",
        "ProactiveAgent Area E — weekly review.",
        [time_of_day("weekly_triage", at=dtime(9, 0))],
        "bounded_background",
        _stub_step,
    )

    # 17. draft_on_observation
    _add(
        "draft_on_observation",
        "ProactiveAgent Area E — draft assist on stated need.",
        [event("draft_on_observation", event_type="USER_STATED_NEED")],
        "bounded_background",
        _stub_step,
    )

    # 18. connection_making
    _add(
        "connection_making",
        "ProactiveAgent Area E — vault cross-references.",
        [time_of_day("connection_making", at=dtime(3, 0))],
        "bounded_background",
        _stub_step,
    )

    # 19. session_bridge_pruning (REAL — replaces BackgroundWorker job)
    _add(
        "session_bridge_pruning",
        "Housekeeping: prune expired session-bridge records.",
        [
            interval(
                "session_bridge_pruning",
                every=timedelta(seconds=3600),
                allowed_phases=["deep_idle"],
            )
        ],
        "bounded_background",
        _session_bridge_pruning_step,
    )

    # 20. skill_refinement (REAL — replaces BackgroundWorker job, Gap 6 fix)
    _add(
        "skill_refinement",
        "LLM review of one skill YAML per day.",
        [time_of_day("skill_refinement", at=dtime(3, 0))],
        "bounded_background",
        _skill_refinement_step,
    )

    STEP_FUNCTIONS = step_map
    return pipelines


def core_step_fns() -> dict[str, StepFn]:
    """Return a ``pipeline_name → step_fn`` map for engine wiring."""
    if not STEP_FUNCTIONS:
        build_core_pipelines()
    return dict(STEP_FUNCTIONS)


def register_core_pipelines(engine: OrchestrationEngine) -> None:
    """Register the 20 core pipelines with *engine*.

    Pipelines are registered via the plain ``register_pipeline`` path
    (not ``register_runtime_pipeline``) because they are code-declared
    and always present at boot — they do not belong in the
    ``runtime_pipelines`` table.
    """
    pipelines = build_core_pipelines()
    for pipeline in pipelines:
        engine.register_pipeline(pipeline)
    log.debug(
        "core_pipelines_registered",
        count=len(pipelines),
        real=sum(
            1
            for name, fn in STEP_FUNCTIONS.items()
            if fn is not _stub_step
        ),
    )
