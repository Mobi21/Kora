"""Phase 8c acceptance tests — Vault Organizer stage handlers.

Acceptance items:
- 52: Vault Organizer re-indexing detects filesystem-edited notes
- 53: Vault Organizer structure enforces folder hierarchy on Inbox triage
- 54: Wikilinks injected without corrupting frontmatter or code blocks
- 55: Entity pages generated with backlinks, relationships, mention dates
- 56: MOC pages regenerated when threshold reached
- 57: Session index and per-session notes populated
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# pysqlite3 monkey-patch
try:
    import pysqlite3 as _pysqlite3  # type: ignore[import-untyped]

    sys.modules["sqlite3"] = _pysqlite3
except ImportError:
    pass

import pytest

from kora_v2.agents.background.vault_organizer import (
    FOLDER_HIERARCHY,
    EntityPageData,
    build_entity_page,
    inject_wikilinks,
)
from kora_v2.agents.background.vault_organizer_handlers import (
    links_step,
    moc_sessions_step,
    pending_entity_merges,
    pending_notes,
    reindex_step,
    structure_step,
)
from kora_v2.memory.store import _parse_frontmatter, _render_note
from kora_v2.runtime.orchestration.worker_task import (
    StepContext,
    WorkerTask,
    WorkerTaskConfig,
)

# ══════════════════════════════════════════════════════════════════════════
# Shared test helpers
# ══════════════════════════════════════════════════════════════════════════


class MockEmbeddingModel:
    """Deterministic mock producing 768-dim vectors from text hash."""

    dimension = 768
    is_loaded = True

    def embed(self, text: str, task_type: str = "search_query") -> list[float]:
        h = hashlib.md5(text.encode()).digest()  # noqa: S324
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


def _make_task(stage_name: str = "run") -> WorkerTask:
    return WorkerTask(
        id=f"task-8c-{stage_name}",
        pipeline_instance_id="8c-acceptance-pipeline",
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
        goal="acceptance test",
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
    elif memory_type in ("episodic", "reflective", "procedural"):
        target_dir = base_path / "Long-Term"
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
# 52: Vault Organizer re-indexing detects filesystem-edited notes
# ══════════════════════════════════════════════════════════════════════════


class TestAcceptance52ReIndexing:
    """Re-indexing detects filesystem-edited notes and re-indexes them."""

    @pytest.mark.asyncio
    async def test_stale_entry_detected_and_reindexed(self, tmp_path: Path) -> None:
        from kora_v2.memory.projection import StaleEntry

        # Write a note file
        note_path = _write_note(tmp_path, "edited-note", "Updated by user in Obsidian")

        # Create stale entry pointing to it
        stale = StaleEntry(
            record_id="edited-note",
            source_path=str(note_path),
            source_table="memories",
            db_updated_at="2025-01-01T00:00:00+00:00",
            fs_mtime="2025-06-01T00:00:00+00:00",
        )

        container = MagicMock()
        container.projection_db = AsyncMock()
        container.projection_db.detect_stale_entries = AsyncMock(return_value=[stale])
        container.projection_db.update_memory_content = AsyncMock()
        container.projection_db._db = AsyncMock()
        container.projection_db._db.execute = AsyncMock(
            return_value=AsyncMock(fetchall=AsyncMock(return_value=[]))
        )
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
        container.projection_db.update_memory_content.assert_called_once()
        call_args = container.projection_db.update_memory_content.call_args
        assert call_args.kwargs["memory_id"] == "edited-note"
        assert "Updated by user in Obsidian" in call_args.kwargs["content"]


# ══════════════════════════════════════════════════════════════════════════
# 53: Vault Organizer structure enforces folder hierarchy on Inbox triage
# ══════════════════════════════════════════════════════════════════════════


class TestAcceptance53Structure:
    """Structure stage creates folders and triages inbox."""

    @pytest.mark.asyncio
    async def test_folder_hierarchy_created(self, tmp_path: Path) -> None:
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

        for folder in FOLDER_HIERARCHY:
            assert (tmp_path / folder).is_dir(), f"Missing folder: {folder}"

    @pytest.mark.asyncio
    async def test_inbox_triage_with_high_confidence(self, tmp_path: Path) -> None:
        """High-confidence classification moves note out of Inbox."""
        inbox = tmp_path / "Inbox"
        inbox.mkdir(parents=True, exist_ok=True)

        meta = {"id": "triage-001", "memory_type": "episodic", "importance": 0.5}
        (inbox / "triage-001.md").write_text(
            _render_note(meta, "I learned to cook pasta carbonara today."),
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
        container.llm.chat = AsyncMock(
            return_value='{"folder": "Long-Term/Procedural", "confidence": 0.9}'
        )

        with patch(
            "kora_v2.agents.background.vault_organizer_handlers.get_autonomous_context"
        ) as mock_ctx:
            mock_ctx.return_value = MagicMock(container=container, db_path=tmp_path / "op.db")

            task = _make_task("structure")
            ctx = _make_ctx(task)
            result = await structure_step(task, ctx)

        assert result.outcome == "complete"
        container.memory_store.move_note.assert_called_once()
        call_args = container.memory_store.move_note.call_args
        assert call_args.kwargs["note_id"] == "triage-001"
        new_path = call_args.kwargs["new_path"]
        assert "Long-Term" in str(new_path) and "Procedural" in str(new_path)


# ══════════════════════════════════════════════════════════════════════════
# 54: Wikilinks injected without corrupting frontmatter or code blocks
# ══════════════════════════════════════════════════════════════════════════


class TestAcceptance54Wikilinks:
    """Wikilink injection preserves frontmatter and code blocks."""

    def test_frontmatter_preserved(self) -> None:
        body = "---\nauthor: Sarah\ntags: [test]\n---\n\nI met Sarah at the park."
        result = inject_wikilinks(body, ["Sarah"])

        # Parse frontmatter from result — should be intact
        meta, body_out = _parse_frontmatter(result)
        assert meta.get("author") == "Sarah"
        assert "[[Sarah]] at the park." in body_out

    def test_code_block_preserved(self) -> None:
        body = "```python\nuser = Sarah\n```\n\nSarah is a person."
        result = inject_wikilinks(body, ["Sarah"])

        # Code block should be intact
        assert "```python\nuser = Sarah\n```" in result
        # Text Sarah should be linked
        assert "[[Sarah]] is a person." in result

    def test_multiple_excluded_regions(self) -> None:
        body = (
            "---\nentity: Sarah\n---\n\n"
            "Sarah said hello.\n\n"
            "```\nSarah = code\n```\n\n"
            "`Sarah` is inline.\n\n"
            "[[Sarah]] already linked.\n\n"
            "<!-- Sarah comment -->\n\n"
            "[Sarah](http://example.com)\n"
        )
        result = inject_wikilinks(body, ["Sarah"])

        # Count wikilinks — only the first occurrence in the plain-text
        # section should be linked
        meta, body_out = _parse_frontmatter(result)

        # Frontmatter should be intact
        assert meta.get("entity") == "Sarah"

        # Code block should be intact
        assert "```\nSarah = code\n```" in result

        # Inline code should be intact
        assert "`Sarah`" in result

        # HTML comment should be intact
        assert "<!-- Sarah comment -->" in result


# ══════════════════════════════════════════════════════════════════════════
# 55: Entity pages generated with backlinks, relationships, mention dates
# ══════════════════════════════════════════════════════════════════════════


class TestAcceptance55EntityPages:
    """Entity pages contain backlinks, relationships, and mention dates."""

    def test_entity_page_has_backlinks(self) -> None:
        data = EntityPageData(
            name="Sarah Connor",
            entity_type="person",
            backlinks=[
                {"id": "note-1", "content": "Met Sarah Connor today", "created_at": "2025-01-01"},
                {"id": "note-2", "content": "Sarah Connor called", "created_at": "2025-02-01"},
            ],
            relationships=[
                {"entity_name": "John Connor", "co_occurrence_count": 5},
                {"entity_name": "Kyle Reese", "co_occurrence_count": 2},
            ],
            first_mention="2025-01-01",
            last_mention="2025-06-15",
        )
        page = build_entity_page(data)

        assert "# Sarah Connor" in page
        assert "person" in page
        assert "[[note-1]]" in page
        assert "[[note-2]]" in page
        assert "[[John Connor]]" in page
        assert "[[Kyle Reese]]" in page
        assert "First mentioned" in page
        assert "2025-01-01" in page
        assert "Last mentioned" in page
        assert "2025-06-15" in page

    @pytest.mark.asyncio
    async def test_entity_page_generated_by_links_step(self, tmp_path: Path) -> None:
        from kora_v2.memory.projection import EntityRecord, EntityRelationship, MemoryRecord
        from kora_v2.memory.store import NoteContent, NoteMetadata

        pending_notes.clear()
        pending_entity_merges.clear()
        pending_notes.append("note-entity-test")

        note_path = _write_note(
            tmp_path, "note-entity-test", "Talked to Sarah Connor about plans."
        )

        mock_entity = EntityRecord(
            id="ent-sc",
            name="Sarah Connor",
            canonical_name="sarah connor",
            entity_type="person",
            active_link_count=2,
            first_mention="2025-01-01",
            last_mention="2025-06-15",
        )

        mock_note = NoteContent(
            metadata=NoteMetadata(
                id="note-entity-test",
                memory_type="episodic",
                source_path=str(note_path),
            ),
            body="Talked to Sarah Connor about plans.",
        )

        mock_memory = MemoryRecord(
            id="note-entity-test",
            content="Talked to Sarah Connor about plans.",
            memory_type="episodic",
            created_at="2025-01-01",
        )

        mock_relationship = EntityRelationship(
            entity_id="ent-jc",
            entity_name="John Connor",
            co_occurrence_count=3,
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

        entity_dir = tmp_path / "Entities" / "People"
        entity_files = list(entity_dir.glob("*.md"))
        assert len(entity_files) >= 1

        page_content = entity_files[0].read_text(encoding="utf-8")
        assert "Sarah Connor" in page_content
        assert "[[John Connor]]" in page_content
        assert "First mentioned" in page_content


# ══════════════════════════════════════════════════════════════════════════
# 56: MOC pages regenerated when threshold reached
# ══════════════════════════════════════════════════════════════════════════


class TestAcceptance56MOCPages:
    """MOC pages regenerated when at least N notes changed."""

    @pytest.mark.asyncio
    async def test_moc_regenerated_with_enough_notes(self, tmp_path: Path) -> None:
        container = MagicMock()
        container.projection_db = AsyncMock()

        # Simulate enough notes for episodic domain
        mock_cursor_type = AsyncMock()
        mock_cursor_type.fetchall = AsyncMock(return_value=[("episodic", 8)])

        mock_cursor_domain = AsyncMock()
        mock_cursor_domain.fetchall = AsyncMock(return_value=[("identity", 2)])

        note_rows = [
            (f"note-{i}", f"Content of note {i}", 0.5 + i * 0.05, f"2025-01-{i + 1:02d}")
            for i in range(8)
        ]
        mock_cursor_notes = AsyncMock()
        mock_cursor_notes.fetchall = AsyncMock(return_value=note_rows)

        mock_cursor_facts = AsyncMock()
        mock_cursor_facts.fetchall = AsyncMock(return_value=[])

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
            mock_ctx.return_value = MagicMock(container=container, db_path=tmp_path / "op.db")

            task = _make_task("moc_sessions")
            ctx = _make_ctx(task)
            result = await moc_sessions_step(task, ctx)

        assert result.outcome == "complete"

        moc_dir = tmp_path / "Maps of Content"
        moc_files = list(moc_dir.glob("MOC - *.md"))
        assert len(moc_files) >= 1

        moc_content = moc_files[0].read_text(encoding="utf-8")
        assert "MOC" in moc_content
        assert "[[note-" in moc_content


# ══════════════════════════════════════════════════════════════════════════
# 57: Session index and per-session notes populated
# ══════════════════════════════════════════════════════════════════════════


class TestAcceptance57Sessions:
    """Session index and per-session notes populated from bridges."""

    @pytest.mark.asyncio
    async def test_session_notes_and_index_created(self, tmp_path: Path) -> None:
        # Create bridge files
        bridges_dir = tmp_path / ".kora" / "bridges"
        bridges_dir.mkdir(parents=True, exist_ok=True)

        bridges = [
            {
                "session_id": "session-001",
                "ended_at": "2025-03-15T10:30:00+00:00",
                "topics": ["morning routine", "medications"],
                "emotional_trajectory": "groggy -> focused",
                "open_threads": ["check med dosage"],
            },
            {
                "session_id": "session-002",
                "ended_at": "2025-03-15T16:00:00+00:00",
                "topics": ["project planning"],
                "emotional_trajectory": "focused -> tired",
                "open_threads": [],
            },
        ]

        for bridge in bridges:
            (bridges_dir / f"{bridge['session_id']}.json").write_text(
                json.dumps(bridge), encoding="utf-8"
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

        # Session notes should exist
        session_dir = tmp_path / "Sessions" / "2025" / "03"
        assert session_dir.is_dir()
        session_files = list(session_dir.glob("*.md"))
        assert len(session_files) == 2

        # Check content of first session note
        first_note = None
        for sf in session_files:
            content = sf.read_text(encoding="utf-8")
            if "morning routine" in content:
                first_note = content
                break
        assert first_note is not None
        assert "morning routine" in first_note
        assert "medications" in first_note
        assert "groggy -> focused" in first_note

        # Session index should exist
        index_file = tmp_path / "Sessions" / "index.md"
        assert index_file.is_file()
        index_content = index_file.read_text(encoding="utf-8")
        assert "Session Index" in index_content
        assert "2025-03" in index_content
