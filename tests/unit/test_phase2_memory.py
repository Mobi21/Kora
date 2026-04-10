"""Phase 2 (Memory) unit tests — 55 tests across all new modules.

Tests cover: MigrationRunner, FilesystemMemoryStore, ProjectionDB,
retrieval (vector, FTS5, merge_and_rank, time-weighting), dedup,
WritePipeline, recall tool, MemoryWorker prompts, and DI container
memory wiring.

Uses real SQLite (with sqlite-vec) for projection tests. Embedding
model is mocked — no 270 MB download.
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

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


# ==================================================================
# 1. Migration Tests
# ==================================================================


class TestMigrationRunner:
    """MigrationRunner applies numbered .sql files and tracks versions."""

    async def test_migration_creates_schema_version_table(self, tmp_path):
        """Running migrations on an empty DB creates schema_version table."""
        from kora_v2.core.migrations import MigrationRunner

        db_path = tmp_path / "empty.db"
        async with aiosqlite.connect(str(db_path), check_same_thread=False) as db:
            runner = MigrationRunner()
            await runner.run_migrations(db, tmp_path / "no_migrations")

            cursor = await db.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='schema_version'"
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == "schema_version"

    async def test_migration_applies_sql_file(self, tmp_path):
        """Migration 001 creates all expected tables."""
        from kora_v2.core.migrations import MigrationRunner

        migrations_dir = Path(__file__).resolve().parents[2] / "kora_v2" / "memory" / "migrations"
        db_path = tmp_path / "test_mig.db"

        async with aiosqlite.connect(str(db_path), check_same_thread=False) as db:
            # sqlite-vec needed for vec0 virtual tables in the migration
            import sqlite_vec

            conn = db._connection
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)

            runner = MigrationRunner()
            applied = await runner.run_migrations(db, migrations_dir)
            assert applied >= 1

            # Verify key tables
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
            tables = {row[0] for row in await cursor.fetchall()}
            assert "memories" in tables
            assert "user_model_facts" in tables
            assert "entities" in tables
            assert "entity_links" in tables

    async def test_migration_is_idempotent(self, tmp_path):
        """Running the same migrations twice produces no errors."""
        from kora_v2.core.migrations import MigrationRunner

        migrations_dir = Path(__file__).resolve().parents[2] / "kora_v2" / "memory" / "migrations"
        db_path = tmp_path / "idem.db"

        async with aiosqlite.connect(str(db_path), check_same_thread=False) as db:
            import sqlite_vec

            conn = db._connection
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)

            runner = MigrationRunner()
            first = await runner.run_migrations(db, migrations_dir)
            second = await runner.run_migrations(db, migrations_dir)

            assert first >= 1
            assert second == 0  # nothing new to apply

    async def test_migration_skips_applied(self, tmp_path):
        """Already-applied versions are not re-applied."""
        from kora_v2.core.migrations import MigrationRunner

        migrations_dir = Path(__file__).resolve().parents[2] / "kora_v2" / "memory" / "migrations"
        db_path = tmp_path / "skip.db"

        async with aiosqlite.connect(str(db_path), check_same_thread=False) as db:
            import sqlite_vec

            conn = db._connection
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)

            runner = MigrationRunner()
            await runner.run_migrations(db, migrations_dir)

            # Check applied versions
            applied = await runner._get_applied_versions(db)
            assert 1 in applied

            # Running again should apply 0
            count = await runner.run_migrations(db, migrations_dir)
            assert count == 0


# ==================================================================
# 2. FilesystemMemoryStore Tests
# ==================================================================


class TestFilesystemMemoryStore:
    """File-backed memory notes with YAML frontmatter."""

    async def test_write_and_read_note(self, memory_store):
        """Round-trip: write a note, read it back, verify content."""
        meta = await memory_store.write_note(
            content="My wife Sarah loves hiking.",
            memory_type="episodic",
            entities=["Sarah"],
            importance=0.7,
        )
        assert meta.id
        assert meta.memory_type == "episodic"

        note = await memory_store.read_note(meta.id)
        assert note is not None
        assert note.body == "My wife Sarah loves hiking."
        assert note.metadata.importance == 0.7
        assert "Sarah" in note.metadata.entities

    async def test_write_note_creates_frontmatter(self, memory_store):
        """Written file contains valid YAML frontmatter."""
        meta = await memory_store.write_note(
            content="Test note body",
            memory_type="reflective",
            tags=["test"],
        )
        # Read the raw file
        note = await memory_store.read_note(meta.id)
        assert note is not None
        assert note.metadata.memory_type == "reflective"
        assert "test" in note.metadata.tags

        # Verify the file starts with frontmatter delimiters
        raw = Path(meta.source_path).read_text()
        assert raw.startswith("---\n")
        assert "memory_type: reflective" in raw

    async def test_read_nonexistent_note(self, memory_store):
        """Reading a note that does not exist returns None."""
        result = await memory_store.read_note("nonexistent-id-12345678")
        assert result is None

    async def test_update_note_preserves_frontmatter(self, memory_store):
        """Updating body text preserves original frontmatter metadata."""
        meta = await memory_store.write_note(
            content="Original body",
            memory_type="episodic",
            entities=["Alice"],
            importance=0.9,
        )

        updated = await memory_store.update_note(meta.id, "Updated body")
        assert updated is not None
        assert updated.importance == 0.9
        assert "Alice" in updated.entities

        note = await memory_store.read_note(meta.id)
        assert note is not None
        assert note.body == "Updated body"
        assert note.metadata.importance == 0.9

    async def test_list_notes_by_layer(self, memory_store):
        """list_notes('long_term') returns only Long-Term notes."""
        await memory_store.write_note(
            content="Long-term memory", memory_type="episodic",
        )
        await memory_store.write_note(
            content="User fact", memory_type="user_model", domain="identity",
        )

        lt_notes = await memory_store.list_notes(layer="long_term")
        um_notes = await memory_store.list_notes(layer="user_model")

        assert len(lt_notes) == 1
        assert lt_notes[0].memory_type == "episodic"
        assert len(um_notes) == 1
        assert um_notes[0].memory_type == "user_model"

    async def test_delete_note(self, memory_store):
        """Deleting a note removes the file, returns True."""
        meta = await memory_store.write_note(content="To be deleted")
        assert await memory_store.delete_note(meta.id) is True
        assert await memory_store.read_note(meta.id) is None
        # Deleting again returns False
        assert await memory_store.delete_note(meta.id) is False

    async def test_write_note_with_explicit_id(self, memory_store):
        """Providing an explicit note_id uses that ID instead of generating one."""
        meta = await memory_store.write_note(
            content="Explicit ID test",
            note_id="custom-id-123",
        )
        assert meta.id == "custom-id-123"

        note = await memory_store.read_note("custom-id-123")
        assert note is not None
        assert note.body == "Explicit ID test"

    async def test_update_nonexistent_note(self, memory_store):
        """Updating a nonexistent note returns None."""
        result = await memory_store.update_note("nonexistent-999", "new content")
        assert result is None

    async def test_list_notes_all_layer(self, memory_store):
        """list_notes('all') returns notes from both layers."""
        await memory_store.write_note(content="LT note", memory_type="episodic")
        await memory_store.write_note(
            content="UM note", memory_type="user_model", domain="preferences",
        )

        all_notes = await memory_store.list_notes(layer="all")
        assert len(all_notes) == 2


# ==================================================================
# 3. ProjectionDB Tests
# ==================================================================


class TestProjectionDB:
    """Projection database with sqlite-vec and FTS5."""

    async def test_projection_db_initializes(self, projection_db):
        """ProjectionDB.initialize creates tables and loads sqlite-vec."""
        # Verify key tables exist
        cursor = await projection_db._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        tables = {row[0] for row in await cursor.fetchall()}
        assert "memories" in tables
        assert "user_model_facts" in tables
        assert "entities" in tables
        assert "schema_version" in tables

    async def test_index_and_get_memory(self, projection_db, mock_embedding_model):
        """Insert a memory and retrieve it by ID."""
        vec = mock_embedding_model.embed("test content")
        rowid = await projection_db.index_memory(
            memory_id="mem-001",
            content="Sarah loves hiking in the mountains",
            summary="hiking memory",
            importance=0.7,
            memory_type="episodic",
            created_at="2025-01-01T00:00:00+00:00",
            updated_at="2025-01-01T00:00:00+00:00",
            entities='["Sarah"]',
            tags='["hiking"]',
            source_path="/test/mem-001.md",
            embedding=vec,
        )
        assert rowid > 0

        result = await projection_db.get_memory_by_id("mem-001")
        assert result is not None
        assert result["content"] == "Sarah loves hiking in the mountains"
        assert result["importance"] == 0.7

    async def test_index_user_model_fact(self, projection_db, mock_embedding_model):
        """Insert a user-model fact and retrieve it."""
        vec = mock_embedding_model.embed("user name is Alex")
        rowid = await projection_db.index_user_model_fact(
            fact_id="fact-001",
            domain="identity",
            content="User's name is Alex",
            confidence=0.33,
            evidence_count=1,
            contradiction_count=0,
            created_at="2025-01-01T00:00:00+00:00",
            updated_at="2025-01-01T00:00:00+00:00",
            source_path="/test/fact-001.md",
            embedding=vec,
        )
        assert rowid > 0

        fact = await projection_db.get_fact_by_id("fact-001")
        assert fact is not None
        assert fact["domain"] == "identity"
        assert fact["content"] == "User's name is Alex"

    async def test_delete_memory(self, projection_db, mock_embedding_model):
        """Deleting a memory removes it from the base table and vec table."""
        vec = mock_embedding_model.embed("deletable content")
        await projection_db.index_memory(
            memory_id="mem-del",
            content="Will be deleted",
            summary=None,
            importance=0.5,
            memory_type="episodic",
            created_at="2025-01-01T00:00:00+00:00",
            updated_at="2025-01-01T00:00:00+00:00",
            entities=None,
            tags=None,
            source_path="/test/del.md",
            embedding=vec,
        )

        # Verify it exists
        assert await projection_db.get_memory_by_id("mem-del") is not None

        # Delete and verify gone
        await projection_db.delete_memory("mem-del")
        assert await projection_db.get_memory_by_id("mem-del") is None

    async def test_find_or_create_entity(self, projection_db):
        """find_or_create_entity creates a new entity, then finds existing."""
        eid1 = await projection_db.find_or_create_entity("Sarah", "person")
        eid2 = await projection_db.find_or_create_entity("Sarah", "person")
        assert eid1 == eid2  # same canonical name returns same ID

        eid3 = await projection_db.find_or_create_entity("Portland", "place")
        assert eid3 != eid1  # different entity

    async def test_update_memory_content(self, projection_db, mock_embedding_model):
        """Updating memory content re-indexes the embedding."""
        vec = mock_embedding_model.embed("original content")
        await projection_db.index_memory(
            memory_id="mem-upd",
            content="Original",
            summary=None,
            importance=0.5,
            memory_type="episodic",
            created_at="2025-01-01T00:00:00+00:00",
            updated_at="2025-01-01T00:00:00+00:00",
            entities=None,
            tags=None,
            source_path="/test/upd.md",
            embedding=vec,
        )

        new_vec = mock_embedding_model.embed("updated content")
        await projection_db.update_memory_content(
            memory_id="mem-upd",
            content="Updated content here",
            summary="updated summary",
            updated_at="2025-06-01T00:00:00+00:00",
            embedding=new_vec,
        )

        result = await projection_db.get_memory_by_id("mem-upd")
        assert result is not None
        assert result["content"] == "Updated content here"
        assert result["summary"] == "updated summary"

    async def test_delete_nonexistent_memory(self, projection_db):
        """Deleting a nonexistent memory does not error."""
        await projection_db.delete_memory("does-not-exist")
        # Should not raise


# ==================================================================
# 4. Retrieval Tests
# ==================================================================


class TestRetrieval:
    """Hybrid search: vector, FTS5, merge_and_rank, time-weighting."""

    async def test_sanitize_fts5_empty_query(self):
        """Empty and whitespace-only queries return empty string."""
        from kora_v2.memory.retrieval import _sanitize_fts5_query

        assert _sanitize_fts5_query("") == ""
        assert _sanitize_fts5_query("   ") == ""

    async def test_sanitize_fts5_query(self):
        """FTS5 reserved operators are escaped with double quotes."""
        from kora_v2.memory.retrieval import _sanitize_fts5_query

        assert _sanitize_fts5_query("cats AND dogs") == 'cats "AND" dogs'
        assert _sanitize_fts5_query("NOT a keyword") == '"NOT" a keyword'
        assert _sanitize_fts5_query("near me") == '"near" me'
        assert _sanitize_fts5_query("hello world") == "hello world"
        # Already-quoted phrases stay intact
        assert _sanitize_fts5_query('"exact phrase" OR more') == '"exact phrase" "OR" more'

    async def test_vector_search_returns_results(
        self, projection_db, mock_embedding_model,
    ):
        """Insert vectors, search, get nearest-neighbour matches."""
        from kora_v2.memory.retrieval import vector_search

        # Insert two memories with different embeddings
        vec1 = mock_embedding_model.embed("hiking in the mountains")
        await projection_db.index_memory(
            memory_id="vs-1",
            content="We went hiking in the mountains last Saturday",
            summary=None,
            importance=0.6,
            memory_type="episodic",
            created_at="2025-03-01T00:00:00+00:00",
            updated_at="2025-03-01T00:00:00+00:00",
            entities='["Sarah"]',
            tags=None,
            source_path="/test/vs1.md",
            embedding=vec1,
        )

        vec2 = mock_embedding_model.embed("cooking pasta dinner")
        await projection_db.index_memory(
            memory_id="vs-2",
            content="Made pasta for dinner tonight",
            summary=None,
            importance=0.4,
            memory_type="episodic",
            created_at="2025-03-02T00:00:00+00:00",
            updated_at="2025-03-02T00:00:00+00:00",
            entities=None,
            tags=None,
            source_path="/test/vs2.md",
            embedding=vec2,
        )

        # Query with hiking-related embedding
        query_vec = mock_embedding_model.embed("hiking in the mountains")
        results = await vector_search(projection_db, query_vec, table="memories", k=5)

        assert len(results) >= 1
        # The hiking memory should rank highest (closest to query)
        assert results[0].id == "vs-1"
        assert results[0].score > 0  # similarity > 0

    async def test_fts5_search_returns_results(
        self, projection_db, mock_embedding_model,
    ):
        """FTS5 full-text search finds content by keyword."""
        from kora_v2.memory.retrieval import fts5_search

        vec = mock_embedding_model.embed("i love chocolate cake")
        await projection_db.index_memory(
            memory_id="fts-1",
            content="I love chocolate cake, it is the best dessert",
            summary="chocolate dessert",
            importance=0.5,
            memory_type="episodic",
            created_at="2025-01-01T00:00:00+00:00",
            updated_at="2025-01-01T00:00:00+00:00",
            entities=None,
            tags=None,
            source_path="/test/fts1.md",
            embedding=vec,
        )

        results = await fts5_search(projection_db, "chocolate", table="memories")
        assert len(results) >= 1
        assert results[0].id == "fts-1"
        assert results[0].score > 0

    async def test_fts5_search_no_match(self, projection_db, mock_embedding_model):
        """FTS5 returns empty list when no content matches."""
        from kora_v2.memory.retrieval import fts5_search

        vec = mock_embedding_model.embed("unrelated content")
        await projection_db.index_memory(
            memory_id="fts-nomatch",
            content="The weather was sunny today",
            summary=None,
            importance=0.3,
            memory_type="episodic",
            created_at="2025-01-01T00:00:00+00:00",
            updated_at="2025-01-01T00:00:00+00:00",
            entities=None,
            tags=None,
            source_path="/test/fts_nomatch.md",
            embedding=vec,
        )

        results = await fts5_search(projection_db, "quantum physics", table="memories")
        assert results == []

    async def test_merge_and_rank_combined_scores(self):
        """merge_and_rank correctly combines weighted scores from two sources."""
        from kora_v2.memory.retrieval import MemoryResult, merge_and_rank

        vec_results = [
            MemoryResult(id="a", content="A", score=0.9, source="long_term"),
            MemoryResult(id="b", content="B", score=0.5, source="long_term"),
        ]
        fts_results = [
            MemoryResult(id="a", content="A", score=0.8, source="long_term"),
            MemoryResult(id="c", content="C", score=0.6, source="long_term"),
        ]

        merged = merge_and_rank(vec_results, fts_results, vec_weight=0.7, fts_weight=0.3)

        ids = [r.id for r in merged]
        assert "a" in ids  # appears in both sources, highest combined score
        assert "b" in ids
        assert "c" in ids
        # "a" should have highest score (vec contribution + fts contribution)
        assert merged[0].id == "a"

    async def test_merge_and_rank_empty_source_fallback(self):
        """When one source is empty, the other gets effective weight 1.0."""
        from kora_v2.memory.retrieval import MemoryResult, merge_and_rank

        fts_only = [
            MemoryResult(id="x", content="X", score=0.8, source="long_term"),
        ]

        # vec_results empty => fts_weight should become 1.0
        merged = merge_and_rank([], fts_only, vec_weight=0.7, fts_weight=0.3)

        assert len(merged) == 1
        # With weight 1.0 and only one result, score should be 1.0
        assert merged[0].score == 1.0

    async def test_merge_and_rank_both_empty(self):
        """When both sources are empty, merge returns empty list."""
        from kora_v2.memory.retrieval import merge_and_rank

        assert merge_and_rank([], []) == []

    async def test_apply_time_weighting(self):
        """Recent memories get higher time-weighted scores than old ones."""
        from kora_v2.memory.retrieval import MemoryResult, apply_time_weighting

        now = datetime.now(UTC)
        today_str = now.strftime("%Y-%m-%d")

        results = [
            MemoryResult(
                id="old",
                content="Old memory",
                score=1.0,
                source_path=f"/data/2020-01-01/old.md",
            ),
            MemoryResult(
                id="new",
                content="New memory",
                score=1.0,
                source_path=f"/data/{today_str}/new.md",
            ),
        ]

        weighted = apply_time_weighting(results, decay_factor=0.01)

        # The old memory (from 2020) should have a lower score due to decay
        old_result = next(r for r in weighted if r.id == "old")
        new_result = next(r for r in weighted if r.id == "new")
        assert new_result.score >= old_result.score

    async def test_apply_time_weighting_no_date(self):
        """Memories without parseable dates get no decay (score unchanged)."""
        from kora_v2.memory.retrieval import MemoryResult, apply_time_weighting

        results = [
            MemoryResult(
                id="no-date",
                content="No date memory",
                score=0.8,
                source_path="/data/notes/some_note.md",
            ),
        ]

        weighted = apply_time_weighting(results, decay_factor=0.1)
        assert weighted[0].score == 0.8  # unchanged, no date to decay


# ==================================================================
# 5. Dedup Tests
# ==================================================================


class TestDedup:
    """Deduplication via FTS5 candidate search + LLM judgment."""

    async def test_parse_dedup_response_duplicate(self):
        """ACTION: DUPLICATE is correctly parsed."""
        from kora_v2.memory.dedup import _parse_dedup_response

        action, merged = _parse_dedup_response("ACTION: DUPLICATE\n")
        assert action == "duplicate"
        assert merged is None

    async def test_parse_dedup_response_merge(self):
        """ACTION: MERGE with MERGED text is correctly parsed."""
        from kora_v2.memory.dedup import _parse_dedup_response

        response = (
            "ACTION: MERGE\n"
            "MERGED: User's name is Alex and they live in Portland.\n"
            "They also enjoy hiking."
        )
        action, merged = _parse_dedup_response(response)
        assert action == "merge"
        assert merged is not None
        assert "Alex" in merged
        assert "hiking" in merged

    async def test_parse_dedup_response_new(self):
        """ACTION: NEW is correctly parsed."""
        from kora_v2.memory.dedup import _parse_dedup_response

        action, merged = _parse_dedup_response("ACTION: NEW\n")
        assert action == "new"
        assert merged is None

    async def test_parse_dedup_response_case_insensitive(self):
        """Parsing works regardless of casing in ACTION line."""
        from kora_v2.memory.dedup import _parse_dedup_response

        action, _ = _parse_dedup_response("action: Duplicate\n")
        assert action == "duplicate"

    async def test_parse_dedup_response_multiline_merged(self):
        """Continuation lines after MERGED are included in merged text."""
        from kora_v2.memory.dedup import _parse_dedup_response

        response = (
            "ACTION: MERGE\n"
            "MERGED: First line of merged text.\n"
            "Second line with more detail.\n"
            "Third line finishes the merge."
        )
        action, merged = _parse_dedup_response(response)
        assert action == "merge"
        assert merged is not None
        assert "First line" in merged
        assert "Third line" in merged

    async def test_dedup_check_no_candidates(self, tmp_path):
        """dedup_check returns NEW when no FTS5 candidates match."""
        from kora_v2.memory.dedup import DedupAction, dedup_check

        import sqlite_vec

        db_path = tmp_path / "dedup_no_cand.db"
        async with aiosqlite.connect(str(db_path), check_same_thread=False) as db:
            conn = db._connection
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)

            # Create the FTS5 table (standalone, not content-linked)
            await db.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts "
                "USING fts5(content, summary)"
            )
            await db.commit()

            mock_llm = AsyncMock(return_value="ACTION: NEW")
            result = await dedup_check(
                "brand new information nobody has ever heard",
                db,
                mock_llm,
                table="memories_fts",
            )

            assert result.action == DedupAction.NEW
            # LLM should NOT have been called (no candidates)
            mock_llm.assert_not_called()

    async def test_dedup_check_merge_found(self, tmp_path):
        """dedup_check returns MERGE with merged content when LLM says merge."""
        from kora_v2.memory.dedup import DedupAction, dedup_check

        import sqlite_vec

        db_path = tmp_path / "dedup_merge.db"
        async with aiosqlite.connect(str(db_path), check_same_thread=False) as db:
            conn = db._connection
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)

            # Create base table and content-linked FTS5 table
            await db.execute(
                "CREATE TABLE IF NOT EXISTS memories ("
                "  id TEXT PRIMARY KEY, content TEXT NOT NULL)"
            )
            await db.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts "
                "USING fts5(content, content=memories, content_rowid=rowid)"
            )
            # Insert into base table (triggers auto-sync only with triggers;
            # for this minimal test, insert into both manually)
            await db.execute(
                "INSERT INTO memories(rowid, id, content) "
                "VALUES (42, 'test-mem-42', 'Alex lives in Portland and likes hiking')"
            )
            await db.execute(
                "INSERT INTO memories_fts(rowid, content) "
                "VALUES (42, 'Alex lives in Portland and likes hiking')"
            )
            await db.commit()

            mock_llm = AsyncMock(
                return_value=(
                    "ACTION: MERGE\n"
                    "MERGED: Alex lives in Portland, likes hiking, and recently started climbing."
                ),
            )

            # FTS5 implicit AND requires all terms to appear in document.
            # Use a query with words that overlap the existing content.
            result = await dedup_check(
                "Alex likes Portland",
                db,
                mock_llm,
                table="memories_fts",
                score_threshold=0.0,  # low threshold to ensure match
            )

            assert result.action == DedupAction.MERGE
            assert result.merged_content is not None
            assert "climbing" in result.merged_content
            mock_llm.assert_called_once()

    async def test_dedup_sanitize_fts5_query(self):
        """Dedup's own _sanitize_fts5_query escapes operators."""
        from kora_v2.memory.dedup import _sanitize_fts5_query

        assert '"OR"' in _sanitize_fts5_query("cats OR dogs")
        assert '"AND"' in _sanitize_fts5_query("this AND that")
        # Single quotes are stripped
        assert "'" not in _sanitize_fts5_query("it's a test")


