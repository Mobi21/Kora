"""Tests for DB schema migrations — specifically Phase 9 Task 4 columns."""

from __future__ import annotations

import aiosqlite
import pytest

from kora_v2.core.db import init_operational_db


class TestPermissionGrantMigrations:
    """Verify that Phase 9 Task 4 columns are created on permission_grants."""

    async def _columns(self, db: aiosqlite.Connection, table: str) -> set[str]:
        async with db.execute(f"PRAGMA table_info({table})") as cursor:
            rows = await cursor.fetchall()
        return {row[1] for row in rows}

    @pytest.mark.asyncio
    async def test_new_columns_exist_after_init(self, tmp_path):
        db_path = tmp_path / "test.db"
        await init_operational_db(db_path)

        async with aiosqlite.connect(str(db_path)) as db:
            columns = await self._columns(db, "permission_grants")

        assert "capability" in columns
        assert "account" in columns
        assert "action_key" in columns
        assert "resource" in columns

    @pytest.mark.asyncio
    async def test_legacy_columns_still_present(self, tmp_path):
        db_path = tmp_path / "test_legacy.db"
        await init_operational_db(db_path)

        async with aiosqlite.connect(str(db_path)) as db:
            columns = await self._columns(db, "permission_grants")

        # Original columns from the CREATE TABLE statement
        assert "tool_name" in columns
        assert "scope" in columns
        assert "risk_level" in columns
        assert "decision" in columns
        assert "session_id" in columns

    @pytest.mark.asyncio
    async def test_migration_is_idempotent(self, tmp_path):
        """Running init_operational_db twice on the same DB must not raise."""
        db_path = tmp_path / "idempotent.db"
        await init_operational_db(db_path)
        await init_operational_db(db_path)  # must not fail

        async with aiosqlite.connect(str(db_path)) as db:
            columns = await self._columns(db, "permission_grants")

        assert "capability" in columns

    @pytest.mark.asyncio
    async def test_capability_index_exists(self, tmp_path):
        db_path = tmp_path / "index.db"
        await init_operational_db(db_path)

        async with aiosqlite.connect(str(db_path)) as db:
            async with db.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_permission_grants_capability'"
            ) as cursor:
                row = await cursor.fetchone()

        assert row is not None, "idx_permission_grants_capability index should exist"

    @pytest.mark.asyncio
    async def test_new_columns_are_nullable_text(self, tmp_path):
        """New columns must be nullable TEXT (no NOT NULL constraint)."""
        db_path = tmp_path / "nullable.db"
        await init_operational_db(db_path)

        async with aiosqlite.connect(str(db_path)) as db:
            async with db.execute("PRAGMA table_info(permission_grants)") as cursor:
                rows = await cursor.fetchall()

        col_info = {row[1]: row for row in rows}
        for col in ("capability", "account", "action_key", "resource"):
            row = col_info[col]
            # PRAGMA table_info: row[2]=type, row[3]=notnull
            assert row[2].upper() in ("TEXT", ""), f"{col} should be TEXT, got {row[2]}"
            assert row[3] == 0, f"{col} should be nullable (notnull=0), got {row[3]}"

    @pytest.mark.asyncio
    async def test_can_insert_with_capability_columns(self, tmp_path):
        """Sanity: can write a grant row with the new columns populated."""
        import datetime

        db_path = tmp_path / "insert.db"
        await init_operational_db(db_path)

        async with aiosqlite.connect(str(db_path)) as db:
            await db.execute(
                """
                INSERT INTO permission_grants
                    (id, tool_name, scope, risk_level, decision, granted_at,
                     capability, account, action_key, resource)
                VALUES
                    (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "grant-001",
                    "workspace.gmail.send",
                    "session",
                    "medium",
                    "approved",
                    datetime.datetime.utcnow().isoformat(),
                    "workspace",
                    "personal",
                    "gmail.send",
                    None,
                ),
            )
            await db.commit()

            async with db.execute(
                "SELECT capability, account, action_key, resource FROM permission_grants WHERE id='grant-001'"
            ) as cursor:
                row = await cursor.fetchone()

        assert row[0] == "workspace"
        assert row[1] == "personal"
        assert row[2] == "gmail.send"
        assert row[3] is None
