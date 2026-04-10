"""Phase 6 DB schema tests — new tables created and usable."""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
import pytest

from kora_v2.core.db import init_operational_db


# ── Fixture ───────────────────────────────────────────────────────────────


@pytest.fixture
async def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "operational.db"
    await init_operational_db(path)
    return path


def _now() -> str:
    return datetime.now(UTC).isoformat()


# ── Tests ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_all_new_tables_exist(db_path: Path) -> None:
    """All Phase 6 tables are created by init_operational_db."""
    expected_tables = {
        "autonomous_plans",
        "items",
        "item_state_history",
        "item_artifact_links",
        "item_deps",
        "routines",
        "routine_sessions",
    }
    async with aiosqlite.connect(str(db_path)) as db:
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        rows = await cursor.fetchall()
    existing = {row[0] for row in rows}
    assert expected_tables.issubset(existing), (
        f"Missing tables: {expected_tables - existing}"
    )


@pytest.mark.asyncio
async def test_items_insert_and_query(db_path: Path) -> None:
    """Items can be inserted and queried back."""
    item_id = str(uuid.uuid4())
    now = _now()
    async with aiosqlite.connect(str(db_path)) as db:
        db.row_factory = aiosqlite.Row
        await db.execute(
            """
            INSERT INTO items
                (id, type, owner, title, description, status,
                 energy_level, estimated_minutes, context_tags,
                 progress_pct, created_at, updated_at)
            VALUES
                (?, 'task', 'kora', 'Test task', 'A test item',
                 'planned', 'medium', 30, '["focus"]', 0.0, ?, ?)
            """,
            (item_id, now, now),
        )
        await db.commit()

        cursor = await db.execute(
            "SELECT * FROM items WHERE id = ?", (item_id,)
        )
        row = await cursor.fetchone()

    assert row is not None
    assert row["id"] == item_id
    assert row["title"] == "Test task"
    assert row["status"] == "planned"
    assert row["owner"] == "kora"
    assert json.loads(row["context_tags"]) == ["focus"]


@pytest.mark.asyncio
async def test_item_state_history_insert_and_query(db_path: Path) -> None:
    """item_state_history records can be inserted and queried."""
    item_id = str(uuid.uuid4())
    now = _now()
    async with aiosqlite.connect(str(db_path)) as db:
        db.row_factory = aiosqlite.Row
        await db.execute(
            "INSERT INTO items (id, type, owner, title, status, created_at, updated_at)"
            " VALUES (?, 'task', 'kora', 'H item', 'planned', ?, ?)",
            (item_id, now, now),
        )
        await db.execute(
            """
            INSERT INTO item_state_history
                (item_id, from_status, to_status, reason, recorded_at)
            VALUES (?, 'planned', 'in_progress', 'Starting work', ?)
            """,
            (item_id, now),
        )
        await db.commit()

        cursor = await db.execute(
            "SELECT * FROM item_state_history WHERE item_id = ?", (item_id,)
        )
        row = await cursor.fetchone()

    assert row is not None
    assert row["item_id"] == item_id
    assert row["from_status"] == "planned"
    assert row["to_status"] == "in_progress"
    assert row["reason"] == "Starting work"


@pytest.mark.asyncio
async def test_item_artifact_links_insert_and_query(db_path: Path) -> None:
    """item_artifact_links can be inserted and queried."""
    item_id = str(uuid.uuid4())
    now = _now()
    async with aiosqlite.connect(str(db_path)) as db:
        db.row_factory = aiosqlite.Row
        await db.execute(
            "INSERT INTO items (id, type, owner, title, status, created_at, updated_at)"
            " VALUES (?, 'task', 'kora', 'Art item', 'planned', ?, ?)",
            (item_id, now, now),
        )
        artifact_id = str(uuid.uuid4())
        await db.execute(
            """
            INSERT INTO item_artifact_links
                (item_id, artifact_id, artifact_type, uri, label, size_bytes, created_at)
            VALUES (?, ?, 'file', '/tmp/output.txt', 'Output file', 1024, ?)
            """,
            (item_id, artifact_id, now),
        )
        await db.commit()

        cursor = await db.execute(
            "SELECT * FROM item_artifact_links WHERE item_id = ?", (item_id,)
        )
        row = await cursor.fetchone()

    assert row is not None
    assert row["item_id"] == item_id
    assert row["artifact_type"] == "file"
    assert row["uri"] == "/tmp/output.txt"
    assert row["size_bytes"] == 1024


