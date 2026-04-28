"""Phase 8a unit tests — soft-delete schema, read-path filtering,
new ProjectionDB methods, FilesystemMemoryStore methods, signal queue,
and session transcript persistence.

Uses real SQLite (with sqlite-vec when available) for projection tests.
Embedding model is mocked -- no 270 MB download.
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import UTC, datetime
from unittest.mock import MagicMock

# pysqlite3 monkey-patch: must happen before aiosqlite imports sqlite3
try:
    import pysqlite3 as _pysqlite3  # type: ignore[import-untyped]
    sys.modules["sqlite3"] = _pysqlite3
except ImportError:
    pass

import aiosqlite
import pytest

# ==================================================================
# Mock embedding model (deterministic, no real model loaded)
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
# Shared fixtures
# ==================================================================


@pytest.fixture
async def projection_db(tmp_path):
    """Real ProjectionDB with sqlite-vec loaded and migrations applied."""
    from kora_v2.memory.projection import ProjectionDB

    db_path = tmp_path / "test_projection.db"
    db = await ProjectionDB.initialize(db_path)
    yield db
    await db.close()


@pytest.fixture
def memory_store(tmp_path):
    """FilesystemMemoryStore rooted in a temp directory."""
    from kora_v2.memory.store import FilesystemMemoryStore

    return FilesystemMemoryStore(tmp_path / "_KoraMemory")


@pytest.fixture
def mock_embedding_model():
    return MockEmbeddingModel()


async def _insert_memory(
    db,
    memory_id: str,
    content: str,
    embedding_model,
    *,
    status: str = "active",
    source_path: str = "/tmp/test.md",
):
    """Helper to insert a memory with a given status."""
    now = datetime.now(UTC).isoformat(timespec="seconds")
    embedding = embedding_model.embed(content)
    rowid = await db.index_memory(
        memory_id=memory_id,
        content=content,
        summary=f"Summary of {content}",
        importance=0.5,
        memory_type="episodic",
        created_at=now,
        updated_at=now,
        entities=None,
        tags=None,
        source_path=source_path,
        embedding=embedding,
    )
    if status != "active":
        await db._db.execute(
            "UPDATE memories SET status = ? WHERE id = ?",
            (status, memory_id),
        )
        await db._db.commit()
    return rowid


async def _insert_fact(
    db,
    fact_id: str,
    content: str,
    embedding_model,
    *,
    status: str = "active",
    domain: str = "identity",
):
    """Helper to insert a user model fact with a given status."""
    now = datetime.now(UTC).isoformat(timespec="seconds")
    embedding = embedding_model.embed(content)
    rowid = await db.index_user_model_fact(
        fact_id=fact_id,
        domain=domain,
        content=content,
        confidence=0.8,
        evidence_count=1,
        contradiction_count=0,
        created_at=now,
        updated_at=now,
        source_path="/tmp/test_fact.md",
        embedding=embedding,
    )
    if status != "active":
        await db._db.execute(
            "UPDATE user_model_facts SET status = ? WHERE id = ?",
            (status, fact_id),
        )
        await db._db.commit()
    return rowid


# ==================================================================
# 1. Soft-delete schema migration tests
# ==================================================================


class TestSoftDeleteMigration:
    """Soft-delete schema migration applies correctly."""

    async def test_memories_has_status_column(self, projection_db):
        """memories table has status column after migration."""
        cursor = await projection_db._db.execute("PRAGMA table_info(memories)")
        columns = {row[1] for row in await cursor.fetchall()}
        assert "status" in columns

    async def test_memories_has_consolidated_into_column(self, projection_db):
        """memories table has consolidated_into column."""
        cursor = await projection_db._db.execute("PRAGMA table_info(memories)")
        columns = {row[1] for row in await cursor.fetchall()}
        assert "consolidated_into" in columns

    async def test_memories_has_merged_from_column(self, projection_db):
        """memories table has merged_from column."""
        cursor = await projection_db._db.execute("PRAGMA table_info(memories)")
        columns = {row[1] for row in await cursor.fetchall()}
        assert "merged_from" in columns

    async def test_memories_has_deleted_at_column(self, projection_db):
        """memories table has deleted_at column."""
        cursor = await projection_db._db.execute("PRAGMA table_info(memories)")
        columns = {row[1] for row in await cursor.fetchall()}
        assert "deleted_at" in columns

    async def test_user_model_facts_has_status_column(self, projection_db):
        """user_model_facts table has status column."""
        cursor = await projection_db._db.execute(
            "PRAGMA table_info(user_model_facts)"
        )
        columns = {row[1] for row in await cursor.fetchall()}
        assert "status" in columns

    async def test_user_model_facts_has_soft_delete_columns(self, projection_db):
        """user_model_facts has consolidated_into, merged_from, deleted_at."""
        cursor = await projection_db._db.execute(
            "PRAGMA table_info(user_model_facts)"
        )
        columns = {row[1] for row in await cursor.fetchall()}
        assert "consolidated_into" in columns
        assert "merged_from" in columns
        assert "deleted_at" in columns

    async def test_default_status_is_active(self, projection_db, mock_embedding_model):
        """New records get status='active' by default."""
        await _insert_memory(
            projection_db, "test-1", "Test content", mock_embedding_model
        )
        cursor = await projection_db._db.execute(
            "SELECT status FROM memories WHERE id = 'test-1'"
        )
        row = await cursor.fetchone()
        assert row[0] == "active"

    async def test_status_index_exists(self, projection_db):
        """Index idx_memories_status exists."""
        cursor = await projection_db._db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_memories_status'"
        )
        row = await cursor.fetchone()
        assert row is not None


# ==================================================================
# 2. get_memory_by_id filtering tests
# ==================================================================


class TestGetMemoryByIdFiltering:
    """get_memory_by_id filters soft-deleted records by default."""

    async def test_returns_active_memory(self, projection_db, mock_embedding_model):
        """Active memory is returned normally."""
        await _insert_memory(
            projection_db, "active-1", "Active memory", mock_embedding_model
        )
        result = await projection_db.get_memory_by_id("active-1")
        assert result is not None
        assert result["id"] == "active-1"

    async def test_filters_soft_deleted_by_default(
        self, projection_db, mock_embedding_model
    ):
        """Soft-deleted memory returns None by default."""
        await _insert_memory(
            projection_db,
            "deleted-1",
            "Deleted memory",
            mock_embedding_model,
            status="soft_deleted",
        )
        result = await projection_db.get_memory_by_id("deleted-1")
        assert result is None

    async def test_returns_soft_deleted_with_flag(
        self, projection_db, mock_embedding_model
    ):
        """Soft-deleted memory returned when include_soft_deleted=True."""
        await _insert_memory(
            projection_db,
            "deleted-2",
            "Deleted memory 2",
            mock_embedding_model,
            status="soft_deleted",
        )
        result = await projection_db.get_memory_by_id(
            "deleted-2", include_soft_deleted=True
        )
        assert result is not None
        assert result["id"] == "deleted-2"

    async def test_filters_merged_by_default(
        self, projection_db, mock_embedding_model
    ):
        """Merged memory returns None by default."""
        await _insert_memory(
            projection_db,
            "merged-1",
            "Merged memory",
            mock_embedding_model,
            status="merged",
        )
        result = await projection_db.get_memory_by_id("merged-1")
        assert result is None


# ==================================================================
# 3. get_fact_by_id filtering tests
# ==================================================================


class TestGetFactByIdFiltering:
    """get_fact_by_id filters soft-deleted facts by default."""

    async def test_returns_active_fact(self, projection_db, mock_embedding_model):
        """Active fact is returned normally."""
        await _insert_fact(
            projection_db, "fact-active-1", "User likes coffee", mock_embedding_model
        )
        result = await projection_db.get_fact_by_id("fact-active-1")
        assert result is not None
        assert result["id"] == "fact-active-1"

    async def test_filters_soft_deleted_fact_by_default(
        self, projection_db, mock_embedding_model
    ):
        """Soft-deleted fact returns None by default."""
        await _insert_fact(
            projection_db,
            "fact-deleted-1",
            "Old fact",
            mock_embedding_model,
            status="soft_deleted",
        )
        result = await projection_db.get_fact_by_id("fact-deleted-1")
        assert result is None

    async def test_returns_soft_deleted_fact_with_flag(
        self, projection_db, mock_embedding_model
    ):
        """Soft-deleted fact returned when include_soft_deleted=True."""
        await _insert_fact(
            projection_db,
            "fact-deleted-2",
            "Another old fact",
            mock_embedding_model,
            status="soft_deleted",
        )
        result = await projection_db.get_fact_by_id(
            "fact-deleted-2", include_soft_deleted=True
        )
        assert result is not None
        assert result["id"] == "fact-deleted-2"


# ==================================================================
# 4. Vector search excludes soft-deleted records
# ==================================================================


class TestVectorSearchFiltering:
    """vector_search excludes soft-deleted records."""

    async def test_vector_search_excludes_soft_deleted(
        self, projection_db, mock_embedding_model
    ):
        """Vector search does not return soft-deleted memories."""
        if not projection_db._vector_available:
            pytest.skip("sqlite-vec not available")

        await _insert_memory(
            projection_db, "vec-active", "The weather is sunny", mock_embedding_model
        )
        await _insert_memory(
            projection_db,
            "vec-deleted",
            "The weather is sunny today",
            mock_embedding_model,
            status="soft_deleted",
        )

        from kora_v2.memory.retrieval import vector_search

        query_embedding = mock_embedding_model.embed("sunny weather")
        results = await vector_search(projection_db, query_embedding, table="memories")

        result_ids = {r.id for r in results}
        assert "vec-active" in result_ids
        assert "vec-deleted" not in result_ids


# ==================================================================
# 5. FTS5 search excludes soft-deleted records
# ==================================================================


class TestFTS5SearchFiltering:
    """fts5_search excludes soft-deleted records."""

    async def test_fts5_excludes_soft_deleted(
        self, projection_db, mock_embedding_model
    ):
        """FTS5 search does not return soft-deleted memories."""
        await _insert_memory(
            projection_db,
            "fts-active",
            "Bicycle riding in the park is fun",
            mock_embedding_model,
        )
        await _insert_memory(
            projection_db,
            "fts-deleted",
            "Bicycle riding downtown is dangerous",
            mock_embedding_model,
            status="soft_deleted",
        )

        from kora_v2.memory.retrieval import fts5_search

        results = await fts5_search(projection_db, "bicycle riding")

        result_ids = {r.id for r in results}
        assert "fts-active" in result_ids
        assert "fts-deleted" not in result_ids


# ==================================================================
# 6. hybrid_search excludes soft-deleted records
# ==================================================================


class TestHybridSearchFiltering:
    """hybrid_search excludes soft-deleted records."""

    async def test_hybrid_excludes_soft_deleted(
        self, projection_db, mock_embedding_model
    ):
        """Hybrid search does not return soft-deleted memories."""
        await _insert_memory(
            projection_db,
            "hybrid-active",
            "Machine learning algorithms are fascinating",
            mock_embedding_model,
        )
        await _insert_memory(
            projection_db,
            "hybrid-deleted",
            "Machine learning algorithms are hard",
            mock_embedding_model,
            status="soft_deleted",
        )

        from kora_v2.memory.retrieval import hybrid_search

        query_embedding = mock_embedding_model.embed("machine learning")
        results = await hybrid_search(
            projection_db,
            query="machine learning",
            query_embedding=query_embedding,
        )

        result_ids = {r.id for r in results}
        assert "hybrid-active" in result_ids
        assert "hybrid-deleted" not in result_ids


# ==================================================================
# 7. recall() never returns soft-deleted entries
# ==================================================================


class TestRecallFiltering:
    """recall() tool never returns soft-deleted entries."""

    async def test_recall_excludes_soft_deleted(
        self, projection_db, mock_embedding_model
    ):
        """recall() does not return soft-deleted memories."""
        await _insert_memory(
            projection_db,
            "recall-active",
            "Python programming language is versatile",
            mock_embedding_model,
        )
        await _insert_memory(
            projection_db,
            "recall-deleted",
            "Python programming language tutorial",
            mock_embedding_model,
            status="soft_deleted",
        )

        from kora_v2.tools.recall import recall

        # Create a mock container
        container = MagicMock()
        container.embedding_model = mock_embedding_model
        container.projection_db = projection_db

        result_json = await recall(
            query="Python programming",
            container=container,
        )
        result = json.loads(result_json)
        result_ids = {r["id"] for r in result.get("results", [])}

        assert "recall-active" in result_ids
        assert "recall-deleted" not in result_ids


# ==================================================================
# 8. consolidate() only considers active records
# ==================================================================


class TestConsolidateFiltering:
    """consolidate() only considers active records."""

    async def test_consolidate_skips_soft_deleted(
        self, projection_db, mock_embedding_model
    ):
        """consolidate() does not include soft-deleted records in candidates."""
        if not projection_db._vector_available:
            pytest.skip("sqlite-vec not available")

        # Insert identical content -- one active, one soft-deleted
        await _insert_memory(
            projection_db,
            "cons-active",
            "identical content for consolidation test",
            mock_embedding_model,
        )
        await _insert_memory(
            projection_db,
            "cons-deleted",
            "identical content for consolidation test",
            mock_embedding_model,
            status="soft_deleted",
        )

        groups = await projection_db.consolidate(threshold=0.5)
        # The soft-deleted record should not appear in any group
        for group in groups:
            record_ids = {r.id for r in group.records}
            assert "cons-deleted" not in record_ids


# ==================================================================
# 9. deduplicate() only considers active records
# ==================================================================


class TestDeduplicateFiltering:
    """deduplicate() only considers active records."""

    async def test_deduplicate_skips_soft_deleted(
        self, projection_db, mock_embedding_model
    ):
        """deduplicate() does not pair soft-deleted records."""
        if not projection_db._vector_available:
            pytest.skip("sqlite-vec not available")

        await _insert_memory(
            projection_db,
            "dedup-active",
            "exact duplicate content here",
            mock_embedding_model,
        )
        await _insert_memory(
            projection_db,
            "dedup-deleted",
            "exact duplicate content here",
            mock_embedding_model,
            status="soft_deleted",
        )

        pairs = await projection_db.deduplicate(threshold=0.5)
        for pair in pairs:
            assert pair.record_a.id != "dedup-deleted"
            assert pair.record_b.id != "dedup-deleted"

    async def test_deduplicate_includes_exact_text_rows_without_vectors(
        self, projection_db
    ):
        """Exact active duplicates are found even if vector rows are missing."""
        now = datetime.now(UTC).isoformat(timespec="seconds")
        duplicate = "same local first dashboard preference"
        for memory_id in ("text-only-a", "text-only-b"):
            await projection_db._db.execute(
                "INSERT INTO memories "
                "(id, content, summary, importance, memory_type, created_at, "
                "updated_at, entities, tags, source_path, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    memory_id,
                    duplicate,
                    None,
                    0.5,
                    "semantic",
                    now,
                    now,
                    None,
                    None,
                    f"/tmp/{memory_id}.md",
                    "active",
                ),
            )
        await projection_db._db.commit()

        pairs = await projection_db.deduplicate(threshold=0.92)
        pair_ids = {frozenset((p.record_a.id, p.record_b.id)) for p in pairs}

        assert frozenset(("text-only-a", "text-only-b")) in pair_ids


# ==================================================================
# 10. soft_delete() correctly marks records
# ==================================================================


class TestSoftDelete:
    """soft_delete() correctly marks records."""

    async def test_soft_delete_memory(self, projection_db, mock_embedding_model):
        """soft_delete marks a memory as soft_deleted."""
        await _insert_memory(
            projection_db, "sd-1", "Content to delete", mock_embedding_model
        )

        await projection_db.soft_delete(
            table="memories",
            record_id="sd-1",
            successor_id="sd-successor",
            reason="consolidated",
        )

        # Should not be returned by default
        result = await projection_db.get_memory_by_id("sd-1")
        assert result is None

        # Should be returned with include_soft_deleted
        result = await projection_db.get_memory_by_id("sd-1", include_soft_deleted=True)
        assert result is not None
        assert result["status"] == "merged"  # "consolidated" reason maps to "merged"
        assert result["consolidated_into"] == "sd-successor"
        assert result["deleted_at"] is not None

    async def test_soft_delete_user_model_fact(
        self, projection_db, mock_embedding_model
    ):
        """soft_delete marks a user model fact as soft_deleted."""
        await _insert_fact(
            projection_db, "sd-fact-1", "Fact to delete", mock_embedding_model
        )

        await projection_db.soft_delete(
            table="user_model_facts",
            record_id="sd-fact-1",
            successor_id=None,
            reason="obsolete",
        )

        result = await projection_db.get_fact_by_id("sd-fact-1")
        assert result is None

        result = await projection_db.get_fact_by_id(
            "sd-fact-1", include_soft_deleted=True
        )
        assert result is not None
        assert result["status"] == "soft_deleted"

    async def test_soft_delete_invalid_table(self, projection_db):
        """soft_delete raises ValueError for invalid table names."""
        with pytest.raises(ValueError, match="Invalid table"):
            await projection_db.soft_delete(
                table="invalid_table",
                record_id="x",
                successor_id=None,
                reason="test",
            )


# ==================================================================
# 11. FilesystemMemoryStore.update_frontmatter() preserves body
# ==================================================================


class TestUpdateFrontmatter:
    """update_frontmatter() preserves body content."""

    async def test_preserves_body(self, memory_store):
        """Updating frontmatter does not change the note body."""
        meta = await memory_store.write_note(
            content="This is the body text.",
            memory_type="episodic",
            importance=0.7,
            tags=["test"],
        )

        # Update frontmatter with new fields
        result = await memory_store.update_frontmatter(
            meta.id,
            {"custom_field": "custom_value"},
        )
        assert result is not None

        # Read back and verify body is preserved
        note = await memory_store.read_note(meta.id)
        assert note is not None
        assert note.body == "This is the body text."

    async def test_adds_new_fields(self, memory_store):
        """update_frontmatter can add new fields."""
        meta = await memory_store.write_note(
            content="Body here.",
            memory_type="episodic",
        )

        await memory_store.update_frontmatter(
            meta.id,
            {"consolidated_into": "other-id", "status": "merged"},
        )

        # Read raw file to check frontmatter
        note = await memory_store.read_note(meta.id)
        assert note is not None
        # The body should still be intact
        assert note.body == "Body here."

    async def test_returns_none_for_missing_note(self, memory_store):
        """update_frontmatter returns None if note doesn't exist."""
        result = await memory_store.update_frontmatter(
            "nonexistent-id", {"key": "val"}
        )
        assert result is None