# ==================================================================
# 6. Write Pipeline Tests
# ==================================================================


class TestWritePipeline:
    """WritePipeline: dedup -> store -> index -> embed -> link entities."""

    async def test_extract_entities_person(self):
        """Regex extraction detects person names after relationship words."""
        from kora_v2.memory.write_pipeline import _extract_entities

        entities = _extract_entities("My wife Sarah loves hiking")
        names = {name for name, etype in entities}
        types = {etype for _, etype in entities}
        assert "Sarah" in names
        assert "person" in types

    async def test_extract_entities_location(self):
        """Regex extraction detects locations after 'I live in'."""
        from kora_v2.memory.write_pipeline import _extract_entities

        entities = _extract_entities("I live in Portland and love it")
        names = {name for name, _ in entities}
        assert "Portland" in names
        types_for_portland = {etype for name, etype in entities if name == "Portland"}
        assert "place" in types_for_portland

    async def test_extract_entities_medication(self):
        """Regex extraction detects medication names."""
        from kora_v2.memory.write_pipeline import _extract_entities

        entities = _extract_entities("I take adderall every morning")
        names = {name for name, _ in entities}
        assert "Adderall" in names
        types = {etype for name, etype in entities if name == "Adderall"}
        assert "medication" in types

    async def test_extract_entities_multiple(self):
        """Multiple entity types are extracted from a single sentence."""
        from kora_v2.memory.write_pipeline import _extract_entities

        text = "My friend Bob and I moved to Seattle, I take lexapro daily"
        entities = _extract_entities(text)
        names = {name for name, _ in entities}
        assert "Bob" in names
        assert "Seattle" in names
        assert "Lexapro" in names

    async def test_extract_entities_no_match(self):
        """Plain text without entity patterns returns empty list."""
        from kora_v2.memory.write_pipeline import _extract_entities

        entities = _extract_entities("the weather is nice today")
        assert entities == []

    async def test_store_creates_note_and_indexes(
        self, memory_store, projection_db, mock_embedding_model,
    ):
        """Full pipeline: creates FS note + projection DB row."""
        from kora_v2.memory.write_pipeline import WritePipeline

        pipeline = WritePipeline(
            store=memory_store,
            projection_db=projection_db,
            embedding_model=mock_embedding_model,
            llm=None,  # no dedup LLM
        )

        result = await pipeline.store(
            content="My wife Sarah loves hiking in the mountains",
            memory_type="episodic",
            importance=0.7,
            tags=["outdoor"],
            skip_dedup=True,
        )

        assert result.action == "created"
        assert result.note_id
        assert "Sarah" in result.entities_extracted

        # Verify in projection DB
        mem = await projection_db.get_memory_by_id(result.note_id)
        assert mem is not None
        assert mem["content"] == "My wife Sarah loves hiking in the mountains"

        # Verify filesystem note
        note = await memory_store.read_note(result.note_id)
        assert note is not None
        assert note.body == "My wife Sarah loves hiking in the mountains"

    async def test_store_user_model_fact(
        self, memory_store, projection_db, mock_embedding_model,
    ):
        """store_user_model_fact creates fact with correct domain."""
        from kora_v2.memory.write_pipeline import WritePipeline

        pipeline = WritePipeline(
            store=memory_store,
            projection_db=projection_db,
            embedding_model=mock_embedding_model,
            llm=None,
        )

        result = await pipeline.store_user_model_fact(
            content="User's name is Alex",
            domain="identity",
            importance=0.8,
            skip_dedup=True,
        )

        assert result.action == "created"
        assert result.note_id

        # Verify in projection DB
        fact = await projection_db.get_fact_by_id(result.note_id)
        assert fact is not None
        assert fact["domain"] == "identity"
        assert fact["evidence_count"] == 1

        # Verify filesystem note
        note = await memory_store.read_note(result.note_id)
        assert note is not None
        assert note.metadata.memory_type == "user_model"