@pytest.mark.asyncio
async def test_routines_insert_and_query(db_path: Path) -> None:
    """Routines table accepts insert and returns correct data."""
    routine_id = str(uuid.uuid4())
    now = _now()
    steps = json.dumps([{"index": 0, "title": "Step 0", "description": "Do it"}])
    async with aiosqlite.connect(str(db_path)) as db:
        db.row_factory = aiosqlite.Row
        await db.execute(
            """
            INSERT INTO routines
                (id, name, description, steps_json, tags, created_at, updated_at)
            VALUES (?, 'Morning', 'Start well', ?, '["health"]', ?, ?)
            """,
            (routine_id, steps, now, now),
        )
        await db.commit()

        cursor = await db.execute(
            "SELECT * FROM routines WHERE id = ?", (routine_id,)
        )
        row = await cursor.fetchone()

    assert row is not None
    assert row["name"] == "Morning"
    assert json.loads(row["tags"]) == ["health"]
    loaded_steps = json.loads(row["steps_json"])
    assert len(loaded_steps) == 1
    assert loaded_steps[0]["title"] == "Step 0"


@pytest.mark.asyncio
async def test_routine_sessions_insert_and_query(db_path: Path) -> None:
    """routine_sessions table stores and retrieves session state."""
    routine_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    now = _now()
    steps = json.dumps([{"index": 0, "title": "S0", "description": "Go"}])

    async with aiosqlite.connect(str(db_path)) as db:
        db.row_factory = aiosqlite.Row
        await db.execute(
            "INSERT INTO routines (id, name, steps_json, created_at, updated_at)"
            " VALUES (?, 'R', ?, ?, ?)",
            (routine_id, steps, now, now),
        )
        await db.execute(
            """
            INSERT INTO routine_sessions
                (id, routine_id, variant, current_step_index,
                 completed_steps, skipped_steps, completion_confidence,
                 status, started_at)
            VALUES (?, ?, 'standard', 0, '[]', '[]', 0.0, 'active', ?)
            """,
            (session_id, routine_id, now),
        )
        await db.commit()

        cursor = await db.execute(
            "SELECT * FROM routine_sessions WHERE id = ?", (session_id,)
        )
        row = await cursor.fetchone()

    assert row is not None
    assert row["id"] == session_id
    assert row["routine_id"] == routine_id
    assert row["variant"] == "standard"
    assert row["status"] == "active"
    assert json.loads(row["completed_steps"]) == []
    assert json.loads(row["skipped_steps"]) == []


@pytest.mark.asyncio
async def test_autonomous_plans_insert_and_query(db_path: Path) -> None:
    """autonomous_plans table stores goal-level items correctly."""
    plan_id = str(uuid.uuid4())
    now = _now()
    async with aiosqlite.connect(str(db_path)) as db:
        db.row_factory = aiosqlite.Row
        await db.execute(
            """
            INSERT INTO autonomous_plans
                (id, goal, mode, status, confidence, created_at)
            VALUES (?, 'Finish the report', 'task', 'planned', 0.85, ?)
            """,
            (plan_id, now),
        )
        await db.commit()

        cursor = await db.execute(
            "SELECT * FROM autonomous_plans WHERE id = ?", (plan_id,)
        )
        row = await cursor.fetchone()

    assert row is not None
    assert row["goal"] == "Finish the report"
    assert row["mode"] == "task"
    assert row["status"] == "planned"
    assert abs(row["confidence"] - 0.85) < 1e-6


@pytest.mark.asyncio
async def test_item_deps_insert_and_query(db_path: Path) -> None:
    """item_deps stores dependency relationships between items."""
    now = _now()
    id_a, id_b = str(uuid.uuid4()), str(uuid.uuid4())
    async with aiosqlite.connect(str(db_path)) as db:
        db.row_factory = aiosqlite.Row
        for item_id, title in ((id_a, "Item A"), (id_b, "Item B")):
            await db.execute(
                "INSERT INTO items (id, type, owner, title, status, created_at, updated_at)"
                " VALUES (?, 'task', 'kora', ?, 'planned', ?, ?)",
                (item_id, title, now, now),
            )
        await db.execute(
            "INSERT INTO item_deps (from_item, to_item, rel_type) VALUES (?, ?, 'blocks')",
            (id_a, id_b),
        )
        await db.commit()

        cursor = await db.execute(
            "SELECT * FROM item_deps WHERE from_item = ?", (id_a,)
        )
        row = await cursor.fetchone()

    assert row is not None
    assert row["from_item"] == id_a
    assert row["to_item"] == id_b
    assert row["rel_type"] == "blocks"


@pytest.mark.asyncio
async def test_init_is_idempotent(db_path: Path) -> None:
    """Calling init_operational_db twice does not raise or corrupt the DB."""
    await init_operational_db(db_path)  # second call
    async with aiosqlite.connect(str(db_path)) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM routines")
        row = await cursor.fetchone()
    assert row[0] == 0  # empty but intact
