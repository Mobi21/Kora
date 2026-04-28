"""Runtime trigger evaluator for orchestration pipelines."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import aiosqlite
import structlog

from kora_v2.core.events import EventEmitter, EventType
from kora_v2.runtime.orchestration.ledger import LedgerEventType, WorkLedger
from kora_v2.runtime.orchestration.registry import TriggerStateStore
from kora_v2.runtime.orchestration.triggers import (
    Trigger,
    TriggerContext,
    TriggerKind,
)

if TYPE_CHECKING:
    from kora_v2.runtime.orchestration.engine import OrchestrationEngine
    from kora_v2.runtime.orchestration.pipeline import Pipeline
    from kora_v2.runtime.orchestration.system_state import SystemStateMachine

log = structlog.get_logger(__name__)


class TriggerEvaluator:
    """Evaluate registered pipeline triggers and dispatch runnable work."""

    def __init__(
        self,
        *,
        engine: OrchestrationEngine,
        event_bus: EventEmitter | None,
        state_machine: SystemStateMachine,
        trigger_state: TriggerStateStore,
        ledger: WorkLedger,
        tick_interval: float = 5.0,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._engine = engine
        self._event_bus = event_bus
        self._state_machine = state_machine
        self._trigger_state = trigger_state
        self._ledger = ledger
        self._tick_interval = tick_interval
        self._clock = clock or (lambda: datetime.now(UTC))

        self._running = False
        self._stop_event = asyncio.Event()
        self._loop_task: asyncio.Task[None] | None = None
        self._tick_lock = asyncio.Lock()
        self._firing_locks: set[str] = set()
        self._pending_event_payloads: dict[str, dict[str, Any]] = {}
        self._subscriptions: list[tuple[EventType, Any]] = []
        self._kick_tasks: set[asyncio.Task[Any]] = set()

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._subscribe_events()
        self._loop_task = asyncio.create_task(self._run_loop())
        log.info(
            "trigger_evaluator_started",
            tick_interval=self._tick_interval,
            subscriptions=len(self._subscriptions),
        )

    async def stop(self, *, graceful: bool = True) -> None:
        if not self._running:
            return
        self._running = False
        self._stop_event.set()
        if self._event_bus is not None:
            for event_type, handler in self._subscriptions:
                self._event_bus.off(event_type, handler)
        self._subscriptions.clear()

        if self._loop_task is not None:
            try:
                await asyncio.wait_for(self._loop_task, timeout=5.0 if graceful else 0.1)
            except TimeoutError:
                self._loop_task.cancel()
            self._loop_task = None
        for task in list(self._kick_tasks):
            task.cancel()
        self._kick_tasks.clear()
        log.info("trigger_evaluator_stopped", graceful=graceful)

    async def tick_once(self) -> int:
        """Run one evaluation pass. Returns the number of pipelines fired."""
        async with self._tick_lock:
            return await self._evaluate_once()

    async def _run_loop(self) -> None:
        try:
            while self._running:
                try:
                    await self.tick_once()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("trigger_evaluator_tick_failed")
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self._tick_interval,
                    )
                except TimeoutError:
                    pass
        except asyncio.CancelledError:
            log.debug("trigger_evaluator_loop_cancelled")
            raise

    def _subscribe_events(self) -> None:
        if self._event_bus is None:
            return
        event_types: set[EventType] = set()
        for pipeline in self._engine.pipelines.all():
            for trigger in pipeline.triggers:
                event_types.update(_event_types_for(trigger))
        for event_type in sorted(event_types, key=lambda e: e.name):
            async def _handler(payload: dict[str, Any], *, et: EventType = event_type) -> None:
                self._on_event(et, payload)

            self._event_bus.on(event_type, _handler)
            self._subscriptions.append((event_type, _handler))

    def _on_event(self, event_type: EventType, payload: dict[str, Any]) -> None:
        if not self._running:
            return
        self._pending_event_payloads[event_type.name] = dict(payload)
        task = asyncio.create_task(self.tick_once())
        self._kick_tasks.add(task)

        def _done(done: asyncio.Task[Any]) -> None:
            self._kick_tasks.discard(done)
            try:
                done.result()
            except asyncio.CancelledError:
                pass
            except Exception:
                log.exception("trigger_evaluator_event_tick_failed")

        task.add_done_callback(_done)

    async def _evaluate_once(self) -> int:
        now = self._clock()
        phase = self._state_machine.current_phase(now).value
        event_snapshot = dict(self._pending_event_payloads)
        try:
            ctx = await self._build_context(
                now=now,
                phase=phase,
                event_payloads=event_snapshot,
            )
            await self._hydrate_trigger_state(ctx.now)
            active_counts = await self._active_pipeline_counts()
            fired = 0
            for pipeline in self._engine.pipelines.all():
                if not pipeline.triggers:
                    continue
                if pipeline.name in self._firing_locks:
                    continue
                if active_counts.get(pipeline.name, 0) >= pipeline.max_concurrent_instances:
                    continue
                for trigger in pipeline.triggers:
                    matched = _first_firing_trigger(trigger, ctx)
                    if matched is None:
                        continue
                    if await self._fire_pipeline(pipeline, matched, ctx):
                        active_counts[pipeline.name] = active_counts.get(pipeline.name, 0) + 1
                        fired += 1
                    break
            return fired
        finally:
            for name, payload in event_snapshot.items():
                if self._pending_event_payloads.get(name) == payload:
                    self._pending_event_payloads.pop(name, None)

    async def _build_context(
        self,
        *,
        now: datetime,
        phase: str,
        event_payloads: dict[str, dict[str, Any]],
    ) -> TriggerContext:
        completed_sequence_times = await self._completed_pipeline_times()
        user_action_times = await self._recent_user_action_times()
        return TriggerContext(
            now=now,
            phase=phase,
            last_event_payloads=event_payloads,
            completed_sequences=set(completed_sequence_times),
            completed_sequence_times=completed_sequence_times,
            user_actions=set(user_action_times),
            user_action_times=user_action_times,
        )

    async def _hydrate_trigger_state(self, now: datetime) -> None:
        rows = await self._trigger_state.load_all()
        for pipeline in self._engine.pipelines.all():
            for trigger in pipeline.triggers:
                for node in _walk_triggers(trigger):
                    last = rows.get(node.id)
                    if last is None:
                        continue
                    if last > now and node.kind in {TriggerKind.INTERVAL, TriggerKind.TIME_OF_DAY}:
                        log.warning(
                            "trigger_clock_regression",
                            trigger_id=node.id,
                            last_fired_at=last.isoformat(),
                            now=now.isoformat(),
                        )
                        last = now - timedelta(seconds=1)
                    node.last_fired_at = last

    async def _active_pipeline_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        try:
            active = await self._engine.instance_registry.load_active()
        except Exception:  # noqa: BLE001
            log.exception("trigger_active_pipeline_query_failed")
            return counts
        for instance in active:
            counts[instance.pipeline_name] = counts.get(instance.pipeline_name, 0) + 1
        return counts

    async def _completed_pipeline_times(self) -> dict[str, datetime]:
        db_path = getattr(self._engine, "_db_path")
        async with aiosqlite.connect(str(db_path)) as db:
            cursor = await db.execute(
                """
                SELECT pi.pipeline_name, MAX(wl.timestamp)
                FROM work_ledger wl
                JOIN pipeline_instances pi ON pi.id = wl.pipeline_instance_id
                WHERE wl.event_type = ?
                  AND pi.state = 'completed'
                GROUP BY pi.pipeline_name
                """,
                (LedgerEventType.PIPELINE_COMPLETED,),
            )
            rows = await cursor.fetchall()
        return {
            str(row[0]): datetime.fromisoformat(row[1])
            for row in rows
            if row[0] and row[1]
        }

    async def _recent_user_action_times(self) -> dict[str, datetime]:
        db_path = getattr(self._engine, "_db_path")
        since = (self._clock() - timedelta(hours=1)).isoformat()
        async with aiosqlite.connect(str(db_path)) as db:
            cursor = await db.execute(
                """
                SELECT reason, MAX(timestamp)
                FROM work_ledger
                WHERE event_type = 'user_action'
                  AND timestamp >= ?
                  AND reason IS NOT NULL
                GROUP BY reason
                """,
                (since,),
            )
            rows = await cursor.fetchall()
        return {
            str(row[0]): datetime.fromisoformat(row[1])
            for row in rows
            if row[0] and row[1]
        }

    async def _fire_pipeline(
        self,
        pipeline: Pipeline,
        trigger: Trigger,
        ctx: TriggerContext,
    ) -> bool:
        self._firing_locks.add(pipeline.name)
        try:
            instance = await self._engine.start_triggered_pipeline(
                pipeline.name,
                goal=_goal_for(pipeline),
                parent_session_id=None,
                trigger_id=trigger.id,
            )
            fired_at = _trigger_fired_at(trigger, ctx)
            trigger.mark_fired(fired_at)
            await self._trigger_state.save_last_fired(
                trigger.id,
                pipeline.name,
                fired_at,
                reason=trigger.kind.value,
                next_eligible_at=trigger.next_eligible(fired_at),
            )
            await self._ledger.record(
                LedgerEventType.TRIGGER_FIRED,
                pipeline_instance_id=instance.id,
                trigger_name=trigger.id,
                reason=pipeline.name,
                metadata={
                    "trigger_kind": trigger.kind.value,
                    "phase": ctx.phase,
                    "source_fired_at": fired_at.isoformat(),
                    "completed_sequences": sorted(ctx.completed_sequences),
                    "event_names": sorted(ctx.last_event_payloads),
                },
            )
            if self._event_bus is not None:
                await self._event_bus.emit(
                    EventType.TRIGGER_FIRED,
                    pipeline_name=pipeline.name,
                    pipeline_instance_id=instance.id,
                    trigger_id=trigger.id,
                    trigger_kind=trigger.kind.value,
                )
            log.info(
                "trigger_pipeline_fired",
                pipeline=pipeline.name,
                trigger_id=trigger.id,
                trigger_kind=trigger.kind.value,
                instance_id=instance.id,
            )
            return True
        except Exception:  # noqa: BLE001
            log.exception(
                "trigger_pipeline_dispatch_failed",
                pipeline=pipeline.name,
                trigger_id=trigger.id,
            )
            return False
        finally:
            self._firing_locks.discard(pipeline.name)


def _goal_for(pipeline: Pipeline) -> str:
    return pipeline.description or pipeline.name


def _trigger_fired_at(trigger: Trigger, ctx: TriggerContext) -> datetime:
    if trigger.kind is TriggerKind.SEQUENCE_COMPLETE and trigger.sequence_name:
        return ctx.completed_sequence_times.get(trigger.sequence_name, ctx.now)
    if trigger.kind is TriggerKind.USER_ACTION and trigger.user_action_name:
        return ctx.user_action_times.get(trigger.user_action_name, ctx.now)
    return ctx.now


def _walk_triggers(trigger: Trigger) -> list[Trigger]:
    nodes = [trigger]
    for child in trigger.children:
        nodes.extend(_walk_triggers(child))
    return nodes


def _event_types_for(trigger: Trigger) -> set[EventType]:
    events: set[EventType] = set()
    if trigger.kind is TriggerKind.EVENT and trigger.event_type:
        try:
            events.add(EventType[trigger.event_type])
        except KeyError:
            log.warning("unknown_trigger_event_type", event_type=trigger.event_type)
    for child in trigger.children:
        events.update(_event_types_for(child))
    return events


def _first_firing_trigger(trigger: Trigger, ctx: TriggerContext) -> Trigger | None:
    if trigger.kind is TriggerKind.ANY_OF:
        if trigger.allowed_phases is not None and ctx.phase is not None:
            if ctx.phase not in trigger.allowed_phases:
                return None
        for child in trigger.children:
            matched = _first_firing_trigger(child, ctx)
            if matched is not None:
                return matched
        return None
    if trigger.kind is TriggerKind.ALL_OF:
        return trigger if trigger.should_fire(ctx) else None
    return trigger if trigger.should_fire(ctx) else None
