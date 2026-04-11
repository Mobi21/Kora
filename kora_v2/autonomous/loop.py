"""Kora V2 — Autonomous execution loop.

``AutonomousExecutionLoop`` drives the Phase 6A autonomous graph as
an asyncio background task. It manages:

- node dispatch based on route_next_node()
- periodic checkpoint scheduling (every N minutes)
- budget enforcement before each step
- interruption signals
- decision wait / poll
- topic overlap detection on incoming user messages

Usage::

    loop = AutonomousExecutionLoop(
        goal="Research 3 project management tools",
        session_id="abc123",
        container=container,
        db_path=Path("data/operational.db"),
    )
    # Spawn as background asyncio task
    task = asyncio.create_task(loop.run())
    # Later, from the main conversation thread:
    loop.request_interruption()

Synchronous-write hedge
-----------------------

The main conversation may write a deliverable file *synchronously* while
this background loop is still producing its own version of the same
artifact. This is intentional — ADHD users cannot afford a multi-minute
wait with no visible output, so the foreground path hedges with a
best-effort draft and discloses that the background loop is still
running. When the loop's own execute_step writes the same path, it MUST
NOT clobber an existing file; execute_step and its worker dispatch are
expected to honor ``merge_strategy="keep_existing"`` semantics by
reading before writing. A future refactor that removes the foreground
hedge must also document the user-facing wait behavior it replaces.
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite
import structlog

from kora_v2.autonomous import graph as graph_nodes
from kora_v2.autonomous.budget import BudgetEnforcer
from kora_v2.autonomous.checkpoint import CheckpointManager
from kora_v2.autonomous.decisions import DecisionManager
from kora_v2.autonomous.state import AutonomousState

log = structlog.get_logger(__name__)

# Seconds to sleep between loop iterations when waiting on user / overlap
_POLL_INTERVAL: float = 2.0

# Maximum seconds to wait for a user decision (safety cap; NEVER policy ignores this)
_MAX_DECISION_WAIT_SECONDS: float = 60 * 60 * 24  # 24 hours

# Nodes that are allowed to route back to themselves repeatedly without
# tripping the same-node watchdog (they cycle by design).
_LEGITIMATELY_CYCLIC_NODES: frozenset[str] = frozenset({
    "waiting_on_user",
    "checkpointing",
})

# If the same non-cyclic node is routed to this many times in a row, we
# treat it as a stuck-loop bug and fail the session.
_MAX_SAME_NODE_REPEATS: int = 5


class AutonomousExecutionLoop:
    """Drives the autonomous graph in a background asyncio task.

    Args:
        goal: The user's natural-language goal.
        session_id: Conversation session ID (used for state and tracking).
        container: DI container with workers, settings, event emitter.
        db_path: Path to operational.db for item/plan persistence.
        checkpoint_interval_minutes: How often to force a checkpoint.
        auto_continue_seconds: How long to pause after a checkpoint
            before auto-continuing (gives user a window to interrupt).
    """

    def __init__(
        self,
        goal: str,
        session_id: str,
        container: Any,
        db_path: Path,
        checkpoint_interval_minutes: int = 30,
        auto_continue_seconds: int = 30,
    ) -> None:
        self._goal = goal
        self._session_id = session_id
        self._container = container
        self._db_path = db_path
        self._checkpoint_interval_seconds = checkpoint_interval_minutes * 60
        self._auto_continue_seconds = auto_continue_seconds

        # Instantiate infrastructure
        self._checkpoint_mgr = CheckpointManager(db_path)
        self._decision_mgr = DecisionManager()

        settings = getattr(container, "settings", None)
        auto_settings = getattr(settings, "autonomous", None)
        llm_settings = getattr(settings, "llm", None)
        self._budget = BudgetEnforcer(
            autonomous=auto_settings,
            llm=llm_settings,
            request_warning_threshold=getattr(auto_settings, "request_warning_threshold", 0.85),
            request_hard_stop_threshold=getattr(auto_settings, "request_hard_stop_threshold", 1.0),
        )

        # Loop state
        self._state: AutonomousState | None = None
        self._interrupted = asyncio.Event()
        self._wall_start: float = time.monotonic()
        self._last_checkpoint_at: float = time.monotonic()

    # ── Public API ────────────────────────────────────────────────────

    def request_interruption(self) -> None:
        """Signal the loop to stop at the next safe boundary."""
        log.info("autonomous_interruption_requested", session_id=self._session_id)
        self._interrupted.set()
        if self._state is not None:
            self._state = self._state.model_copy(deep=True)
            self._state.interruption_pending = True

    def submit_decision(self, decision_id: str, chosen: str) -> None:
        """Submit a user answer for a pending decision.

        Args:
            decision_id: ID of the pending decision.
            chosen: The user's chosen option.
        """
        self._decision_mgr.submit_answer(decision_id, chosen)
        log.info(
            "autonomous_decision_submitted",
            session_id=self._session_id,
            decision_id=decision_id,
            chosen=chosen,
        )

    def set_overlap_score(self, score: float) -> None:
        """Update the topic overlap score from the main conversation thread.

        The loop will check this at the next safe boundary.

        Args:
            score: Overlap score 0.0–1.0.
        """
        if self._state is not None:
            self._state = self._state.model_copy(deep=True)
            self._state.overlap_score = score
            log.debug(
                "autonomous_overlap_score_updated",
                session_id=self._session_id,
                score=score,
            )

    @property
    def state(self) -> AutonomousState | None:
        """Current AutonomousState (read-only snapshot)."""
        return self._state

    @property
    def is_terminal(self) -> bool:
        """True if the loop has reached a terminal state."""
        if self._state is None:
            return False
        return self._state.status in graph_nodes.TERMINAL_STATUSES

    # ── Main Run Loop ──────────────────────────────────────────────────

    async def run(self) -> AutonomousState:
        """Execute the autonomous plan until a terminal state is reached.

        Returns:
            The final AutonomousState.
        """
        # 1. Classify and initialise state
        self._state = graph_nodes.classify_request(
            goal=self._goal,
            session_id=self._session_id,
        )
        log.info(
            "autonomous_loop_start",
            session_id=self._session_id,
            goal=self._goal[:80],
            mode=self._state.mode,
        )

        # Defense-in-depth watchdog: if the router keeps returning the same
        # non-cyclic node over and over, a node is failing to transition
        # state. Treat this as a stuck-loop bug and fail the session.
        prev_node: str | None = None
        consecutive_same_node: int = 0

        try:
            while not self._should_stop():
                self._update_elapsed()

                # Check for interruption signal
                if self._interrupted.is_set() or self._state.interruption_pending:
                    log.info(
                        "autonomous_loop_interrupted",
                        session_id=self._session_id,
                        status=self._state.status,
                    )
                    self._state = await graph_nodes.checkpoint(
                        self._state,
                        self._checkpoint_mgr,
                        reason="termination",
                    )
                    # Mark as cancelled so is_terminal returns True.
                    self._state = self._state.model_copy(deep=True)
                    self._state.status = "cancelled"
                    break

                # Route to next node
                next_node = graph_nodes.route_next_node(self._state)

                # Same-node watchdog — catches tight retry loops that slip
                # past individual node error handlers.
                if next_node in _LEGITIMATELY_CYCLIC_NODES:
                    consecutive_same_node = 0
                elif next_node == prev_node:
                    consecutive_same_node += 1
                else:
                    consecutive_same_node = 0
                prev_node = next_node

                if consecutive_same_node >= _MAX_SAME_NODE_REPEATS:
                    log.error(
                        "autonomous_loop_stuck",
                        session_id=self._session_id,
                        node=next_node,
                        repeats=consecutive_same_node,
                        status=self._state.status,
                    )
                    fail_reason = (
                        f"Stuck in node '{next_node}' for "
                        f"{consecutive_same_node} consecutive iterations"
                    )
                    self._state = await graph_nodes.failed(
                        self._state,
                        fail_reason,
                        db_path=self._db_path,
                    )
                    await self._emit_failed_event(fail_reason)
                    break

                if next_node == "END":
                    break

                if next_node == "waiting_on_user":
                    await self._handle_decision_wait()
                    continue

                # Check budget before executing a step
                if next_node in {"execute_step", "plan", "replan"}:
                    budget_result = self._budget.check_before_step(self._state)
                    if budget_result.hard_stop:
                        log.warning(
                            "autonomous_budget_hard_stop",
                            reason=budget_result.reason,
                            dimension=budget_result.dimension,
                        )
                        fail_reason = f"Budget limit reached: {budget_result.reason}"
                        self._state = await graph_nodes.failed(
                            self._state,
                            fail_reason,
                            db_path=self._db_path,
                        )
                        await self._emit_failed_event(fail_reason)
                        break
                    if budget_result.soft_warning:
                        self._state = self._state.model_copy(deep=True)
                        self._state.metadata["budget_soft_warning"] = True
                        log.info(
                            "autonomous_budget_soft_warning",
                            reason=budget_result.reason,
                        )

                # Execute the node
                self._state = await self._run_node(next_node)

                # Check if periodic checkpoint is due.
                # Only trigger on work nodes (plan, execute, review, replan) —
                # not on administrative nodes to prevent infinite checkpoint loops.
                _WORK_NODES = {"plan", "execute_step", "review_step", "replan"}
                if next_node in _WORK_NODES and self._should_checkpoint():
                    self._state = await graph_nodes.checkpoint(
                        self._state,
                        self._checkpoint_mgr,
                        reason="periodic",
                    )
                    self._last_checkpoint_at = time.monotonic()
                    # Persist unread update for foreground delivery
                    await self._persist_checkpoint_update(reason="periodic")
                    await self._emit_checkpoint_event()
                    # Auto-continue window
                    if self._auto_continue_seconds > 0:
                        log.info(
                            "autonomous_auto_continue_window",
                            seconds=self._auto_continue_seconds,
                        )
                        await asyncio.sleep(self._auto_continue_seconds)

        except asyncio.CancelledError:
            log.info("autonomous_loop_cancelled", session_id=self._session_id)
            if self._state is not None:
                self._state = await graph_nodes.checkpoint(
                    self._state, self._checkpoint_mgr, reason="termination"
                )
            raise

        except Exception as exc:
            log.error(
                "autonomous_loop_unexpected_error",
                session_id=self._session_id,
                error=str(exc),
            )
            fail_reason = f"Unexpected error: {exc}"
            if self._state is not None:
                self._state = await graph_nodes.failed(
                    self._state,
                    fail_reason,
                    db_path=self._db_path,
                )
            await self._emit_failed_event(fail_reason)

        log.info(
            "autonomous_loop_complete",
            session_id=self._session_id,
            status=self._state.status if self._state else "unknown",
        )

        # Persist a completion record so the foreground conversation can
        # surface what finished while the user was away.
        await self._persist_completion_update()
        await self._emit_complete_event()

        return self._state

    # ── Plan / Update Persistence ──────────────────────────────────────

    async def _update_plan_budget(self) -> None:
        """Write current budget counters back to the ``autonomous_plans`` row."""
        state = self._state
        if state is None or not state.plan_id:
            return
        try:
            async with aiosqlite.connect(str(self._db_path)) as db:
                await db.execute(
                    """UPDATE autonomous_plans
                       SET request_count=?, token_estimate=?, cost_estimate=?, updated_at=?
                       WHERE id=?""",
                    (
                        state.request_count,
                        state.token_estimate,
                        state.cost_estimate,
                        datetime.now(UTC).isoformat(),
                        state.plan_id,
                    ),
                )
                await db.commit()
        except Exception as exc:
            log.warning(
                "autonomous_plan_budget_update_failed",
                session_id=self._session_id,
                error=str(exc),
            )

    async def _persist_checkpoint_update(self, *, reason: str) -> None:
        """Persist an ``autonomous_updates`` row summarising the checkpoint."""
        state = self._state
        if state is None:
            return
        steps_completed = len(state.completed_step_ids)
        steps_pending = len(state.pending_step_ids)
        goal = (state.metadata.get("goal", "") or "").strip()
        goal_short = (goal[:80] + "…") if len(goal) > 80 else goal
        summary = (
            f"Checkpoint ({reason}): {steps_completed} step(s) done, "
            f"{steps_pending} remaining"
        )
        if goal_short:
            summary = f"{summary} — {goal_short}"
        payload = {
            "reason": reason,
            "status": state.status,
            "steps_completed": steps_completed,
            "steps_pending": steps_pending,
            "elapsed_seconds": state.elapsed_seconds,
            "request_count": state.request_count,
            "token_estimate": state.token_estimate,
            "cost_estimate": state.cost_estimate,
        }
        await self._insert_update_row(
            update_type="checkpoint",
            summary=summary,
            payload=payload,
        )

    async def _persist_completion_update(self) -> None:
        """Persist an ``autonomous_updates`` row summarising the terminal state."""
        state = self._state
        if state is None:
            return
        status = state.status
        steps_completed = len(state.completed_step_ids)
        goal = (state.metadata.get("goal", "") or "").strip()
        goal_short = (goal[:80] + "…") if len(goal) > 80 else goal
        if status == "completed":
            verb = "finished"
        elif status == "failed":
            verb = "failed"
        elif status == "cancelled":
            verb = "cancelled"
        else:
            verb = status
        summary = f"Background task {verb} after {steps_completed} step(s)"
        if goal_short:
            summary = f"{summary} — {goal_short}"
        payload = {
            "status": status,
            "steps_completed": steps_completed,
            "elapsed_seconds": state.elapsed_seconds,
            "request_count": state.request_count,
            "token_estimate": state.token_estimate,
            "cost_estimate": state.cost_estimate,
            "completion_summary": state.metadata.get("completion_summary"),
            "failure_reason": state.metadata.get("failure_reason"),
        }
        await self._insert_update_row(
            update_type="completion",
            summary=summary,
            payload=payload,
        )

    async def _insert_update_row(
        self,
        *,
        update_type: str,
        summary: str,
        payload: dict[str, Any],
    ) -> None:
        """Insert a row into ``autonomous_updates`` (best-effort)."""
        state = self._state
        if state is None:
            return
        try:
            async with aiosqlite.connect(str(self._db_path)) as db:
                await db.execute(
                    """
                    INSERT INTO autonomous_updates
                        (session_id, plan_id, update_type, summary, payload, delivered, created_at)
                    VALUES (?, ?, ?, ?, ?, 0, ?)
                    """,
                    (
                        self._session_id,
                        state.plan_id,
                        update_type,
                        summary,
                        json.dumps(payload),
                        datetime.now(UTC).isoformat(),
                    ),
                )
                await db.commit()
        except Exception as exc:
            log.warning(
                "autonomous_update_persist_failed",
                session_id=self._session_id,
                update_type=update_type,
                error=str(exc),
            )

    async def _emit_checkpoint_event(self) -> None:
        """Emit AUTONOMOUS_CHECKPOINT on the container's event emitter."""
        emitter = getattr(self._container, "event_emitter", None)
        if emitter is None:
            return
        try:
            from kora_v2.core.events import EventType

            state = self._state
            await emitter.emit(
                EventType.AUTONOMOUS_CHECKPOINT,
                session_id=self._session_id,
                plan_id=state.plan_id if state else None,
                elapsed_seconds=state.elapsed_seconds if state else 0,
                steps_completed=len(state.completed_step_ids) if state else 0,
            )
        except Exception as exc:
            log.debug(
                "autonomous_checkpoint_emit_failed",
                session_id=self._session_id,
                error=str(exc),
            )

    async def _emit_failed_event(self, reason: str) -> None:
        """Emit AUTONOMOUS_FAILED on the container's event emitter."""
        emitter = getattr(self._container, "event_emitter", None)
        if emitter is None:
            return
        try:
            from kora_v2.core.events import EventType

            await emitter.emit(
                EventType.AUTONOMOUS_FAILED,
                session_id=self._session_id,
                goal=self._goal[:200] if self._goal else "",
                reason=reason,
            )
        except Exception as exc:
            log.debug(
                "autonomous_failed_emit_failed",
                session_id=self._session_id,
                error=str(exc),
            )

    async def _emit_complete_event(self) -> None:
        """Emit AUTONOMOUS_COMPLETE on the container's event emitter."""
        emitter = getattr(self._container, "event_emitter", None)
        if emitter is None:
            return
        try:
            from kora_v2.core.events import EventType

            state = self._state
            await emitter.emit(
                EventType.AUTONOMOUS_COMPLETE,
                session_id=self._session_id,
                plan_id=state.plan_id if state else None,
                status=state.status if state else "unknown",
            )
        except Exception as exc:
            log.debug(
                "autonomous_complete_emit_failed",
                session_id=self._session_id,
                error=str(exc),
            )

    # ── Node Dispatch ──────────────────────────────────────────────────

    async def _run_node(self, node_name: str) -> AutonomousState:
        """Dispatch to the appropriate graph node function."""
        state = self._state
        assert state is not None

        log.debug(
            "autonomous_run_node",
            node=node_name,
            status=state.status,
            session_id=state.session_id,
        )

        if node_name == "plan":
            return await graph_nodes.plan(state, self._container)

        if node_name == "persist_plan":
            return await graph_nodes.persist_plan(state, self._db_path)

        if node_name == "execute_step":
            result = await graph_nodes.execute_step(
                state, self._container, db_path=self._db_path
            )
            # Persist budget counters after each step execution
            self._state = result
            await self._update_plan_budget()
            return result

        if node_name == "review_step":
            return await graph_nodes.review_step(state, self._container)

        if node_name == "checkpoint":
            self._last_checkpoint_at = time.monotonic()
            result = await graph_nodes.checkpoint(
                state, self._checkpoint_mgr, reason="node_triggered"
            )
            self._state = result
            await self._persist_checkpoint_update(reason="node_triggered")
            await self._emit_checkpoint_event()
            return result

        if node_name == "reflect":
            updated_state, next_action = graph_nodes.reflect(state)
            return await self._handle_reflect_action(updated_state, next_action)

        if node_name == "replan":
            reason = state.metadata.get("failure_reason", "quality drift")
            return await graph_nodes.replan(state, self._container, reason)

        if node_name == "complete":
            return await graph_nodes.complete(state, db_path=self._db_path)

        if node_name == "failed":
            reason = state.metadata.get("failure_reason", "Unknown failure")
            return await graph_nodes.failed(state, reason, db_path=self._db_path)

        if node_name == "paused_for_overlap":
            updated = graph_nodes.paused_for_overlap(state)
            # Checkpoint at safe boundary, then restore status so _should_stop
            # exits the loop cleanly instead of cycling through reflect again.
            checkpointed = await graph_nodes.checkpoint(
                updated, self._checkpoint_mgr, reason="overlap"
            )
            result = checkpointed.model_copy(deep=True)
            result.status = "paused_for_overlap"
            return result

        log.warning("autonomous_unknown_node", node=node_name)
        return state

    # ── Reflect Action Handler ─────────────────────────────────────────

    async def _handle_reflect_action(
        self, state: AutonomousState, next_action: str
    ) -> AutonomousState:
        """Route the reflection decision to the appropriate action.

        Args:
            state: State returned by reflect().
            next_action: Action string from reflect().

        Returns:
            Updated state after executing the reflect action.
        """
        if next_action == "complete":
            return await graph_nodes.complete(state, db_path=self._db_path)

        if next_action == "paused_for_overlap":
            updated = graph_nodes.paused_for_overlap(state)
            checkpointed = await graph_nodes.checkpoint(
                updated, self._checkpoint_mgr, reason="overlap"
            )
            # Restore status so _should_stop() exits the loop instead of
            # cycling back through checkpointing → reflect → paused_for_overlap.
            result = checkpointed.model_copy(deep=True)
            result.status = "paused_for_overlap"
            return result

        if next_action == "decision_request":
            # Generic branch decision — can be extended per use case
            updated, decision = graph_nodes.decision_request(
                state,
                decision_manager=self._decision_mgr,
                options=["continue", "cancel"],
                recommendation="continue",
                policy="auto_select",
                timeout_minutes=10,
            )
            self._state = updated
            await self._handle_decision_wait()
            return self._state

        if next_action == "replan":
            reason = state.latest_reflection or "quality drift"
            return await graph_nodes.replan(state, self._container, reason)

        if next_action != "continue":
            log.warning(
                "reflect_unknown_action",
                action=next_action,
                session_id=self._session_id,
            )

        # next_action == "continue" (or unrecognised — default to continue)
        # Update status so route_next_node maps to execute_step
        state = state.model_copy(deep=True)
        state.status = "planned"
        return state

    # ── Decision Wait ──────────────────────────────────────────────────

    async def _handle_decision_wait(self) -> None:
        """Poll pending decisions until resolved or timed out."""
        if self._state is None:
            return

        decision_ids = list(self._state.decision_queue)
        if not decision_ids:
            # No decisions — clear waiting state
            if self._state.status == "waiting_on_user":
                self._state = self._state.model_copy(deep=True)
                self._state.status = "planned"
            return

        decision_id = decision_ids[0]
        pending = self._decision_mgr.get_pending(decision_id)
        if pending is None:
            # Already resolved
            self._state = self._state.model_copy(deep=True)
            queue = [d for d in self._state.decision_queue if d != decision_id]
            self._state.decision_queue = queue
            if not queue:
                self._state.status = "planned"
            return

        # Check for timeout resolution
        result = self._decision_mgr.check_timeout(pending)
        if result is not None:
            log.info(
                "autonomous_decision_resolved",
                decision_id=decision_id,
                chosen=result.chosen,
                method=result.method,
            )
            self._state = self._state.model_copy(deep=True)
            queue = [d for d in self._state.decision_queue if d != decision_id]
            self._state.decision_queue = queue
            self._state.metadata[f"decision_{decision_id}"] = result.model_dump()
            if not queue:
                self._state.status = "planned"
            return

        # Still waiting — sleep briefly then return (loop will come back)
        await asyncio.sleep(_POLL_INTERVAL)

    # ── Internal Helpers ───────────────────────────────────────────────

    def _should_stop(self) -> bool:
        """Return True if the loop should exit immediately."""
        if self._state is None:
            return False
        if self._state.status in graph_nodes.TERMINAL_STATUSES:
            return True
        # paused_for_overlap is a soft-terminal state: loop exits but session
        # is resumable via resume_from_checkpoint().
        return self._state.status == "paused_for_overlap"

    def _should_checkpoint(self) -> bool:
        """Return True if the periodic checkpoint interval has elapsed."""
        elapsed = time.monotonic() - self._last_checkpoint_at
        return elapsed >= self._checkpoint_interval_seconds

    def _update_elapsed(self) -> None:
        """Update elapsed_seconds on the current state."""
        if self._state is None:
            return
        total = int(time.monotonic() - self._wall_start)
        if self._state.elapsed_seconds != total:
            self._state = self._state.model_copy(deep=True)
            self._state.elapsed_seconds = total


