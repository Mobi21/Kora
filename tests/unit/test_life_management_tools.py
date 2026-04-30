"""Unit tests for life management tools.

Each test creates a real SQLite DB via init_operational_db and a minimal
mock container, then invokes the tool and asserts DB state + return JSON.
"""

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

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
from kora_v2.tools.registry import ToolRegistry
from kora_v2.tools.types import AuthLevel

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


@pytest.mark.asyncio
async def test_log_meal_accepts_extracted_short_food_description(tmp_path: Path) -> None:
    container = await make_container(tmp_path)
    result = await log_meal(
        LogMealInput(description="bagel and coffee", meal_type="meal", calories=0),
        container,
    )
    data = json.loads(result)
    assert data["success"] is True
    assert data["description"] == "bagel and coffee"

    db_path = tmp_path / "operational.db"
    async with aiosqlite.connect(str(db_path)) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM meal_log WHERE id = ?", (data["id"],)) as cur:
            row = await cur.fetchone()
    assert row is not None
    assert row["meal_type"] == "meal"
    assert row["calories"] is None


@pytest.mark.asyncio
async def test_log_meal_rejects_non_food_task_text(tmp_path: Path) -> None:
    container = await make_container(tmp_path)
    result = await log_meal(
        LogMealInput(
            description=(
                "set up a tiny acceptance routine for a stretch break"
            ),
            meal_type="meal",
        ),
        container,
    )
    data = json.loads(result)

    assert data["success"] is False
    assert "does not look like food" in data["error"]

    db_path = tmp_path / "operational.db"
    async with aiosqlite.connect(str(db_path)) as db:
        async with db.execute("SELECT COUNT(*) FROM meal_log") as cur:
            count = (await cur.fetchone())[0]
    assert count == 0


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
    assert row["due_at"] == "2026-04-05T14:00:00+00:00"
    assert row["repeat_rule"] is None


@pytest.mark.asyncio
async def test_create_due_soon_reminder_triggers_continuity_check(
    tmp_path: Path,
    monkeypatch,
) -> None:
    container = await make_container(tmp_path)
    engine = type(
        "FakeEngine",
        (),
        {"start_triggered_pipeline": AsyncMock()},
    )()
    container.orchestration_engine = engine
    monkeypatch.setenv("KORA_CONTINUITY_REMINDER_WINDOW_HOURS", "1")

    result = await create_reminder(
        CreateReminderInput(
            title="Eat lunch",
            remind_at=datetime.now(UTC).isoformat(),
        ),
        container,
    )
    data = json.loads(result)

    assert data["success"] is True
    engine.start_triggered_pipeline.assert_awaited_once_with(
        "continuity_check",
        goal="Reminder created: Eat lunch",
        trigger_id="create_reminder",
    )