# ==================================================================
# 12. FilesystemMemoryStore.soft_delete_note() updates both FS and DB
# ==================================================================


class TestSoftDeleteNote:
    """soft_delete_note() updates both filesystem and projection DB."""

    async def test_updates_filesystem(self, memory_store):
        """soft_delete_note updates note frontmatter on filesystem."""
        meta = await memory_store.write_note(
            content="To be soft-deleted.",
            memory_type="episodic",
        )

        result = await memory_store.soft_delete_note(
            meta.id,
            reason="consolidated",
            successor_id="successor-1",
        )
        assert result is True

        # Read back and verify frontmatter was updated
        file_path = memory_store._find_note_file(meta.id)
        assert file_path is not None
        text = file_path.read_text(encoding="utf-8")
        assert "merged" in text or "soft_deleted" in text
        assert "deleted_at" in text

    async def test_updates_projection_db(self, memory_store, projection_db, mock_embedding_model):
        """soft_delete_note updates projection DB when provided."""
        # Write a note to filesystem and index it
        meta = await memory_store.write_note(
            content="Content for projection test.",
            memory_type="episodic",
        )
        await _insert_memory(
            projection_db,
            meta.id,
            "Content for projection test.",
            mock_embedding_model,
        )

        # Soft-delete with projection DB
        result = await memory_store.soft_delete_note(
            meta.id,
            reason="duplicate",
            successor_id="new-id",
            projection_db=projection_db,
        )
        assert result is True

        # Check projection DB was updated
        record = await projection_db.get_memory_by_id(meta.id)
        assert record is None  # Filtered out

        record = await projection_db.get_memory_by_id(
            meta.id, include_soft_deleted=True
        )
        assert record is not None
        assert record["status"] in ("merged", "soft_deleted")

    async def test_returns_false_for_missing_note(self, memory_store):
        """soft_delete_note returns False for nonexistent notes."""
        result = await memory_store.soft_delete_note(
            "nonexistent", reason="test", successor_id=None
        )
        assert result is False


