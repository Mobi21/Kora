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

import json
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
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
    Pipeline,
    PipelineInstance,
    PipelineInstanceState,
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
    WorkingDocStore,
)

if TYPE_CHECKING:
    from kora_v2.core.events import EventEmitter, EventType

log = structlog.get_logger(__name__)


StepFn = Callable[[WorkerTask, StepContext], Awaitable[StepResult]]


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
        self._schedule_profile = schedule_profile or UserScheduleProfile()
        self._memory_root = memory_root or (db_path.parent.parent / "_KoraMemory")

        self.pipelines = PipelineRegistry()
        self.task_registry = WorkerTaskRegistry(db_path)
        self.instance_registry = PipelineInstanceRegistry(db_path)
        self.trigger_state = TriggerStateStore(db_path)
        self.limiter = RequestLimiter(db_path)
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
        )
        self._started = False

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
        await self.dispatcher.start()
        self._started = True
        log.debug("orchestration_engine_started")

    async def stop(self, *, graceful: bool = False) -> None:
        if not self._started:
            return
        await self.dispatcher.stop(graceful=graceful)
        self._started = False
        log.debug("orchestration_engine_stopped", graceful=graceful)

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
                }
                for stage in pipeline.stages
            ],
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
        by_id: dict[str, WorkerTask] = {task.id: task for task in persisted}
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
                task.state in {WorkerTaskState.FAILED, WorkerTaskState.COMPLETED}
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
        self, *, limit: int = 50
    ) -> list[OpenDecision]:
        return await self.open_decisions.get_pending(limit=limit)

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
        self.state_machine.note_session_start(datetime.now(UTC))

    async def note_session_end(self) -> None:
        self.state_machine.note_session_end(datetime.now(UTC))

    # ── Single-step helper (for unit tests) ──────────────────────

    async def tick_once(self) -> int:
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
