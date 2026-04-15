"""OrchestrationEngine — spec §4.

Top-level service object the DI container hands to callers who want to
run pipelines or single tasks. The engine bundles all orchestration
sub-components (registries, limiter, ledger, dispatcher, state machine)
into one cohesive surface so the rest of Kora talks to *one* object.

Slice 7.5a scope: the engine can start/stop, register pipelines,
dispatch a single :class:`WorkerTask` via the dispatcher, and surface
list/ inspect helpers for tests and the CLI. Trigger evaluation,
full pipeline lifecycle wiring, and the working-doc gate land in
Slice 7.5b.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from kora_v2.runtime.orchestration.checkpointing import CheckpointStore
from kora_v2.runtime.orchestration.dispatcher import Dispatcher
from kora_v2.runtime.orchestration.ledger import LedgerEventType, WorkLedger
from kora_v2.runtime.orchestration.limiter import RequestLimiter
from kora_v2.runtime.orchestration.pipeline import (
    Pipeline,
    PipelineInstance,
    PipelineInstanceState,
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
from kora_v2.runtime.orchestration.worker_task import (
    StepContext,
    StepResult,
    WorkerTask,
    WorkerTaskConfig,
    get_preset,
)

if TYPE_CHECKING:
    from kora_v2.core.events import EventEmitter

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
    ) -> None:
        self._db_path = db_path
        self._emitter = event_emitter
        self._schedule_profile = schedule_profile or UserScheduleProfile()

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
        await self.dispatcher.start()
        self._started = True
        log.debug("orchestration_engine_started")

    async def stop(self, *, graceful: bool = False) -> None:
        if not self._started:
            return
        await self.dispatcher.stop(graceful=graceful)
        self._started = False
        log.debug("orchestration_engine_stopped", graceful=graceful)

    # ── Pipeline surface ─────────────────────────────────────────

    def register_pipeline(self, pipeline: Pipeline) -> None:
        self.pipelines.register(pipeline)

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