# ==================================================================
# 7. Recall Tool Tests
# ==================================================================


class TestRecallTool:
    """recall() — fast deterministic memory search tool."""

    async def test_recall_empty_query(self):
        """Empty query returns empty results with message."""
        from kora_v2.tools.recall import recall

        result_json = await recall(query="   ", container=None)
        result = json.loads(result_json)
        assert result["results"] == []
        assert "Empty query" in result["message"]

    async def test_recall_no_container(self):
        """Missing container returns error message."""
        from kora_v2.tools.recall import recall

        result_json = await recall(query="test query", container=None)
        result = json.loads(result_json)
        assert result["results"] == []
        assert "No service container" in result["message"]

    async def test_recall_with_results(
        self, projection_db, mock_embedding_model,
    ):
        """recall() returns formatted JSON results from populated DB."""
        from kora_v2.tools.recall import recall

        # Seed data
        vec = mock_embedding_model.embed("chocolate cake recipe")
        await projection_db.index_memory(
            memory_id="recall-1",
            content="Grandma's chocolate cake recipe is the best",
            summary="chocolate cake",
            importance=0.8,
            memory_type="episodic",
            created_at="2025-01-01T00:00:00+00:00",
            updated_at="2025-01-01T00:00:00+00:00",
            entities=None,
            tags=None,
            source_path="/test/recall1.md",
            embedding=vec,
        )

        # Build a minimal container mock
        class FakeContainer:
            pass

        container = FakeContainer()
        container.embedding_model = mock_embedding_model
        container.projection_db = projection_db

        result_json = await recall(
            query="chocolate cake",
            container=container,
            max_results=5,
        )
        result = json.loads(result_json)
        assert len(result["results"]) >= 1
        assert result["results"][0]["id"] == "recall-1"
        assert result["results"][0]["type"] == "episodic"

    async def test_recall_not_initialized(self):
        """Container with None subsystem returns informative message."""
        from kora_v2.tools.recall import recall

        class EmptyContainer:
            embedding_model = None
            projection_db = None

        result_json = await recall(query="test", container=EmptyContainer())
        result = json.loads(result_json)
        assert "not initialized" in result["message"]


