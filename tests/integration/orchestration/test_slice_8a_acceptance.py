"""Phase 8a acceptance tests — end-to-end verification of data foundation.

Verifies:
- Soft-delete read filtering across all paths (projection, retrieval, recall)
- Session transcript persistence via SessionManager.end_session()
- Signal queue persistence via SessionManager.end_session()
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import UTC, datetime
from unittest.mock import MagicMock

# pysqlite3 monkey-patch
try:
    import pysqlite3 as _pysqlite3  # type: ignore[import-untyped]
    sys.modules["sqlite3"] = _pysqlite3
except ImportError:
    pass

import aiosqlite
import pytest


# ==================================================================
# Mock embedding model
# ==================================================================


class MockEmbeddingModel:
    """Deterministic mock that returns a 768-dim vector from text hash."""

    dimension = 768
    is_loaded = True

    def embed(self, text: str, task_type: str = "search_query") -> list[float]:
        h = hashlib.md5(text.encode()).digest()
        base = [b / 255.0 for b in h]
        return (base * 48)[:768]

    def embed_batch(
        self,
        texts: list[str],
        task_type: str = "search_document",
        batch_size: int = 64,
    ) -> list[list[float]]:
        return [self.embed(t, task_type) for t in texts]

    def load(self) -> None:
        pass

    def unload(self) -> None:
        pass


# ==================================================================
# Fixtures
# ==================================================================


@pytest.fixture
async def projection_db(tmp_path):
    """ProjectionDB with migrations applied."""
    from kora_v2.memory.projection import ProjectionDB

    db_path = tmp_path / "projection.db"
    db = await ProjectionDB.initialize(db_path)
    yield db
    await db.close()


@pytest.fixture
def mock_embedding_model():
    return MockEmbeddingModel()


@pytest.fixture
async def operational_db(tmp_path):
    """Initialized operational.db path."""
    from kora_v2.core.db import init_operational_db

    db_path = tmp_path / "operational.db"
    await init_operational_db(db_path)
    return db_path


# ==================================================================
# Acceptance Test 1: Soft-delete read filtering across all paths
# ==================================================================


class TestSoftDeleteReadFilteringAcceptance:
    """Soft-delete read filtering verified across all read paths.

    Inserts active and soft-deleted records, then verifies that
    every retrieval path (get_by_id, vector_search, fts5_search,
    hybrid_search, recall) excludes soft-deleted entries.
    """

    async def test_all_read_paths_filter_soft_deleted(
        self, projection_db, mock_embedding_model
    ):
        """End-to-end: no soft-deleted record leaks through any read path."""
        now = datetime.now(UTC).isoformat(timespec="seconds")

        # Insert 3 active + 2 soft-deleted memories
        for i in range(1, 4):
            embedding = mock_embedding_model.embed(f"Active memory {i} about dogs")
            await projection_db.index_memory(
                memory_id=f"accept-active-{i}",
                content=f"Active memory {i} about dogs and parks",
                summary=f"Active {i}",
                importance=0.5,
                memory_type="episodic",
                created_at=now,
                updated_at=now,
                entities=None,
                tags=None,
                source_path=f"/tmp/active_{i}.md",
                embedding=embedding,
            )

        for i in range(1, 3):
            embedding = mock_embedding_model.embed(f"Deleted memory {i} about dogs")
            await projection_db.index_memory(
                memory_id=f"accept-deleted-{i}",
                content=f"Deleted memory {i} about dogs and parks",
                summary=f"Deleted {i}",
                importance=0.5,
                memory_type="episodic",
                created_at=now,
                updated_at=now,
                entities=None,
                tags=None,
                source_path=f"/tmp/deleted_{i}.md",
                embedding=embedding,
            )
            await projection_db.soft_delete(
                table="memories",
                record_id=f"accept-deleted-{i}",
                successor_id=None,
                reason="test_deletion",
            )

        # Path 1: get_memory_by_id
        for i in range(1, 4):
            result = await projection_db.get_memory_by_id(f"accept-active-{i}")
            assert result is not None, f"Active memory {i} should be found"

        for i in range(1, 3):
            result = await projection_db.get_memory_by_id(f"accept-deleted-{i}")
            assert result is None, f"Deleted memory {i} should be filtered"

        # Path 2: vector_search
        from kora_v2.memory.retrieval import vector_search

        if projection_db._vector_available:
            query_emb = mock_embedding_model.embed("dogs parks")
            vec_results = await vector_search(
                projection_db, query_emb, table="memories"
            )
            vec_ids = {r.id for r in vec_results}
            for i in range(1, 3):
                assert f"accept-deleted-{i}" not in vec_ids, \
                    f"vector_search must exclude accept-deleted-{i}"

        # Path 3: fts5_search
        from kora_v2.memory.retrieval import fts5_search

        fts_results = await fts5_search(projection_db, "dogs parks")
        fts_ids = {r.id for r in fts_results}
        for i in range(1, 3):
            assert f"accept-deleted-{i}" not in fts_ids, \
                f"fts5_search must exclude accept-deleted-{i}"

        # Path 4: hybrid_search
        from kora_v2.memory.retrieval import hybrid_search

        query_emb = mock_embedding_model.embed("dogs parks")
        hybrid_results = await hybrid_search(
            projection_db, query="dogs parks", query_embedding=query_emb
        )
        hybrid_ids = {r.id for r in hybrid_results}
        for i in range(1, 3):
            assert f"accept-deleted-{i}" not in hybrid_ids, \
                f"hybrid_search must exclude accept-deleted-{i}"

        # Path 5: recall()
        from kora_v2.tools.recall import recall

        container = MagicMock()
        container.embedding_model = mock_embedding_model
        container.projection_db = projection_db

        recall_json = await recall(query="dogs parks", container=container)
        recall_data = json.loads(recall_json)
        recall_ids = {r["id"] for r in recall_data.get("results", [])}
        for i in range(1, 3):
            assert f"accept-deleted-{i}" not in recall_ids, \
                f"recall must exclude accept-deleted-{i}"

    async def test_soft_deleted_facts_filtered(
        self, projection_db, mock_embedding_model
    ):
        """Soft-deleted user_model_facts are also filtered."""
        now = datetime.now(UTC).isoformat(timespec="seconds")
        embedding = mock_embedding_model.embed("User likes cats")

        await projection_db.index_user_model_fact(
            fact_id="accept-fact-active",
            domain="preferences",
            content="User likes cats",
            confidence=0.9,
            evidence_count=3,
            contradiction_count=0,
            created_at=now,
            updated_at=now,
            source_path="/tmp/fact_active.md",
            embedding=embedding,
        )

        await projection_db.index_user_model_fact(
            fact_id="accept-fact-deleted",
            domain="preferences",
            content="User likes cats too",
            confidence=0.9,
            evidence_count=1,
            contradiction_count=0,
            created_at=now,
            updated_at=now,
            source_path="/tmp/fact_deleted.md",
            embedding=mock_embedding_model.embed("User likes cats too"),
        )
        await projection_db.soft_delete(
            table="user_model_facts",
            record_id="accept-fact-deleted",
            successor_id="accept-fact-active",
            reason="duplicate",
        )

        # get_fact_by_id
        result = await projection_db.get_fact_by_id("accept-fact-active")
        assert result is not None
        result = await projection_db.get_fact_by_id("accept-fact-deleted")
        assert result is None

        # FTS5 search on user_model_facts
        from kora_v2.memory.retrieval import fts5_search

        fts_results = await fts5_search(
            projection_db, "cats", table="user_model_facts"
        )
        fts_ids = {r.id for r in fts_results}
        assert "accept-fact-deleted" not in fts_ids


# ==================================================================
# Acceptance Test 2: Session transcript persistence
# ==================================================================


class TestSessionTranscriptPersistenceAcceptance:
    """Session transcript persistence verified via end_session flow."""

    async def test_end_session_persists_transcript(self, tmp_path):
        """end_session() writes a transcript to session_transcripts."""
        from kora_v2.core.db import init_operational_db
        from kora_v2.core.models import EmotionalState, EnergyEstimate, SessionState
        from kora_v2.daemon.session import SessionManager
        from kora_v2.memory.signal_scanner import SignalScanner

        # Set up operational DB
        db_path = tmp_path / "operational.db"
        await init_operational_db(db_path)

        # Insert a session row (end_session updates it)
        async with aiosqlite.connect(str(db_path)) as db:
            await db.execute(
                "INSERT INTO sessions (id, started_at) VALUES (?, ?)",
                ("accept-session", datetime.now(UTC).isoformat()),
            )
            await db.commit()

        # Set up container
        settings = MagicMock()
        settings.data_dir = tmp_path
        settings.memory = MagicMock()
        settings.memory.kora_memory_path = str(tmp_path / "_KoraMemory")

        container = MagicMock()
        container.settings = settings
        container.signal_scanner = SignalScanner()
        container.event_emitter = None  # Disable event emission for test

        mgr = SessionManager(container)
        mgr.active_session = SessionState(
            session_id="accept-session",
            turn_count=4,
            started_at=datetime.now(UTC),
            emotional_state=EmotionalState(
                valence=0.2, arousal=0.4, dominance=0.5,
            ),
            energy_estimate=EnergyEstimate(
                level="medium", focus="normal", confidence=0.6,
            ),
        )

        messages = [
            {"role": "user", "content": "I started a new job at Google today"},
            {"role": "assistant", "content": "That's amazing! Congratulations!"},
            {"role": "user", "content": "Thanks, I'm really excited about it."},
            {"role": "assistant", "content": "You should be! When do you start?"},
        ]

        emotional_state = EmotionalState(
            valence=0.8, arousal=0.6, dominance=0.7,
            mood_label="excited",
        )

        await mgr.end_session(messages, emotional_state)

        # Verify transcript was persisted
        async with aiosqlite.connect(str(db_path)) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM session_transcripts WHERE session_id = 'accept-session'"
            )
            row = await cursor.fetchone()
            assert row is not None, "Transcript should be persisted"
            rd = dict(row)

            assert rd["message_count"] == 4
            assert rd["processed_at"] is None  # Not yet consumed by Memory Steward

            msgs = json.loads(rd["messages"])
            assert len(msgs) == 4
            assert msgs[0]["content"] == "I started a new job at Google today"

        # Verify signal queue was populated
        async with aiosqlite.connect(str(db_path)) as db:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM signal_queue WHERE session_id = 'accept-session'"
            )
            count_row = await cursor.fetchone()
            # "I started a new job at Google today" matches life_event pattern
            assert count_row[0] >= 1, "Signal queue should have entries from scanning"
