"""Dispatcher — spec §5.

The dispatcher is the engine's scheduling loop. Every tick it:

    1. Computes the current :class:`SystemStatePhase`
    2. Loads the ready set (non-terminal, non-paused, dependencies met)
    3. Filters by allowed phase and budget availability
    4. Orders by priority (conversation > notification > background)
    5. Calls each task's step function with a fresh :class:`StepContext`
    6. Applies the returned :class:`StepResult` to update task state
    7. Emits ledger rows for every transition

This slice implements enough of the loop to satisfy acceptance items
12, 24, 32, 33 and 45. It deliberately does *not* yet wire in working
docs, notifications, or topic-overlap detection — those land in 7.5b.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from kora_v2.runtime.orchestration.checkpointing import CheckpointStore
from kora_v2.runtime.orchestration.ledger import LedgerEventType, WorkLedger
from kora_v2.runtime.orchestration.limiter import RequestLimiter
from kora_v2.runtime.orchestration.registry import (
    PipelineInstanceRegistry,
    WorkerTaskRegistry,
)
from kora_v2.runtime.orchestration.system_state import (
    SystemStateMachine,
    SystemStatePhase,
)
from kora_v2.runtime.orchestration.worker_task import (
    RequestClass,
    StepContext,
    StepResult,
    WorkerTask,
    WorkerTaskState,
)

if TYPE_CHECKING:
    from kora_v2.core.events import EventEmitter

log = structlog.get_logger(__name__)


# Priority ordering: lower number = higher priority
_REQUEST_CLASS_PRIORITY: dict[RequestClass, int] = {
    RequestClass.CONVERSATION: 0,
    RequestClass.NOTIFICATION: 1,
    RequestClass.BACKGROUND: 2,
}

# Fairness boost thresholds — spec §7.3 rule 3. A pending task that has
# been waiting longer than its class's threshold is bumped one rank up
# so it does not starve under steady pressure from same-class siblings.
FAIRNESS_THRESHOLD_BACKGROUND_SECONDS = 300
FAIRNESS_THRESHOLD_IN_TURN_SECONDS = 30


class Dispatcher:
    """Single-loop scheduler for :class:`WorkerTask` execution."""

    def __init__(
        self,
        *,
        db_path: Path,
        task_registry: WorkerTaskRegistry,
        instance_registry: PipelineInstanceRegistry,
        limiter: RequestLimiter,
        ledger: WorkLedger,
        checkpoint_store: CheckpointStore,
        state_machine: SystemStateMachine,
        event_emitter: EventEmitter | None = None,
        tick_interval: float = 0.5,
    ) -> None:
        self._db_path = db_path
        self._task_registry = task_registry
        self._instance_registry = instance_registry
        self._limiter = limiter
        self._ledger = ledger
        self._checkpoint_store = checkpoint_store
        self._state_machine = state_machine
        self._emitter = event_emitter
        self._tick_interval = tick_interval

        self._live_tasks: dict[str, WorkerTask] = {}
        self._running = False
        self._stop_event = asyncio.Event()
        self._loop_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    # ── Lifecycle ────────────────────────────────────────────────

    async def start(self) -> None:
        """Rehydrate state from SQL and start the dispatch loop.

        Crash-recovery (spec §7.6 step 4): any task that was in a
        non-terminal *active* state (RUNNING, PLANNING, CHECKPOINTING)
        when the previous process exited gets transitioned to
        ``paused_for_state`` with reason ``crash_recovery`` so it
        re-enters the dispatch loop cleanly on the next tick. Tasks
        that were already in a paused state are left as-is.
        """
        if self._running:
            return
        await self._limiter.replay_from_log()
        rehydrated = await self._task_registry.load_all_non_terminal()
        now = datetime.now(UTC)
        recovered = 0
        async with self._lock:
            for task in rehydrated:
                self._live_tasks[task.id] = task
        for task in rehydrated:
            if task.state in (
                WorkerTaskState.RUNNING,
                WorkerTaskState.PLANNING,
                WorkerTaskState.CHECKPOINTING,
            ):
                await self._transition(
                    task,
                    WorkerTaskState.PAUSED_FOR_STATE,
                    reason="crash_recovery",
                    now=now,
                )
                recovered += 1
        log.debug(
            "dispatcher_started",
            rehydrated=len(rehydrated),
            crash_recovered=recovered,
        )
        self._running = True
        self._stop_event.clear()
        self._loop_task = asyncio.create_task(self._run_loop())

    async def stop(self, *, graceful: bool = False) -> None:
        if not self._running:
            return
        self._running = False
        self._stop_event.set()
        if self._loop_task is not None:
            try:
                await asyncio.wait_for(self._loop_task, timeout=5.0)
            except TimeoutError:
                self._loop_task.cancel()
        self._loop_task = None
        log.debug("dispatcher_stopped", graceful=graceful)

    # ── Task registration ────────────────────────────────────────

    async def submit(self, task: WorkerTask) -> None:
        """Add *task* to the dispatcher's ready set and persist it."""
        async with self._lock:
            self._live_tasks[task.id] = task
        await self._task_registry.save(task)
        await self._ledger.record(
            LedgerEventType.TASK_CREATED,
            pipeline_instance_id=task.pipeline_instance_id,
            worker_task_id=task.id,
            reason=task.config.preset,
            metadata={"stage": task.stage_name, "goal": task.goal},
        )

    async def request_cancellation(self, task_id: str) -> bool:
        async with self._lock:
            task = self._live_tasks.get(task_id)
            if task is None or not task.config.can_be_cancelled:
                return False
            task.request_cancellation()
        await self._ledger.record(
            LedgerEventType.TASK_PAUSED,
            worker_task_id=task_id,
            reason="cancellation_requested",
        )
        return True

    def live_task(self, task_id: str) -> WorkerTask | None:
        return self._live_tasks.get(task_id)

    def live_tasks(self) -> list[WorkerTask]:
        return list(self._live_tasks.values())

    # ── Single-step helpers (used by engine.run_task / tests) ────

    async def tick_once(self) -> int:
        """Run one dispatch pass and return the number of tasks stepped."""
        return await self._dispatch_pass()

    # ── Internal loop ────────────────────────────────────────────

    async def _run_loop(self) -> None:
        try:
            while self._running:
                try:
                    await self._dispatch_pass()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    log.exception("dispatcher_tick_failed", error=str(exc))
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self._tick_interval,
                    )
                except TimeoutError:
                    pass
        except asyncio.CancelledError:
            log.debug("dispatcher_loop_cancelled")
            raise

    async def _dispatch_pass(self) -> int:
        now = datetime.now(UTC)
        # Publish state transitions on every tick so subscribers
        # (including the dispatcher's own pause-on-conversation logic)
        # see them. publish_if_changed() also writes the
        # system_state_log audit row — see spec §6.3.
        phase = await self._state_machine.publish_if_changed(
            now, reason="dispatcher_tick"
        )

        async with self._lock:
            candidates = list(self._live_tasks.values())

        # Spec §7.5: react to phase transitions before scheduling.
        # Any running task that no longer fits the current phase (or
        # whose pause_on_conversation flag fires) gets parked here so
        # it does not even enter the ready set.
        await self._react_to_phase(candidates, phase, now)

        async with self._lock:
            candidates = list(self._live_tasks.values())

        ready = [t for t in candidates if self._is_ready(t, phase)]
        ready.sort(key=lambda t: _task_priority(t, now))

        stepped = 0
        for task in ready:
            if await self._step_one(task, phase, now):
                stepped += 1
        return stepped

    async def _react_to_phase(
        self,
        tasks: list[WorkerTask],
        phase: SystemStatePhase,
        now: datetime,
    ) -> None:
        """Pause any running task that the current phase has displaced.

        Implements the dispatcher half of spec §7.5: a CONVERSATION
        phase against a task with ``pause_on_conversation=True`` moves
        the task to ``paused_for_state`` before the ready set is
        computed. Only RUNNING tasks need this treatment — PENDING
        tasks are gated by ``_is_ready`` and cannot start a step in a
        disallowed phase, so they stay PENDING until the phase moves
        back. Already-terminal and already-paused tasks are skipped.
        """
        for task in tasks:
            if task.is_terminal() or task.is_paused():
                continue
            if task.state is not WorkerTaskState.RUNNING:
                continue

            should_pause_for_conversation = (
                phase is SystemStatePhase.CONVERSATION
                and task.config.pause_on_conversation
            )
            should_pause_for_phase = phase not in task.config.allowed_states

            if not (should_pause_for_conversation or should_pause_for_phase):
                continue

            reason = (
                "conversation_began"
                if should_pause_for_conversation
                else f"phase_disallowed:{phase.value}"
            )
            await self._transition(
                task,
                WorkerTaskState.PAUSED_FOR_STATE,
                reason=reason,
                now=now,
            )
            if should_pause_for_conversation and self._emitter is not None:
                from kora_v2.core.events import EventType  # local import — avoid cycle

                try:
                    await self._emitter.emit(
                        EventType.TASK_CHECKPOINTED,
                        task_id=task.id,
                        pipeline_instance_id=task.pipeline_instance_id,
                        reason=reason,
                    )
                except Exception:
                    log.exception("event_emit_failed", task_id=task.id)

    def _is_ready(self, task: WorkerTask, phase: SystemStatePhase) -> bool:
        if task.is_terminal() or task.is_paused():
            return False
        if task.cancellation_requested:
            return True  # let _step_one transition it
        if task.state not in (WorkerTaskState.PENDING, WorkerTaskState.RUNNING, WorkerTaskState.PLANNING):
            return False
        if phase not in task.config.allowed_states:
            return False
        return True

    async def _step_one(
        self,
        task: WorkerTask,
        phase: SystemStatePhase,
        now: datetime,
    ) -> bool:
        """Drive one step of *task* and persist the result. Returns True if stepped."""
        if task.cancellation_requested:
            await self._transition(
                task,
                WorkerTaskState.CANCELLED,
                reason="cancellation_requested",
                now=now,
            )
            return False

        # Per spec §7.5: a task with pause_on_conversation=True must be
        # paused as soon as the phase becomes CONVERSATION, regardless
        # of where it is in its step lifecycle. Re-check the phase
        # here in case it changed between ready-set computation and the
        # actual step invocation.
        if (
            phase is SystemStatePhase.CONVERSATION
            and task.config.pause_on_conversation
            and task.state is not WorkerTaskState.PAUSED_FOR_STATE
        ):
            await self._transition(
                task,
                WorkerTaskState.PAUSED_FOR_STATE,
                reason="conversation_began",
                now=now,
            )
            # Cooperative-pause checkpoint signal — subscribers care.
            if self._emitter is not None:
                from kora_v2.core.events import EventType  # local import — avoid cycle

                try:
                    await self._emitter.emit(
                        EventType.TASK_CHECKPOINTED,
                        task_id=task.id,
                        pipeline_instance_id=task.pipeline_instance_id,
                        reason="conversation_began",
                    )
                except Exception:
                    log.exception("event_emit_failed", task_id=task.id)
            # Step function was NOT called; the task is now parked.
            return False

        # Re-check allowed_states defensively — _is_ready already
        # filters on this, but the phase may have moved between that
        # check and now.
        if phase not in task.config.allowed_states:
            await self._transition(
                task,
                WorkerTaskState.PAUSED_FOR_STATE,
                reason=f"phase_disallowed:{phase.value}",
                now=now,
            )
            return False

        # Budget gate (duration)
        if self._duration_exceeded(task, now):
            await self._transition(
                task,
                WorkerTaskState.FAILED,
                error_message="max_duration_exceeded",
                reason="duration_budget",
                now=now,
            )
            return False

        # Budget gate (request count)
        if task.config.max_requests is not None and task.request_count >= task.config.max_requests:
            await self._transition(
                task,
                WorkerTaskState.FAILED,
                error_message="max_requests_exceeded",
                reason="request_budget",
                now=now,
            )
            return False

        # Rate limiter — every step counts as one request for budget purposes.
        acquired = await self._limiter.acquire(
            task.config.request_class,
            worker_task_id=task.id,
        )
        if not acquired:
            # Don't touch task state — just record and move on.
            await self._ledger.record(
                LedgerEventType.RATE_LIMIT_REJECTED,
                worker_task_id=task.id,
                reason=task.config.request_class.value,
            )
            return False

        if task.step_fn is None:
            await self._transition(
                task,
                WorkerTaskState.FAILED,
                error_message="step_fn_missing",
                reason="programmer_error",
                now=now,
            )
            return False

        # Running transition (first time only)
        if task.state in (WorkerTaskState.PENDING, WorkerTaskState.PLANNING):
            await self._transition(task, WorkerTaskState.RUNNING, reason="first_step", now=now)

        task.last_step_at = now
        context = StepContext(
            task=task,
            limiter=self._limiter,
            cancellation_flag=lambda: task.cancellation_requested,
            now=lambda: datetime.now(UTC),
            extras={"phase": phase.value},
        )

        try:
            result: StepResult = await task.step_fn(task, context)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("step_fn_raised", task_id=task.id, error=str(exc))
            await self._transition(
                task,
                WorkerTaskState.FAILED,
                error_message=str(exc),
                reason="step_exception",
                now=now,
            )
            return True

        task.apply_step_result(result)
        await self._handle_result(task, result, now)
        await self._task_registry.save(task)
        return True

    async def _handle_result(
        self,
        task: WorkerTask,
        result: StepResult,
        now: datetime,
    ) -> None:
        outcome = result.outcome
        if outcome == "continue":
            await self._ledger.record(
                LedgerEventType.TASK_PROGRESS,
                worker_task_id=task.id,
                pipeline_instance_id=task.pipeline_instance_id,
                reason="step_continue",
                metadata={"artifacts": result.artifacts, "marker": result.progress_marker},
            )
            return

        if outcome == "complete":
            task.result_summary = result.result_summary
            task.completed_at = now
            await self._transition(
                task,
                WorkerTaskState.COMPLETED,
                reason="step_complete",
                result_summary=result.result_summary,
                now=now,
            )
            return

        if outcome == "failed":
            task.error_message = result.error_message
            await self._transition(
                task,
                WorkerTaskState.FAILED,
                error_message=result.error_message or "step_failed",
                reason="step_failed",
                now=now,
            )
            return

        # One of the four paused outcomes
        paused_map = {
            "paused_for_state": WorkerTaskState.PAUSED_FOR_STATE,
            "paused_for_rate_limit": WorkerTaskState.PAUSED_FOR_RATE_LIMIT,
            "paused_for_decision": WorkerTaskState.PAUSED_FOR_DECISION,
            "paused_for_dependency": WorkerTaskState.PAUSED_FOR_DEPENDENCY,
        }
        target = paused_map.get(outcome)
        if target is None:
            log.warning("unknown_step_outcome", outcome=outcome, task_id=task.id)
            return
        await self._transition(
            task,
            target,
            reason=f"step_{outcome}",
            now=now,
        )

    async def _transition(
        self,
        task: WorkerTask,
        state: WorkerTaskState,
        *,
        reason: str,
        now: datetime,
        error_message: str | None = None,
        result_summary: str | None = None,
    ) -> None:
        previous = task.state
        task.state = state
        if state in (WorkerTaskState.COMPLETED, WorkerTaskState.FAILED, WorkerTaskState.CANCELLED):
            task.completed_at = now
            async with self._lock:
                self._live_tasks.pop(task.id, None)
        await self._task_registry.update_state(
            task.id,
            state,
            error_message=error_message,
            result_summary=result_summary,
        )
        event_type = _state_to_ledger_event(state)
        await self._ledger.record(
            event_type,
            pipeline_instance_id=task.pipeline_instance_id,
            worker_task_id=task.id,
            reason=reason,
            metadata={
                "from": previous.value,
                "to": state.value,
                "error": error_message,
            },
        )
        if self._emitter is not None:
            await self._emit_transition(state, task, reason)

    async def _emit_transition(
        self,
        state: WorkerTaskState,
        task: WorkerTask,
        reason: str,
    ) -> None:
        from kora_v2.core.events import EventType  # local import — avoid cycle

        event_map = {
            WorkerTaskState.COMPLETED: EventType.TASK_COMPLETED,
            WorkerTaskState.FAILED: EventType.TASK_FAILED,
            WorkerTaskState.CHECKPOINTING: EventType.TASK_CHECKPOINTED,
        }
        event_type = event_map.get(state)
        if event_type is None:
            return
        try:
            await self._emitter.emit(  # type: ignore[union-attr]
                event_type,
                task_id=task.id,
                pipeline_instance_id=task.pipeline_instance_id,
                reason=reason,
            )
        except Exception:
            log.exception("event_emit_failed", task_id=task.id, state=state.value)

    def _duration_exceeded(self, task: WorkerTask, now: datetime) -> bool:
        if task.config.max_duration_seconds <= 0:
            return False
        elapsed = (now - task.created_at).total_seconds()
        return elapsed >= task.config.max_duration_seconds