# ==================================================================
# 13. FilesystemMemoryStore.update_body() preserves frontmatter
# ==================================================================


class TestUpdateBody:
    """update_body() preserves frontmatter."""

    async def test_preserves_frontmatter(self, memory_store):
        """Updating body preserves all frontmatter fields."""
        meta = await memory_store.write_note(
            content="Original body.",
            memory_type="procedural",
            importance=0.9,
            tags=["important", "keep"],
            entities=["Alice"],
        )

        result = await memory_store.update_body(meta.id, "New body content.")
        assert result is not None

        # Read back
        note = await memory_store.read_note(meta.id)
        assert note is not None
        assert note.body == "New body content."
        assert note.metadata.memory_type == "procedural"
        assert note.metadata.importance == 0.9
        assert "important" in note.metadata.tags
        assert "Alice" in note.metadata.entities

    async def test_returns_none_for_missing_note(self, memory_store):
        """update_body returns None for nonexistent notes."""
        result = await memory_store.update_body("nonexistent", "new body")
        assert result is None


# ==================================================================
# 14. FilesystemMemoryStore.move_note() updates path in DB
# ==================================================================


class TestMoveNote:
    """move_note() moves file and updates projection DB."""

    async def test_moves_file(self, memory_store, tmp_path):
        """move_note moves the file to the new path."""
        meta = await memory_store.write_note(
            content="Content to move.",
            memory_type="episodic",
        )

        new_path = tmp_path / "moved" / f"{meta.id}.md"
        result = await memory_store.move_note(meta.id, new_path)
        assert result is not None
        assert result.source_path == str(new_path)
        assert new_path.exists()

    async def test_updates_projection_db_path(
        self, memory_store, projection_db, mock_embedding_model, tmp_path
    ):
        """move_note updates source_path in projection DB."""
        meta = await memory_store.write_note(
            content="Content to move with db.",
            memory_type="episodic",
        )
        await _insert_memory(
            projection_db,
            meta.id,
            "Content to move with db.",
            mock_embedding_model,
        )

        new_path = tmp_path / "moved_db" / f"{meta.id}.md"
        await memory_store.move_note(
            meta.id, new_path, projection_db=projection_db
        )

        # Check that projection DB has the new path
        record = await projection_db.get_memory_by_id(meta.id)
        assert record is not None
        assert record["source_path"] == str(new_path)

    async def test_returns_none_for_missing_note(self, memory_store, tmp_path):
        """move_note returns None for nonexistent notes."""
        result = await memory_store.move_note(
            "nonexistent", tmp_path / "nowhere.md"
        )
        assert result is None


