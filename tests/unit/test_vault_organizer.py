"""Unit tests for Vault Organizer — Phase 8c.

Tests for:
- Reindex step detects stale entries and re-indexes
- Reindex step finds new files on disk
- Reindex step soft-deletes missing files
- Structure step creates folder hierarchy
- Structure step moves misplaced notes
- Structure step triages inbox with confidence gating
- Structure step skips working documents (pipeline frontmatter)
- Links step drains pending queue
- Links step generates entity pages
- MOC step regenerates when threshold met
- MOC step skips when below threshold
- Session mirror creates session notes
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

# pysqlite3 monkey-patch
try:
    import pysqlite3 as _pysqlite3  # type: ignore[import-untyped]

    sys.modules["sqlite3"] = _pysqlite3
except ImportError:
    pass

import pytest

from kora_v2.agents.background.vault_organizer import (
    EntityPageData,
    build_entity_page,
    build_moc_page,
    build_session_index,
    build_session_note,
)
from kora_v2.agents.background.vault_organizer_handlers import (
    _is_working_document,
    _on_memory_stored,
    links_step,
    moc_sessions_step,
    pending_entity_merges,
    pending_notes,
    reindex_step,
    structure_step,
)
from kora_v2.memory.store import _render_note
from kora_v2.runtime.orchestration.worker_task import (
    StepContext,
    WorkerTask,
    WorkerTaskConfig,
)

# ══════════════════════════════════════════════════════════════════════════
# Mock helpers
# ══════════════════════════════════════════════════════════════════════════


class MockEmbeddingModel:
    dimension = 768
    is_loaded = True

    def embed(self, text: str, task_type: str = "search_query") -> list[float]:
        h = hashlib.md5(text.encode()).digest()  # noqa: S324
        base = [b / 255.0 for b in h]
        return (base * 48)[:768]

    def embed_batch(self, texts: list[str], **kwargs: object) -> list[list[float]]:
        return [self.embed(t) for t in texts]


def _make_task(stage_name: str = "run") -> WorkerTask:
    return WorkerTask(
        id=f"task-8c-{stage_name}",
        pipeline_instance_id="8c-test-pipeline",
        stage_name=stage_name,
        config=WorkerTaskConfig(
            preset="bounded_background",
            max_duration_seconds=1800,
            checkpoint_every_seconds=300,
            request_class="background",  # type: ignore[arg-type]
            allowed_states=frozenset(),
            tool_scope=[],
            pause_on_conversation=False,
            pause_on_topic_overlap=False,
            report_via=frozenset(),
            blocks_parent=False,
        ),
        goal="vault organizer test",
        system_prompt="",
    )


def _make_ctx(task: WorkerTask) -> StepContext:
    return StepContext(
        task=task,
        limiter=MagicMock(),
        cancellation_flag=lambda: False,
        now=lambda: datetime.now(UTC),
    )


def _write_note(
    base_path: Path,
    note_id: str,
    body: str,
    memory_type: str = "episodic",
    domain: str | None = None,
    extra_meta: dict | None = None,
) -> Path:
    """Write a note file and return its path."""
    if memory_type == "user_model" and domain:
        target_dir = base_path / "User Model" / domain
    else:
        target_dir = base_path / "Long-Term"
    target_dir.mkdir(parents=True, exist_ok=True)

    meta = {
        "id": note_id,
        "memory_type": memory_type,
        "importance": 0.5,
        "entities": [],
        "tags": [],
        "created_at": "2025-01-01T00:00:00+00:00",
        "updated_at": "2025-01-01T00:00:00+00:00",
    }
    if extra_meta:
        meta.update(extra_meta)

    file_path = target_dir / f"{note_id}.md"
    file_path.write_text(_render_note(meta, body), encoding="utf-8")
    return file_path


# ══════════════════════════════════════════════════════════════════════════
# Working document detection
# ══════════════════════════════════════════════════════════════════════════


class TestWorkingDocumentDetection:
    """Structure step skips working documents (pipeline frontmatter)."""

    def test_regular_note_not_working_doc(self, tmp_path: Path) -> None:
        file_path = _write_note(tmp_path, "note-001", "Regular note body")
        assert not _is_working_document(file_path)

    def test_pipeline_frontmatter_is_working_doc(self, tmp_path: Path) -> None:
        file_path = _write_note(
            tmp_path,
            "note-002",
            "Working document body",
            extra_meta={"pipeline": "japan_research"},
        )
        assert _is_working_document(file_path)

    def test_missing_file_not_working_doc(self, tmp_path: Path) -> None:
        assert not _is_working_document(tmp_path / "nonexistent.md")


# ══════════════════════════════════════════════════════════════════════════
# Event-to-queue discipline
# ══════════════════════════════════════════════════════════════════════════


class TestEventToQueue:
    """MEMORY_STORED handler ONLY appends to queue, no FS writes."""

    @pytest.fixture(autouse=True)
    def _clear_queue(self) -> None:
        pending_notes.clear()

    @pytest.mark.asyncio
    async def test_on_memory_stored_queues_note_id(self) -> None:
        await _on_memory_stored({"note_id": "note-123"})
        assert "note-123" in pending_notes

    @pytest.mark.asyncio
    async def test_on_memory_stored_no_duplicates(self) -> None:
        await _on_memory_stored({"note_id": "note-123"})
        await _on_memory_stored({"note_id": "note-123"})
        assert pending_notes.count("note-123") == 1

    @pytest.mark.asyncio
    async def test_on_memory_stored_handles_memory_id_key(self) -> None:
        await _on_memory_stored({"memory_id": "note-456"})
        assert "note-456" in pending_notes


# ══════════════════════════════════════════════════════════════════════════
# Reindex step
# ══════════════════════════════════════════════════════════════════════════


class TestReindexStep:
    """Reindex step detects stale/new/deleted files."""

    @pytest.mark.asyncio
    async def test_reindex_detects_stale_entries(self, tmp_path: Path) -> None:
        from kora_v2.memory.projection import StaleEntry

        # Create mock stale entry
        stale_entry = StaleEntry(
            record_id="stale-001",
            source_path=str(tmp_path / "Long-Term" / "stale-001.md"),
            source_table="memories",
            db_updated_at="2025-01-01T00:00:00+00:00",
            fs_mtime="2025-06-01T00:00:00+00:00",
        )

        # Write the file
        _write_note(tmp_path, "stale-001", "Updated content")

        # Mock container
        container = MagicMock()
        container.projection_db = AsyncMock()
        container.projection_db.detect_stale_entries = AsyncMock(
            return_value=[stale_entry]
        )
        container.projection_db.update_memory_content = AsyncMock()
        container.projection_db._db = AsyncMock()
        container.projection_db._db.execute = AsyncMock(return_value=AsyncMock(fetchall=AsyncMock(return_value=[])))
        container.memory_store = MagicMock()
        container.memory_store._base = tmp_path
        container.embedding_model = MockEmbeddingModel()

        with patch(
            "kora_v2.agents.background.vault_organizer_handlers.get_autonomous_context"
        ) as mock_ctx:
            mock_ctx.return_value = MagicMock(container=container, db_path=tmp_path / "op.db")

            task = _make_task("reindex")
            ctx = _make_ctx(task)
            result = await reindex_step(task, ctx)

        assert result.outcome == "complete"
        assert "updated" in (result.result_summary or "")

    @pytest.mark.asyncio
    async def test_reindex_soft_deletes_missing_files(self, tmp_path: Path) -> None:
        """Reindex should soft-delete DB rows with no corresponding file."""
        container = MagicMock()
        container.projection_db = AsyncMock()
        container.projection_db.detect_stale_entries = AsyncMock(return_value=[])
        container.projection_db.soft_delete = AsyncMock()

        # The reindex step runs three phases of DB queries:
        # Phase 1: detect_stale_entries (mocked above)
        # Phase 2: "SELECT source_path FROM {table}" for known paths
        # Phase 3: "SELECT id, source_path FROM {table}" for delete scan
        missing_path = str(tmp_path / "Long-Term" / "missing-001.md")

        async def mock_execute(query, *args):
            mock = AsyncMock()
            if "SELECT source_path FROM" in query:
                # Known paths query — return the missing file path so it
                # is "known" (prevents new-file indexing) but its file is
                # absent on disk, triggering soft-delete in phase 3.
                mock.fetchall = AsyncMock(return_value=[(missing_path,)])
                return mock
            if "SELECT id, source_path FROM" in query:
                # Delete-scan query — return the row whose file is gone
                mock.fetchall = AsyncMock(return_value=[
                    ("missing-001", missing_path),
                ])
                return mock
            mock.fetchall = AsyncMock(return_value=[])
            return mock

        container.projection_db._db = AsyncMock()
        container.projection_db._db.execute = mock_execute
        container.memory_store = MagicMock()
        container.memory_store._base = tmp_path
        container.embedding_model = MockEmbeddingModel()

        with patch(
            "kora_v2.agents.background.vault_organizer_handlers.get_autonomous_context"
        ) as mock_ctx:
            mock_ctx.return_value = MagicMock(container=container, db_path=tmp_path / "op.db")

            task = _make_task("reindex")
            ctx = _make_ctx(task)
            result = await reindex_step(task, ctx)

        assert result.outcome == "complete"
        container.projection_db.soft_delete.assert_called()


# ══════════════════════════════════════════════════════════════════════════
# Structure step
# ══════════════════════════════════════════════════════════════════════════


class TestStructureStep:
    """Structure step creates folder hierarchy and triages inbox."""

    @pytest.mark.asyncio
    async def test_creates_folder_hierarchy(self, tmp_path: Path) -> None:
        container = MagicMock()
        container.memory_store = MagicMock()
        container.memory_store._base = tmp_path
        container.projection_db = AsyncMock()
        container.projection_db._db = AsyncMock()
        container.projection_db._db.execute = AsyncMock(
            return_value=AsyncMock(fetchall=AsyncMock(return_value=[]))
        )

        with patch(
            "kora_v2.agents.background.vault_organizer_handlers.get_autonomous_context"
        ) as mock_ctx:
            mock_ctx.return_value = MagicMock(container=container, db_path=tmp_path / "op.db")

            task = _make_task("structure")
            ctx = _make_ctx(task)
            result = await structure_step(task, ctx)

        assert result.outcome == "complete"
        # Check key folders exist
        assert (tmp_path / "Inbox").is_dir()
        assert (tmp_path / "Long-Term" / "Episodic").is_dir()
        assert (tmp_path / "Long-Term" / "Reflective").is_dir()
        assert (tmp_path / "Long-Term" / "Procedural").is_dir()
        assert (tmp_path / "Entities" / "People").is_dir()
        assert (tmp_path / "Maps of Content").is_dir()
        assert (tmp_path / "Sessions").is_dir()
        assert (tmp_path / "References").is_dir()
        assert (tmp_path / "Ideas").is_dir()
        assert (tmp_path / ".kora").is_dir()

    @pytest.mark.asyncio
    async def test_skips_working_documents_in_inbox(self, tmp_path: Path) -> None:
        """Files with pipeline frontmatter should be left in Inbox."""
        inbox = tmp_path / "Inbox"
        inbox.mkdir(parents=True, exist_ok=True)

        # Write a working document in inbox
        meta = {
            "id": "working-001",
            "pipeline": "japan_research",
            "memory_type": "episodic",
        }
        (inbox / "working-001.md").write_text(
            _render_note(meta, "Research in progress..."),
            encoding="utf-8",
        )

        container = MagicMock()
        container.memory_store = MagicMock()
        container.memory_store._base = tmp_path
        container.memory_store.move_note = AsyncMock()
        container.projection_db = AsyncMock()
        container.projection_db._db = AsyncMock()
        container.projection_db._db.execute = AsyncMock(
            return_value=AsyncMock(fetchall=AsyncMock(return_value=[]))
        )
        container.llm = AsyncMock()
        container.llm.chat = AsyncMock(return_value='{"folder": "Long-Term/Episodic", "confidence": 0.95}')

        with patch(
            "kora_v2.agents.background.vault_organizer_handlers.get_autonomous_context"
        ) as mock_ctx:
            mock_ctx.return_value = MagicMock(container=container, db_path=tmp_path / "op.db")

            task = _make_task("structure")
            ctx = _make_ctx(task)
            result = await structure_step(task, ctx)

        assert result.outcome == "complete"
        # Working document should NOT have been moved
        container.memory_store.move_note.assert_not_called()
        # File should still be in Inbox
        assert (inbox / "working-001.md").is_file()

    @pytest.mark.asyncio
    async def test_triages_inbox_with_confidence_gating(self, tmp_path: Path) -> None:
        """Inbox notes should only be moved if LLM confidence > 0.75."""
        inbox = tmp_path / "Inbox"
        inbox.mkdir(parents=True, exist_ok=True)

        # Write a note in inbox
        _write_note(tmp_path / "placeholder", "note-inbox", "body")  # helper needs base path
        # Actually write directly in inbox
        meta = {
            "id": "inbox-note-001",
            "memory_type": "episodic",
            "importance": 0.5,
        }
        (inbox / "inbox-note-001.md").write_text(
            _render_note(meta, "Some note about health."),
            encoding="utf-8",
        )

        container = MagicMock()
        container.memory_store = MagicMock()
        container.memory_store._base = tmp_path
        container.memory_store.move_note = AsyncMock()
        container.projection_db = AsyncMock()
        container.projection_db._db = AsyncMock()
        container.projection_db._db.execute = AsyncMock(
            return_value=AsyncMock(fetchall=AsyncMock(return_value=[]))
        )
        # LLM returns low confidence
        container.llm = AsyncMock()
        container.llm.chat = AsyncMock(
            return_value='{"folder": "Long-Term/Episodic", "confidence": 0.5}'
        )

        with patch(
            "kora_v2.agents.background.vault_organizer_handlers.get_autonomous_context"
        ) as mock_ctx:
            mock_ctx.return_value = MagicMock(container=container, db_path=tmp_path / "op.db")

            task = _make_task("structure")
            ctx = _make_ctx(task)
            result = await structure_step(task, ctx)

        assert result.outcome == "complete"
        # Low confidence -> should NOT be moved
        container.memory_store.move_note.assert_not_called()


# ══════════════════════════════════════════════════════════════════════════
# Links step
# ══════════════════════════════════════════════════════════════════════════


class TestLinksStep:
    """Links step drains queue and generates entity pages."""

    @pytest.fixture(autouse=True)
    def _clear_queues(self) -> None:
        pending_notes.clear()
        pending_entity_merges.clear()

    @pytest.mark.asyncio
    async def test_drains_pending_queue(self, tmp_path: Path) -> None:
        # Populate pending queue
        pending_notes.extend(["note-001", "note-002"])

        container = MagicMock()
        container.projection_db = AsyncMock()
        container.projection_db.get_entities_by_type = AsyncMock(return_value=[])
        container.memory_store = MagicMock()
        container.memory_store._base = tmp_path
        container.memory_store.read_note = AsyncMock(return_value=None)

        with patch(
            "kora_v2.agents.background.vault_organizer_handlers.get_autonomous_context"
        ) as mock_ctx:
            mock_ctx.return_value = MagicMock(container=container, db_path=tmp_path / "op.db")

            task = _make_task("links")
            ctx = _make_ctx(task)
            result = await links_step(task, ctx)

        assert result.outcome == "complete"
        # Queue should be drained
        assert len(pending_notes) == 0

    @pytest.mark.asyncio
    async def test_generates_entity_pages(self, tmp_path: Path) -> None:
        from kora_v2.memory.projection import EntityRecord, MemoryRecord
        from kora_v2.memory.store import NoteContent, NoteMetadata

        # Set up pending note
        pending_notes.append("note-001")

        # Mock entity
        mock_entity = EntityRecord(
            id="ent-1",
            name="Sarah",
            canonical_name="sarah",
            entity_type="person",
            active_link_count=1,
            first_mention="2025-01-01",
            last_mention="2025-06-01",
        )

        # Mock note
        mock_note = NoteContent(
            metadata=NoteMetadata(
                id="note-001",
                memory_type="episodic",
                source_path=str(tmp_path / "Long-Term" / "note-001.md"),
            ),
            body="I met Sarah at the park.",
        )

        # Write the actual file too so _is_working_document can read it
        _write_note(tmp_path, "note-001", "I met Sarah at the park.")

        # Mock memory record
        mock_memory = MemoryRecord(
            id="note-001",
            content="I met Sarah at the park.",
            memory_type="episodic",
            created_at="2025-01-01",
        )

        container = MagicMock()
        container.projection_db = AsyncMock()
        container.projection_db.get_entities_by_type = AsyncMock(
            side_effect=lambda t: [mock_entity] if t == "person" else []
        )
        container.projection_db.get_memories_by_entity = AsyncMock(
            return_value=[mock_memory]
        )
        container.projection_db.get_entity_relationships = AsyncMock(return_value=[])
        container.memory_store = MagicMock()
        container.memory_store._base = tmp_path
        container.memory_store.read_note = AsyncMock(return_value=mock_note)
        container.memory_store.update_body = AsyncMock()

        with patch(
            "kora_v2.agents.background.vault_organizer_handlers.get_autonomous_context"
        ) as mock_ctx:
            mock_ctx.return_value = MagicMock(container=container, db_path=tmp_path / "op.db")

            task = _make_task("links")
            ctx = _make_ctx(task)
            result = await links_step(task, ctx)

        assert result.outcome == "complete"
        # Entity page should be generated
        entity_dir = tmp_path / "Entities" / "People"
        assert entity_dir.is_dir()
        entity_files = list(entity_dir.glob("*.md"))
        assert len(entity_files) >= 1

    @pytest.mark.asyncio
    async def test_backfills_entity_pages_without_pending_notes(
        self, tmp_path: Path
    ) -> None:
        from kora_v2.memory.projection import EntityRecord, EntityRelationship, MemoryRecord

        mock_entity = EntityRecord(
            id="ent-1",
            name="Sarah",
            canonical_name="sarah",
            entity_type="person",
            active_link_count=1,
            first_mention="2025-01-01",
            last_mention="2025-06-01",
        )
        mock_memory = MemoryRecord(
            id="note-001",
            content="I met Sarah at the park.",
            memory_type="episodic",
            created_at="2025-01-01",
        )
        mock_relationship = EntityRelationship(
            entity_id="ent-2",
            entity_name="John",
            co_occurrence_count=2,
        )

        container = MagicMock()
        container.projection_db = AsyncMock()
        container.projection_db.get_entities_by_type = AsyncMock(
            side_effect=lambda t: [mock_entity] if t == "person" else []
        )
        container.projection_db.get_memories_by_entity = AsyncMock(
            return_value=[mock_memory]
        )
        container.projection_db.get_entity_relationships = AsyncMock(
            return_value=[mock_relationship]
        )
        container.memory_store = MagicMock()
        container.memory_store._base = tmp_path
        container.memory_store.read_note = AsyncMock(return_value=None)
        container.memory_store.update_body = AsyncMock()

        with patch(
            "kora_v2.agents.background.vault_organizer_handlers.get_autonomous_context"
        ) as mock_ctx:
            mock_ctx.return_value = MagicMock(container=container, db_path=tmp_path / "op.db")

            task = _make_task("links")
            ctx = _make_ctx(task)
            result = await links_step(task, ctx)

        assert result.outcome == "complete"
        entity_file = tmp_path / "Entities" / "People" / "Sarah.md"
        assert entity_file.is_file()
        page = entity_file.read_text(encoding="utf-8")
        assert "[[note-001]]" in page
        assert "[[John]]" in page
        assert "First mentioned" in page


# ══════════════════════════════════════════════════════════════════════════
# MOC & Sessions step
# ══════════════════════════════════════════════════════════════════════════


class TestMocSessionsStep:
    """MOC step regenerates when threshold met, skips otherwise."""

    @pytest.mark.asyncio
    async def test_regenerates_moc_when_threshold_met(self, tmp_path: Path) -> None:
        container = MagicMock()
        container.projection_db = AsyncMock()

        # Return enough notes for one domain to trigger MOC regen
        mock_cursor_type = AsyncMock()
        mock_cursor_type.fetchall = AsyncMock(return_value=[
            ("episodic", 10),  # above threshold of 5
        ])

        mock_cursor_domain = AsyncMock()
        mock_cursor_domain.fetchall = AsyncMock(return_value=[
            ("identity", 3),  # below threshold
        ])

        mock_cursor_notes = AsyncMock()
        mock_cursor_notes.fetchall = AsyncMock(return_value=[
            ("note-1", "Content about memory 1", 0.8, "2025-01-01"),
            ("note-2", "Content about memory 2", 0.6, "2025-01-02"),
            ("note-3", "Content about memory 3", 0.9, "2025-01-03"),
            ("note-4", "Content about memory 4", 0.5, "2025-01-04"),
            ("note-5", "Content about memory 5", 0.7, "2025-01-05"),
        ])

        mock_cursor_facts = AsyncMock()
        mock_cursor_facts.fetchall = AsyncMock(return_value=[])

        call_idx = 0
        async def mock_execute(query, *args):
            nonlocal call_idx
            call_idx += 1
            if "GROUP BY memory_type" in query:
                return mock_cursor_type
            if "GROUP BY domain" in query:
                return mock_cursor_domain
            if "FROM memories" in query and "ORDER BY" in query:
                return mock_cursor_notes
            if "FROM user_model_facts" in query and "ORDER BY" in query:
                return mock_cursor_facts
            m = AsyncMock()
            m.fetchall = AsyncMock(return_value=[])
            return m

        container.projection_db._db = AsyncMock()
        container.projection_db._db.execute = mock_execute
        container.memory_store = MagicMock()
        container.memory_store._base = tmp_path

        with patch(
            "kora_v2.agents.background.vault_organizer_handlers.get_autonomous_context"
        ) as mock_ctx:
            mock_ctx.return_value = MagicMock(container=container, db_path=tmp_path / "op.db")

            task = _make_task("moc_sessions")
            ctx = _make_ctx(task)
            result = await moc_sessions_step(task, ctx)

        assert result.outcome == "complete"
        moc_dir = tmp_path / "Maps of Content"
        moc_files = list(moc_dir.glob("*.md"))
        assert len(moc_files) >= 1  # At least one MOC generated

    @pytest.mark.asyncio
    async def test_skips_moc_when_below_threshold(self, tmp_path: Path) -> None:
        container = MagicMock()
        container.projection_db = AsyncMock()

        # Return too few notes for any domain
        mock_cursor = AsyncMock()
        mock_cursor.fetchall = AsyncMock(return_value=[
            ("episodic", 2),  # below threshold of 5
        ])

        mock_cursor_domain = AsyncMock()
        mock_cursor_domain.fetchall = AsyncMock(return_value=[
            ("identity", 1),
        ])

        async def mock_execute(query, *args):
            if "GROUP BY memory_type" in query:
                return mock_cursor
            if "GROUP BY domain" in query:
                return mock_cursor_domain
            m = AsyncMock()
            m.fetchall = AsyncMock(return_value=[])
            return m

        container.projection_db._db = AsyncMock()
        container.projection_db._db.execute = mock_execute
        container.memory_store = MagicMock()
        container.memory_store._base = tmp_path

        with patch(
            "kora_v2.agents.background.vault_organizer_handlers.get_autonomous_context"
        ) as mock_ctx:
            mock_ctx.return_value = MagicMock(container=container, db_path=tmp_path / "op.db")

            task = _make_task("moc_sessions")
            ctx = _make_ctx(task)
            result = await moc_sessions_step(task, ctx)

        assert result.outcome == "complete"
        moc_dir = tmp_path / "Maps of Content"
        if moc_dir.exists():
            moc_files = list(moc_dir.glob("*.md"))
            assert len(moc_files) == 0

    @pytest.mark.asyncio
    async def test_regenerates_overview_moc_when_total_threshold_met(
        self,
        tmp_path: Path,
    ) -> None:
        container = MagicMock()
        container.projection_db = AsyncMock()

        mock_cursor_type = AsyncMock()
        mock_cursor_type.fetchall = AsyncMock(return_value=[
            ("episodic", 3),
        ])
        mock_cursor_domain = AsyncMock()
        mock_cursor_domain.fetchall = AsyncMock(return_value=[
            ("identity", 2),
        ])
        mock_cursor_notes = AsyncMock()
        mock_cursor_notes.fetchall = AsyncMock(return_value=[
            ("note-1", "Content about memory 1", 0.8, "2025-01-01"),
            ("note-2", "Content about memory 2", 0.6, "2025-01-02"),
            ("note-3", "Content about memory 3", 0.9, "2025-01-03"),
        ])
        mock_cursor_facts = AsyncMock()
        mock_cursor_facts.fetchall = AsyncMock(return_value=[
            ("fact-1", "Jordan uses local-first tools", 0.9, "2025-01-04"),
            ("fact-2", "Jordan prefers low maintenance", 0.8, "2025-01-05"),
        ])

        async def mock_execute(query, *args):
            if "GROUP BY memory_type" in query:
                return mock_cursor_type
            if "GROUP BY domain" in query:
                return mock_cursor_domain
            if "FROM memories" in query and "ORDER BY" in query:
                return mock_cursor_notes
            if "FROM user_model_facts" in query and "ORDER BY" in query:
                return mock_cursor_facts
            m = AsyncMock()
            m.fetchall = AsyncMock(return_value=[])
            return m

        container.projection_db._db = AsyncMock()
        container.projection_db._db.execute = mock_execute
        container.memory_store = MagicMock()
        container.memory_store._base = tmp_path

        with patch(
            "kora_v2.agents.background.vault_organizer_handlers.get_autonomous_context"
        ) as mock_ctx:
            mock_ctx.return_value = MagicMock(
                container=container,
                db_path=tmp_path / "op.db",
            )

            task = _make_task("moc_sessions")
            ctx = _make_ctx(task)
            result = await moc_sessions_step(task, ctx)

        assert result.outcome == "complete"
        moc_file = tmp_path / "Maps of Content" / "MOC - Overview.md"
        assert moc_file.exists()

    @pytest.mark.asyncio
    async def test_session_mirror_creates_notes(self, tmp_path: Path) -> None:
        # Create a bridge file
        bridges_dir = tmp_path / ".kora" / "bridges"
        bridges_dir.mkdir(parents=True, exist_ok=True)

        bridge_data = {
            "session_id": "session-001",
            "ended_at": "2025-06-15T14:30:00+00:00",
            "topics": ["project planning", "code review"],
            "emotional_trajectory": "focused -> tired",
            "open_threads": ["finish PR review"],
        }
        (bridges_dir / "session-001.json").write_text(
            json.dumps(bridge_data), encoding="utf-8"
        )

        container = MagicMock()
        container.projection_db = AsyncMock()

        async def mock_execute(query, *args):
            m = AsyncMock()
            m.fetchall = AsyncMock(return_value=[])
            return m

        container.projection_db._db = AsyncMock()
        container.projection_db._db.execute = mock_execute
        container.memory_store = MagicMock()
        container.memory_store._base = tmp_path

        with patch(
            "kora_v2.agents.background.vault_organizer_handlers.get_autonomous_context"
        ) as mock_ctx:
            mock_ctx.return_value = MagicMock(container=container, db_path=tmp_path / "op.db")

            task = _make_task("moc_sessions")
            ctx = _make_ctx(task)
            result = await moc_sessions_step(task, ctx)

        assert result.outcome == "complete"
        # Session note should exist
        sessions_dir = tmp_path / "Sessions" / "2025" / "06"
        assert sessions_dir.is_dir()
        session_files = list(sessions_dir.glob("*.md"))
        assert len(session_files) >= 1

        # Session index should exist
        index_file = tmp_path / "Sessions" / "index.md"
        assert index_file.is_file()

    @pytest.mark.asyncio
    async def test_session_mirror_reads_markdown_bridges(
        self, tmp_path: Path
    ) -> None:
        bridges_dir = tmp_path / ".kora" / "bridges"
        bridges_dir.mkdir(parents=True, exist_ok=True)
        bridge = bridges_dir / "20250615_143000_session-002.md"
        bridge.write_text(
            "---\n"
            "session_id: session-002\n"
            "created_at: '2025-06-15T14:30:00+00:00'\n"
            "open_threads:\n"
            "  - finish PR review\n"
            "emotional_trajectory: focused -> tired\n"
            "---\n\n"
            "# Session: session-002\n"
            "We planned the next implementation pass.\n",
            encoding="utf-8",
        )

        container = MagicMock()
        container.projection_db = AsyncMock()

        async def mock_execute(query, *args):
            m = AsyncMock()
            m.fetchall = AsyncMock(return_value=[])
            return m

        container.projection_db._db = AsyncMock()
        container.projection_db._db.execute = mock_execute
        container.memory_store = MagicMock()
        container.memory_store._base = tmp_path

        with patch(
            "kora_v2.agents.background.vault_organizer_handlers.get_autonomous_context"
        ) as mock_ctx:
            mock_ctx.return_value = MagicMock(
                container=container,
                db_path=tmp_path / "op.db",
            )

            task = _make_task("moc_sessions")
            ctx = _make_ctx(task)
            result = await moc_sessions_step(task, ctx)

        assert result.outcome == "complete"
        sessions_dir = tmp_path / "Sessions" / "2025" / "06"
        session_files = list(sessions_dir.glob("*.md"))
        assert session_files
        assert (tmp_path / "Sessions" / "index.md").is_file()

    @pytest.mark.asyncio
    async def test_session_mirror_uses_runtime_memory_root(
        self, tmp_path: Path
    ) -> None:
        runtime_root = tmp_path / "runtime_memory"
        legacy_root = tmp_path / "legacy_memory"
        bridges_dir = runtime_root / ".kora" / "bridges"
        bridges_dir.mkdir(parents=True, exist_ok=True)
        bridge = bridges_dir / "20250615_143000_session-runtime.md"
        bridge.write_text(
            "---\n"
            "session_id: session-runtime\n"
            "created_at: '2025-06-15T14:30:00+00:00'\n"
            "summary: Runtime bridge summary\n"
            "open_threads:\n"
            "  - finish PR review\n"
            "emotional_trajectory: focused -> tired\n"
            "---\n\n"
            "Runtime bridge body.\n",
            encoding="utf-8",
        )

        container = MagicMock()
        container.settings = SimpleNamespace(
            memory=SimpleNamespace(kora_memory_path=str(runtime_root))
        )
        container.projection_db = AsyncMock()

        async def mock_execute(query, *args):
            m = AsyncMock()
            m.fetchall = AsyncMock(return_value=[])
            return m

        container.projection_db._db = AsyncMock()
        container.projection_db._db.execute = mock_execute
        container.memory_store = MagicMock()
        container.memory_store._base = legacy_root

        with patch(
            "kora_v2.agents.background.vault_organizer_handlers.get_autonomous_context"
        ) as mock_ctx:
            mock_ctx.return_value = MagicMock(
                container=container,
                db_path=tmp_path / "op.db",
            )

            task = _make_task("moc_sessions")
            ctx = _make_ctx(task)
            result = await moc_sessions_step(task, ctx)

        assert result.outcome == "complete"
        runtime_index = runtime_root / "Sessions" / "index.md"
        assert runtime_index.is_file()
        assert not (legacy_root / "Sessions" / "index.md").exists()

        session_files = list((runtime_root / "Sessions" / "2025" / "06").glob("*.md"))
        assert session_files
        session_note = session_files[0].read_text(encoding="utf-8")
        assert "Runtime bridge summary" in session_note


# ══════════════════════════════════════════════════════════════════════════
# Helper builders
# ══════════════════════════════════════════════════════════════════════════


class TestHelperBuilders:
    """Test MOC page, entity page, and session note builders."""

    def test_build_entity_page(self) -> None:
        data = EntityPageData(
            name="Sarah",
            entity_type="person",
            backlinks=[
                {"id": "note-1", "content": "Met Sarah", "created_at": "2025-01-01"},
            ],
            relationships=[
                {"entity_name": "John", "co_occurrence_count": 3},
            ],
            first_mention="2025-01-01",
            last_mention="2025-06-15",
        )
        page = build_entity_page(data)
        assert "# Sarah" in page
        assert "person" in page
        assert "[[note-1]]" in page
        assert "[[John]]" in page
        assert "First mentioned" in page

    def test_build_moc_page(self) -> None:
        notes = [
            {"id": "n1", "content": "First note", "importance": 0.9, "created_at": "2025-01-01"},
            {"id": "n2", "content": "Second note", "importance": 0.5, "created_at": "2025-01-02"},
        ]
        page = build_moc_page("identity", notes)
        assert "# MOC - Identity" in page
        assert "[[n1]]" in page
        assert "[[n2]]" in page

    def test_build_moc_page_empty(self) -> None:
        page = build_moc_page("health", [])
        assert "# MOC - Health" in page
        assert "No notes" in page

    def test_build_session_note(self) -> None:
        bridge = {
            "session_id": "s1",
            "topics": ["work", "health"],
            "emotional_trajectory": "calm -> stressed",
            "open_threads": ["finish task"],
        }
        note = build_session_note(bridge, "2025-06-15")
        assert "Session: 2025-06-15" in note
        assert "work" in note
        assert "health" in note
        assert "calm -> stressed" in note
        assert "finish task" in note

    def test_build_session_index(self) -> None:
        sessions = [
            {"date": "2025-06-15", "topics": ["work"], "note_name": "2025-06-15_work"},
            {"date": "2025-05-01", "topics": ["travel"], "note_name": "2025-05-01_travel"},
        ]
        index = build_session_index(sessions)
        assert "Session Index" in index
        assert "2025-06" in index
        assert "2025-05" in index
        assert "[[2025-06-15_work]]" in index

    def test_build_session_index_empty(self) -> None:
        index = build_session_index([])
        assert "Session Index" in index
        assert "No sessions" in index
