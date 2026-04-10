"""Tests for session write pipeline in SessionManager.

Validates that init_session writes to the sessions table,
end_session updates the record, signal scanner is called,
and everything degrades gracefully when DB is unavailable.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kora_v2.core.models import EmotionalState
from kora_v2.daemon.session import SessionManager


def _make_container(tmp_path: Path, *, with_memory: bool = False, with_scanner: bool = False):
    """Build a minimal mock container with settings pointing to tmp_path."""
    settings = MagicMock()
    settings.data_dir = tmp_path
    settings.memory = MagicMock()
    settings.memory.kora_memory_path = str(tmp_path / "_KoraMemory")

    container = MagicMock()
    container.settings = settings
    container.event_emitter = AsyncMock()
    container.projection_db = None
    container.db = None

    if with_memory:
        memory_store = AsyncMock()
        memory_store.list_notes = AsyncMock(return_value=["pref1", "pref2"])
        container.memory_store = memory_store
    else:
        container.memory_store = None

    if with_scanner:
        scanner = AsyncMock()
        scanner.scan = AsyncMock()
        container.signal_scanner = scanner
    else:
        container.signal_scanner = None

    return container


@pytest.fixture
def db_path(tmp_path):
    """Create operational.db with the sessions schema."""
    import asyncio
    from kora_v2.core.db import init_operational_db

    path = tmp_path / "operational.db"
    asyncio.get_event_loop().run_until_complete(init_operational_db(path))
    return path


@pytest.fixture
def tmp_container(tmp_path, db_path):
    """Container whose settings.data_dir points to tmp_path (containing operational.db)."""
    return _make_container(tmp_path)


class TestInitSessionWrites:
    """init_session should INSERT into the sessions table."""

    @pytest.mark.asyncio
    async def test_writes_session_start(self, tmp_path, db_path):
        container = _make_container(tmp_path)
        mgr = SessionManager(container)
        session = await mgr.init_session()

        # Verify the row exists
        import aiosqlite

        async with aiosqlite.connect(str(db_path)) as db:
            async with db.execute("SELECT id, started_at, emotional_state_start FROM sessions") as cur:
                rows = await cur.fetchall()

        assert len(rows) == 1
        assert rows[0][0] == session.session_id
        assert rows[0][1] is not None  # started_at populated

        # emotional_state_start should be valid JSON
        emo = json.loads(rows[0][2])
        assert "valence" in emo
        assert "mood_label" in emo

    @pytest.mark.asyncio
    async def test_user_context_loaded_from_memory(self, tmp_path, db_path):
        container = _make_container(tmp_path, with_memory=True)
        mgr = SessionManager(container)
        await mgr.init_session()

        # memory_store.list_notes was called
        container.memory_store.list_notes.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_graceful_when_db_missing(self, tmp_path):
        """init_session should not raise even if operational.db is absent."""
        # Don't create the DB -- just point settings at an empty dir
        container = _make_container(tmp_path)
        mgr = SessionManager(container)

        # Should complete without error (DB write will fail silently)
        session = await mgr.init_session()
        assert session.session_id is not None


class TestEndSessionWrites:
    """end_session should UPDATE the sessions row and call signal scanner."""

    @pytest.mark.asyncio
    async def test_updates_session_end(self, tmp_path, db_path):
        container = _make_container(tmp_path, with_scanner=True)
        mgr = SessionManager(container)
        session = await mgr.init_session()

        # Simulate some turns
        mgr.active_session.turn_count = 3

        emotional_state = EmotionalState(
            valence=0.5, arousal=0.6, dominance=0.7,
            mood_label="happy", confidence=0.8, source="fast",
        )
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]

        bridge = await mgr.end_session(messages, emotional_state)

        # Verify DB was updated
        import aiosqlite

        async with aiosqlite.connect(str(db_path)) as db:
            async with db.execute(
                "SELECT ended_at, turn_count, duration_seconds, emotional_state_end FROM sessions WHERE id=?",
                (session.session_id,),
            ) as cur:
                row = await cur.fetchone()

        assert row is not None
        assert row[0] is not None  # ended_at populated
        assert row[1] == 3  # turn_count
        assert row[2] >= 0  # duration_seconds
        emo_end = json.loads(row[3])
        assert emo_end["mood_label"] == "happy"

    @pytest.mark.asyncio
    async def test_signal_scanner_called(self, tmp_path, db_path):
        container = _make_container(tmp_path, with_scanner=True)
        mgr = SessionManager(container)
        await mgr.init_session()

        messages = [{"role": "user", "content": "test"}]
        emotional_state = EmotionalState(
            valence=0, arousal=0.3, dominance=0.5,
            mood_label="neutral", confidence=0.5, source="loaded",
        )

        await mgr.end_session(messages, emotional_state)

        container.signal_scanner.scan.assert_awaited_once_with(messages)

    @pytest.mark.asyncio
    async def test_scanner_failure_does_not_block(self, tmp_path, db_path):
        container = _make_container(tmp_path, with_scanner=True)
        container.signal_scanner.scan = AsyncMock(side_effect=RuntimeError("scanner broke"))
        mgr = SessionManager(container)
        await mgr.init_session()

        messages = [{"role": "user", "content": "test"}]
        emotional_state = EmotionalState(
            valence=0, arousal=0.3, dominance=0.5,
            mood_label="neutral", confidence=0.5, source="loaded",
        )

        # Should not raise
        bridge = await mgr.end_session(messages, emotional_state)
        assert bridge.session_id is not None

    @pytest.mark.asyncio
    async def test_graceful_when_db_unavailable(self, tmp_path):
        """end_session should not raise when the DB is not set up."""
        container = _make_container(tmp_path)
        mgr = SessionManager(container)
        await mgr.init_session()

        emotional_state = EmotionalState(
            valence=0, arousal=0.3, dominance=0.5,
            mood_label="neutral", confidence=0.5, source="loaded",
        )

        bridge = await mgr.end_session([], emotional_state)
        assert bridge.session_id is not None