# ==================================================================
# 15. Signal queue persistence
# ==================================================================


class TestSignalQueuePersistence:
    """Signal queue persistence works end-to-end."""

    async def test_signal_queue_table_created(self, tmp_path):
        """signal_queue table is created by init_operational_db."""
        from kora_v2.core.db import init_operational_db

        db_path = tmp_path / "operational.db"
        await init_operational_db(db_path)

        async with aiosqlite.connect(str(db_path)) as db:
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='signal_queue'"
            )
            row = await cursor.fetchone()
            assert row is not None

    async def test_signal_queue_insert(self, tmp_path):
        """Can insert into signal_queue table."""
        from kora_v2.core.db import init_operational_db

        db_path = tmp_path / "operational.db"
        await init_operational_db(db_path)

        async with aiosqlite.connect(str(db_path)) as db:
            await db.execute(
                "INSERT INTO signal_queue "
                "(id, session_id, message_text, signal_types, priority, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    "sig-1",
                    "sess-1",
                    "I got promoted today!",
                    '["life_event"]',
                    2,
                    "pending",
                    datetime.now(UTC).isoformat(),
                ),
            )
            await db.commit()

            cursor = await db.execute(
                "SELECT * FROM signal_queue WHERE id = 'sig-1'"
            )
            row = await cursor.fetchone()
            assert row is not None

    async def test_scan_and_persist_signals(self, tmp_path):
        """_scan_and_persist_signals correctly persists scanner results."""
        from kora_v2.core.db import init_operational_db
        from kora_v2.daemon.session import SessionManager
        from kora_v2.memory.signal_scanner import SignalScanner

        db_path = tmp_path / "operational.db"
        await init_operational_db(db_path)

        # Create a mock container with settings
        settings = MagicMock()
        settings.data_dir = tmp_path
        container = MagicMock()
        container.settings = settings

        mgr = SessionManager(container)
        scanner = SignalScanner()

        messages = [
            {"role": "user", "content": "I got promoted at work today!"},
            {"role": "assistant", "content": "Congratulations! That's wonderful news."},
            {"role": "user", "content": "Thanks!"},
        ]

        await mgr._scan_and_persist_signals(scanner, messages, "test-session")

        # Check signal_queue has entries
        async with aiosqlite.connect(str(db_path)) as db:
            cursor = await db.execute("SELECT COUNT(*) FROM signal_queue")
            row = await cursor.fetchone()
            # The first message about promotion should generate a signal
            assert row[0] >= 1


