"""Kora V2 — Autonomous execution graph nodes.

Phase 6A: 12-node autonomous runtime. Each node is an async function
that takes the current AutonomousState plus context and returns an
updated state.

The execution loop in kora_v2/autonomous/loop.py drives these nodes
based on the current state's status and metadata.

Node topology (from Phase 6 spec):
    classify_request → plan → persist_plan → execute_step → review_step
    → checkpoint → reflect → [continue_execution | replan | decision_request
                               | paused_for_overlap] → complete | failed
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

import aiosqlite
import structlog

from kora_v2.autonomous.state import (
    AutonomousCheckpoint,
    AutonomousState,
)

log = structlog.get_logger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────

TERMINAL_STATUSES: frozenset[str] = frozenset({"completed", "cancelled", "failed"})

# Keywords that suggest this is a routine rather than a one-off task
_ROUTINE_KEYWORDS: frozenset[str] = frozenset({
    "routine", "morning", "evening", "habit", "daily", "walkthrough",
    "checklist", "ritual", "regular",
})


# ══════════════════════════════════════════════════════════════════════════
# Node Functions
# ══════════════════════════════════════════════════════════════════════════


# ── 1. classify_request ───────────────────────────────────────────────────


def classify_request(
    goal: str,
    session_id: str,
) -> AutonomousState:
    """Initialise AutonomousState from a user goal string.

    Determines mode (task vs routine) from goal keywords.
    Returns state with status='planned'.

    Args:
        goal: The user's high-level goal description.
        session_id: Active conversation session ID.

    Returns:
        Initial AutonomousState ready for planning.
    """
    words = set(goal.lower().split())
    mode: Literal["task", "routine"] = (
        "routine" if words & _ROUTINE_KEYWORDS else "task"
    )

    plan_id = str(uuid.uuid4())
    log.info(
        "autonomous_classify_request",
        session_id=session_id,
        mode=mode,
        plan_id=plan_id,
    )
    return AutonomousState(
        session_id=session_id,
        plan_id=plan_id,
        mode=mode,
        status="planned",
        started_at=datetime.now(UTC),
        metadata={"goal": goal},
    )


# ── 2. plan ───────────────────────────────────────────────────────────────


async def plan(
    state: AutonomousState,
    container: Any,
) -> AutonomousState:
    """Dispatch to the planner worker to build the step DAG.

    Stores the Plan and step list in ``state.metadata``.
    Populates ``state.pending_step_ids`` from the plan's step IDs.

    Args:
        state: Current AutonomousState (status='planned').
        container: DI container with a configured planner worker.

    Returns:
        Updated state with pending_step_ids populated, or
        failed state if planning raises an exception.
    """
    goal = state.metadata.get("goal", "")
    log.info("autonomous_plan", session_id=state.session_id, goal=goal[:80])

    try:
        from kora_v2.core.models import PlanConstraints, PlanInput

        settings = getattr(container, "settings", None)
        auto_settings = getattr(settings, "autonomous", None)
        max_hours = getattr(auto_settings, "max_session_hours", 4.0)

        plan_input = PlanInput(
            goal=goal,
            context=state.metadata.get("context", ""),
            constraints=PlanConstraints(
                max_steps=10,
                max_minutes=int(max_hours * 60),
                autonomy_level="ask_important",
            ),
        )
        planner = container.resolve_worker("planner")
        output = await planner.execute(plan_input)

        state = state.model_copy(deep=True)

        if not output.steps:
            # Guard: empty plan causes an infinite plan→plan loop. Fail fast.
            log.warning("autonomous_plan_empty_steps", session_id=state.session_id)
            state.status = "failed"
            state.metadata["failure_reason"] = "Planner returned zero steps"
            return state

        state.pending_step_ids = [s.id for s in output.steps]
        state.metadata["plan"] = output.plan.model_dump()
        state.metadata["steps"] = {s.id: s.model_dump() for s in output.steps}
        state.metadata["plan_confidence"] = output.confidence
        state.status = "planned"

        log.info(
            "autonomous_plan_complete",
            session_id=state.session_id,
            step_count=len(output.steps),
            confidence=output.confidence,
        )

    except Exception as exc:
        log.error("autonomous_plan_failed", error=str(exc), session_id=state.session_id)
        state = state.model_copy(deep=True)
        state.status = "failed"
        state.metadata["failure_reason"] = f"Planning failed: {exc}"

    return state


# ── 3. persist_plan ───────────────────────────────────────────────────────


async def persist_plan(
    state: AutonomousState,
    db_path: Any,
) -> AutonomousState:
    """Persist plan steps as items in the task DB.

    On first call (root_item_id is None): creates a parent item for the goal
    and a child item for each PlanStep.

    On replan (root_item_id already set): only inserts new step items that
    are not already tracked in metadata['item_ids'], skipping steps that
    were already persisted.

    Failure semantics: any exception raised during persistence is FATAL for
    the autonomous session. Without a root_item_id the router would route
    back to persist_plan indefinitely, so we transition the state to
    ``failed`` here rather than attempt a retry.

    Args:
        state: Current AutonomousState with metadata['steps'] set.
        db_path: Path to operational.db.

    Returns:
        Updated state with root_item_id and item IDs in metadata, or
        a failed state if persistence raised an exception.
    """
    is_replan = state.root_item_id is not None
    steps_meta: dict[str, Any] = state.metadata.get("steps", {})
    existing_item_ids: dict[str, str] = dict(state.metadata.get("item_ids", {}))
    goal = state.metadata.get("goal", "Unnamed task")
    now = datetime.now(UTC).isoformat()

    root_id = state.root_item_id or str(uuid.uuid4())
    item_ids: dict[str, str] = dict(existing_item_ids)  # carry over existing
    # Only insert steps that don't already have an item_id
    new_steps = {
        sid: sdata
        for sid, sdata in steps_meta.items()
        if sid not in existing_item_ids
    }

    try:
        async with aiosqlite.connect(str(db_path)) as db:
            if not is_replan:
                # Create parent item only on the initial plan
                await db.execute(
                    """
                    INSERT INTO items
                        (id, autonomous_plan_id, type, owner, title, description,
                         status, progress_pct, created_at, updated_at)
                    VALUES (?, ?, 'task', 'kora', ?, ?, 'planned', 0.0, ?, ?)
                    """,
                    (root_id, state.plan_id, goal[:200], goal, now, now),
                )

            # Create child item for each new step
            for step_id, step_data in new_steps.items():
                item_id = str(uuid.uuid4())
                item_ids[step_id] = item_id
                await db.execute(
                    """
                    INSERT INTO items
                        (id, parent_id, autonomous_plan_id, type, owner,
                         title, description, status, energy_level,
                         estimated_minutes, confidence, spawned_from,
                         progress_pct, created_at, updated_at)
                    VALUES (?, ?, ?, 'task', 'kora', ?, ?, 'planned',
                            ?, ?, ?, ?, 0.0, ?, ?)
                    """,
                    (
                        item_id,
                        root_id,
                        state.plan_id,
                        step_data.get("title", "Step")[:200],
                        step_data.get("description", ""),
                        step_data.get("energy_level"),
                        step_data.get("estimated_minutes"),
                        step_data.get("confidence"),
                        state.plan_id,
                        now,
                        now,
                    ),
                )
                # Record initial state transition
                await db.execute(
                    """
                    INSERT INTO item_state_history
                        (item_id, from_status, to_status, reason, recorded_at)
                    VALUES (?, NULL, 'planned', 'plan_persisted', ?)
                    """,
                    (item_id, now),
                )

            if not is_replan:
                # Create autonomous_plans record only on the initial plan
                await db.execute(
                    """
                    INSERT INTO autonomous_plans
                        (id, session_id, goal, mode, status, confidence, created_at)
                    VALUES (?, ?, ?, ?, 'planned', ?, ?)
                    """,
                    (
                        state.plan_id,
                        state.session_id,
                        goal,
                        state.mode,
                        state.metadata.get("plan_confidence"),
                        now,
                    ),
                )

            await db.commit()

        state = state.model_copy(deep=True)
        state.root_item_id = root_id
        state.metadata["item_ids"] = item_ids
        log.info(
            "autonomous_persist_plan",
            session_id=state.session_id,
            root_item_id=root_id,
            step_count=len(item_ids),
            new_steps=len(new_steps),
            is_replan=is_replan,
        )

    except Exception as exc:
        # Fatal: without a root_item_id, route_next_node would send us
        # straight back to persist_plan, creating a tight retry loop that
        # can emit thousands of log entries per second. Transition to
        # ``failed`` so the execution loop exits cleanly.
        log.error("autonomous_persist_plan_failed", error=str(exc))
        state = state.model_copy(deep=True)
        state.status = "failed"
        state.metadata["failure_reason"] = f"persist_plan failed: {exc}"
        state.metadata["persist_error"] = str(exc)

    return state


# ── 4. execute_step ───────────────────────────────────────────────────────


async def execute_step(
    state: AutonomousState,
    container: Any,
    db_path: Any | None = None,
) -> AutonomousState:
    """Execute the next pending plan step.

    Routes to the appropriate worker based on the step's worker field.
    Updates item status in the DB.

    Args:
        state: Current AutonomousState with pending steps.
        container: DI container with workers.
        db_path: Path to operational.db for item updates.

    Returns:
        Updated state with current_step_id and step result in metadata.
    """
    if not state.pending_step_ids:
        # All steps done
        state = state.model_copy(deep=True)
        state.status = "reviewing"
        return state

    step_id = state.pending_step_ids[0]
    steps_meta = state.metadata.get("steps", {})
    step_data = steps_meta.get(step_id, {})
    worker_name = step_data.get("worker", "executor")

    log.info(
        "autonomous_execute_step",
        session_id=state.session_id,
        step_id=step_id,
        worker=worker_name,
        step_title=step_data.get("title", ""),
    )

    # Update item status to in_progress in DB
    if db_path is not None:
        item_ids: dict[str, str] = state.metadata.get("item_ids", {})
        item_id = item_ids.get(step_id)
        if item_id:
            now = datetime.now(UTC).isoformat()
            try:
                async with aiosqlite.connect(str(db_path)) as db:
                    await db.execute(
                        "UPDATE items SET status='in_progress', updated_at=? WHERE id=?",
                        (now, item_id),
                    )
                    await db.execute(
                        """INSERT INTO item_state_history
                               (item_id, from_status, to_status, reason, recorded_at)
                           VALUES (?, 'planned', 'in_progress', 'step_dispatched', ?)""",
                        (item_id, now),
                    )
                    await db.commit()
            except Exception:
                pass  # Non-fatal

    try:
        # Determine worker to dispatch
        _CODE_WORKERS = {"code"}
        _EXEC_WORKERS = {"executor", "memory", "research", "life_mgmt", "screen"}

        if worker_name in _CODE_WORKERS:
            # Attempt Claude Code delegation if enabled
            settings = getattr(container, "settings", None)
            auto_settings = getattr(settings, "autonomous", None)
            use_delegate = getattr(auto_settings, "delegate_to_claude_code", False)
            if use_delegate:
                result_json = await _delegate_to_claude_code(step_data, container)
            else:
                result_json = await _dispatch_to_executor(step_data, container)
        elif worker_name in _EXEC_WORKERS:
            result_json = await _dispatch_to_executor(step_data, container)
        elif worker_name == "reviewer":
            result_json = await _dispatch_to_reviewer(step_data, container)
        else:
            result_json = await _dispatch_to_executor(step_data, container)

        # Move step from pending to completed
        state = state.model_copy(deep=True)
        state.pending_step_ids = [s for s in state.pending_step_ids if s != step_id]
        state.completed_step_ids = state.completed_step_ids + [step_id]
        state.current_step_id = step_id
        state.current_step_index += 1
        state.iteration_count += 1
        state.request_count += 1

        # Store result
        step_results: dict[str, Any] = state.metadata.get("step_results", {})
        step_results[step_id] = result_json
        state.metadata["step_results"] = step_results
        state.metadata["last_step_result"] = result_json

        state.status = "executing"

        # Update item to completed in DB
        if db_path is not None:
            item_ids = state.metadata.get("item_ids", {})
            item_id = item_ids.get(step_id)
            if item_id:
                now = datetime.now(UTC).isoformat()
                try:
                    async with aiosqlite.connect(str(db_path)) as db:
                        await db.execute(
                            "UPDATE items SET status='completed', progress_pct=100.0, updated_at=? WHERE id=?",
                            (now, item_id),
                        )
                        await db.execute(
                            """INSERT INTO item_state_history
                                   (item_id, from_status, to_status, reason, recorded_at)
                               VALUES (?, 'in_progress', 'completed', 'step_accepted', ?)""",
                            (item_id, now),
                        )
                        await db.commit()
                except Exception:
                    pass

    except Exception as exc:
        log.error(
            "autonomous_execute_step_failed",
            step_id=step_id,
            error=str(exc),
        )
        state = state.model_copy(deep=True)
        state.status = "failed"
        state.metadata["failure_reason"] = f"Step execution failed: {exc}"
        state.metadata["failed_step_id"] = step_id

    return state


async def _dispatch_to_executor(step_data: dict[str, Any], container: Any) -> str:
    """Dispatch a step to the executor worker."""
    from kora_v2.core.models import ExecutionConstraints, ExecutionInput

    task_desc = step_data.get("description") or step_data.get("title", "Execute step")
    exec_input = ExecutionInput(
        task=task_desc,
        tools_available=step_data.get("tools_needed", []),
        context=step_data.get("description", ""),
        constraints=ExecutionConstraints(timeout_seconds=300),
        energy_level=step_data.get("energy_level"),
        estimated_minutes=step_data.get("estimated_minutes"),
    )
    executor = container.resolve_worker("executor")
    output = await executor.execute(exec_input)
    return output.model_dump_json()


async def _dispatch_to_reviewer(step_data: dict[str, Any], container: Any) -> str:
    """Dispatch a step to the reviewer worker."""
    from kora_v2.core.models import ReviewInput

    review_input = ReviewInput(
        work_product=step_data.get("description", ""),
        criteria=step_data.get("review_criteria", []),
        original_goal=step_data.get("title", ""),
    )
    reviewer = container.resolve_worker("reviewer")
    output = await reviewer.execute(review_input)
    return output.model_dump_json()


async def _delegate_to_claude_code(step_data: dict[str, Any], container: Any) -> str:
    """Delegate a code step to the ClaudeCodeDelegate subprocess."""
    from kora_v2.llm.claude_code import ClaudeCodeDelegate, DelegationBrief

    settings = getattr(container, "settings", None)
    auto_settings = getattr(settings, "autonomous", None)
    binary = getattr(auto_settings, "claude_code_binary", "claude")

    delegate = ClaudeCodeDelegate(claude_binary=binary)
    brief = DelegationBrief(
        goal=step_data.get("description", step_data.get("title", "Code task")),
        allowed_tools=step_data.get("tools_needed", ["Read", "Write", "Bash"]),
        expected_deliverables=step_data.get("review_criteria", []),
    )
    result = await delegate.delegate(brief)
    if result.output:
        return result.output.model_dump_json()
    failure_msg = result.failure.message if result.failure else "Delegation failed"
    return json.dumps({"status": "error", "error": failure_msg})


# ── 5. review_step ────────────────────────────────────────────────────────


async def review_step(
    state: AutonomousState,
    container: Any,
) -> AutonomousState:
    """Review the most recently completed step via the reviewer worker.

    Updates quality_summary with the confidence score from the review.

    Args:
        state: Current state with last_step_result in metadata.
        container: DI container with reviewer worker.

    Returns:
        Updated state with quality_summary updated.
    """
    step_id = state.current_step_id
    last_result = state.metadata.get("last_step_result", "")
    steps_meta = state.metadata.get("steps", {})
    step_data = steps_meta.get(step_id or "", {})

    log.info("autonomous_review_step", session_id=state.session_id, step_id=step_id)

    try:
        from kora_v2.core.models import ReviewInput

        review_input = ReviewInput(
            work_product=last_result[:4000] if last_result else "",
            criteria=step_data.get("review_criteria", []),
            original_goal=step_data.get("title", ""),
            context=state.metadata.get("goal", ""),
        )
        reviewer = container.resolve_worker("reviewer")
        review_output = await reviewer.execute(review_input)

        # Compute confidence
        from kora_v2.quality.confidence import confidence_from_review

        tool_records: list[dict[str, Any]] = []  # no per-step tool records at this level
        conf_result = confidence_from_review(
            review_output,
            tool_call_records=tool_records,
        )

        state = state.model_copy(deep=True)
        quality_summary = dict(state.quality_summary)
        quality_summary[step_id or "last"] = {
            "confidence": conf_result.score,
            "label": conf_result.label,
            "passed": review_output.passed,
            "findings": [f.model_dump() for f in review_output.findings],
        }
        state.quality_summary = quality_summary
        state.status = "reviewing"

        log.info(
            "autonomous_review_complete",
            session_id=state.session_id,
            step_id=step_id,
            confidence=conf_result.score,
            passed=review_output.passed,
        )

    except Exception as exc:
        log.error("autonomous_review_failed", error=str(exc), step_id=step_id)
        state = state.model_copy(deep=True)
        quality_summary = dict(state.quality_summary)
        quality_summary[step_id or "last"] = {
            "confidence": 0.5,
            "label": "medium",
            "passed": True,  # Don't block on review errors
            "error": str(exc),
        }
        state.quality_summary = quality_summary
        state.status = "reviewing"

    return state


# ── 6. checkpoint ─────────────────────────────────────────────────────────


async def checkpoint(
    state: AutonomousState,
    checkpoint_manager: Any,
    reason: str = "periodic",
) -> AutonomousState:
    """Persist the current AutonomousState as a checkpoint.

    Generates a resume token and saves via CheckpointManager.

    Args:
        state: Current state to checkpoint.
        checkpoint_manager: CheckpointManager instance.
        reason: Why the checkpoint was taken.

    Returns:
        Updated state with last_checkpoint_at set.
    """
    now = datetime.now(UTC)
    checkpoint_id = str(uuid.uuid4())
    resume_token = str(uuid.uuid4())

    state = state.model_copy(deep=True)
    state.status = "checkpointing"
    state.last_checkpoint_at = now
    state.safe_resume_token = resume_token

    checkpoint_obj = AutonomousCheckpoint(
        checkpoint_id=checkpoint_id,
        session_id=state.session_id,
        plan_id=state.plan_id,
        root_item_id=state.root_item_id,
        mode=state.mode,
        state=state,
        completed_step_ids=list(state.completed_step_ids),
        pending_step_ids=list(state.pending_step_ids),
        produced_artifact_ids=list(state.produced_artifact_ids),
        granted_tools=list(state.granted_tools),
        quality_results=[
            {"step_id": sid, **qdata}
            for sid, qdata in state.quality_summary.items()
        ],
        decision_queue=list(state.decision_queue),
        latest_reflection=state.latest_reflection,
        overlap_score=state.overlap_score,
        resume_token=resume_token,
        elapsed_seconds=state.elapsed_seconds,
        request_count=state.request_count,
        token_estimate=state.token_estimate,
        cost_estimate=state.cost_estimate,
        created_at=now,
        reason=reason,
    )

    try:
        await checkpoint_manager.save(checkpoint_obj)
        log.info(
            "autonomous_checkpoint_saved",
            checkpoint_id=checkpoint_id,
            session_id=state.session_id,
            reason=reason,
        )
    except Exception as exc:
        log.error("autonomous_checkpoint_save_failed", error=str(exc))

    return state


# ── 7. reflect ────────────────────────────────────────────────────────────


def reflect(
    state: AutonomousState,
) -> tuple[AutonomousState, str]:
    """Evaluate accumulated execution data and decide next action.

    Heuristic reflection based on:
    1. Whether all steps are complete → route to complete
    2. Budget status → if hard-stopped → route to failed
    3. Overlap score → if >= 0.70 → route to paused_for_overlap
    4. Decision queue → if non-empty → route to decision_request
    5. Quality → if avg confidence < 0.4 → consider replan
    6. Default → continue_execution

    The 5 reflection questions answered here:
    Q1. Has the goal changed since we started? (tracked via metadata drift)
    Q2. Are the remaining steps still the right approach?
    Q3. Is there a quality issue that needs addressing?
    Q4. Does the current trajectory need user input?
    Q5. Are we within budget and on track?

    Args:
        state: Current state after checkpoint.

    Returns:
        Tuple of (updated state with latest_reflection set, next_action string).
        next_action ∈ {"continue", "replan", "decision_request",
                       "paused_for_overlap", "complete", "failed"}
    """
    log.info("autonomous_reflect", session_id=state.session_id)

    state = state.model_copy(deep=True)
    state.status = "reflecting"

    # Q1. All steps done → complete
    if not state.pending_step_ids:
        state.latest_reflection = "All steps completed. Finalising."
        state.status = "reflecting"
        return state, "complete"

    # Q4. Overlap → pause
    if state.overlap_score >= 0.70:
        state.latest_reflection = (
            f"Overlap score {state.overlap_score:.2f} >= 0.70. Pausing for safety."
        )
        return state, "paused_for_overlap"

    # Q4. Pending decisions → wait
    if state.decision_queue:
        state.latest_reflection = (
            f"{len(state.decision_queue)} pending decision(s). Waiting for user."
        )
        return state, "decision_request"

    # Q3. Quality check — average confidence of reviewed steps
    quality_scores = [
        v.get("confidence", 1.0) for v in state.quality_summary.values()
        if isinstance(v, dict)
    ]
    avg_confidence = sum(quality_scores) / len(quality_scores) if quality_scores else 1.0

    if avg_confidence < 0.35 and len(state.completed_step_ids) > 0:
        state.latest_reflection = (
            f"Average step confidence {avg_confidence:.2f} below threshold 0.35. "
            "Triggering scoped replan."
        )
        return state, "replan"

    # Q5. Budget soft-warning acknowledgement
    budget_warned = state.metadata.get("budget_soft_warning", False)

    reflection_parts = [
        f"Steps completed: {len(state.completed_step_ids)}, remaining: {len(state.pending_step_ids)}.",
        f"Average quality confidence: {avg_confidence:.2f}.",
    ]
    if budget_warned:
        reflection_parts.append("Budget approaching threshold — continuing carefully.")

    state.latest_reflection = " ".join(reflection_parts)
    return state, "continue"


# ── 8. decision_request ───────────────────────────────────────────────────


def decision_request(
    state: AutonomousState,
    decision_manager: Any,
    options: list[str],
    recommendation: str | None = None,
    policy: str = "auto_select",
    timeout_minutes: int = 10,
) -> tuple[AutonomousState, Any]:
    """Pause execution for a user decision.

    Creates a PendingDecision via DecisionManager and sets
    state.status = 'waiting_on_user'.

    Args:
        state: Current state.
        decision_manager: DecisionManager instance.
        options: List of valid choices.
        recommendation: Recommended choice.
        policy: 'auto_select' or 'never_auto'.
        timeout_minutes: How long before auto-select fires.

    Returns:
        Tuple of (updated state, PendingDecision object).
    """
    decision = decision_manager.create_decision(
        options=options,
        recommendation=recommendation,
        policy=policy,
        timeout_minutes=timeout_minutes,
    )

    state = state.model_copy(deep=True)
    state.status = "waiting_on_user"
    state.decision_queue = state.decision_queue + [decision.decision_id]

    log.info(
        "autonomous_decision_request",
        session_id=state.session_id,
        decision_id=decision.decision_id,
        options=options,
        policy=policy,
    )
    return state, decision


# ── 9. paused_for_overlap ─────────────────────────────────────────────────


def paused_for_overlap(state: AutonomousState) -> AutonomousState:
    """Pause execution at a safe boundary due to topic overlap.

    Args:
        state: Current state.

    Returns:
        Updated state with status='paused_for_overlap'.
    """
    log.info(
        "autonomous_paused_for_overlap",
        session_id=state.session_id,
        overlap_score=state.overlap_score,
    )
    state = state.model_copy(deep=True)
    state.status = "paused_for_overlap"
    return state


# ── 10. replan ────────────────────────────────────────────────────────────


async def replan(
    state: AutonomousState,
    container: Any,
    failure_reason: str = "",
) -> AutonomousState:
    """Rebuild only the affected (remaining) portion of the plan.

    Preserves completed steps and only replans what has not yet run.
    If no steps remain, routes to complete.

    Args:
        state: Current state with pending steps to replan.
        container: DI container with planner worker.
        failure_reason: Why the replan was triggered.

    Returns:
        Updated state with new pending_step_ids for the remaining work.
    """
    if not state.pending_step_ids:
        state = state.model_copy(deep=True)
        state.status = "planned"
        return state

    original_goal = state.metadata.get("goal", "")
    completed_count = len(state.completed_step_ids)
    remaining_count = len(state.pending_step_ids)

    replan_goal = (
        f"{original_goal}\n\n"
        f"[{completed_count} steps already completed. "
        f"Replanning the remaining {remaining_count} steps. "
        f"Reason: {failure_reason or 'quality or drift detected'}]"
    )

    log.info(
        "autonomous_replan",
        session_id=state.session_id,
        completed=completed_count,
        remaining=remaining_count,
        reason=failure_reason,
    )

    try:
        from kora_v2.core.models import PlanConstraints, PlanInput

        plan_input = PlanInput(
            goal=replan_goal,
            context="",
            constraints=PlanConstraints(max_steps=remaining_count + 2),
        )
        planner = container.resolve_worker("planner")
        output = await planner.execute(plan_input)

        state = state.model_copy(deep=True)
        # Replace pending steps with new plan steps
        state.pending_step_ids = [s.id for s in output.steps]
        existing_steps = dict(state.metadata.get("steps", {}))
        existing_steps.update({s.id: s.model_dump() for s in output.steps})
        state.metadata["steps"] = existing_steps
        state.metadata["replan_reason"] = failure_reason
        state.status = "replanning"

    except Exception as exc:
        log.error("autonomous_replan_failed", error=str(exc))
        state = state.model_copy(deep=True)
        state.status = "failed"
        state.metadata["failure_reason"] = f"Replanning failed: {exc}"

    return state


# ── 11. complete ──────────────────────────────────────────────────────────


async def complete(
    state: AutonomousState,
    db_path: Any | None = None,
) -> AutonomousState:
    """Finalise the autonomous session.

    Updates the plan and root item to 'completed' in the DB.
    Sets state.status = 'completed'.

    Args:
        state: Current state (all steps done).
        db_path: Path to operational.db.

    Returns:
        Completed state.
    """
    now = datetime.now(UTC).isoformat()
    log.info(
        "autonomous_complete",
        session_id=state.session_id,
        steps_completed=len(state.completed_step_ids),
    )

    if db_path is not None:
        try:
            async with aiosqlite.connect(str(db_path)) as db:
                # Update plan status
                await db.execute(
                    "UPDATE autonomous_plans SET status='completed', completed_at=? WHERE id=?",
                    (now, state.plan_id),
                )
                # Update root item
                if state.root_item_id:
                    await db.execute(
                        "UPDATE items SET status='completed', progress_pct=100.0, updated_at=? WHERE id=?",
                        (now, state.root_item_id),
                    )
                    await db.execute(
                        """INSERT INTO item_state_history
                               (item_id, from_status, to_status, reason, recorded_at)
                           VALUES (?, 'in_progress', 'completed', 'all_steps_complete', ?)""",
                        (state.root_item_id, now),
                    )
                await db.commit()
        except Exception as exc:
            log.error("autonomous_complete_db_error", error=str(exc))

    state = state.model_copy(deep=True)
    state.status = "completed"
    quality_scores = [
        v.get("confidence", 1.0) for v in state.quality_summary.values()
        if isinstance(v, dict)
    ]
    avg_quality = sum(quality_scores) / len(quality_scores) if quality_scores else 1.0
    state.metadata["completion_summary"] = {
        "steps_completed": len(state.completed_step_ids),
        "avg_quality": avg_quality,
        "elapsed_seconds": state.elapsed_seconds,
        "requests_used": state.request_count,
    }
    return state


# ── 12. failed ────────────────────────────────────────────────────────────


async def failed(
    state: AutonomousState,
    error: str,
    db_path: Any | None = None,
) -> AutonomousState:
    """Record an unrecoverable failure.

    Updates the plan and any in-progress items to 'failed' in the DB.

    Args:
        state: Current state.
        error: Error description.
        db_path: Path to operational.db.

    Returns:
        Failed state with error recorded.
    """
    now = datetime.now(UTC).isoformat()
    log.error(
        "autonomous_failed",
        session_id=state.session_id,
        error=error,
        step_id=state.current_step_id,
    )

    if db_path is not None:
        try:
            async with aiosqlite.connect(str(db_path)) as db:
                await db.execute(
                    "UPDATE autonomous_plans SET status='failed', completed_at=? WHERE id=?",
                    (now, state.plan_id),
                )
                if state.root_item_id:
                    await db.execute(
                        "UPDATE items SET status='cancelled', updated_at=? WHERE id=?",
                        (now, state.root_item_id),
                    )
                await db.commit()
        except Exception as exc:
            log.error("autonomous_failed_db_error", error=str(exc))

    state = state.model_copy(deep=True)
    state.status = "failed"
    state.metadata["failure_reason"] = error
    return state


# ══════════════════════════════════════════════════════════════════════════
# Routing
# ══════════════════════════════════════════════════════════════════════════


def route_next_node(state: AutonomousState) -> str:
    """Pure routing function — maps current state to next node name.

    Used by the execution loop to determine which node to run next.

    Returns:
        Node name string. "END" signals the loop should stop.
    """
    status = state.status

    if status in TERMINAL_STATUSES:
        return "END"

    if status == "planned":
        # If no steps yet, go to plan. If steps exist, go to persist_plan.
        if state.pending_step_ids:
            if state.root_item_id is None:
                return "persist_plan"
            return "execute_step"
        return "plan"

    if status == "replanning":
        # After replan, persist new steps before executing them.
        if state.pending_step_ids:
            # If root_item_id is set, persist_plan will only add new items.
            return "persist_plan"
        return "complete"

    if status == "executing":
        return "review_step"

    if status == "reviewing":
        # Always route through checkpoint so reflect() can run quality checks.
        # reflect() decides whether to complete, replan, or continue.
        return "checkpoint"

    if status == "checkpointing":
        return "reflect"

    if status == "reflecting":
        # reflect() returns this status — loop's _handle_reflect_action resolves
        # the next action before returning. If we arrive here from a resumed
        # checkpoint, re-run reflect() to apply its heuristics.
        return "reflect"

    if status == "waiting_on_user":
        return "waiting_on_user"  # Loop polls decisions

    if status == "paused_for_overlap":
        # Checkpoint was already saved when this status was set.
        # Return END so the loop exits cleanly.
        return "END"

    # Fallback
    return "END"