# ══════════════════════════════════════════════════════════════════════════
# Resume from checkpoint
# ══════════════════════════════════════════════════════════════════════════


async def resume_from_checkpoint(
    session_id: str,
    container: Any,
    db_path: Path,
) -> AutonomousExecutionLoop | None:
    """Attempt to resume a paused autonomous session from its latest checkpoint.

    Args:
        session_id: Session ID to look up.
        container: DI container.
        db_path: Path to operational.db.

    Returns:
        A new AutonomousExecutionLoop pre-loaded from checkpoint, or None
        if no checkpoint is found for the session.
    """
    checkpoint_mgr = CheckpointManager(db_path)
    checkpoint = await checkpoint_mgr.load_latest(session_id)

    if checkpoint is None:
        log.info("autonomous_no_checkpoint_found", session_id=session_id)
        return None

    state = checkpoint.state
    if state.status in graph_nodes.TERMINAL_STATUSES:
        log.info(
            "autonomous_checkpoint_terminal",
            session_id=session_id,
            status=state.status,
        )
        return None

    # Build a loop and inject the restored state
    settings = getattr(container, "settings", None)
    auto_settings = getattr(settings, "autonomous", None)

    loop = AutonomousExecutionLoop(
        goal=state.metadata.get("goal", ""),
        session_id=session_id,
        container=container,
        db_path=db_path,
        checkpoint_interval_minutes=getattr(
            auto_settings, "checkpoint_interval_minutes", 30
        ),
        auto_continue_seconds=getattr(auto_settings, "auto_continue_seconds", 30),
    )
    loop._state = state
    loop._wall_start = time.monotonic() - state.elapsed_seconds

    log.info(
        "autonomous_resumed_from_checkpoint",
        session_id=session_id,
        checkpoint_id=checkpoint.checkpoint_id,
        status=state.status,
        steps_remaining=len(state.pending_step_ids),
    )
    return loop
