"""Tests for kora_v2.core.db — Operational database schema and init."""

import pytest
import aiosqlite

from kora_v2.core.db import init_operational_db, get_db


class TestInitOperationalDb:
    """Test database initialization and schema creation."""

    async def test_init_operational_db(self, tmp_path):
        """Create DB and verify all tables exist (Phase 4.67 adds 3 more)."""
        db_path = tmp_path / "test_operational.db"
        await init_operational_db(db_path)

        async with aiosqlite.connect(str(db_path)) as db:
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
            tables = {row[0] for row in await cursor.fetchall()}

        base_tables = {
            "audit_log",
            "autonomous_checkpoints",
            "notifications",
            "notification_engagement",
            "quality_evaluations",
            "quality_metrics",
            "sessions",
            "telemetry",
        }
        runtime_tables = {
            "turn_traces",
            "turn_trace_events",
            "permission_grants",
        }
        assert base_tables <= tables
        assert runtime_tables <= tables

    async def test_idempotent_init(self, tmp_path):
        """Call init twice, should not raise errors."""
        db_path = tmp_path / "test_idempotent.db"
        await init_operational_db(db_path)
        await init_operational_db(db_path)  # Second call should be fine

        async with aiosqlite.connect(str(db_path)) as db:
            cursor = await db.execute(
                "SELECT count(*) FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'"
            )
            count = (await cursor.fetchone())[0]
        # Phase 5 added calendar_entries, finance_log, energy_log -> 27.
        # Phase 8a added signal_queue, session_transcripts -> 29.
        # Phase 8b added dedup_rejected_pairs -> 30.
        assert count == 30

    async def test_sessions_table_schema(self, tmp_path):
        """Verify column names for sessions table."""
        db_path = tmp_path / "test_schema.db"
        await init_operational_db(db_path)

        async with aiosqlite.connect(str(db_path)) as db:
            cursor = await db.execute("PRAGMA table_info(sessions)")
            columns = [row[1] for row in await cursor.fetchall()]

        expected = [
            "id",
            "started_at",
            "ended_at",
            "turn_count",
            "duration_seconds",
            "emotional_state_start",
            "emotional_state_end",
            "continuation_of",
            "bridge_note_path",
        ]
        assert columns == expected

    async def test_telemetry_table_schema(self, tmp_path):
        """Verify column names for telemetry table."""
        db_path = tmp_path / "test_telemetry.db"
        await init_operational_db(db_path)

        async with aiosqlite.connect(str(db_path)) as db:
            cursor = await db.execute("PRAGMA table_info(telemetry)")
            columns = [row[1] for row in await cursor.fetchall()]

        expected = [
            "id",
            "session_id",
            "agent_name",
            "tokens_in",
            "tokens_out",
            "latency_ms",
            "tool_calls",
            "quality_gate_passed",
            "recorded_at",
        ]
        assert columns == expected

    async def test_wal_mode_enabled(self, tmp_path):
        """init_operational_db should set WAL journal mode."""
        db_path = tmp_path / "test_wal.db"
        await init_operational_db(db_path)

        async with aiosqlite.connect(str(db_path)) as db:
            cursor = await db.execute("PRAGMA journal_mode")
            mode = (await cursor.fetchone())[0]
        assert mode == "wal"

    async def test_parent_directory_created(self, tmp_path):
        """init should create parent directories if they don't exist."""
        db_path = tmp_path / "subdir" / "deep" / "test.db"
        await init_operational_db(db_path)
        assert db_path.exists()


class TestGetDb:
    """Test the get_db connection factory."""

    async def test_get_db_returns_connection(self, tmp_path):
        """get_db should return an aiosqlite connection."""
        db_path = tmp_path / "test_get.db"
        db = await get_db(db_path)
        try:
            assert db is not None
            # Should be able to execute queries
            await db.execute("SELECT 1")
        finally:
            await db.close()

    async def test_get_db_row_factory(self, tmp_path):
        """get_db should set row_factory to aiosqlite.Row."""
        db_path = tmp_path / "test_row_factory.db"
        db = await get_db(db_path)
        try:
            assert db.row_factory is aiosqlite.Row
        finally:
            await db.close()