# ==================================================================
# 16. Session transcript persistence
# ==================================================================


class TestSessionTranscriptPersistence:
    """Session transcript persistence works."""

    async def test_session_transcripts_table_created(self, tmp_path):
        """session_transcripts table is created by init_operational_db."""
        from kora_v2.core.db import init_operational_db

        db_path = tmp_path / "operational.db"
        await init_operational_db(db_path)

        async with aiosqlite.connect(str(db_path)) as db:
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='session_transcripts'"
            )
            row = await cursor.fetchone()
            assert row is not None

    async def test_transcript_insert(self, tmp_path):
        """Can insert into session_transcripts table."""
        from kora_v2.core.db import init_operational_db

        db_path = tmp_path / "operational.db"
        await init_operational_db(db_path)

        now = datetime.now(UTC).isoformat(timespec="seconds")
        messages = [
            {"role": "user", "content": "Hello", "timestamp": now},
            {"role": "assistant", "content": "Hi there!", "timestamp": now},
        ]

        async with aiosqlite.connect(str(db_path)) as db:
            await db.execute(
                "INSERT INTO session_transcripts "
                "(session_id, created_at, ended_at, message_count, messages, emotional_trajectory) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    "sess-1",
                    now,
                    now,
                    2,
                    json.dumps(messages),
                    "neutral",
                ),
            )
            await db.commit()

            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM session_transcripts WHERE session_id = 'sess-1'"
            )
            row = await cursor.fetchone()
            assert row is not None
            rd = dict(row)
            assert rd["message_count"] == 2
            assert rd["processed_at"] is None

    async def test_persist_session_transcript(self, tmp_path):
        """_persist_session_transcript writes correct data."""
        from kora_v2.core.db import init_operational_db
        from kora_v2.core.models import EmotionalState, EnergyEstimate, SessionState
        from kora_v2.daemon.session import SessionManager

        db_path = tmp_path / "operational.db"
        await init_operational_db(db_path)

        settings = MagicMock()
        settings.data_dir = tmp_path
        container = MagicMock()
        container.settings = settings

        mgr = SessionManager(container)
        mgr.active_session = SessionState(
            session_id="transcript-test",
            turn_count=3,
            started_at=datetime.now(UTC),
            emotional_state=EmotionalState(
                valence=0.0, arousal=0.3, dominance=0.5,
            ),
            energy_estimate=EnergyEstimate(
                level="medium", focus="normal", confidence=0.5
            ),
        )

        messages = [
            {"role": "user", "content": "What's the weather?"},
            {"role": "assistant", "content": "It's sunny today."},
            {"role": "user", "content": "Thanks!"},
        ]

        await mgr._persist_session_transcript(
            session_id="transcript-test",
            messages=messages,
            emotional_trajectory="neutral throughout",
        )

        async with aiosqlite.connect(str(db_path)) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM session_transcripts WHERE session_id = 'transcript-test'"
            )
            row = await cursor.fetchone()
            assert row is not None
            rd = dict(row)
            assert rd["message_count"] == 3
            assert rd["emotional_trajectory"] == "neutral throughout"
            assert rd["processed_at"] is None

            # Verify messages JSON structure
            msgs = json.loads(rd["messages"])
            assert len(msgs) == 3
            assert msgs[0]["role"] == "user"
            assert msgs[0]["content"] == "What's the weather?"


