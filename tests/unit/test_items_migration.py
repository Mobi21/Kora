"""Unit tests for the Phase 5 items-table migration."""

from __future__ import annotations

import aiosqlite

from kora_v2.core.db import init_operational_db


async def _column_set(db_path, table: str) -> set[str]:
    async with aiosqlite.connect(str(db_path)) as db:
        async with db.execute(f"PRAGMA table_info({table})") as cur:
            rows = await cur.fetchall()
    return {row[1] for row in rows}


async def test_items_table_gets_new_columns(tmp_path):
    db_path = tmp_path / "op.db"
    await init_operational_db(db_path)
    cols = await _column_set(db_path, "items")
    assert {"due_date", "priority", "goal_scope"} <= cols


async def test_focus_blocks_gets_calendar_entry_id(tmp_path):
    db_path = tmp_path / "op.db"
    await init_operational_db(db_path)
    cols = await _column_set(db_path, "focus_blocks")
    assert "calendar_entry_id" in cols


async def test_migrations_are_idempotent(tmp_path):
    db_path = tmp_path / "op.db"
    await init_operational_db(db_path)
    # Insert a row with new columns populated.
    async with aiosqlite.connect(str(db_path)) as db:
        await db.execute(
            "INSERT INTO items (id, type, owner, title, status, priority, "
            "due_date, goal_scope, created_at, updated_at) "
            "VALUES ('i1', 'task', 'kora', 'Test', 'planned', 2, "
            "'2026-04-13', 'weekly_goal', datetime('now'), datetime('now'))"
        )
        await db.commit()
    # Re-initialize — must be a no-op for existing rows.
    await init_operational_db(db_path)
    async with aiosqlite.connect(str(db_path)) as db:
        async with db.execute(
            "SELECT priority, due_date, goal_scope FROM items WHERE id = 'i1'"
        ) as cur:
            row = await cur.fetchone()
    assert row == (2, "2026-04-13", "weekly_goal")