def _task_priority(task: WorkerTask, now: datetime) -> tuple[int, datetime]:
    """Compute the sort key for *task* (lower = higher priority).

    Implements spec §7.3 rules 1, 3, and 5: hard request-class
    priority, fairness boost for pending tasks past the starvation
    threshold, and creation-time tiebreaker. Rules 2 (dependency depth)
    and 4 (cheapest-first under pressure) land with 7.5b alongside the
    full pipeline reconciliation pass.
    """
    base_priority = _REQUEST_CLASS_PRIORITY.get(task.config.request_class, 99)

    # Fairness boost for tasks that have been waiting too long.
    if task.config.request_class is RequestClass.CONVERSATION:
        threshold = FAIRNESS_THRESHOLD_IN_TURN_SECONDS
    else:
        threshold = FAIRNESS_THRESHOLD_BACKGROUND_SECONDS
    age = (now - task.created_at).total_seconds()
    if age > threshold:
        base_priority -= 1

    return (base_priority, task.created_at)


def _state_to_ledger_event(state: WorkerTaskState) -> str:
    return {
        WorkerTaskState.PENDING: LedgerEventType.TASK_CREATED,
        WorkerTaskState.PLANNING: LedgerEventType.TASK_STARTED,
        WorkerTaskState.RUNNING: LedgerEventType.TASK_STARTED,
        WorkerTaskState.CHECKPOINTING: LedgerEventType.TASK_CHECKPOINTED,
        WorkerTaskState.PAUSED_FOR_STATE: LedgerEventType.TASK_PAUSED,
        WorkerTaskState.PAUSED_FOR_RATE_LIMIT: LedgerEventType.TASK_PAUSED,
        WorkerTaskState.PAUSED_FOR_DECISION: LedgerEventType.TASK_PAUSED,
        WorkerTaskState.PAUSED_FOR_DEPENDENCY: LedgerEventType.TASK_PAUSED,
        WorkerTaskState.COMPLETED: LedgerEventType.TASK_COMPLETED,
        WorkerTaskState.FAILED: LedgerEventType.TASK_FAILED,
        WorkerTaskState.CANCELLED: LedgerEventType.TASK_CANCELLED,
    }[state]
