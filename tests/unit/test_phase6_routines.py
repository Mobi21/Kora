"""Phase 6B: Guided Routines unit tests."""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from kora_v2.core.db import init_operational_db
from kora_v2.life.routines import (
    Routine,
    RoutineManager,
    RoutineSessionState,
    RoutineStep,
    RoutineVariant,
)


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
async def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "operational.db"
    await init_operational_db(path)
    return path


@pytest.fixture
def manager(db_path: Path) -> RoutineManager:
    return RoutineManager(db_path)


def _make_steps(n: int = 3, energy: str = "medium") -> list[RoutineStep]:
    return [
        RoutineStep(
            index=i,
            title=f"Step {i}",
            description=f"Description for step {i}",
            estimated_minutes=5,
            energy_required=energy,  # type: ignore[arg-type]
            skippable=True,
            cue=f"Cue {i}",
        )
        for i in range(n)
    ]


def _make_routine(
    steps: list[RoutineStep] | None = None,
    low_steps: list[RoutineStep] | None = None,
    tags: list[str] | None = None,
) -> Routine:
    std_steps = steps or _make_steps(3)
    standard = RoutineVariant(
        name="standard",
        steps=std_steps,
        estimated_total_minutes=sum(s.estimated_minutes for s in std_steps),
    )
    low_energy = None
    if low_steps:
        low_energy = RoutineVariant(
            name="low_energy",
            steps=low_steps,
            estimated_total_minutes=sum(s.estimated_minutes for s in low_steps),
        )
    now = datetime.now(UTC)
    return Routine(
        id=str(uuid.uuid4()),
        name="Morning Routine",
        description="Start the day right.",
        standard=standard,
        low_energy=low_energy,
        tags=tags or [],
        created_at=now,
        updated_at=now,
    )


# ── Tests ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_and_load_routine(manager: RoutineManager) -> None:
    """Create a routine and load it back — fields round-trip correctly."""
    routine = _make_routine(tags=["morning", "health"])
    returned_id = await manager.create_routine(routine)
    assert returned_id == routine.id

    loaded = await manager.get_routine(routine.id)
    assert loaded is not None
    assert loaded.id == routine.id
    assert loaded.name == routine.name
    assert loaded.description == routine.description
    assert loaded.tags == ["morning", "health"]
    assert len(loaded.standard.steps) == 3
    assert loaded.standard.steps[0].title == "Step 0"
    assert loaded.standard.steps[0].cue == "Cue 0"


@pytest.mark.asyncio
async def test_start_session_and_get_session(manager: RoutineManager) -> None:
    """Start a session and retrieve it."""
    routine = _make_routine()
    await manager.create_routine(routine)

    session_id = str(uuid.uuid4())
    state = await manager.start_session(routine.id, session_id)
    assert state.session_id == session_id
    assert state.routine_id == routine.id
    assert state.variant == "standard"
    assert state.current_step_index == 0
    assert state.completed_steps == []
    assert state.skipped_steps == []
    assert state.status == "active"

    loaded = await manager.get_session(session_id)
    assert loaded is not None
    assert loaded.session_id == session_id
    assert loaded.status == "active"


@pytest.mark.asyncio
async def test_advance_step_marks_completed(manager: RoutineManager) -> None:
    """Advancing a step marks it as completed and updates current index."""
    routine = _make_routine()
    await manager.create_routine(routine)
    session_id = str(uuid.uuid4())
    await manager.start_session(routine.id, session_id)

    state = await manager.advance_step(session_id, step_index=0)
    assert 0 in state.completed_steps
    assert state.current_step_index == 1
    assert state.completion_confidence > 0.0


@pytest.mark.asyncio
async def test_skip_step_is_not_counted_as_completed(manager: RoutineManager) -> None:
    """Skipping a step adds it to skipped_steps, NOT completed_steps."""
    routine = _make_routine()
    await manager.create_routine(routine)
    session_id = str(uuid.uuid4())
    await manager.start_session(routine.id, session_id)

    state = await manager.advance_step(session_id, step_index=1, skipped=True)
    assert 1 in state.skipped_steps
    assert 1 not in state.completed_steps


@pytest.mark.asyncio
async def test_get_progress_after_steps(manager: RoutineManager) -> None:
    """get_progress returns correct counts and a shame-free message."""
    routine = _make_routine(_make_steps(4))
    await manager.create_routine(routine)
    session_id = str(uuid.uuid4())
    state = await manager.start_session(routine.id, session_id)

    # Complete two steps
    state = await manager.advance_step(session_id, 0)
    state = await manager.advance_step(session_id, 1)

    progress = manager.get_progress(state, routine)
    assert progress.total_steps == 4
    assert progress.completed == 2
    assert progress.skipped == 0
    assert progress.remaining == 2
    assert progress.completion_pct == 50.0
    # 50% → "Got partway through — that counts."
    assert "partway" in progress.message