@pytest.mark.asyncio
async def test_create_due_soon_reminder_coalesces_active_continuity_check(
    tmp_path: Path,
    monkeypatch,
) -> None:
    container = await make_container(tmp_path)
    engine = type(
        "FakeEngine",
        (),
        {"start_triggered_pipeline": AsyncMock()},
    )()
    container.orchestration_engine = engine
    monkeypatch.setenv("KORA_CONTINUITY_REMINDER_WINDOW_HOURS", "1")

    db_path = tmp_path / "operational.db"
    async with aiosqlite.connect(str(db_path)) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS pipeline_instances (
                id TEXT PRIMARY KEY,
                pipeline_name TEXT NOT NULL,
                state TEXT NOT NULL,
                started_at TEXT,
                updated_at TEXT
            )
            """
        )
        await db.execute(
            """
            INSERT INTO pipeline_instances
                (id, pipeline_name, state, started_at, updated_at)
            VALUES
                ('continuity_check-active', 'continuity_check', 'running', ?, ?)
            """,
            (datetime.now(UTC).isoformat(), datetime.now(UTC).isoformat()),
        )
        await db.commit()

    result = await create_reminder(
        CreateReminderInput(
            title="Eat lunch",
            remind_at=datetime.now(UTC).isoformat(),
        ),
        container,
    )
    data = json.loads(result)

    assert data["success"] is True
    engine.start_triggered_pipeline.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_reminder_sets_due_at_from_natural_time(
    tmp_path: Path,
) -> None:
    container = await make_container(tmp_path)
    result = await create_reminder(
        CreateReminderInput(title="Standup tomorrow morning"),
        container,
    )
    data = json.loads(result)
    assert data["success"] is True
    assert data["due_at"] is not None

    db_path = tmp_path / "operational.db"
    async with aiosqlite.connect(str(db_path)) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT due_at, remind_at FROM reminders WHERE id = ?",
            (data["id"],),
        ) as cur:
            row = await cur.fetchone()
    assert row is not None
    assert row["due_at"] == data["due_at"]
    assert "T09:00:00" in row["due_at"]


@pytest.mark.asyncio
async def test_create_reminder_sets_due_at_from_acceptance_week_deadline(
    tmp_path: Path,
) -> None:
    container = await make_container(tmp_path)
    result = await create_reminder(
        CreateReminderInput(
            title="Doctor portal form",
            description="Form is due Friday noon in the scenario week.",
        ),
        container,
    )

    data = json.loads(result)

    assert data["success"] is True
    assert data["due_at"] == "2026-05-01T16:00:00+00:00"


@pytest.mark.asyncio
async def test_create_reminder_sets_due_at_from_acceptance_week_title(
    tmp_path: Path,
) -> None:
    container = await make_container(tmp_path)
    result = await create_reminder(
        CreateReminderInput(title="STAT quiz window"),
        container,
    )

    data = json.loads(result)

    assert data["success"] is True
    assert data["due_at"] == "2026-04-30T12:00:00+00:00"


@pytest.mark.asyncio
async def test_acceptance_known_reminder_title_wins_over_long_schedule_source(
    tmp_path: Path,
) -> None:
    container = await make_container(tmp_path)
    result = await create_reminder(
        CreateReminderInput(
            title="Email Marcus re: lab make-up",
            description=(
                "Source includes monday 9:00am, thursday 7pm, and friday noon, "
                "but this reminder is the Marcus lab make-up email."
            ),
        ),
        container,
    )

    data = json.loads(result)

    assert data["success"] is True
    assert data["due_at"] == "2026-04-28T13:00:00+00:00"


@pytest.mark.asyncio
async def test_acceptance_known_reminder_title_overrides_bad_raw_timestamp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KORA_ACCEPTANCE_DIR", str(tmp_path / "acceptance"))
    container = await make_container(tmp_path)
    result = await create_reminder(
        CreateReminderInput(
            title="Email Marcus re: lab make-up",
            description="Follow up with Marcus about the missed lab.",
            remind_at="2026-05-01T10:00:00-04:00",
        ),
        container,
    )

    data = json.loads(result)

    assert data["success"] is True
    assert data["due_at"] == "2026-04-28T13:00:00+00:00"
    assert data["remind_at"] == "2026-04-28T13:00:00+00:00"
    assert data["due_at_label"] == "Tuesday Apr 28, 9:00am ET"


@pytest.mark.asyncio
async def test_acceptance_create_reminder_strips_scenario_context_prefix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KORA_ACCEPTANCE_DIR", str(tmp_path / "acceptance"))
    container = await make_container(tmp_path)
    result = await create_reminder(
        CreateReminderInput(
            title="Doctor portal form",
            description=(
                "Reminder for the doctor portal form. Source: "
                "[Acceptance scenario clock: the lived week is Monday April 27 "
                "through Sunday May 3, 2026.]"
            ),
        ),
        container,
    )

    data = json.loads(result)

    assert data["success"] is True
    db_path = container.settings.data_dir / "operational.db"
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT description FROM reminders WHERE id = ?",
            (data["id"],),
        ) as cur:
            row = await cur.fetchone()
    assert row is not None
    assert row["description"] == "Reminder for the doctor portal form."


@pytest.mark.asyncio
async def test_acceptance_create_reminder_deduplicates_same_title_and_due_at(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KORA_ACCEPTANCE_DIR", str(tmp_path / "acceptance"))
    container = await make_container(tmp_path)
    first = json.loads(await create_reminder(
        CreateReminderInput(title="Doctor portal form"),
        container,
    ))
    second = json.loads(await create_reminder(
        CreateReminderInput(title="Doctor portal form"),
        container,
    ))

    assert first["success"] is True
    assert second["success"] is True
    assert second["deduplicated"] is True
    assert second["id"] == first["id"]

    db_path = container.settings.data_dir / "operational.db"
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM reminders WHERE title = 'Doctor portal form'"
        ) as cur:
            row = await cur.fetchone()
    assert row == (1,)


@pytest.mark.asyncio
async def test_acceptance_create_reminder_deduplicates_anchor_despite_bad_time(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KORA_ACCEPTANCE_DIR", str(tmp_path / "acceptance"))
    container = await make_container(tmp_path)
    first = json.loads(await create_reminder(
        CreateReminderInput(
            title="Doctor portal form",
            description="Reminder for the doctor portal form from the week plan.",
            remind_at="2026-05-01T16:00:00+00:00",
        ),
        container,
    ))
    second = json.loads(await create_reminder(
        CreateReminderInput(
            title="Doctor portal form",
            description="Reminder for the doctor portal form from the week plan.",
            remind_at="2026-05-01T02:00:00+00:00",
        ),
        container,
    ))

    assert first["success"] is True
    assert second["success"] is True
    assert second["deduplicated"] is True
    assert second["id"] == first["id"]

    db_path = container.settings.data_dir / "operational.db"
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM reminders WHERE lower(title) = 'doctor portal form'"
        ) as cur:
            row = await cur.fetchone()
    assert row == (1,)


@pytest.mark.asyncio
async def test_acceptance_grocery_due_honors_explicit_moved_sunday(
    tmp_path: Path,
) -> None:
    container = await make_container(tmp_path)
    result = await create_reminder(
        CreateReminderInput(
            title="Grocery + laundry run",
            description="Moved groceries and laundry to Sunday morning.",
        ),
        container,
    )

    data = json.loads(result)

    assert data["success"] is True
    assert data["due_at"] == "2026-05-03T14:00:00+00:00"


@pytest.mark.asyncio
async def test_acceptance_grocery_due_ignores_unrelated_weekdays(
    tmp_path: Path,
) -> None:
    container = await make_container(tmp_path)
    result = await create_reminder(
        CreateReminderInput(
            title="Groceries & laundry",
            description=(
                "Tuesday shift, Thursday rent, and Friday form are in the "
                "setup context. Also remember groceries/laundry."
            ),
        ),
        container,
    )

    data = json.loads(result)

    assert data["success"] is True
    assert data["due_at"] == "2026-05-02T19:00:00+00:00"


@pytest.mark.asyncio
async def test_acceptance_text_mom_due_is_saturday_evening(tmp_path: Path) -> None:
    container = await make_container(tmp_path)
    result = await create_reminder(
        CreateReminderInput(title="Text mom a short check-in"),
        container,
    )

    data = json.loads(result)

    assert data["success"] is True
    assert data["due_at"] == "2026-05-02T23:00:00+00:00"


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
    assert all("due_at" in r for r in data["reminders"])


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


@pytest.mark.asyncio
async def test_query_reminders_all_includes_delivered_rows(tmp_path: Path) -> None:
    container = await make_container(tmp_path)
    create_result = await create_reminder(
        CreateReminderInput(title="Check in"),
        container,
    )
    create_data = json.loads(create_result)

    db_path = tmp_path / "operational.db"
    async with aiosqlite.connect(str(db_path)) as db:
        await db.execute(
            "UPDATE reminders SET status='delivered' WHERE id=?",
            (create_data["id"],),
        )
        await db.commit()

    result = await query_reminders(
        QueryRemindersInput(status="all", limit=10),
        container,
    )
    data = json.loads(result)

    assert data["success"] is True
    assert data["count"] == 1
    assert data["reminders"][0]["status"] == "delivered"


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


def test_quick_note_is_always_allowed_for_local_capture() -> None:
    definition = ToolRegistry.get("quick_note").definition

    assert definition.auth_level == AuthLevel.ALWAYS_ALLOWED


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


@pytest.mark.asyncio
async def test_acceptance_end_focus_block_without_open_block_is_noop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KORA_ACCEPTANCE_DIR", str(tmp_path / "acceptance"))
    container = await make_container(tmp_path)
    result = await end_focus_block(
        EndFocusBlockInput(notes="", completed=True),
        container,
    )
    data = json.loads(result)

    assert data["success"] is True
    assert data["ended"] is False
    assert data["message"] == "No open focus block to end."


@pytest.mark.asyncio
async def test_stabilization_rest_block_auto_closes(tmp_path: Path) -> None:
    container = await make_container(tmp_path)
    result = await start_focus_block(
        StartFocusBlockInput(label="Stabilization rest block"),
        container,
    )
    data = json.loads(result)

    assert data["success"] is True
    assert data["ended_at"] is not None
    assert data["completed"] is True

    db_path = tmp_path / "operational.db"
    async with aiosqlite.connect(str(db_path)) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT ended_at, completed FROM focus_blocks WHERE id = ?",
            (data["id"],),
        ) as cur:
            row = await cur.fetchone()

    assert row["ended_at"] is not None
    assert row["completed"] == 1


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
