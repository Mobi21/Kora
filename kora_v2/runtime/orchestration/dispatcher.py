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
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from kora_v2.runtime.orchestration.checkpointing import CheckpointStore
from kora_v2.runtime.orchestration.ledger import LedgerEventType, WorkLedger
from kora_v2.runtime.orchestration.limiter import RequestLimiter
from kora_v2.runtime.orchestration.pipeline import PipelineInstanceState
from kora_v2.runtime.orchestration.registry import (
    PipelineInstanceRegistry,
    WorkerTaskRegistry,
)
from kora_v2.runtime.orchestration.system_state import (
    SystemStateMachine,
    SystemStatePhase,
)
from kora_v2.runtime.orchestration.worker_task import (
    TERMINAL_STATES,
    Checkpoint,
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

# These pipelines are background work, but they are also the durable
# finalization path for a conversation. Keep them behind foreground
# conversation traffic while letting them outrank routine background
# maintenance under a compressed or saturated budget.
PROTECTED_FINALIZATION_PIPELINES = frozenset(
    {"post_session_memory", "post_memory_vault"}
)

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
        pipeline_terminal_hook: (
            Callable[[str, PipelineInstanceState, str], Awaitable[None]] | None
        ) = None,
        adaptive_reconcile_hook: (
            Callable[[], Awaitable[None]] | None
        ) = None,
        step_fn_resolver: (
            Callable[
                [WorkerTask],
                Awaitable[
                    Callable[[WorkerTask, StepContext], Awaitable[StepResult]]
                    | None
                ],
            ]
            | None
        ) = None,
        rate_limit_hook: Callable[[WorkerTask], Awaitable[None]] | None = None,
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
        self._step_fn_resolver = step_fn_resolver
        self._pipeline_terminal_hook = pipeline_terminal_hook
        self._adaptive_reconcile_hook = adaptive_reconcile_hook
        self._rate_limit_hook = rate_limit_hook

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

        # Orphan pipeline sweep: any pipeline_instance still marked
        # ``running``/``pending``/``paused`` whose sibling tasks are
        # all terminal (or do not exist at all) is a zombie from a
        # previous daemon crash. Reconcile it so subsequent dispatches
        # of the same pipeline are not blocked and the DB doesn't grow
        # an unbounded set of permanent in-flight rows.
        pipeline_orphans = 0
        try:
            active_instances = await self._instance_registry.load_active()
            live_task_ids = {t.id for t in rehydrated}
            for instance in active_instances:
                sibling_tasks = await self._task_registry.load_by_pipeline(
                    instance.id
                )
                if not sibling_tasks:
                    # No tasks at all — either never created or wiped.
                    # ``_reconcile_pipeline_instance`` bails on empty
                    # siblings, so force the transition ourselves:
                    # mark as cancelled so the row stops blocking new
                    # dispatches and the ledger has an explicit record.
                    instance.state = PipelineInstanceState.CANCELLED
                    instance.completed_at = now
                    instance.completion_reason = "orphaned_no_tasks"
                    await self._instance_registry.save(instance)
                    await self._ledger.record(
                        LedgerEventType.PIPELINE_COMPLETED,
                        pipeline_instance_id=instance.id,
                        reason="orphaned_no_tasks",
                        metadata={"final_state": "cancelled"},
                    )
                    log.info(
                        "pipeline_orphan_cancelled",
                        pipeline_instance_id=instance.id,
                        pipeline_name=instance.pipeline_name,
                    )
                    if self._pipeline_terminal_hook is not None:
                        try:
                            await self._pipeline_terminal_hook(
                                instance.id,
                                PipelineInstanceState.CANCELLED,
                                "orphaned_no_tasks",
                            )
                        except Exception:  # noqa: BLE001
                            log.exception(
                                "pipeline_terminal_hook_failed_orphan",
                                pipeline_instance_id=instance.id,
                            )
                    pipeline_orphans += 1
                    continue
                all_terminal = all(
                    t.state in TERMINAL_STATES for t in sibling_tasks
                )
                any_failed = any(
                    t.state is WorkerTaskState.FAILED for t in sibling_tasks
                )
                any_rehydrated = any(
                    t.id in live_task_ids for t in sibling_tasks
                )
                if any_failed:
                    # A failed stage under FAIL_PIPELINE can leave
                    # downstream dependency tasks pending forever. Cancel
                    # those blocked siblings and reconcile so restart
                    # recovery does not preserve zombie pipelines.
                    await self._cancel_blocked_siblings_after_failure(
                        instance.id, now
                    )
                    await self._reconcile_pipeline_instance(instance.id, now)
                    pipeline_orphans += 1
                    continue
                if all_terminal and not any_rehydrated:
                    # Every task finished before the previous process
                    # exited but reconcile never ran. Do it now.
                    await self._reconcile_pipeline_instance(instance.id, now)
                    pipeline_orphans += 1
        except Exception:  # noqa: BLE001
            log.exception("pipeline_orphan_sweep_failed")

        log.debug(
            "dispatcher_started",
            rehydrated=len(rehydrated),
            crash_recovered=recovered,
            pipeline_orphans_reconciled=pipeline_orphans,
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
        immediate_cancel = False
        task: WorkerTask | None = None
        async with self._lock:
            task = self._live_tasks.get(task_id)
            if task is None:
                task = await self._task_registry.load(task_id)
                if task is None or task.state in TERMINAL_STATES:
                    return False
                self._live_tasks[task_id] = task
            if not task.config.can_be_cancelled:
                return False
            task.request_cancellation()
            await self._task_registry.save(task)
            immediate_cancel = task.state != WorkerTaskState.RUNNING
        if immediate_cancel:
            await self._transition(
                task,
                WorkerTaskState.CANCELLED,
                reason="cancellation_requested",
                now=datetime.now(UTC),
            )
            return True
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

        # Adaptive working-doc reconciliation runs *before* the ready
        # set is computed so newly-added ``- [ ]`` items become real
        # WorkerTasks on the same tick (spec §11.5). The hook mutates
        # the live task set directly via ``submit``/cancellation so we
        # re-read candidates below.
        if self._adaptive_reconcile_hook is not None:
            try:
                await self._adaptive_reconcile_hook()
            except Exception:  # noqa: BLE001
                log.exception("adaptive_reconcile_hook_failed")

        async with self._lock:
            candidates = list(self._live_tasks.values())

        await self._reactivate_paused_tasks(candidates, phase)

        async with self._lock:
            candidates = list(self._live_tasks.values())

        # Spec §7.5: react to phase transitions before scheduling.
        # Any running task that no longer fits the current phase (or
        # whose pause_on_conversation flag fires) gets parked here so
        # it does not even enter the ready set.
        await self._react_to_phase(candidates, phase, now)

        async with self._lock:
            candidates = list(self._live_tasks.values())

        ready = []
        for task in candidates:
            if not self._is_ready(task, phase):
                continue
            if not await self._dependencies_satisfied(task):
                continue
            ready.append(task)
        ready.sort(key=lambda t: _task_priority(t, now))

        stepped = 0
        for task in ready:
            if await self._step_one(task, phase, now):
                stepped += 1
        return stepped

    async def _submit_proposed_tasks(
        self,
        parent: WorkerTask,
        proposals: list[dict[str, Any]],
    ) -> None:
        """Create sibling WorkerTasks proposed by *parent*'s step result.

        Each entry must supply ``goal``/``stage_name`` at minimum. The
        new task inherits *parent*'s pipeline_instance_id and config
        unless the proposal overrides them. A step_fn entry is optional
        — if missing, the resolver at dispatch time handles it like any
        other rehydrated task.
        """
        import uuid as _uuid

        for proposal in proposals:
            try:
                goal = str(proposal.get("goal") or "").strip()
                if not goal:
                    continue
                stage_name = str(
                    proposal.get("stage_name")
                    or f"{parent.stage_name}_sub"
                )
                new_task = WorkerTask(
                    id=f"task-{_uuid.uuid4().hex[:12]}",
                    pipeline_instance_id=(
                        proposal.get("pipeline_instance_id")
                        or parent.pipeline_instance_id
                    ),
                    stage_name=stage_name,
                    config=parent.config,
                    goal=goal,
                    system_prompt=str(
                        proposal.get("system_prompt")
                        or parent.system_prompt
                    ),
                    parent_task_id=parent.id,
                    depends_on=list(proposal.get("depends_on") or []),
                    created_at=datetime.now(UTC),
                )
                new_task.step_fn = proposal.get("step_fn") or parent.step_fn
                await self.submit(new_task)
            except Exception:  # noqa: BLE001
                log.exception(
                    "proposed_task_submit_failed",
                    parent_task_id=parent.id,
                    proposal=proposal,
                )

    async def _resolve_rehydrated_step_fn(
        self, task: WorkerTask
    ) -> (
        Callable[[WorkerTask, StepContext], Awaitable[StepResult]] | None
    ):
        """Re-attach a step function to a rehydrated task.

        After a daemon restart the dispatcher rehydrates WorkerTasks
        from SQL, but ``step_fn`` is an in-memory only field so it is
        always ``None`` on rehydration. Without this resolver the next
        step attempt transitions the task to FAILED with
        ``step_fn_missing``, which for long-running user pipelines
        destroys in-progress work. Tasks whose step_fn cannot be
        resolved get paused (see caller) instead.
        """
        if self._step_fn_resolver is None:
            return None
        try:
            return await self._step_fn_resolver(task)
        except Exception:  # noqa: BLE001
            log.exception(
                "step_fn_resolver_failed",
                task_id=task.id,
                stage=task.stage_name,
            )
            return None

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
                phase == SystemStatePhase.CONVERSATION
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

    async def _reactivate_paused_tasks(
        self,
        tasks: list[WorkerTask],
        phase: SystemStatePhase,
    ) -> None:
        """Move recoverable paused tasks back into the ready set.

        Pausing is useful only if there is a deterministic wake path.
        Crash recovery and unresolved step-function pauses are both
        transient: after boot, the phase may be allowed again and the
        resolver may now know how to reattach a step function. Without
        this pass those rows remain live but unschedulable forever.
        """
        for task in tasks:
            if task.state is WorkerTaskState.PAUSED_FOR_DECISION:
                continue
            if task.state not in {
                WorkerTaskState.PAUSED_FOR_STATE,
                WorkerTaskState.PAUSED_FOR_RATE_LIMIT,
                WorkerTaskState.PAUSED_FOR_DEPENDENCY,
            }:
                continue
            if phase not in task.config.allowed_states:
                continue
            if (
                phase == SystemStatePhase.CONVERSATION
                and task.config.pause_on_conversation
            ):
                continue

            reason = ""
            if task.state is WorkerTaskState.PAUSED_FOR_DEPENDENCY:
                if task.step_fn is None:
                    resolved = await self._resolve_rehydrated_step_fn(task)
                    if resolved is None:
                        continue
                    task.step_fn = resolved
                    reason = "dependency_resolved:step_fn"
                else:
                    reason = "dependency_resolved"
            elif task.state is WorkerTaskState.PAUSED_FOR_RATE_LIMIT:
                snapshot = await self._limiter.snapshot()
                if snapshot.remaining_for(task.config.request_class) <= 0:
                    continue
                reason = "rate_limit_retry"
            else:
                reason = "state_resumed"

            await self._resume_task(task, reason=reason)

    async def _resume_task(
        self,
        task: WorkerTask,
        *,
        reason: str,
    ) -> None:
        previous = task.state
        task.state = WorkerTaskState.PENDING
        async with self._lock:
            self._live_tasks[task.id] = task
        await self._task_registry.update_state(task.id, WorkerTaskState.PENDING)
        await self._ledger.record(
            LedgerEventType.TASK_RESUMED,
            pipeline_instance_id=task.pipeline_instance_id,
            worker_task_id=task.id,
            reason=reason,
            metadata={"from": previous.value, "to": "pending"},
        )
        log.info(
            "worker_task_resumed",
            task_id=task.id,
            reason=reason,
        )

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

    async def _dependencies_satisfied(self, task: WorkerTask) -> bool:
        if not task.depends_on:
            return True
        if task.pipeline_instance_id is None:
            return False
        try:
            siblings = await self._task_registry.load_by_pipeline(
                task.pipeline_instance_id
            )
        except Exception:  # noqa: BLE001
            log.exception("task_dependency_load_failed", task_id=task.id)
            return False
        by_stage = {sibling.stage_name: sibling for sibling in siblings}
        for dep in task.depends_on:
            dep_task = by_stage.get(dep)
            if dep_task is None or dep_task.state is not WorkerTaskState.COMPLETED:
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
            phase == SystemStatePhase.CONVERSATION
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
            await self._ledger.record(
                LedgerEventType.RATE_LIMIT_REJECTED,
                pipeline_instance_id=task.pipeline_instance_id,
                worker_task_id=task.id,
                reason="rate_limit_paused",
                metadata={"request_class": task.config.request_class.value},
            )
            if self._rate_limit_hook is not None:
                try:
                    await self._rate_limit_hook(task)
                except Exception:  # noqa: BLE001
                    log.debug(
                        "rate_limit_hook_failed",
                        task_id=task.id,
                        exc_info=True,
                    )
            await self._transition(
                task,
                WorkerTaskState.PAUSED_FOR_RATE_LIMIT,
                reason="rate_limit_paused",
                now=now,
            )
            return False

        if task.step_fn is None:
            resolved = await self._resolve_rehydrated_step_fn(task)
            if resolved is not None:
                task.step_fn = resolved
                await self._ledger.record(
                    LedgerEventType.TASK_PROGRESS,
                    worker_task_id=task.id,
                    pipeline_instance_id=task.pipeline_instance_id,
                    reason="step_fn_rehydrated",
                    metadata={"stage": task.stage_name},
                )
            else:
                # Pause rather than fail: a step function may become
                # resolvable later (e.g. after a runtime pipeline is
                # re-registered) and we do not want to burn through a
                # user-owned pipeline just because the process restarted
                # before the resolver was wired in.
                await self._transition(
                    task,
                    WorkerTaskState.PAUSED_FOR_DEPENDENCY,
                    reason="step_fn_unresolved",
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
            checkpoint_callback=self._make_checkpoint_callback(task),
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

        result_now = datetime.now(UTC)
        task.apply_step_result(result)
        if task.cancellation_requested:
            await self._transition(
                task,
                WorkerTaskState.CANCELLED,
                reason="cancellation_requested",
                now=result_now,
            )
            await self._task_registry.save(task)
            return True
        await self._handle_result(task, result, result_now)
        await self._task_registry.save(task)
        return True

    async def _handle_result(
        self,
        task: WorkerTask,
        result: StepResult,
        now: datetime,
    ) -> None:
        # Step-proposed sibling tasks — the step function peeled off a
        # sub-task the supervisor should treat as a real WorkerTask (e.g.
        # an autonomous driver that decomposes its plan mid-step). The
        # entries carry the same shape as ``dispatch_task`` kwargs.
        if result.proposed_new_tasks:
            await self._submit_proposed_tasks(task, result.proposed_new_tasks)

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

        # Reconcile the owning pipeline instance when a task reaches a
        # terminal state. Spec §10 expects PIPELINE_COMPLETED /
        # PIPELINE_FAILED to fire once every sibling task is terminal,
        # and the pipeline_instances row to move out of ``running`` so
        # restart continuity, task surfacing, and audit queries all see
        # the same truth.
        if state in TERMINAL_STATES and task.pipeline_instance_id is not None:
            try:
                if state is WorkerTaskState.FAILED:
                    await self._cancel_blocked_siblings_after_failure(
                        task.pipeline_instance_id, now
                    )
                await self._reconcile_pipeline_instance(
                    task.pipeline_instance_id, now
                )
            except Exception:  # noqa: BLE001
                log.exception(
                    "pipeline_reconciliation_failed",
                    pipeline_instance_id=task.pipeline_instance_id,
                )

    async def _reconcile_pipeline_instance(
        self, instance_id: str, now: datetime
    ) -> None:
        """Mark the pipeline instance terminal when every sibling task is.

        Called from :meth:`_transition` after a task moves to
        COMPLETED/FAILED/CANCELLED. The instance inherits the worst
        sibling outcome (any failure → failed, all cancelled → cancelled,
        otherwise completed) and PIPELINE_COMPLETED / PIPELINE_FAILED is
        written to the ledger.
        """
        instance = await self._instance_registry.load(instance_id)
        if instance is None:
            return
        if instance.state in {
            PipelineInstanceState.COMPLETED,
            PipelineInstanceState.FAILED,
            PipelineInstanceState.CANCELLED,
        }:
            return

        tasks = await self._task_registry.load_by_pipeline(instance_id)
        if not tasks:
            return
        if any(t.state not in TERMINAL_STATES for t in tasks):
            return

        sibling_states = {t.state for t in tasks}
        if WorkerTaskState.FAILED in sibling_states:
            final_state = PipelineInstanceState.FAILED
            completion_reason = "task_failed"
            event_type = LedgerEventType.PIPELINE_FAILED
        elif WorkerTaskState.CANCELLED in sibling_states:
            final_state = PipelineInstanceState.CANCELLED
            completion_reason = "task_cancelled"
            event_type = LedgerEventType.PIPELINE_COMPLETED
        else:
            final_state = PipelineInstanceState.COMPLETED
            completion_reason = "tasks_complete"
            event_type = LedgerEventType.PIPELINE_COMPLETED

        instance.state = final_state
        instance.completed_at = now
        instance.completion_reason = completion_reason
        await self._instance_registry.save(instance)
        await self._ledger.record(
            event_type,
            pipeline_instance_id=instance_id,
            reason=completion_reason,
            metadata={
                "task_count": len(tasks),
                "final_state": final_state.value,
            },
        )
        log.info(
            "pipeline_instance_reconciled",
            pipeline_instance_id=instance_id,
            state=final_state.value,
            reason=completion_reason,
        )
        if self._pipeline_terminal_hook is not None:
            try:
                await self._pipeline_terminal_hook(
                    instance_id, final_state, completion_reason
                )
            except Exception:  # noqa: BLE001
                log.exception(
                    "pipeline_terminal_hook_failed",
                    pipeline_instance_id=instance_id,
                    state=final_state.value,
                )

    async def _cancel_blocked_siblings_after_failure(
        self, instance_id: str, now: datetime
    ) -> None:
        tasks = await self._task_registry.load_by_pipeline(instance_id)
        for sibling in tasks:
            if sibling.state in TERMINAL_STATES:
                continue
            previous = sibling.state
            sibling.state = WorkerTaskState.CANCELLED
            sibling.completed_at = now
            async with self._lock:
                self._live_tasks.pop(sibling.id, None)
            await self._task_registry.update_state(
                sibling.id,
                WorkerTaskState.CANCELLED,
                error_message="cancelled because a sibling stage failed",
            )
            await self._ledger.record(
                LedgerEventType.TASK_CANCELLED,
                pipeline_instance_id=instance_id,
                worker_task_id=sibling.id,
                reason="sibling_failed",
                metadata={
                    "from": previous.value,
                    "to": WorkerTaskState.CANCELLED.value,
                },
            )

    def _make_checkpoint_callback(
        self, task: WorkerTask
    ) -> Callable[[dict[str, Any]], Awaitable[None]]:
        """Build the checkpoint callback handed to step functions.

        Step functions call this via :attr:`StepContext.checkpoint_callback`
        to persist their ``scratch_state`` through the orchestration
        :class:`CheckpointStore`. Without this wiring, long-running tasks
        (e.g. the ``long_background`` autonomous preset with
        ``checkpoint_every_seconds=60``) never write
        ``worker_tasks.checkpoint_blob`` or
        ``worker_tasks.last_checkpoint_at``, breaking crash resume.
        """

        async def _callback(scratch: dict[str, Any]) -> None:
            existing = task.checkpoint_blob
            checkpoint_now = datetime.now(UTC)
            checkpoint = Checkpoint(
                task_id=task.id,
                created_at=checkpoint_now,
                state=task.state,
                current_step_index=(
                    existing.current_step_index if existing else 0
                ),
                plan=existing.plan if existing else None,
                accumulated_artifacts=(
                    list(existing.accumulated_artifacts) if existing else []
                ),
                working_doc_mtime=(
                    existing.working_doc_mtime if existing else 0.0
                ),
                scratch_state=dict(scratch),
                request_count=task.request_count,
                agent_turn_count=task.agent_turn_count,
            )
            task.checkpoint_blob = checkpoint
            task.last_checkpoint_at = checkpoint_now
            await self._checkpoint_store.save(checkpoint)
            await self._ledger.record(
                LedgerEventType.TASK_CHECKPOINTED,
                worker_task_id=task.id,
                pipeline_instance_id=task.pipeline_instance_id,
                reason="step_checkpoint",
            )

        return _callback

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


def _task_priority(task: WorkerTask, now: datetime) -> tuple[int, int, datetime]:
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

    pipeline_name = _pipeline_name_from_instance_id(task.pipeline_instance_id)
    protected_rank = 0 if pipeline_name in PROTECTED_FINALIZATION_PIPELINES else 1
    if (
        task.config.request_class is RequestClass.BACKGROUND
        and protected_rank == 0
    ):
        base_priority = min(base_priority, _REQUEST_CLASS_PRIORITY[RequestClass.NOTIFICATION])

    return (base_priority, protected_rank, task.created_at)


def _pipeline_name_from_instance_id(instance_id: str | None) -> str | None:
    if not instance_id:
        return None
    for name in PROTECTED_FINALIZATION_PIPELINES:
        if instance_id == name or instance_id.startswith(f"{name}-"):
            return name
    return None


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