@pytest.mark.asyncio
async def test_complete_session(manager: RoutineManager) -> None:
    """complete_session marks status as completed."""
    routine = _make_routine()
    await manager.create_routine(routine)
    session_id = str(uuid.uuid4())
    await manager.start_session(routine.id, session_id)

    state = await manager.complete_session(session_id)
    assert state.status == "completed"
    assert state.completed_at is not None


@pytest.mark.asyncio
async def test_abandon_session_no_judgment(manager: RoutineManager) -> None:
    """abandon_session sets status=abandoned; no completed_at implied failure."""
    routine = _make_routine()
    await manager.create_routine(routine)
    session_id = str(uuid.uuid4())
    await manager.start_session(routine.id, session_id)

    # Advance one step before abandoning
    await manager.advance_step(session_id, 0)
    state = await manager.abandon_session(session_id)
    assert state.status == "abandoned"
    assert state.completed_at is not None  # timestamp recorded for auditing


@pytest.mark.asyncio
async def test_shame_free_progress_messages(manager: RoutineManager) -> None:
    """Progress messages are shame-free across the full range."""
    routine = _make_routine(_make_steps(10))
    await manager.create_routine(routine)

    async def _session_with(completed: list[int]) -> RoutineSessionState:
        sid = str(uuid.uuid4())
        s = await manager.start_session(routine.id, sid)
        for idx in completed:
            s = await manager.advance_step(sid, idx)
        return s

    # 0% — "Ready when you are."
    s0 = RoutineSessionState(
        session_id="x", routine_id=routine.id, started_at=datetime.now(UTC)
    )
    p0 = manager.get_progress(s0, routine)
    assert p0.message == "Ready when you are."

    # 1 step of 10 → 10% → "You started. That's the hardest part."
    s1 = await _session_with([0])
    p1 = manager.get_progress(s1, routine)
    assert "started" in p1.message.lower()

    # 4 steps of 10 → 40% → "Got partway through — that counts."
    s4 = await _session_with([0, 1, 2, 3])
    p4 = manager.get_progress(s4, routine)
    assert "partway" in p4.message

    # 7 steps of 10 → 70% → "Good progress — X/10 steps done."
    s7 = await _session_with([0, 1, 2, 3, 4, 5, 6])
    p7 = manager.get_progress(s7, routine)
    assert "Good progress" in p7.message

    # 10 steps of 10 → 100% → "You did it. Routine complete."
    s10 = await _session_with(list(range(10)))
    p10 = manager.get_progress(s10, routine)
    assert "You did it" in p10.message


@pytest.mark.asyncio
async def test_list_routines_tag_filter(manager: RoutineManager) -> None:
    """list_routines with tag filter returns only matching routines."""
    r1 = _make_routine(tags=["morning", "health"])
    r2 = _make_routine(tags=["evening"])
    r3 = _make_routine(tags=["morning", "focus"])
    for r in (r1, r2, r3):
        await manager.create_routine(r)

    morning = await manager.list_routines(tags=["morning"])
    morning_ids = {r.id for r in morning}
    assert r1.id in morning_ids
    assert r3.id in morning_ids
    assert r2.id not in morning_ids

    all_routines = await manager.list_routines()
    assert len(all_routines) == 3


@pytest.mark.asyncio
async def test_low_energy_variant(manager: RoutineManager) -> None:
    """Low energy variant stores different steps and is used when variant='low_energy'."""
    std_steps = _make_steps(5, energy="high")
    le_steps = _make_steps(2, energy="low")
    routine = _make_routine(steps=std_steps, low_steps=le_steps)
    await manager.create_routine(routine)

    loaded = await manager.get_routine(routine.id)
    assert loaded is not None
    assert loaded.low_energy is not None
    assert len(loaded.low_energy.steps) == 2
    assert loaded.low_energy.steps[0].energy_required == "low"
    assert len(loaded.standard.steps) == 5

    session_id = str(uuid.uuid4())
    state = await manager.start_session(
        routine.id, session_id, variant="low_energy"
    )
    assert state.variant == "low_energy"

    # Complete both low-energy steps
    state = await manager.advance_step(session_id, 0)
    state = await manager.advance_step(session_id, 1)

    progress = manager.get_progress(state, loaded)
    assert progress.total_steps == 2
    assert progress.completed == 2
    assert progress.completion_pct == 100.0
    assert "You did it" in progress.message


@pytest.mark.asyncio
async def test_get_routine_not_found(manager: RoutineManager) -> None:
    """get_routine returns None for a non-existent ID."""
    result = await manager.get_routine("nonexistent-id")
    assert result is None


@pytest.mark.asyncio
async def test_get_session_not_found(manager: RoutineManager) -> None:
    """get_session returns None for a non-existent session ID."""
    result = await manager.get_session("nonexistent-session")
    assert result is None
