"""Unit tests for kora_v2.tools.routines — routine management tools."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from kora_v2.life.routines import (
    Routine,
    RoutineProgress,
    RoutineSessionState,
    RoutineStep,
    RoutineVariant,
)
from kora_v2.tools.routines import (
    AdvanceRoutineInput,
    CreateRoutineInput,
    ListRoutinesInput,
    RoutineProgressInput,
    StartRoutineInput,
    advance_routine,
    create_routine,
    list_routines,
    routine_progress,
    start_routine,
)

# ── Helpers / Fixtures ──────────────────────────────────────────────────────


def _make_routine(routine_id: str = "morning-1", name: str = "Morning Routine") -> Routine:
    steps = [
        RoutineStep(index=0, title="Drink water", description="8oz glass", estimated_minutes=1),
        RoutineStep(index=1, title="Stretch", description="5 min stretch", estimated_minutes=5),
        RoutineStep(index=2, title="Review plan", description="Check today's plan", estimated_minutes=3),
    ]
    now = datetime.now(UTC)
    return Routine(
        id=routine_id,
        name=name,
        description="A simple morning routine",
        standard=RoutineVariant(name="standard", steps=steps, estimated_total_minutes=9),
        tags=["morning", "adhd"],
        created_at=now,
        updated_at=now,
    )


def _make_session(
    session_id: str = "sess-1",
    routine_id: str = "morning-1",
    completed_steps: list[int] | None = None,
    skipped_steps: list[int] | None = None,
    current_step_index: int = 0,
    status: str = "active",
) -> RoutineSessionState:
    return RoutineSessionState(
        session_id=session_id,
        routine_id=routine_id,
        variant="standard",
        current_step_index=current_step_index,
        completed_steps=completed_steps or [],
        skipped_steps=skipped_steps or [],
        status=status,
        started_at=datetime.now(UTC),
    )


def _mock_container(routine_manager: MagicMock | None = None) -> MagicMock:
    container = MagicMock()
    container.routine_manager = routine_manager
    container.orchestration_engine = None
    container.session_manager = None
    return container


def _parse(result: str) -> dict:
    return json.loads(result)


# ── list_routines ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_routine_registers_existing_duplicate() -> None:
    routine = _make_routine("morning_launch", "Morning Launch")
    mgr = MagicMock()
    mgr.create_routine = AsyncMock(side_effect=Exception("UNIQUE constraint failed: routines.id"))
    mgr.get_routine = AsyncMock(return_value=routine)
    engine = MagicMock()
    engine.register_runtime_pipeline = AsyncMock()
    container = _mock_container(mgr)
    container.orchestration_engine = engine

    result = _parse(
        await create_routine(
            CreateRoutineInput(
                name="Morning Launch",
                steps=["Meds", "Breakfast", "One priority"],
            ),
            container,
        )
    )

    assert result["success"] is True
    assert result["status"] == "existing"
    assert result["routine_id"] == "morning_launch"
    assert result["runtime_pipeline_registered"] is True
    engine.register_runtime_pipeline.assert_awaited_once()


@pytest.mark.asyncio
async def test_list_routines_returns_all():
    mgr = MagicMock()
    mgr.list_routines = AsyncMock(return_value=[_make_routine(), _make_routine("eve-1", "Evening Routine")])
    container = _mock_container(mgr)

    result = _parse(await list_routines(ListRoutinesInput(tags=""), container))

    assert result["success"] is True
    assert result["count"] == 2
    assert result["routines"][0]["id"] == "morning-1"
    assert result["routines"][1]["id"] == "eve-1"


@pytest.mark.asyncio
async def test_list_routines_with_tags():
    mgr = MagicMock()
    mgr.list_routines = AsyncMock(return_value=[_make_routine()])
    container = _mock_container(mgr)

    result = _parse(await list_routines(ListRoutinesInput(tags="morning, adhd"), container))

    assert result["success"] is True
    mgr.list_routines.assert_awaited_once_with(tags=["morning", "adhd"])


@pytest.mark.asyncio
async def test_list_routines_empty():
    mgr = MagicMock()
    mgr.list_routines = AsyncMock(return_value=[])
    container = _mock_container(mgr)

    result = _parse(await list_routines(ListRoutinesInput(tags=""), container))

    assert result["success"] is True
    assert result["count"] == 0
    assert result["routines"] == []


@pytest.mark.asyncio
async def test_list_routines_no_manager():
    container = _mock_container(routine_manager=None)

    result = _parse(await list_routines(ListRoutinesInput(tags=""), container))

    assert result["success"] is False
    assert "not available" in result["error"]


# ── start_routine ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_start_routine_creates_session():
    session = _make_session()
    mgr = MagicMock()
    mgr.start_session = AsyncMock(return_value=session)
    container = _mock_container(mgr)

    result = _parse(await start_routine(
        StartRoutineInput(routine_id="morning-1", session_id="sess-1"),
        container,
    ))

    assert result["success"] is True
    assert result["status"] == "started"
    assert result["session_id"] == "sess-1"
    assert result["routine_id"] == "morning-1"
    assert result["variant"] == "standard"


@pytest.mark.asyncio
async def test_start_routine_low_energy_variant():
    session = _make_session()
    session.variant = "low_energy"
    mgr = MagicMock()
    mgr.start_session = AsyncMock(return_value=session)
    container = _mock_container(mgr)

    result = _parse(await start_routine(
        StartRoutineInput(routine_id="morning-1", session_id="sess-1", variant="low_energy"),
        container,
    ))

    assert result["success"] is True
    assert result["variant"] == "low_energy"
    mgr.start_session.assert_awaited_once_with(
        routine_id="morning-1",
        session_id="sess-1",
        variant="low_energy",
        parent_session_id=None,
    )


@pytest.mark.asyncio
async def test_start_routine_no_manager():
    container = _mock_container(routine_manager=None)

    result = _parse(await start_routine(
        StartRoutineInput(routine_id="morning-1", session_id="sess-1"),
        container,
    ))

    assert result["success"] is False
    assert "not available" in result["error"]


@pytest.mark.asyncio
async def test_start_routine_error_propagated():
    mgr = MagicMock()
    mgr.start_session = AsyncMock(side_effect=ValueError("routine not found"))
    container = _mock_container(mgr)

    result = _parse(await start_routine(
        StartRoutineInput(routine_id="nonexistent", session_id="sess-1"),
        container,
    ))

    assert result["success"] is False
    assert "routine not found" in result["error"]


# ── advance_routine ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_advance_routine_updates_progress():
    session = _make_session(completed_steps=[0], current_step_index=1)
    routine = _make_routine()
    progress = RoutineProgress(
        routine_name="Morning Routine",
        variant="standard",
        total_steps=3,
        completed=1,
        skipped=0,
        remaining=2,
        completion_pct=33.3,
        message="Got partway through — that counts.",
    )

    mgr = MagicMock()
    mgr.advance_step = AsyncMock(return_value=session)
    mgr.get_routine = AsyncMock(return_value=routine)
    mgr.get_progress = MagicMock(return_value=progress)
    container = _mock_container(mgr)

    result = _parse(await advance_routine(
        AdvanceRoutineInput(session_id="sess-1", step_index=0),
        container,
    ))

    assert result["success"] is True
    assert result["completion_pct"] == 33.3
    assert result["completed"] == 1
    assert result["remaining"] == 2
    assert "partway" in result["message"]


@pytest.mark.asyncio
async def test_advance_routine_skip():
    session = _make_session(skipped_steps=[1], current_step_index=2)
    routine = _make_routine()
    progress = RoutineProgress(
        routine_name="Morning Routine",
        variant="standard",
        total_steps=3,
        completed=0,
        skipped=1,
        remaining=2,
        completion_pct=0.0,
        message="Ready when you are.",
    )

    mgr = MagicMock()
    mgr.advance_step = AsyncMock(return_value=session)
    mgr.get_routine = AsyncMock(return_value=routine)
    mgr.get_progress = MagicMock(return_value=progress)
    container = _mock_container(mgr)

    result = _parse(await advance_routine(
        AdvanceRoutineInput(session_id="sess-1", step_index=1, skipped=True),
        container,
    ))

    assert result["success"] is True
    mgr.advance_step.assert_awaited_once_with(
        session_id="sess-1",
        step_index=1,
        skipped=True,
    )


@pytest.mark.asyncio
async def test_advance_routine_routine_not_found():
    """When routine is missing, still return basic session info."""
    session = _make_session(completed_steps=[0], current_step_index=1)

    mgr = MagicMock()
    mgr.advance_step = AsyncMock(return_value=session)
    mgr.get_routine = AsyncMock(return_value=None)
    container = _mock_container(mgr)

    result = _parse(await advance_routine(
        AdvanceRoutineInput(session_id="sess-1", step_index=0),
        container,
    ))

    assert result["success"] is True
    assert result["status"] == "active"
    assert result["step_index"] == 1


@pytest.mark.asyncio
async def test_advance_routine_no_manager():
    container = _mock_container(routine_manager=None)

    result = _parse(await advance_routine(
        AdvanceRoutineInput(session_id="sess-1", step_index=0),
        container,
    ))

    assert result["success"] is False
    assert "not available" in result["error"]


@pytest.mark.asyncio
async def test_advance_routine_session_not_found():
    mgr = MagicMock()
    mgr.advance_step = AsyncMock(side_effect=ValueError("Routine session not found: bad-id"))
    container = _mock_container(mgr)

    result = _parse(await advance_routine(
        AdvanceRoutineInput(session_id="bad-id", step_index=0),
        container,
    ))

    assert result["success"] is False
    assert "not found" in result["error"]


# ── routine_progress ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_routine_progress_returns_full_info():
    session = _make_session(completed_steps=[0, 1], current_step_index=2)
    routine = _make_routine()
    progress = RoutineProgress(
        routine_name="Morning Routine",
        variant="standard",
        total_steps=3,
        completed=2,
        skipped=0,
        remaining=1,
        completion_pct=66.7,
        message="Good progress — 2/3 steps done.",
    )

    mgr = MagicMock()
    mgr.get_session = AsyncMock(return_value=session)
    mgr.get_routine = AsyncMock(return_value=routine)
    mgr.get_progress = MagicMock(return_value=progress)
    container = _mock_container(mgr)

    result = _parse(await routine_progress(
        RoutineProgressInput(session_id="sess-1"),
        container,
    ))

    assert result["success"] is True
    assert result["routine_name"] == "Morning Routine"
    assert result["total_steps"] == 3
    assert result["completed"] == 2
    assert result["skipped"] == 0
    assert result["remaining"] == 1
    assert result["completion_pct"] == 66.7
    assert "2/3" in result["message"]


@pytest.mark.asyncio
async def test_routine_progress_session_not_found():
    mgr = MagicMock()
    mgr.get_session = AsyncMock(return_value=None)
    container = _mock_container(mgr)

    result = _parse(await routine_progress(
        RoutineProgressInput(session_id="nonexistent"),
        container,
    ))

    assert result["success"] is False
    assert "No active routine session" in result["error"]


@pytest.mark.asyncio
async def test_routine_progress_routine_not_found():
    session = _make_session()
    mgr = MagicMock()
    mgr.get_session = AsyncMock(return_value=session)
    mgr.get_routine = AsyncMock(return_value=None)
    container = _mock_container(mgr)

    result = _parse(await routine_progress(
        RoutineProgressInput(session_id="sess-1"),
        container,
    ))

    assert result["success"] is False
    assert "Routine not found" in result["error"]


@pytest.mark.asyncio
async def test_routine_progress_no_manager():
    container = _mock_container(routine_manager=None)

    result = _parse(await routine_progress(
        RoutineProgressInput(session_id="sess-1"),
        container,
    ))

    assert result["success"] is False
    assert "not available" in result["error"]