# ==================================================================
# 17. Entity query methods
# ==================================================================


class TestEntityQueries:
    """Entity query methods work correctly."""

    async def test_get_memories_by_entity(self, projection_db, mock_embedding_model):
        """get_memories_by_entity returns active linked memories."""
        await _insert_memory(
            projection_db,
            "ent-mem-1",
            "Alice went to the park",
            mock_embedding_model,
        )

        entity_id = await projection_db.find_or_create_entity("Alice", "person")
        await projection_db.link_entity(entity_id, memory_id="ent-mem-1", fact_id=None, relationship="mentioned")

        results = await projection_db.get_memories_by_entity("Alice")
        assert len(results) == 1
        assert results[0].id == "ent-mem-1"

    async def test_get_memories_by_entity_excludes_soft_deleted(
        self, projection_db, mock_embedding_model
    ):
        """get_memories_by_entity excludes soft-deleted memories."""
        await _insert_memory(
            projection_db,
            "ent-mem-del",
            "Bob was here",
            mock_embedding_model,
            status="soft_deleted",
        )

        entity_id = await projection_db.find_or_create_entity("Bob", "person")
        await projection_db.link_entity(entity_id, memory_id="ent-mem-del", fact_id=None, relationship="mentioned")

        results = await projection_db.get_memories_by_entity("Bob")
        assert len(results) == 0

    async def test_merge_entities(self, projection_db, mock_embedding_model):
        """merge_entities re-links and removes source entity."""
        await _insert_memory(
            projection_db,
            "merge-mem",
            "Content about merging",
            mock_embedding_model,
        )

        source_id = await projection_db.find_or_create_entity("Bob Smith", "person")
        target_id = await projection_db.find_or_create_entity("Robert Smith", "person")
        await projection_db.link_entity(source_id, memory_id="merge-mem", fact_id=None, relationship="mentioned")

        await projection_db.merge_entities(source_id, target_id)

        # Source entity should be gone
        cursor = await projection_db._db.execute(
            "SELECT * FROM entities WHERE id = ?", (source_id,)
        )
        assert await cursor.fetchone() is None

        # Links should point to target
        cursor = await projection_db._db.execute(
            "SELECT entity_id FROM entity_links WHERE memory_id = 'merge-mem'"
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] == target_id

        cursor = await projection_db._db.execute(
            "SELECT metadata FROM entities WHERE id = ?", (target_id,)
        )
        row = await cursor.fetchone()
        assert row is not None
        metadata = json.loads(row[0])
        assert metadata["merged_from"][0]["id"] == source_id
        assert metadata["merged_from"][0]["name"] == "Bob Smith"