# ==================================================================
# 8. Worker Prompt Tests
# ==================================================================


class TestWorkerPrompt:
    """MemoryWorker input/output models and system prompt."""

    async def test_worker_prompt_exists(self):
        """MEMORY_WORKER_SYSTEM_PROMPT is a non-empty string."""
        from kora_v2.memory.worker_prompt import MEMORY_WORKER_SYSTEM_PROMPT

        assert isinstance(MEMORY_WORKER_SYSTEM_PROMPT, str)
        assert len(MEMORY_WORKER_SYSTEM_PROMPT) > 100
        # Should mention key concepts
        assert "Memory Worker" in MEMORY_WORKER_SYSTEM_PROMPT
        assert "dedup" in MEMORY_WORKER_SYSTEM_PROMPT.lower() or "duplicate" in MEMORY_WORKER_SYSTEM_PROMPT.lower()

    async def test_worker_input_output_models(self):
        """MemoryWorkerInput and MemoryWorkerOutput validate correctly."""
        from kora_v2.memory.worker_prompt import MemoryWorkerInput, MemoryWorkerOutput

        # Valid input
        inp = MemoryWorkerInput(
            operation="recall",
            content="What do I know about Sarah?",
            layer="all",
        )
        assert inp.operation == "recall"
        assert inp.layer == "all"
        assert inp.domain is None

        # Valid output
        out = MemoryWorkerOutput(
            status="success",
            results=[{"id": "m1", "content": "Sarah info"}],
            memory_id=None,
            message="Found 1 result",
        )
        assert out.status == "success"
        assert len(out.results) == 1

    async def test_worker_input_store_operation(self):
        """MemoryWorkerInput validates store operations with domain."""
        from kora_v2.memory.worker_prompt import MemoryWorkerInput

        inp = MemoryWorkerInput(
            operation="store",
            content="User prefers dark mode",
            layer="user_model",
            domain="preferences",
            memory_type="episodic",
        )
        assert inp.operation == "store"
        assert inp.domain == "preferences"
        assert inp.memory_type == "episodic"

    async def test_worker_output_error_status(self):
        """MemoryWorkerOutput can represent error states."""
        from kora_v2.memory.worker_prompt import MemoryWorkerOutput

        out = MemoryWorkerOutput(
            status="error",
            message="Failed to retrieve memories",
        )
        assert out.status == "error"
        assert out.results is None
        assert out.memory_id is None


# ==================================================================
# 9. DI Container Memory Initialization Tests
# ==================================================================


class TestContainerMemory:
    """Container.initialize_memory() and Container.close()."""

    async def test_container_close_without_init(self):
        """Closing a container that was never memory-initialized does not error."""
        from kora_v2.core.di import Container
        from kora_v2.core.settings import Settings

        container = Container(Settings())
        assert container.projection_db is None
        await container.close()  # should not raise

    async def test_container_initial_state(self):
        """Container starts with None for all memory subsystem attributes."""
        from kora_v2.core.di import Container
        from kora_v2.core.settings import Settings

        container = Container(Settings())
        assert container.embedding_model is None
        assert container.projection_db is None
        assert container.memory_store is None
        assert container.write_pipeline is None
        assert container.signal_scanner is None
