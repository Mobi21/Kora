"""Tests for autonomous loop budget persistence and update records.

Verifies that the execution loop properly:
- writes budget counters to autonomous_plans after each execute_step
- writes checkpoint / completion records to autonomous_updates
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest

from kora_v2.autonomous.loop import AutonomousExecutionLoop
from kora_v2.autonomous.state import AutonomousState
from kora_v2.core.db import init_operational_db


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
async def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "operational.db"
    await init_operational_db(path)
    return path


def _make_container() -> SimpleNamespace:
    """Build a minimal container mock for the loop."""
    settings = SimpleNamespace(
        autonomous=SimpleNamespace(
            enabled=True,
            request_warning_threshold=0.85,
            request_hard_stop_threshold=1.0,
            checkpoint_interval_minutes=30,
            auto_continue_seconds=0,
            max_requests=100,
            max_tokens=500_000,
            max_cost=5.0,
            max_wall_clock_minutes=120,
        ),
        llm=SimpleNamespace(
            model="test-model",
            api_key="test-key",
        ),
        data_dir=Path("data"),
    )
    container = SimpleNamespace(
        settings=settings,
        event_emitter=None,  # No event emitter for these tests
    )
    return container


def _make_state(
    session_id: str = "test-sess",
    plan_id: str = "test-plan",
    **overrides: Any,
) -> AutonomousState:
    """Build a simple AutonomousState for tests."""
    defaults = {
        "session_id": session_id,
        "plan_id": plan_id,
        "mode": "task",
        "status": "executing",
        "pending_step_ids": ["step-1"],
        "completed_step_ids": [],
        "request_count": 0,
        "token_estimate": 0,
        "cost_estimate": 0.0,
        "elapsed_seconds": 10,
        "metadata": {"goal": "Test goal"},
    }
    defaults.update(overrides)
    return AutonomousState(**defaults)


async def _seed_plan(db_path: Path, plan_id: str, session_id: str = "test-sess") -> None:
    """Insert a plan row so UPDATE queries have something to hit."""
    now = datetime.now(UTC).isoformat()
    async with aiosqlite.connect(str(db_path)) as db:
        # Ensure the sessions table has the referenced session_id
        await db.execute(
            "INSERT OR IGNORE INTO sessions (id, started_at) VALUES (?, ?)",
            (session_id, now),
        )
        await db.execute(
            """INSERT OR IGNORE INTO autonomous_plans
               (id, session_id, goal, mode, status, created_at)
               VALUES (?, ?, 'test goal', 'task', 'planned', ?)""",
            (plan_id, session_id, now),
        )
        await db.commit()


# ── _update_plan_budget tests ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_plan_budget_writes_counters(db_path: Path) -> None:
    """_update_plan_budget persists request_count, token_estimate, cost_estimate."""
    plan_id = "plan-budget-1"
    await _seed_plan(db_path, plan_id)

    container = _make_container()
    loop = AutonomousExecutionLoop(
        goal="Test",
        session_id="test-sess",
        container=container,
        db_path=db_path,
    )
    loop._state = _make_state(
        plan_id=plan_id,
        request_count=7,
        token_estimate=42000,
        cost_estimate=1.23,
    )

    await loop._update_plan_budget()

    async with aiosqlite.connect(str(db_path)) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT request_count, token_estimate, cost_estimate, updated_at FROM autonomous_plans WHERE id=?",
            (plan_id,),
        ) as cursor:
            row = await cursor.fetchone()
    assert row is not None
    assert row["request_count"] == 7
    assert row["token_estimate"] == 42000
    assert abs(row["cost_estimate"] - 1.23) < 0.001
    assert row["updated_at"] is not None


@pytest.mark.asyncio
async def test_update_plan_budget_noop_when_no_state(db_path: Path) -> None:
    """_update_plan_budget does nothing when _state is None."""
    container = _make_container()
    loop = AutonomousExecutionLoop(
        goal="Test",
        session_id="test-sess",
        container=container,
        db_path=db_path,
    )
    loop._state = None

    # Should not raise
    await loop._update_plan_budget()


# ── _persist_checkpoint_update tests ──────────────────────────────────────


@pytest.mark.asyncio
async def test_persist_checkpoint_update_inserts_record(db_path: Path) -> None:
    """_persist_checkpoint_update writes a checkpoint row to autonomous_updates."""
    container = _make_container()
    loop = AutonomousExecutionLoop(
        goal="Test",
        session_id="test-sess",
        container=container,
        db_path=db_path,
    )
    loop._state = _make_state(
        completed_step_ids=["s1", "s2"],
        pending_step_ids=["s3"],
    )

    await loop._persist_checkpoint_update(reason="periodic")

    async with aiosqlite.connect(str(db_path)) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM autonomous_updates") as cursor:
            rows = await cursor.fetchall()
    assert len(rows) == 1
    row = dict(rows[0])
    assert row["session_id"] == "test-sess"
    assert row["update_type"] == "checkpoint"
    assert "2 step(s) done" in row["summary"]
    assert row["delivered"] == 0
    payload = json.loads(row["payload"])
    assert payload["reason"] == "periodic"
    assert payload["steps_completed"] == 2
    assert payload["steps_pending"] == 1


# ── _persist_completion_update tests ──────────────────────────────────────


@pytest.mark.asyncio
async def test_persist_completion_update_inserts_completion(db_path: Path) -> None:
    """_persist_completion_update writes a completion row to autonomous_updates."""
    container = _make_container()
    loop = AutonomousExecutionLoop(
        goal="Test",
        session_id="test-sess",
        container=container,
        db_path=db_path,
    )
    loop._state = _make_state(
        status="completed",
        completed_step_ids=["s1", "s2", "s3"],
        pending_step_ids=[],
    )

    await loop._persist_completion_update()

    async with aiosqlite.connect(str(db_path)) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM autonomous_updates") as cursor:
            rows = await cursor.fetchall()
    assert len(rows) == 1
    row = dict(rows[0])
    assert row["update_type"] == "completion"
    assert "finished" in row["summary"]
    assert "3 step(s)" in row["summary"]


@pytest.mark.asyncio
async def test_persist_completion_update_failed_status(db_path: Path) -> None:
    """_persist_completion_update uses 'failed' verb for failed status."""
    container = _make_container()
    loop = AutonomousExecutionLoop(
        goal="Test",
        session_id="test-sess",
        container=container,
        db_path=db_path,
    )
    loop._state = _make_state(
        status="failed",
        completed_step_ids=["s1"],
        pending_step_ids=["s2"],
        metadata={"goal": "Failing task", "failure_reason": "out of budget"},
    )

    await loop._persist_completion_update()

    async with aiosqlite.connect(str(db_path)) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM autonomous_updates") as cursor:
            row = await cursor.fetchone()
    assert row is not None
    row = dict(row)
    assert "failed" in row["summary"]
    payload = json.loads(row["payload"])
    assert payload["failure_reason"] == "out of budget"


# ── Event emission tests ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_emit_checkpoint_event(db_path: Path) -> None:
    """_emit_checkpoint_event calls the emitter with correct EventType."""
    emitter = AsyncMock()
    container = _make_container()
    container.event_emitter = emitter

    loop = AutonomousExecutionLoop(
        goal="Test",
        session_id="test-sess",
        container=container,
        db_path=db_path,
    )
    loop._state = _make_state(
        completed_step_ids=["s1"],
    )

    await loop._emit_checkpoint_event()

    emitter.emit.assert_called_once()
    call_args = emitter.emit.call_args
    from kora_v2.core.events import EventType

    assert call_args[0][0] == EventType.AUTONOMOUS_CHECKPOINT


@pytest.mark.asyncio
async def test_emit_complete_event(db_path: Path) -> None:
    """_emit_complete_event calls the emitter with correct EventType."""
    emitter = AsyncMock()
    container = _make_container()
    container.event_emitter = emitter

    loop = AutonomousExecutionLoop(
        goal="Test",
        session_id="test-sess",
        container=container,
        db_path=db_path,
    )
    loop._state = _make_state(status="completed")

    await loop._emit_complete_event()

    emitter.emit.assert_called_once()
    call_args = emitter.emit.call_args
    from kora_v2.core.events import EventType

    assert call_args[0][0] == EventType.AUTONOMOUS_COMPLETE


@pytest.mark.asyncio
async def test_emit_event_noop_when_no_emitter(db_path: Path) -> None:
    """Event emission is a no-op when container has no event_emitter."""
    container = _make_container()
    container.event_emitter = None

    loop = AutonomousExecutionLoop(
        goal="Test",
        session_id="test-sess",
        container=container,
        db_path=db_path,
    )
    loop._state = _make_state()

    # Should not raise
    await loop._emit_checkpoint_event()
    await loop._emit_complete_event()
