"""OrchestrationEngine — spec §4.

Top-level service object the DI container hands to callers who want to
run pipelines or single tasks. The engine bundles all orchestration
sub-components (registries, limiter, ledger, dispatcher, state machine,
working-doc store, template registry, notification gate, and the
open-decisions tracker) into one cohesive surface so the rest of Kora
talks to *one* object.

Slice 7.5b scope (this file):

* Working-doc store construction + ``get_working_doc`` /
  ``get_task_progress`` read helpers.
* Template registry construction (hot-reload is the registry's own
  responsibility; engine only wires it in).
* Notification gate construction + ``notify`` pass-through.
* Supervisor-facing task lifecycle helpers:
  ``list_tasks(relevant_to_session=..., user_message=...)``,
  ``cancel_task``, ``modify_task``, ``acknowledge_task``.
* ``register_runtime_pipeline`` that persists runtime pipelines to
  ``runtime_pipelines``.
* ``subscribe_event`` — thin wrapper over the event emitter for callers
  who don't want to import the events module.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from datetime import time as dtime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import aiosqlite
import structlog

from kora_v2.runtime.orchestration.checkpointing import CheckpointStore
from kora_v2.runtime.orchestration.decisions import (
    OpenDecision,
    OpenDecisionsTracker,
)
from kora_v2.runtime.orchestration.dispatcher import Dispatcher
from kora_v2.runtime.orchestration.ledger import LedgerEventType, WorkLedger
from kora_v2.runtime.orchestration.limiter import RequestLimiter
from kora_v2.runtime.orchestration.notifications import (
    DeliveryChannel,
    DeliveryResult,
    GeneratedNotification,
    NotificationGate,
)
from kora_v2.runtime.orchestration.overlap import check_topic_overlap
from kora_v2.runtime.orchestration.pipeline import (
    FailurePolicy,
    InterruptionPolicy,
    Pipeline,
    PipelineInstance,
    PipelineInstanceState,
    PipelineStage,
)
from kora_v2.runtime.orchestration.profile_bootstrap import (
    ensure_profile_defaults,
)
from kora_v2.runtime.orchestration.registry import (
    PipelineInstanceRegistry,
    PipelineRegistry,
    TriggerStateStore,
    WorkerTaskRegistry,
    init_orchestration_schema,
)
from kora_v2.runtime.orchestration.system_state import (
    SystemStateMachine,
    UserScheduleProfile,
)
from kora_v2.runtime.orchestration.templates import (
    TemplatePriority,
    TemplateRegistry,
)
from kora_v2.runtime.orchestration.triggers import (
    Trigger,
    TriggerKind,
    event,
    interval,
    sequence_complete,
    time_of_day,
    user_action,
)
from kora_v2.runtime.orchestration.worker_task import (
    StepContext,
    StepResult,
    WorkerTask,
    WorkerTaskConfig,
    WorkerTaskState,
    get_preset,
)
from kora_v2.runtime.orchestration.working_doc import (
    WorkingDocHandle,
    WorkingDocStatus,
    WorkingDocStore,
)

if TYPE_CHECKING:
    from kora_v2.core.events import EventEmitter, EventType

log = structlog.get_logger(__name__)


StepFn = Callable[[WorkerTask, StepContext], Awaitable[StepResult]]


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        log.warning("invalid_orchestration_int_env", name=name, value=raw)
        return default
    if value < 1:
        log.warning("invalid_orchestration_int_env", name=name, value=raw)
        return default
    return value


def _env_nonnegative_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        log.warning("invalid_orchestration_int_env", name=name, value=raw)
        return default
    if value < 0:
        log.warning("invalid_orchestration_int_env", name=name, value=raw)
        return default
    return value


def _serialise_trigger(trigger: Trigger) -> dict[str, Any]:
    return {
        "id": trigger.id,
        "kind": trigger.kind.value,
        "event_type": trigger.event_type,
        "time_of_day_local": (
            trigger.time_of_day_local.isoformat()
            if trigger.time_of_day_local is not None
            else None
        ),
        "interval_seconds": (
            trigger.interval.total_seconds()
            if trigger.interval is not None
            else None
        ),
        "sequence_name": trigger.sequence_name,
        "user_action_name": trigger.user_action_name,
        "allowed_phases": (
            sorted(trigger.allowed_phases)
            if trigger.allowed_phases is not None
            else None
        ),
    }


def _deserialise_trigger(
    pipeline_name: str,
    payload: dict[str, Any],
) -> Trigger | None:
    kind = str(payload.get("kind") or "")
    trigger_id = str(payload.get("id") or "").strip() or None
    try:
        if kind == TriggerKind.TIME_OF_DAY.value:
            raw = str(payload.get("time_of_day_local") or "08:00:00")
            return time_of_day(
                pipeline_name,
                at=dtime.fromisoformat(raw),
                id=trigger_id,
            )
        if kind == TriggerKind.INTERVAL.value:
            seconds = float(payload.get("interval_seconds") or 0)
            if seconds <= 0:
                return None
            allowed = payload.get("allowed_phases")
            return interval(
                pipeline_name,
                every=timedelta(seconds=seconds),
                allowed_phases=allowed if isinstance(allowed, list) else None,
                id=trigger_id,
            )
        if kind == TriggerKind.EVENT.value and payload.get("event_type"):
            return event(
                pipeline_name,
                event_type=str(payload["event_type"]),
                id=trigger_id,
            )
        if kind == TriggerKind.USER_ACTION.value and payload.get("user_action_name"):
            return user_action(
                pipeline_name,
                action_name=str(payload["user_action_name"]),
                id=trigger_id,
            )
        if (
            kind == TriggerKind.SEQUENCE_COMPLETE.value
            and payload.get("sequence_name")
        ):
            return sequence_complete(
                pipeline_name,
                sequence_name=str(payload["sequence_name"]),
                id=trigger_id,
            )
    except Exception:
        log.debug(
            "runtime_pipeline_trigger_deserialise_failed",
            pipeline=pipeline_name,
            payload=payload,
            exc_info=True,
        )
    return None


class OrchestrationEngine:
    """Single entry point for the 7.5 orchestration layer."""

    def __init__(
        self,
        db_path: Path,
        *,
        event_emitter: EventEmitter | None = None,
        schedule_profile: UserScheduleProfile | None = None,
        tick_interval: float = 0.5,
        memory_root: Path | None = None,
        websocket_broadcast: (
            Callable[[dict[str, Any]], Awaitable[None]] | None
        ) = None,
        session_active_fn: Callable[[], bool] | None = None,
        hyperfocus_active_fn: Callable[[], bool] | None = None,
        container: Any | None = None,
    ) -> None:
        self._db_path = db_path
        self._emitter = event_emitter
        self._container = container
        self._tick_interval = tick_interval
        self._schedule_profile = schedule_profile or UserScheduleProfile()
        self._memory_root = memory_root or (db_path.parent.parent / "_KoraMemory")

        self.pipelines = PipelineRegistry()
        self.task_registry = WorkerTaskRegistry(db_path)
        self.instance_registry = PipelineInstanceRegistry(db_path)
        self.trigger_state = TriggerStateStore(db_path)
        self.limiter = RequestLimiter(
            db_path,
            capacity=_env_int("KORA_ORCHESTRATION_LIMITER_CAPACITY", 4500),
            conversation_reserve=_env_int(
                "KORA_ORCHESTRATION_LIMITER_CONVERSATION_RESERVE", 300
            ),
            notification_reserve=_env_int(
                "KORA_ORCHESTRATION_LIMITER_NOTIFICATION_RESERVE", 100
            ),
            window_seconds=_env_int(
                "KORA_ORCHESTRATION_LIMITER_WINDOW_SECONDS", 5 * 3600
            ),
        )
        self.ledger = WorkLedger(db_path)
        self.checkpoint_store = CheckpointStore(db_path)
        self.state_machine = SystemStateMachine(
            self._schedule_profile,
            event_emitter=event_emitter,
            db_path=db_path,
        )

        # Slice 7.5b additions — surfaces that bridge the live runtime
        # to the markdown-canonical working-doc and the two-tier
        # messaging system. The working-doc store takes an ``inbox_root``
        # that points directly at the ``Inbox/`` directory; we keep the
        # full memory root alongside for callers that still resolve
        # instance-supplied paths relative to it.
        self.working_docs = WorkingDocStore(self._memory_root / "Inbox")
        self.templates = TemplateRegistry(
            self._memory_root / ".kora" / "templates"
        )
        self.notifications = NotificationGate(
            db_path=db_path,
            templates=self.templates,
            schedule_profile=self._schedule_profile,
            websocket_broadcast=websocket_broadcast,
            session_active_fn=session_active_fn,
            hyperfocus_active_fn=hyperfocus_active_fn,
        )
        self.open_decisions = OpenDecisionsTracker(
            db_path, event_emitter=event_emitter
        )
        self.trigger_evaluator = None

        self.dispatcher = Dispatcher(
            db_path=db_path,
            task_registry=self.task_registry,
            instance_registry=self.instance_registry,
            limiter=self.limiter,
            ledger=self.ledger,
            checkpoint_store=self.checkpoint_store,
            state_machine=self.state_machine,
            event_emitter=event_emitter,
            tick_interval=tick_interval,
            step_fn_resolver=self._resolve_step_fn,
            pipeline_terminal_hook=self._on_pipeline_terminal,
            adaptive_reconcile_hook=self._reconcile_working_docs,
            rate_limit_hook=self._notify_rate_limit_pause,
        )
        self._started = False
        self._decision_aging_stop_event = asyncio.Event()
        self._decision_aging_task: asyncio.Task[None] | None = None

    async def _reconcile_working_docs(self) -> None:
        """Sync non-terminal pipeline instances against their working docs.

        Called once per dispatcher tick *before* the ready set is
        computed. For each active pipeline instance:

        * read the doc (if present and not user-stale);
        * compute the plan diff against its WorkerTask siblings;
        * submit new WorkerTasks for added ``- [ ]`` items;
        * request cancellation for ``- [skip]`` / ``- [cancel]`` items
          that still have a live sibling;
        * acknowledge ``- [x]`` items whose task is already terminal.

        This keeps the markdown surface the canonical editable spec for
        the pipeline — a user writing a new line in the doc causes a new
        task to be scheduled without them touching SQL.
        """
        try:
            active_instances = await self.instance_registry.load_active()
        except Exception:  # noqa: BLE001
            log.debug("adaptive_reconcile_load_active_failed", exc_info=True)
            return
        if not active_instances:
            return

        for instance in active_instances:
            if not instance.working_doc_path:
                continue
            doc_path = Path(instance.working_doc_path)
            if not doc_path.is_absolute():
                doc_path = self._memory_root / doc_path
            if not doc_path.exists():
                continue
            try:
                handle = await self.working_docs.read(doc_path)
            except Exception:  # noqa: BLE001
                log.debug(
                    "adaptive_reconcile_read_failed",
                    path=str(doc_path),
                    exc_info=True,
                )
                continue
            if handle is None:
                continue

            sibling_tasks = await self.task_registry.load_by_pipeline(
                instance.id
            )
            # Known descriptions: both the user-facing goal and the
            # stage name match, because a working-doc seed uses the
            # stage name while user-appended items tend to use
            # descriptive goals. Including both prevents double-create
            # on the first tick after a triggered pipeline is seeded.
            known_desc: list[str] = []
            for t in sibling_tasks:
                if t.goal:
                    known_desc.append(t.goal)
                if t.stage_name:
                    known_desc.append(t.stage_name)
            try:
                pipeline = self.pipelines.get(instance.pipeline_name)
            except KeyError:
                pipeline = None
            if pipeline is not None:
                known_desc.extend(stage.name for stage in pipeline.stages)
                known_desc.extend(
                    stage.goal_template
                    for stage in pipeline.stages
                    if stage.goal_template
                )
            diff = self.working_docs.reconcile_plan(handle, known_desc)

            # Added: user wrote a new `- [ ]` line → new WorkerTask.
            for item in diff.added:
                try:
                    await self.dispatch_task(
                        goal=item.text,
                        system_prompt="",
                        step_fn=self._default_adaptive_step_fn(instance),
                        preset="bounded_background",
                        stage_name="user_added",
                        pipeline_instance_id=instance.id,
                    )
                    await self.ledger.record(
                        LedgerEventType.TASK_CREATED,
                        pipeline_instance_id=instance.id,
                        reason="adaptive_user_added_plan_item",
                        metadata={"text": item.text},
                    )
                except Exception:  # noqa: BLE001
                    log.exception(
                        "adaptive_task_create_failed",
                        pipeline_instance_id=instance.id,
                        item=item.text,
                    )

            # Cancelled: user wrote `- [skip]` / `- [cancel]`.
            text_to_task: dict[str, WorkerTask] = {}
            for t in sibling_tasks:
                if t.goal:
                    text_to_task.setdefault(t.goal, t)
                if t.stage_name:
                    text_to_task.setdefault(t.stage_name, t)
            for item in diff.cancelled:
                t = text_to_task.get(item.text)
                if t is None or t.is_terminal():
                    continue
                try:
                    await self.dispatcher.request_cancellation(t.id)
                except Exception:  # noqa: BLE001
                    log.debug(
                        "adaptive_cancel_failed",
                        task_id=t.id,
                        exc_info=True,
                    )

            # Completed: user ticked off `- [x]` for a still-live task.
            # Treat as explicit acknowledgement.
            for item in diff.completed:
                t = text_to_task.get(item.text)
                if t is None:
                    continue
                if (
                    t.state == WorkerTaskState.COMPLETED
                    and t.result_acknowledged_at is None
                ):
                    t.result_acknowledged_at = datetime.now(UTC)
                    try:
                        await self.task_registry.save(t)
                    except Exception:  # noqa: BLE001
                        log.debug(
                            "adaptive_ack_save_failed",
                            task_id=t.id,
                            exc_info=True,
                        )

    def _default_adaptive_step_fn(
        self, instance: PipelineInstance
    ) -> StepFn:
        """Pick a step function for a plan item the user added mid-flight.

        Prefer the pipeline's own registered function via
        :meth:`_resolve_step_fn` semantics. Fall back to a no-op stub
        that completes immediately so the dispatcher doesn't fail the
        whole pipeline on a missing resolver.
        """
        from kora_v2.runtime.orchestration.core_pipelines import core_step_fns

        step_map = core_step_fns()
        fn = step_map.get(instance.pipeline_name)
        if fn is not None:
            return fn

        async def _noop_step(
            task: WorkerTask, ctx: StepContext
        ) -> StepResult:
            return StepResult(
                outcome="complete",
                result_summary="adaptive_noop",
            )

        return _noop_step

    async def _notify_rate_limit_pause(self, task: WorkerTask) -> None:
        """Persist a zero-provider notification when background work pauses."""
        await self.notify(
            template_id="rate_limit_paused",
            template_vars={"minutes": 1},
            metadata={
                "reason": "rate_limit_paused",
                "task_id": task.id,
                "pipeline_instance_id": task.pipeline_instance_id,
            },
        )

    async def _on_pipeline_terminal(
        self,
        instance_id: str,
        final_state: PipelineInstanceState,
        completion_reason: str,
    ) -> None:
        """Sync the working doc to the terminal pipeline state.

        Spec §11.5: the markdown working doc is the user-visible source
        of truth for a pipeline's progress. When the dispatcher reconciles
        a pipeline to a terminal SQL state, the frontmatter ``status``
        field must follow so a glance at the Inbox still matches the DB
        view, and so ``recall``-style filesystem readers surface the
        completed state without consulting SQL.
        """
        instance = await self.instance_registry.load(instance_id)
        if instance is None:
            return
        if not instance.working_doc_path:
            return
        doc_path = Path(instance.working_doc_path)
        if not doc_path.is_absolute():
            doc_path = self._memory_root / doc_path
        if not doc_path.exists():
            return

        status_map = {
            PipelineInstanceState.COMPLETED: WorkingDocStatus.DONE,
            PipelineInstanceState.FAILED: WorkingDocStatus.FAILED,
            PipelineInstanceState.CANCELLED: WorkingDocStatus.CANCELLED,
        }
        target_status = status_map.get(final_state)
        if target_status is None:
            return

        tasks = await self.task_registry.load_by_pipeline(instance_id)
        task_summaries: list[str] = []
        for t in tasks:
            if t.result_summary:
                task_summaries.append(f"- {t.stage_name}: {t.result_summary}")
            elif t.error_message and t.state == WorkerTaskState.FAILED:
                task_summaries.append(
                    f"- {t.stage_name} failed: {t.error_message}"
                )
            elif (
                final_state is PipelineInstanceState.CANCELLED
                and t.state == WorkerTaskState.CANCELLED
            ):
                task_summaries.append(
                    f"- {t.stage_name}: cancelled at checkpoint; "
                    "existing working doc content preserved"
                )
        completion_text: str | None = None
        if task_summaries:
            completion_text = (
                f"Pipeline {final_state.value} ({completion_reason}).\n\n"
                + "\n".join(task_summaries)
            )
        else:
            completion_text = (
                f"Pipeline {final_state.value} ({completion_reason})."
            )

        try:
            await self.working_docs.mark_status(
                instance_id=instance_id,
                path=doc_path,
                status=target_status,
                reason=f"pipeline_terminal:{completion_reason}",
                completion_text=completion_text,
            )
        except Exception:  # noqa: BLE001
            log.exception(
                "pipeline_terminal_working_doc_update_failed",
                pipeline_instance_id=instance_id,
                path=str(doc_path),
            )
            return

        # Best-effort completion notification. System-triggered pipelines
        # (no parent_session_id) get a quiet ledger-only completion —
        # there is no session to notify. User-triggered pipelines get a
        # templated nudge so the supervisor can surface completion at
        # turn start.
        if instance.parent_session_id is None:
            return
        template_id = (
            "pipeline_completed"
            if final_state is PipelineInstanceState.COMPLETED
            else "pipeline_failed"
        )
        try:
            await self.notify(
                template_id=template_id,
                template_vars={
                    "pipeline_name": instance.pipeline_name,
                    "goal": instance.goal or "",
                    "reason": completion_reason,
                },
            )
        except (KeyError, ValueError):
            # Template missing — fall back to freeform text.
            try:
                await self.notify(
                    text=(
                        f"{instance.pipeline_name} {final_state.value}: "
                        f"{instance.goal or completion_reason}"
                    ),
                )
            except Exception:  # noqa: BLE001
                log.debug(
                    "pipeline_terminal_notify_fallback_failed",
                    pipeline_instance_id=instance_id,
                    exc_info=True,
                )
        except Exception:  # noqa: BLE001
            log.debug(
                "pipeline_terminal_notify_failed",
                pipeline_instance_id=instance_id,
                exc_info=True,
            )

    async def _resolve_step_fn(self, task: WorkerTask) -> StepFn | None:
        """Rehydrate a step function for *task* after a daemon restart.

        Ordering (from most specific to most permissive):

        1. ``pipeline_name:stage_name`` key in :func:`core_step_fns` —
           e.g. ``post_session_memory:extract``.
        2. Bare ``pipeline_name`` key — covers the old-style 1:1
           pipelines (``user_autonomous_task``, ``skill_refinement``…).
        3. Autonomous step function for ``user_autonomous_task`` and
           any pipeline declared at runtime with ``intent_duration=long``
           (these are all multi-step plan→execute→review→replan flows
           that share the same driver).
        """
        if task.pipeline_instance_id is None:
            return None
        try:
            instance = await self.instance_registry.load(task.pipeline_instance_id)
        except Exception:  # noqa: BLE001
            log.debug(
                "step_fn_resolver_instance_load_failed",
                task_id=task.id,
                exc_info=True,
            )
            return None
        if instance is None:
            return None
        pipeline_name = instance.pipeline_name
        try:
            from kora_v2.runtime.orchestration.core_pipelines import (
                core_step_fns,
            )

            step_map = core_step_fns()
        except Exception:  # noqa: BLE001
            step_map = {}
        fn = (
            step_map.get(f"{pipeline_name}:{task.stage_name}")
            or step_map.get(pipeline_name)
        )
        if fn is not None:
            return fn

        # Fallback: autonomous driver for long-running user pipelines
        # registered via decompose_and_dispatch at runtime.
        try:
            from kora_v2.autonomous.pipeline_factory import (
                get_autonomous_step_fn,
            )

            if (
                pipeline_name == "user_autonomous_task"
                or instance.intent_duration == "long"
                or task.config.preset == "long_background"
            ):
                return get_autonomous_step_fn()
        except Exception:  # noqa: BLE001
            log.debug(
                "step_fn_resolver_autonomous_import_failed", exc_info=True
            )
        return None

    # ── Lifecycle ────────────────────────────────────────────────

    async def start(self) -> None:
        if self._started:
            return
        await init_orchestration_schema(self._db_path)
        self.working_docs.ensure_inbox()
        self.templates.ensure_defaults()
        self.templates.reload_if_changed()
        # Spec §16.3 — make sure the User Model profile carries the
        # orchestration anchors before any pipeline reads them. The
        # bootstrapper never overwrites user-set values; it only fills
        # in keys the user has not authored yet.
        try:
            ensure_profile_defaults(self._memory_root)
        except OSError:
            log.warning(
                "profile_bootstrap_failed",
                memory_root=str(self._memory_root),
                exc_info=True,
            )
        # Slice 7.5c — install the process-level autonomous runtime
        # context so the ``user_autonomous_task`` step function can
        # reach the DI container and operational DB. The dispatcher
        # deliberately does not plumb a container into StepContext so
        # this module-level registry is the single narrow leak.
        try:
            from kora_v2.autonomous.runtime_context import (
                set_autonomous_context,
            )

            set_autonomous_context(
                container=self._container, db_path=self._db_path
            )
        except Exception:  # noqa: BLE001
            log.warning(
                "autonomous_runtime_context_install_failed", exc_info=True
            )
        # Slice 7.5c §17.7b — idempotent migration of in-flight
        # legacy autonomous_checkpoints rows into the new
        # worker_tasks + pipeline_instances tables. Guarded by a
        # marker row in work_ledger so reruns are no-ops.
        try:
            from kora_v2.runtime.orchestration.autonomous_migration import (
                migrate_legacy_autonomous_checkpoints,
            )

            await migrate_legacy_autonomous_checkpoints(
                db_path=self._db_path,
                ledger=self.ledger,
                task_registry=self.task_registry,
                instance_registry=self.instance_registry,
                checkpoint_store=self.checkpoint_store,
            )
        except Exception:  # noqa: BLE001
            log.warning(
                "autonomous_migration_failed", exc_info=True
            )
        await self._load_runtime_pipelines()
        await self._record_pending_decision_aging_tick()
        self._decision_aging_stop_event.clear()
        self._decision_aging_task = asyncio.create_task(
            self._decision_aging_loop()
        )
        await self.dispatcher.start()
        if self._trigger_evaluator_enabled():
            try:
                from kora_v2.runtime.orchestration.trigger_evaluator import (
                    TriggerEvaluator,
                )

                self.trigger_evaluator = TriggerEvaluator(
                    engine=self,
                    event_bus=self._emitter,
                    state_machine=self.state_machine,
                    trigger_state=self.trigger_state,
                    ledger=self.ledger,
                    tick_interval=self._trigger_tick_interval(),
                )
                await self.trigger_evaluator.start()
            except Exception:  # noqa: BLE001
                log.exception("trigger_evaluator_start_failed")
        self._started = True
        log.debug(
            "orchestration_engine_started",
            trigger_evaluator=bool(self.trigger_evaluator),
            pipelines=len(self.pipelines.all()),
        )

    async def stop(self, *, graceful: bool = False) -> None:
        if not self._started:
            return
        self._decision_aging_stop_event.set()
        if self._decision_aging_task is not None:
            try:
                await asyncio.wait_for(self._decision_aging_task, timeout=5.0)
            except TimeoutError:
                self._decision_aging_task.cancel()
            self._decision_aging_task = None
        if self.trigger_evaluator is not None:
            await self.trigger_evaluator.stop(graceful=graceful)
            self.trigger_evaluator = None
        await self.dispatcher.stop(graceful=graceful)
        self._started = False
        log.debug("orchestration_engine_stopped", graceful=graceful)

    def _trigger_evaluator_enabled(self) -> bool:
        settings = getattr(self._container, "settings", None)
        orchestration = getattr(settings, "orchestration", None)
        return bool(getattr(orchestration, "trigger_evaluator_enabled", True))

    def _trigger_tick_interval(self) -> float:
        settings = getattr(self._container, "settings", None)
        orchestration = getattr(settings, "orchestration", None)
        return float(getattr(orchestration, "trigger_tick_interval_seconds", 5.0))

    async def _record_pending_decision_aging_tick(self) -> None:
        try:
            older_than_days = _env_nonnegative_int(
                "KORA_OPEN_DECISION_AGING_DAYS", 3
            )
            await self.record_pending_decision_aging(
                older_than_days=older_than_days,
                limit=10,
            )
        except Exception:  # noqa: BLE001
            log.debug("pending_decision_aging_tick_failed", exc_info=True)

    async def _decision_aging_loop(self) -> None:
        interval = max(0.1, self._tick_interval)
        try:
            while True:
                try:
                    await asyncio.wait_for(
                        self._decision_aging_stop_event.wait(),
                        timeout=interval,
                    )
                    return
                except TimeoutError:
                    pass
                await self._record_pending_decision_aging_tick()
        except asyncio.CancelledError:
            log.debug("decision_aging_loop_cancelled")
            raise

    def update_schedule_profile(self, profile: UserScheduleProfile) -> None:
        """Update the schedule profile used by the state machine and gate."""
        self._schedule_profile = profile
        self.state_machine.update_profile(profile)
        self.notifications.update_profile(profile)

    # ── Pipeline surface ─────────────────────────────────────────

    def register_pipeline(self, pipeline: Pipeline) -> None:
        self.pipelines.register(pipeline)

    async def register_runtime_pipeline(
        self,
        pipeline: Pipeline,
        *,
        created_by_session: str | None = None,
    ) -> None:
        """Register *pipeline* and persist it to ``runtime_pipelines``.

        Runtime pipelines are user-created declarations (via the
        ``decompose_and_dispatch`` supervisor tool) that should survive
        a daemon restart. The in-memory registry is updated first so
        the dispatcher can see the new pipeline before the SQL row is
        written — a crash mid-write leaves an in-memory pipeline that
        will be lost at restart but no stale DB row.
        """
        self.pipelines.register(pipeline)
        declaration = {
            "name": pipeline.name,
            "description": pipeline.description,
            "intent_duration": pipeline.intent_duration,
            "stages": [
                {
                    "name": stage.name,
                    "task_preset": stage.task_preset,
                    "goal_template": stage.goal_template,
                    "depends_on": stage.depends_on,
                    "tool_scope": stage.tool_scope,
                    "system_prompt_ref": stage.system_prompt_ref,
                }
                for stage in pipeline.stages
            ],
            "triggers": [_serialise_trigger(t) for t in pipeline.triggers],
        }
        now = datetime.now(UTC).isoformat()
        try:
            async with aiosqlite.connect(str(self._db_path)) as db:
                await db.execute(
                    """
                    INSERT INTO runtime_pipelines (
                        name, declaration_json, created_at,
                        created_by_session, enabled
                    ) VALUES (?, ?, ?, ?, 1)
                    ON CONFLICT(name) DO UPDATE SET
                        declaration_json = excluded.declaration_json,
                        created_at = excluded.created_at,
                        created_by_session = excluded.created_by_session,
                        enabled = 1
                    """,
                    (pipeline.name, json.dumps(declaration), now, created_by_session),
                )
                await db.commit()
        except aiosqlite.OperationalError:
            # Table missing (unit test without the migration) — OK to skip.
            log.debug("runtime_pipelines_table_missing")

    async def _load_runtime_pipelines(self) -> int:
        """Re-register enabled SQL-backed runtime pipelines at boot.

        Runtime pipelines are user/routine declarations created after
        process start. Their task rows survive restart, but the in-memory
        :class:`PipelineRegistry` does not. Rebuilding declarations before
        the dispatcher rehydrates tasks lets dependency checks, working-doc
        reconciliation, and step-function resolution see the same pipeline
        shape that existed when the task was created.
        """
        loaded = 0
        try:
            async with aiosqlite.connect(str(self._db_path)) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(
                    "SELECT name, declaration_json FROM runtime_pipelines "
                    "WHERE enabled = 1"
                )
                rows = await cursor.fetchall()
        except aiosqlite.OperationalError:
            log.debug("runtime_pipelines_table_missing")
            return 0

        for row in rows:
            try:
                declaration = json.loads(row["declaration_json"] or "{}")
                stages: list[PipelineStage] = []
                for stage in declaration.get("stages") or []:
                    if not isinstance(stage, dict):
                        continue
                    name = str(stage.get("name") or "").strip()
                    if not name:
                        continue
                    task_preset = str(
                        stage.get("task_preset") or "bounded_background"
                    )
                    if task_preset not in {
                        "in_turn",
                        "bounded_background",
                        "long_background",
                    }:
                        task_preset = "bounded_background"
                    stages.append(
                        PipelineStage(
                            name=name,
                            task_preset=task_preset,  # type: ignore[arg-type]
                            goal_template=str(
                                stage.get("goal_template") or name
                            ),
                            depends_on=[
                                str(dep)
                                for dep in (stage.get("depends_on") or [])
                                if str(dep).strip()
                            ],
                            tool_scope=[
                                str(tool)
                                for tool in (stage.get("tool_scope") or [])
                                if str(tool).strip()
                            ],
                            system_prompt_ref=str(
                                stage.get("system_prompt_ref") or ""
                            ),
                        )
                    )
                if not stages:
                    continue
                pipeline_name = str(declaration.get("name") or row["name"])
                triggers: list[Trigger] = []
                for trigger_payload in declaration.get("triggers") or []:
                    if not isinstance(trigger_payload, dict):
                        continue
                    trigger = _deserialise_trigger(
                        pipeline_name,
                        trigger_payload,
                    )
                    if trigger is not None:
                        triggers.append(trigger)
                pipeline = Pipeline(
                    name=pipeline_name,
                    description=str(
                        declaration.get("description")
                        or f"Runtime pipeline {row['name']}"
                    ),
                    stages=stages,
                    triggers=triggers,
                    interruption_policy=InterruptionPolicy.PAUSE_ON_CONVERSATION,
                    failure_policy=FailurePolicy.FAIL_PIPELINE,
                    intent_duration=str(
                        declaration.get("intent_duration") or "indefinite"
                    ),
                )
                self.pipelines.register(pipeline)
                loaded += 1
            except Exception:  # noqa: BLE001
                log.warning(
                    "runtime_pipeline_load_failed",
                    name=row["name"],
                    exc_info=True,
                )
        if loaded:
            log.info("runtime_pipelines_loaded", count=loaded)
        return loaded

    async def start_pipeline_instance(
        self,
        pipeline_name: str,
        *,
        goal: str,
        working_doc_path: str,
        parent_session_id: str | None = None,
        parent_task_id: str | None = None,
    ) -> PipelineInstance:
        pipeline = self.pipelines.get(pipeline_name)
        instance = PipelineInstance(
            id=f"{pipeline.name}-{uuid.uuid4().hex[:12]}",
            pipeline_name=pipeline.name,
            working_doc_path=working_doc_path,
            goal=goal,
            parent_session_id=parent_session_id,
            parent_task_id=parent_task_id,
            state=PipelineInstanceState.RUNNING,
            intent_duration=pipeline.intent_duration,
        )
        await self.instance_registry.save(instance)
        await self.ledger.record(
            LedgerEventType.PIPELINE_STARTED,
            pipeline_instance_id=instance.id,
            reason=pipeline_name,
            metadata={"goal": goal},
        )
        return instance

    async def start_triggered_pipeline(
        self,
        pipeline_name: str,
        *,
        goal: str,
        parent_session_id: str | None = None,
        trigger_id: str | None = None,
    ) -> PipelineInstance:
        """Instantiate a pipeline and seed its stage tasks for the dispatcher."""
        pipeline = self.pipelines.get(pipeline_name)
        instance = await self.start_pipeline_instance(
            pipeline_name,
            goal=goal,
            working_doc_path="",
            parent_session_id=parent_session_id,
        )
        doc_path = self.working_docs.doc_path(
            pipeline_name=pipeline.name,
            instance_id=instance.id,
            goal=goal,
        )
        instance.working_doc_path = str(doc_path)
        await self.instance_registry.save(instance)
        try:
            await self.working_docs.create(
                instance_id=instance.id,
                task_id=instance.id,
                pipeline_name=pipeline.name,
                goal=goal,
                intent_duration=pipeline.intent_duration,
                parent_session_id=parent_session_id,
                seed_plan_items=[stage.name for stage in pipeline.stages],
            )
        except Exception:  # noqa: BLE001
            log.debug(
                "triggered_pipeline_working_doc_create_failed",
                pipeline=pipeline.name,
                instance_id=instance.id,
                exc_info=True,
            )

        from kora_v2.runtime.orchestration.core_pipelines import core_step_fns

        step_fns = core_step_fns()
        for stage in pipeline.stages:
            step_fn = (
                step_fns.get(f"{pipeline.name}:{stage.name}")
                or step_fns.get(pipeline.name)
                or (
                    step_fns.get("routine")
                    if pipeline.name.startswith("routine_")
                    else None
                )
            )
            if step_fn is None:
                raise ValueError(
                    f"No step function registered for triggered pipeline "
                    f"{pipeline.name!r} stage {stage.name!r}"
                )
            stage_goal = stage.goal_template.replace("{{goal}}", goal)
            await self.dispatch_task(
                goal=stage_goal or goal,
                system_prompt=stage.system_prompt_ref or pipeline.description,
                step_fn=step_fn,
                preset=stage.task_preset,
                stage_name=stage.name,
                pipeline_instance_id=instance.id,
                depends_on=list(stage.depends_on),
                tool_scope=list(stage.tool_scope) or None,
            )

        await self.ledger.record(
            LedgerEventType.TASK_PROGRESS,
            pipeline_instance_id=instance.id,
            trigger_name=trigger_id,
            reason="triggered_pipeline_seeded",
            metadata={"pipeline": pipeline.name, "stage_count": len(pipeline.stages)},
        )
        return instance

    # ── Worker task surface ──────────────────────────────────────

    async def dispatch_task(
        self,
        *,
        goal: str,
        system_prompt: str,
        step_fn: StepFn,
        preset: str = "bounded_background",
        stage_name: str = "adhoc",
        pipeline_instance_id: str | None = None,
        config_overrides: dict[str, Any] | None = None,
        tool_scope: list[str] | None = None,
        depends_on: list[str] | None = None,
    ) -> WorkerTask:
        """Create, persist, and hand a new :class:`WorkerTask` to the dispatcher."""
        config = get_preset(preset)  # type: ignore[arg-type]
        config = _apply_config_overrides(config, config_overrides, tool_scope)

        task = WorkerTask(
            id=f"task-{uuid.uuid4().hex[:12]}",
            pipeline_instance_id=pipeline_instance_id,
            stage_name=stage_name,
            config=config,
            goal=goal,
            system_prompt=system_prompt,
            depends_on=depends_on or [],
            created_at=datetime.now(UTC),
        )
        task.step_fn = step_fn
        await self.dispatcher.submit(task)
        return task

    async def request_cancellation(self, task_id: str) -> bool:
        return await self.dispatcher.request_cancellation(task_id)

    async def list_live_tasks(self) -> list[WorkerTask]:
        return self.dispatcher.live_tasks()

    async def get_task(self, task_id: str) -> WorkerTask | None:
        task = self.dispatcher.live_task(task_id)
        if task is not None:
            return task
        return await self.task_registry.load(task_id)

    # ── Supervisor-facing task API (spec §13.4) ──────────────────

    async def list_tasks(
        self,
        *,
        relevant_to_session: str | None = None,
        user_message: str | None = None,
    ) -> list[WorkerTask]:
        """Return live tasks, optionally filtered for turn-start relevance.

        Implements the four-condition OR from spec §13.1:

        1. ``parent_session_id == relevant_to_session`` — tasks the
           current session dispatched.
        2. The pipeline is a **system pipeline** (``parent_session_id``
           is NULL) AND the instance is currently running OR completed
           within the last 10 minutes. System pipeline output is
           relevant to whatever session is active when it lands.
        3. The task is in an unacknowledged terminal state
           (COMPLETED/FAILED with ``result_acknowledged_at IS NULL``).
        4. ``user_message`` is non-empty AND
           ``check_topic_overlap(user_message, task).score`` falls in
           the ``0.45 ≤ score ≤ 0.70`` ambiguous band (scores ≥ 0.70
           pause at the dispatcher level and are already in the list
           by construction).

        Callers that want "everything live" pass both filter args as
        ``None``.
        """
        live = self.dispatcher.live_tasks()
        persisted = await self.task_registry.load_all_non_terminal()
        unacknowledged_terminal = await self._load_unacknowledged_terminal_tasks()
        by_id: dict[str, WorkerTask] = {task.id: task for task in persisted}
        for task in unacknowledged_terminal:
            by_id[task.id] = task
        for task in live:
            by_id[task.id] = task
        all_tasks = list(by_id.values())

        if relevant_to_session is None and user_message is None:
            return all_tasks

        # Case 2 prep: pull pipeline_instances for system pipelines
        # (parent_session_id IS NULL) that are currently running or
        # completed within the last 10 minutes. Done up-front so the
        # per-task loop can do a cheap set membership test.
        system_instance_ids: set[str] = set()
        try:
            cutoff = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
            async with aiosqlite.connect(str(self._db_path)) as db:
                cursor = await db.execute(
                    "SELECT id FROM pipeline_instances "
                    "WHERE parent_session_id IS NULL AND ("
                    "  state IN ('pending', 'running', 'paused') "
                    "  OR (state = 'completed' AND completed_at > ?)"
                    ")",
                    (cutoff,),
                )
                rows = await cursor.fetchall()
                system_instance_ids = {row[0] for row in rows}
        except aiosqlite.OperationalError:
            # Table missing (unit test without migration) — silently
            # skip case 2.
            log.debug("pipeline_instances_table_missing")

        matched: list[WorkerTask] = []
        for task in all_tasks:
            # Case 1: parent_session_id matches current session
            if (
                relevant_to_session is not None
                and task.pipeline_instance_id is not None
            ):
                instance = await self.instance_registry.load(
                    task.pipeline_instance_id
                )
                if instance and instance.parent_session_id == relevant_to_session:
                    matched.append(task)
                    continue

            # Case 2: task belongs to a system pipeline (no parent
            # session) that is running or recently completed.
            if (
                task.pipeline_instance_id is not None
                and task.pipeline_instance_id in system_instance_ids
            ):
                matched.append(task)
                continue

            # Case 3: unacknowledged terminal states
            if (
                task.state
                in {
                    WorkerTaskState.COMPLETED,
                    WorkerTaskState.FAILED,
                    WorkerTaskState.CANCELLED,
                }
                and task.result_acknowledged_at is None
            ):
                matched.append(task)
                continue

            # Case 4: topic overlap with the user message (ambiguous band)
            if user_message:
                overlap = await self._task_matches_message(task, user_message)
                if overlap:
                    matched.append(task)
                    continue

        return matched

    async def _load_unacknowledged_terminal_tasks(self) -> list[WorkerTask]:
        """Load terminal task results that have not yet been surfaced."""
        try:
            async with aiosqlite.connect(str(self._db_path)) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(
                    "SELECT * FROM worker_tasks "
                    "WHERE state IN ('completed', 'failed', 'cancelled') "
                    "AND result_acknowledged_at IS NULL "
                    "ORDER BY completed_at ASC, created_at ASC"
                )
                rows = await cursor.fetchall()
        except aiosqlite.OperationalError:
            log.debug("worker_tasks_table_missing")
            return []
        return [self.task_registry._row_to_task(row) for row in rows]

    async def _task_matches_message(
        self,
        task: WorkerTask,
        user_message: str,
    ) -> bool:
        """Return True if *task* overlaps *user_message* in the 0.45–0.70 band.

        Per spec §13.1 case 4, turn-start surfacing uses the
        "ambiguous" overlap band: scores ≥ 0.70 pause at the dispatcher
        level and are already in the list by construction, so the
        engine only needs to catch the softer overlap range.
        """
        topic = task.goal or task.stage_name or ""
        if not topic:
            return False
        try:
            result = await check_topic_overlap(
                user_message,
                topic,
                task.stage_name or "",
                self._container,
            )
        except Exception:  # noqa: BLE001
            log.debug(
                "task_overlap_check_failed",
                task_id=task.id,
                exc_info=True,
            )
            return False
        return 0.45 <= result.score <= 0.70

    async def cancel_task(
        self, task_id: str, *, reason: str = "supervisor_request"
    ) -> bool:
        """Cancel a live task; returns True if the task was found."""
        cancelled = await self.dispatcher.request_cancellation(task_id)
        if cancelled:
            await self.ledger.record(
                LedgerEventType.TASK_CANCELLED,
                worker_task_id=task_id,
                reason=reason,
            )
        return cancelled

    async def modify_task(
        self,
        task_id: str,
        *,
        goal: str | None = None,
        system_prompt: str | None = None,
    ) -> WorkerTask | None:
        """Update a running task's goal or system prompt in place.

        Supervisor flow: the user tells Kora "actually, make it X
        instead of Y". Kora calls ``modify_task`` and the running
        worker picks up the new fields on its next step (the step
        function reads them off ``WorkerTask`` each tick, so the change
        is visible as soon as it lands).
        """
        task = self.dispatcher.live_task(task_id)
        persisted = await self.task_registry.load(task_id) if task is None else None
        current = task or persisted
        if current is None:
            return None
        if goal is not None:
            current.goal = goal
        if system_prompt is not None:
            current.system_prompt = system_prompt
        await self.task_registry.save(current)
        return current

    async def acknowledge_task(self, task_id: str) -> bool:
        """Mark *task_id*'s result as seen by the supervisor.

        Used at turn-end so the same completed task doesn't surface
        again on the next turn's four-case OR filter.
        """
        task = await self.get_task(task_id)
        if task is None:
            return False
        task.result_acknowledged_at = datetime.now(UTC)
        await self.task_registry.save(task)
        return True

    # ── Working doc + notifications (spec §13.4) ─────────────────

    async def get_working_doc(
        self, task_id: str
    ) -> WorkingDocHandle | None:
        """Return the working-doc handle for *task_id*'s pipeline."""
        task = await self.get_task(task_id)
        if task is None or task.pipeline_instance_id is None:
            return None
        instance = await self.instance_registry.load(task.pipeline_instance_id)
        if instance is None:
            return None
        doc_path = Path(instance.working_doc_path)
        if not doc_path.is_absolute():
            doc_path = self._memory_root / doc_path
        return await self.working_docs.read(doc_path)

    async def get_task_progress(self, task_id: str) -> dict[str, Any]:
        """Return a compact progress snapshot for *task_id*.

        This is what the supervisor tool ``get_task_progress`` returns
        verbatim. Missing fields default to ``None``/empty so the tool
        surface stays forgiving.
        """
        task = await self.get_task(task_id)
        if task is None:
            return {"task_id": task_id, "found": False}

        handle: WorkingDocHandle | None = None
        if task.pipeline_instance_id is not None:
            instance = await self.instance_registry.load(task.pipeline_instance_id)
            if instance is not None:
                doc_path = Path(instance.working_doc_path)
                if not doc_path.is_absolute():
                    doc_path = self._memory_root / doc_path
                handle = await self.working_docs.read(doc_path)

        plan_items: list[dict[str, Any]] = []
        if handle is not None:
            for item in handle.parse_current_plan():
                plan_items.append(
                    {
                        "marker": item.marker,
                        "text": item.text,
                    }
                )

        return {
            "task_id": task_id,
            "found": True,
            "state": task.state.value,
            "stage": task.stage_name,
            "goal": task.goal,
            "preset": task.config.preset,
            "request_count": task.request_count,
            "agent_turn_count": task.agent_turn_count,
            "cancellation_requested": task.cancellation_requested,
            "result_summary": task.result_summary,
            "error_message": task.error_message,
            "working_doc_status": (
                handle.status if handle is not None else None
            ),
            "working_doc_path": (
                str(handle.path) if handle is not None else None
            ),
            "plan_items": plan_items,
        }

    async def notify(
        self,
        *,
        template_id: str | None = None,
        text: str | None = None,
        priority: TemplatePriority = TemplatePriority.MEDIUM,
        via: DeliveryChannel = DeliveryChannel.WEBSOCKET,
        template_vars: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> DeliveryResult:
        """Route a message through :class:`NotificationGate`.

        Convenience wrapper so callers do not need to build
        :class:`GeneratedNotification` or call ``send_templated``
        directly. Exactly one of ``template_id`` / ``text`` must be
        supplied.
        """
        if template_id is None and text is None:
            raise ValueError("notify() requires either template_id or text")
        if template_id is not None and text is not None:
            raise ValueError("notify() cannot take both template_id and text")

        if template_id is not None:
            return await self.notifications.send_templated(
                template_id,
                via=via,
                metadata=metadata,
                **(template_vars or {}),
            )
        assert text is not None  # narrowing for type checker
        notification = GeneratedNotification(
            text=text,
            priority=priority,
            metadata=metadata or {},
        )
        return await self.notifications.send_llm(notification, via=via)

    # ── Open decisions (spec §15) ────────────────────────────────

    async def record_open_decision(
        self,
        *,
        topic: str,
        context: str,
        posed_in_session: str | None = None,
    ) -> OpenDecision:
        """Record an open decision posed to the user."""
        return await self.open_decisions.record(
            topic=topic,
            context=context,
            posed_in_session=posed_in_session,
        )

    async def get_pending_decisions(
        self,
        *,
        limit: int = 50,
        older_than_days: int = 0,
    ) -> list[OpenDecision]:
        return await self.open_decisions.get_pending(
            older_than_days=older_than_days,
            limit=limit,
        )

    async def record_pending_decision_aging(
        self,
        *,
        older_than_days: int = 3,
        limit: int = 50,
    ) -> list[OpenDecision]:
        """Emit ledger/event evidence for decisions pending past the aging window."""
        return await self.open_decisions.record_aging_evidence(
            older_than_days=older_than_days,
            ledger=self.ledger,
            limit=limit,
        )

    # ── Event bus passthrough ────────────────────────────────────

    def subscribe_event(
        self,
        event_type: EventType,
        handler: Callable[..., Awaitable[None]],
    ) -> None:
        """Register *handler* to receive *event_type* notifications.

        A no-op when the engine was built without an event emitter
        (useful for orchestration-only unit tests).
        """
        if self._emitter is None:
            return
        self._emitter.on(event_type, handler)

    # ── Helpers / diagnostics ────────────────────────────────────

    async def limiter_snapshot(self) -> dict[str, Any]:
        snap = await self.limiter.snapshot()
        return {
            "now": snap.now.isoformat(),
            "total": snap.total_in_window,
            "remaining": snap.remaining,
            "capacity": snap.capacity,
            "by_class": {cls.value: count for cls, count in snap.by_class.items()},
        }

    def current_phase(self) -> str:
        return self.state_machine.current_phase(datetime.now(UTC)).value

    async def note_session_start(self) -> None:
        now = datetime.now(UTC)
        self.state_machine.note_session_start(now)
        await self.state_machine.publish_if_changed(now, reason="session_start")

    async def note_session_end(self) -> None:
        now = datetime.now(UTC)
        self.state_machine.note_session_end(now)
        await self.state_machine.publish_if_changed(now, reason="session_end")

    # ── Single-step helper (for unit tests) ──────────────────────

    async def tick_once(self) -> int:
        await self._record_pending_decision_aging_tick()
        return await self.dispatcher.tick_once()

    async def run_task_to_completion(
        self,
        task: WorkerTask,
        *,
        max_ticks: int = 100,
    ) -> WorkerTask:
        """Drive *task* via repeated :meth:`Dispatcher.tick_once` calls.

        Used by unit tests and the ``demo_tick`` integration test to
        exercise the full loop without starting a real background task.
        """
        for _ in range(max_ticks):
            await self.dispatcher.tick_once()
            current = self.dispatcher.live_task(task.id)
            if current is None:
                # Fell out of the live set → terminal
                persisted = await self.task_registry.load(task.id)
                return persisted or task
            if current.is_terminal():
                return current
        return task


def _apply_config_overrides(
    config: WorkerTaskConfig,
    overrides: dict[str, Any] | None,
    tool_scope: list[str] | None,
) -> WorkerTaskConfig:
    from dataclasses import replace
    patch: dict[str, Any] = {}
    if tool_scope is not None:
        patch["tool_scope"] = tool_scope
    if overrides:
        patch.update(overrides)
    if not patch:
        return config
    return replace(config, **patch)
