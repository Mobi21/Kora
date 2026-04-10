"""Unit tests for life management tools.

Each test creates a real SQLite DB via init_operational_db and a minimal
mock container, then invokes the tool and asserts DB state + return JSON.
"""

import json
import time
from pathlib import Path
from typing import Any

import aiosqlite
import pytest

from kora_v2.core.db import init_operational_db
from kora_v2.tools.life_management import (
    CreateReminderInput,
    EndFocusBlockInput,
    LogMealInput,
    LogMedicationInput,
    QueryRemindersInput,
    QuickNoteInput,
    StartFocusBlockInput,
    create_reminder,
    end_focus_block,
    log_meal,
    log_medication,
    query_reminders,
    quick_note,
    start_focus_block,
)

# ── Helpers ───────────────────────────────────────────────────────────────────


async def make_container(tmp_path: Path) -> Any:
    """Create a real operational.db and return a minimal mock container."""
    db_path = tmp_path / "operational.db"
    await init_operational_db(db_path)

    class FakeSettings:
        @property
        def data_dir(self) -> Path:
            return tmp_path

    class FakeContainer:
        settings = FakeSettings()

    return FakeContainer()


# ── log_medication ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_log_medication_inserts_row(tmp_path: Path) -> None:
    container = await make_container(tmp_path)
    result = await log_medication(
        LogMedicationInput(medication_name="Vyvanse", dose="30mg", notes="with food"),
        container,
    )
    data = json.loads(result)
    assert data["success"] is True
    assert data["medication_name"] == "Vyvanse"
    assert "id" in data

    # Verify DB row
    db_path = tmp_path / "operational.db"
    async with aiosqlite.connect(str(db_path)) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM medication_log WHERE id = ?", (data["id"],)) as cur:
            row = await cur.fetchone()
    assert row is not None
    assert row["medication_name"] == "Vyvanse"
    assert row["dose"] == "30mg"
    assert row["notes"] == "with food"


# ── log_meal ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_log_meal_inserts_row(tmp_path: Path) -> None:
    container = await make_container(tmp_path)
    result = await log_meal(
        LogMealInput(description="Chicken salad", meal_type="lunch", calories=450),
        container,
    )
    data = json.loads(result)
    assert data["success"] is True
    assert data["description"] == "Chicken salad"
    assert "id" in data

    db_path = tmp_path / "operational.db"
    async with aiosqlite.connect(str(db_path)) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM meal_log WHERE id = ?", (data["id"],)) as cur:
            row = await cur.fetchone()
    assert row is not None
    assert row["meal_type"] == "lunch"
    assert row["calories"] == 450


# ── create_reminder ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_reminder_inserts_row(tmp_path: Path) -> None:
    container = await make_container(tmp_path)
    result = await create_reminder(
        CreateReminderInput(
            title="Take afternoon meds",
            description="Vyvanse booster",
            remind_at="2026-04-05T14:00:00+00:00",
        ),
        container,
    )
    data = json.loads(result)
    assert data["success"] is True
    assert data["title"] == "Take afternoon meds"
    assert data["status"] == "pending"
    assert "id" in data

    db_path = tmp_path / "operational.db"
    async with aiosqlite.connect(str(db_path)) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM reminders WHERE id = ?", (data["id"],)) as cur:
            row = await cur.fetchone()
    assert row is not None
    assert row["title"] == "Take afternoon meds"
    assert row["status"] == "pending"


# ── query_reminders ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_query_reminders_returns_created_reminder(tmp_path: Path) -> None:
    container = await make_container(tmp_path)

    # Create a reminder first
    create_result = await create_reminder(
        CreateReminderInput(title="Check in"),
        container,
    )
    create_data = json.loads(create_result)
    assert create_data["success"] is True

    # Now query
    result = await query_reminders(
        QueryRemindersInput(status="pending", limit=10),
        container,
    )
    data = json.loads(result)
    assert data["success"] is True
    assert data["count"] >= 1
    titles = [r["title"] for r in data["reminders"]]
    assert "Check in" in titles


@pytest.mark.asyncio
async def test_query_reminders_empty_for_unknown_status(tmp_path: Path) -> None:
    container = await make_container(tmp_path)
    result = await query_reminders(
        QueryRemindersInput(status="done", limit=10),
        container,
    )
    data = json.loads(result)
    assert data["success"] is True
    assert data["count"] == 0
    assert data["reminders"] == []


# ── quick_note ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_quick_note_inserts_row(tmp_path: Path) -> None:
    container = await make_container(tmp_path)
    result = await quick_note(
        QuickNoteInput(content="Remember to buy groceries", tags="shopping,errands"),
        container,
    )
    data = json.loads(result)
    assert data["success"] is True
    assert "id" in data
    assert data["message"] == "Note captured"

    db_path = tmp_path / "operational.db"
    async with aiosqlite.connect(str(db_path)) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM quick_notes WHERE id = ?", (data["id"],)) as cur:
            row = await cur.fetchone()
    assert row is not None
    assert row["content"] == "Remember to buy groceries"
    assert row["tags"] == "shopping,errands"


# ── start_focus_block ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_start_focus_block_inserts_open_block(tmp_path: Path) -> None:
    container = await make_container(tmp_path)
    result = await start_focus_block(
        StartFocusBlockInput(label="Deep Work", notes="Working on feature X"),
        container,
    )
    data = json.loads(result)
    assert data["success"] is True
    assert data["label"] == "Deep Work"
    assert "id" in data

    db_path = tmp_path / "operational.db"
    async with aiosqlite.connect(str(db_path)) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM focus_blocks WHERE id = ?", (data["id"],)
        ) as cur:
            row = await cur.fetchone()
    assert row is not None
    assert row["ended_at"] is None
    assert row["completed"] == 0


# ── end_focus_block ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_end_focus_block_closes_open_block(tmp_path: Path) -> None:
    container = await make_container(tmp_path)

    # Start a block
    start_result = await start_focus_block(
        StartFocusBlockInput(label="Sprint"),
        container,
    )
    start_data = json.loads(start_result)
    assert start_data["success"] is True
    block_id = start_data["id"]

    # Small sleep to ensure duration > 0
    time.sleep(0.05)

    # End the block
    end_result = await end_focus_block(
        EndFocusBlockInput(notes="Got a lot done!", completed=True),
        container,
    )
    end_data = json.loads(end_result)
    assert end_data["success"] is True
    assert end_data["id"] == block_id
    assert end_data["completed"] is True
    assert "duration_minutes" in end_data

    # Verify DB row was updated
    db_path = tmp_path / "operational.db"
    async with aiosqlite.connect(str(db_path)) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM focus_blocks WHERE id = ?", (block_id,)
        ) as cur:
            row = await cur.fetchone()
    assert row["ended_at"] is not None
    assert row["completed"] == 1


@pytest.mark.asyncio
async def test_end_focus_block_no_open_block_returns_error(tmp_path: Path) -> None:
    container = await make_container(tmp_path)
    result = await end_focus_block(
        EndFocusBlockInput(notes="", completed=True),
        container,
    )
    data = json.loads(result)
    assert data["success"] is False
    assert "no open focus block" in data["error"]


# ── Error handling ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_log_medication_no_data_dir_returns_error() -> None:
    """Tool returns error JSON when container.settings.data_dir is None."""

    class NoDataDirSettings:
        data_dir = None

    class BadContainer:
        settings = NoDataDirSettings()

    result = await log_medication(
        LogMedicationInput(medication_name="Aspirin"),
        BadContainer(),
    )
    data = json.loads(result)
    assert data["success"] is False
    assert "no database available" in data["error"]
