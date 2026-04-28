"""TriggerEvaluator runtime wiring tests."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite
import pytest

from kora_v2.core.events import EventEmitter, EventType
from kora_v2.runtime.orchestration.engine import OrchestrationEngine
from kora_v2.runtime.orchestration.ledger import LedgerEventType
from kora_v2.runtime.orchestration.pipeline import Pipeline, PipelineStage
from kora_v2.runtime.orchestration.registry import init_orchestration_schema
from kora_v2.runtime.orchestration.system_state import UserScheduleProfile
from kora_v2.runtime.orchestration.trigger_evaluator import TriggerEvaluator
from kora_v2.runtime.orchestration.triggers import (
    any_of,
    event,
    interval,
    sequence_complete,
)
from kora_v2.runtime.orchestration.worker_task import StepContext, StepResult, WorkerTask


async def _complete_step(task: WorkerTask, ctx: StepContext) -> StepResult:
    return StepResult(outcome="complete", result_summary=f"done:{task.stage_name}")


def _pipeline(
    name: str,
    triggers: list,
    *,
    stages: list[PipelineStage] | None = None,
) -> Pipeline:
    return Pipeline(
        name=name,
        description=f"{name} pipeline",
        stages=stages
        or [
            PipelineStage(
                name="run",
                task_preset="bounded_background",
                goal_template=f"{name} goal",
            )
        ],
        triggers=triggers,
    )


async def _engine(tmp_path: Path, emitter: EventEmitter | None = None) -> OrchestrationEngine:
    db_path = tmp_path / "operational.db"
    await init_orchestration_schema(db_path)
    return OrchestrationEngine(
        db_path,
        event_emitter=emitter,
        schedule_profile=UserScheduleProfile(timezone="UTC"),
        memory_root=tmp_path / "_KoraMemory",
        tick_interval=0.01,
    )


@pytest.fixture
def fixed_now() -> datetime:
    return datetime(2026, 4, 21, 12, 0, tzinfo=UTC)


async def test_tick_dispatches_pipeline_tasks(
    tmp_path: Path,
    fixed_now: datetime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = await _engine(tmp_path)
    engine.register_pipeline(_pipeline("demo", [interval("demo", every=timedelta(minutes=5))]))
    monkeypatch.setattr(
        "kora_v2.runtime.orchestration.core_pipelines.core_step_fns",
        lambda: {"demo": _complete_step},
    )

    evaluator = TriggerEvaluator(
        engine=engine,
        event_bus=None,
        state_machine=engine.state_machine,
        trigger_state=engine.trigger_state,
        ledger=engine.ledger,
        clock=lambda: fixed_now,
    )

    assert await evaluator.tick_once() == 1
    assert len(engine.dispatcher.live_tasks()) == 1

    assert await engine.dispatcher.tick_once() == 1
    events = await engine.ledger.read_recent(20)
    event_types = {event.event_type for event in events}
    assert LedgerEventType.TRIGGER_FIRED in event_types
    assert LedgerEventType.TASK_COMPLETED in event_types


async def test_trigger_state_is_persisted_by_trigger_id(
    tmp_path: Path,
    fixed_now: datetime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = await _engine(tmp_path)
    engine.register_pipeline(_pipeline("demo", [interval("demo", every=timedelta(minutes=5))]))
    monkeypatch.setattr(
        "kora_v2.runtime.orchestration.core_pipelines.core_step_fns",
        lambda: {"demo": _complete_step},
    )
    evaluator = TriggerEvaluator(
        engine=engine,
        event_bus=None,
        state_machine=engine.state_machine,
        trigger_state=engine.trigger_state,
        ledger=engine.ledger,
        clock=lambda: fixed_now,
    )

    assert await evaluator.tick_once() == 1
    assert await engine.dispatcher.tick_once() == 1
    assert await evaluator.tick_once() == 0

    async with aiosqlite.connect(str(tmp_path / "operational.db")) as db:
        rows = await (await db.execute("SELECT trigger_id, pipeline_name FROM trigger_state")).fetchall()
    assert rows == [("demo.interval", "demo")]


async def test_sequence_complete_uses_completed_pipeline_name(
    tmp_path: Path,
    fixed_now: datetime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = await _engine(tmp_path)
    engine.register_pipeline(_pipeline("first", [interval("first", every=timedelta(minutes=5))]))
    engine.register_pipeline(_pipeline("second", [sequence_complete("second", sequence_name="first")]))
    monkeypatch.setattr(
        "kora_v2.runtime.orchestration.core_pipelines.core_step_fns",
        lambda: {"first": _complete_step, "second": _complete_step},
    )
    evaluator = TriggerEvaluator(
        engine=engine,
        event_bus=None,
        state_machine=engine.state_machine,
        trigger_state=engine.trigger_state,
        ledger=engine.ledger,
        clock=lambda: fixed_now,
    )

    assert await evaluator.tick_once() == 1
    assert await engine.dispatcher.tick_once() == 1
    assert await evaluator.tick_once() == 1
    assert await engine.dispatcher.tick_once() == 1
    assert await evaluator.tick_once() == 0

    names = [
        event.reason
        for event in await engine.ledger.read_recent(20)
        if event.event_type == LedgerEventType.TRIGGER_FIRED
    ]
    assert "first" in names
    assert "second" in names


async def test_nested_allowed_phase_suppresses_any_of_child(
    tmp_path: Path,
    fixed_now: datetime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = await _engine(tmp_path)
    engine.state_machine.note_session_start(fixed_now)
    engine.register_pipeline(
        _pipeline(
            "deep_only",
            [
                any_of(
                    "deep_only",
                    interval(
                        "deep_only",
                        every=timedelta(minutes=5),
                        allowed_phases=["deep_idle"],
                    ),
                )
            ],
        )
    )
    monkeypatch.setattr(
        "kora_v2.runtime.orchestration.core_pipelines.core_step_fns",
        lambda: {"deep_only": _complete_step},
    )
    evaluator = TriggerEvaluator(
        engine=engine,
        event_bus=None,
        state_machine=engine.state_machine,
        trigger_state=engine.trigger_state,
        ledger=engine.ledger,
        clock=lambda: fixed_now,
    )

    assert await evaluator.tick_once() == 0
    assert engine.dispatcher.live_tasks() == []


async def test_event_trigger_fires_once_per_event(
    tmp_path: Path,
    fixed_now: datetime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    emitter = EventEmitter()
    engine = await _engine(tmp_path, emitter)
    engine.register_pipeline(_pipeline("on_session_end", [event("on_session_end", event_type="SESSION_END")]))
    monkeypatch.setattr(
        "kora_v2.runtime.orchestration.core_pipelines.core_step_fns",
        lambda: {"on_session_end": _complete_step},
    )
    evaluator = TriggerEvaluator(
        engine=engine,
        event_bus=emitter,
        state_machine=engine.state_machine,
        trigger_state=engine.trigger_state,
        ledger=engine.ledger,
        clock=lambda: fixed_now,
    )
    await evaluator.start()
    try:
        await emitter.emit(EventType.SESSION_END, session_id="s1")
        await asyncio.sleep(0.05)
        assert len(engine.dispatcher.live_tasks()) == 1
        assert await engine.dispatcher.tick_once() == 1
        assert await evaluator.tick_once() == 0
    finally:
        await evaluator.stop()
