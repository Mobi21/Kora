"""Phase 8b acceptance tests — Memory Steward stage handlers.

Acceptance items:
- 47: Memory extraction from transcripts produces domain-typed facts
- 48: Memory consolidation merges related notes without losing facts
- 49: Memory deduplication preserves richer note, soft-deletes other
- 50: Entity resolution merges fuzzy variants across sessions
- 51: ADHD profile weekly refinement runs and updates User Model
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

import aiosqlite

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
        id=f"task-8b-{stage_name}",
        pipeline_instance_id="8b-test-pipeline",
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


async def _init_db(tmp_path: Path) -> Path:
    from kora_v2.core.db import init_operational_db

    db_path = tmp_path / "operational.db"
    await init_operational_db(db_path)
    return db_path


def _make_container(
    tmp_path: Path,
    *,
    llm_response: str = "[]",
) -> MagicMock:
    container = MagicMock()
    container.llm = AsyncMock()
    container.llm.chat = AsyncMock(return_value=llm_response)

    memory_path = tmp_path / "_KoraMemory"
    memory_path.mkdir(parents=True, exist_ok=True)

    from kora_v2.memory.store import FilesystemMemoryStore

    store = FilesystemMemoryStore(memory_path)
    container.memory_store = store

    emitter = MagicMock()
    emitter.emit = AsyncMock()
    container.event_emitter = emitter

    container.write_pipeline = AsyncMock()
    container.write_pipeline.store = AsyncMock(
        return_value=MagicMock(
            note_id="note-accept-001",
            action="created",
            source_path=str(memory_path / "test.md"),
        )
    )
    container.write_pipeline.store_user_model_fact = AsyncMock(
        return_value=MagicMock(
            note_id="fact-accept-001",
            action="created",
            source_path=str(memory_path / "test-fact.md"),
        )
    )

    container.projection_db = AsyncMock()
    container.projection_db.consolidate = AsyncMock(return_value=[])
    container.projection_db.deduplicate = AsyncMock(return_value=[])
    container.projection_db.get_entities_by_type = AsyncMock(return_value=[])
    container.projection_db.get_memories_by_entity = AsyncMock(return_value=[])
    container.projection_db.merge_entities = AsyncMock()
    container.projection_db.soft_delete = AsyncMock()

    container.embedding_model = MockEmbeddingModel()
    container.settings = MagicMock()
    container.settings.data_dir = tmp_path

    return container


# ══════════════════════════════════════════════════════════════════════════
# Item 47: Memory extraction from transcripts produces domain-typed facts
# ══════════════════════════════════════════════════════════════════════════


class TestItem47MemoryExtraction:
    """Memory extraction from transcripts produces domain-typed facts."""

    async def test_extraction_produces_typed_facts(self, tmp_path: Path) -> None:
        """End-to-end: extraction pulls from transcripts and produces
        facts with correct memory_type and domain classification."""
        db_path = await _init_db(tmp_path)

        # Insert transcript with facts spanning multiple domains
        messages = [
            {"role": "user", "content": "I started a new job at Google today"},
            {"role": "assistant", "content": "Congratulations!"},
            {"role": "user", "content": "My sister Sarah helped me prepare"},
            {"role": "assistant", "content": "That's great support!"},
        ]
        now = datetime.now(UTC).isoformat(timespec="seconds")
        async with aiosqlite.connect(str(db_path)) as db:
            await db.execute(
                "INSERT INTO session_transcripts "
                "(session_id, created_at, ended_at, message_count, messages) "
                "VALUES (?, ?, ?, ?, ?)",
                ("accept-47", now, now, 4, json.dumps(messages)),
            )
            await db.commit()

        # LLM returns domain-typed facts
        llm_response = json.dumps(
            [
                {
                    "content": "User started a new job at Google",
                    "memory_type": "user_model",
                    "domain": "work",
                    "importance": 0.9,
                    "entities": ["Google"],
                    "tags": ["career", "life_event"],
                },
                {
                    "content": "User's sister Sarah helped with job preparation",
                    "memory_type": "episodic",
                    "domain": "relationships",
                    "importance": 0.6,
                    "entities": ["Sarah"],
                    "tags": ["family", "support"],
                },
            ]
        )

        container = _make_container(tmp_path, llm_response=llm_response)

        with patch(
            "kora_v2.agents.background.memory_steward_handlers.get_autonomous_context"
        ) as mock_ctx:
            mock_ctx.return_value = MagicMock(container=container, db_path=db_path)

            from kora_v2.agents.background.memory_steward_handlers import (
                extract_step,
            )

            task = _make_task("extract")
            ctx = _make_ctx(task)
            result = await extract_step(task, ctx)

        assert result.outcome == "complete"
        assert "2 facts extracted" in (result.result_summary or "")

        # Verify the write pipeline was called with correct types
        calls = container.write_pipeline.store_user_model_fact.call_args_list
        assert len(calls) >= 1
        fact_call = calls[0]
        assert fact_call[1]["domain"] == "work"

        ep_calls = container.write_pipeline.store.call_args_list
        assert len(ep_calls) >= 1
        assert ep_calls[0][1]["memory_type"] == "episodic"

        # Verify transcript marked as processed
        async with aiosqlite.connect(str(db_path)) as db:
            cursor = await db.execute(
                "SELECT processed_at FROM session_transcripts "
                "WHERE session_id = 'accept-47'"
            )
            row = await cursor.fetchone()
            assert row[0] is not None


# ══════════════════════════════════════════════════════════════════════════
# Item 48: Memory consolidation merges notes without losing facts
# ══════════════════════════════════════════════════════════════════════════


class TestItem48MemoryConsolidation:
    """Memory consolidation merges related notes without losing facts."""

    async def test_consolidation_preserves_facts(self, tmp_path: Path) -> None:
        """Consolidation merges multiple related notes into one,
        preserving all distinct facts and not over-summarizing."""
        db_path = await _init_db(tmp_path)
        container = _make_container(tmp_path)

        from kora_v2.memory.projection import MemoryRecord, MergeCandidateGroup

        groups = [
            MergeCandidateGroup(
                records=[
                    MemoryRecord(
                        id="merge-a",
                        content="User likes hiking in the mountains",
                        importance=0.7,
                        memory_type="episodic",
                        source_table="memories",
                    ),
                    MemoryRecord(
                        id="merge-b",
                        content="User goes hiking with Sarah on weekends",
                        importance=0.6,
                        memory_type="episodic",
                        source_table="memories",
                    ),
                ],
                avg_similarity=0.85,
            )
        ]
        container.projection_db.consolidate = AsyncMock(return_value=groups)

        # Mock read_note to return distinct content
        container.memory_store = MagicMock()
        container.memory_store.read_note = AsyncMock(
            side_effect=[
                MagicMock(body="User likes hiking in the mountains."),
                MagicMock(body="User goes hiking with Sarah on weekends."),
            ]
        )
        container.memory_store.soft_delete_note = AsyncMock(return_value=True)
        container.memory_store._base = tmp_path / "_KoraMemory"

        # LLM produces a consolidation that preserves both facts
        consolidated = (
            "User enjoys hiking in the mountains and regularly goes "
            "hiking with Sarah on weekends."
        )
        container.llm.chat = AsyncMock(return_value=consolidated)

        with patch(
            "kora_v2.agents.background.memory_steward_handlers.get_autonomous_context"
        ) as mock_ctx:
            mock_ctx.return_value = MagicMock(container=container, db_path=db_path)

            from kora_v2.agents.background.memory_steward_handlers import (
                consolidate_step,
            )

            task = _make_task("consolidate")
            ctx = _make_ctx(task)
            result = await consolidate_step(task, ctx)

        assert result.outcome == "complete"
        assert "1 groups merged" in (result.result_summary or "")

        # New note stored via WritePipeline
        container.write_pipeline.store.assert_called_once()
        store_call = container.write_pipeline.store.call_args
        assert "hiking" in store_call[1]["content"]

        # Both originals soft-deleted
        assert container.memory_store.soft_delete_note.call_count == 2


# ══════════════════════════════════════════════════════════════════════════
# Item 49: Memory deduplication preserves richer note
# ══════════════════════════════════════════════════════════════════════════


class TestItem49MemoryDeduplication:
    """Memory deduplication preserves richer note, soft-deletes other."""

    async def test_dedup_keeps_richer_deletes_poorer(
        self, tmp_path: Path
    ) -> None:
        """When two notes are confirmed duplicates, the richer one is
        kept and the poorer one is soft-deleted with merged_from provenance."""
        db_path = await _init_db(tmp_path)
        container = _make_container(tmp_path)

        from kora_v2.memory.projection import DuplicatePair, MemoryRecord

        pairs = [
            DuplicatePair(
                record_a=MemoryRecord(
                    id="rich",
                    content="Detailed note about user's love for hiking with Sarah",
                    importance=0.9,
                    entities=json.dumps(["Sarah"]),
                    source_table="memories",
                ),
                record_b=MemoryRecord(
                    id="poor",
                    content="User likes hiking",
                    importance=0.3,
                    source_table="memories",
                ),
                similarity=0.95,
            )
        ]
        container.projection_db.deduplicate = AsyncMock(return_value=pairs)

        container.memory_store = MagicMock()
        container.memory_store.read_note = AsyncMock(
            side_effect=[
                MagicMock(body="Detailed note about user's love for hiking with Sarah"),
                MagicMock(body="User likes hiking"),
            ]
        )
        container.memory_store.soft_delete_note = AsyncMock(return_value=True)
        container.memory_store.update_frontmatter = AsyncMock(return_value=MagicMock())

        container.llm.chat = AsyncMock(
            return_value='{"is_duplicate": true, "reasoning": "Same underlying fact"}'
        )

        with patch(
            "kora_v2.agents.background.memory_steward_handlers.get_autonomous_context"
        ) as mock_ctx:
            mock_ctx.return_value = MagicMock(container=container, db_path=db_path)

            from kora_v2.agents.background.memory_steward_handlers import (
                dedup_step,
            )

            task = _make_task("dedup")
            ctx = _make_ctx(task)
            result = await dedup_step(task, ctx)

        assert result.outcome == "complete"
        assert "1 duplicates removed" in (result.result_summary or "")

        # The poor note (less important, no entities) should be deleted
        delete_call = container.memory_store.soft_delete_note.call_args
        assert "poor" in str(delete_call)

        # The richer note gets merged_from provenance
        fm_call = container.memory_store.update_frontmatter.call_args
        assert "merged_from" in str(fm_call)


# ══════════════════════════════════════════════════════════════════════════
# Item 50: Entity resolution merges fuzzy variants
# ══════════════════════════════════════════════════════════════════════════


class TestItem50EntityResolution:
    """Entity resolution merges fuzzy variants across sessions."""

    async def test_fuzzy_entity_merge(self, tmp_path: Path) -> None:
        """Entities with similar names (e.g. Sarah/Sara) are merged
        after LLM confirmation."""
        db_path = await _init_db(tmp_path)
        container = _make_container(tmp_path)

        from kora_v2.memory.projection import EntityRecord, MemoryRecord

        entities = [
            EntityRecord(
                id="e-sarah",
                name="Sarah",
                canonical_name="sarah",
                entity_type="person",
                active_link_count=5,
            ),
            EntityRecord(
                id="e-sara",
                name="Sara",
                canonical_name="sara",
                entity_type="person",
                active_link_count=2,
            ),
        ]
        container.projection_db.get_entities_by_type = AsyncMock(
            side_effect=lambda t: entities if t == "person" else []
        )
        container.projection_db.get_memories_by_entity = AsyncMock(
            return_value=[
                MemoryRecord(
                    id="m1",
                    content="Sarah is my sister",
                )
            ]
        )

        container.llm.chat = AsyncMock(
            return_value='{"is_same": true, "reasoning": "Same person, variant spelling"}'
        )

        with patch(
            "kora_v2.agents.background.memory_steward_handlers.get_autonomous_context"
        ) as mock_ctx:
            mock_ctx.return_value = MagicMock(container=container, db_path=db_path)

            from kora_v2.agents.background.memory_steward_handlers import (
                entities_step,
            )

            task = _make_task("entities")
            ctx = _make_ctx(task)
            result = await entities_step(task, ctx)

        assert result.outcome == "complete"
        assert "1 merged" in (result.result_summary or "")

        # Sara merged into Sarah (fewer links → source)
        container.projection_db.merge_entities.assert_called_once_with(
            source_id="e-sara",
            target_id="e-sarah",
        )

        # ENTITY_MERGED event emitted
        from kora_v2.core.events import EventType

        emit_calls = container.event_emitter.emit.call_args_list
        entity_merged_calls = [
            c for c in emit_calls if c[0][0] == EventType.ENTITY_MERGED
        ]
        assert len(entity_merged_calls) >= 1


# ══════════════════════════════════════════════════════════════════════════
# Item 51: ADHD profile weekly refinement
# ══════════════════════════════════════════════════════════════════════════


class TestItem51ADHDProfileRefinement:
    """ADHD profile weekly refinement runs and updates User Model."""

    async def test_adhd_refinement_updates_profile(self, tmp_path: Path) -> None:
        """Weekly ADHD profile refinement reads session data, calls LLM,
        and writes an updated profile to the User Model directory."""
        db_path = await _init_db(tmp_path)
        container = _make_container(tmp_path)

        memory_path = tmp_path / "_KoraMemory"
        from kora_v2.memory.store import FilesystemMemoryStore

        store = FilesystemMemoryStore(memory_path)
        container.memory_store = store

        # No existing profile — first run
        llm_response = (
            "peak_focus_windows:\n"
            "  - '09:00-11:30'\n"
            "  - '14:00-16:00'\n"
            "afternoon_crash_start: '13:00'\n"
            "afternoon_crash_end: '14:00'\n"
            "time_estimation_factor: 1.4\n"
            "energy_pattern: Highest in morning, dip after lunch\n"
            "focus_session_optimal_length: 45\n"
            "break_interval: 25\n"
        )
        container.llm.chat = AsyncMock(return_value=llm_response)

        with patch(
            "kora_v2.agents.background.memory_steward_handlers.get_autonomous_context"
        ) as mock_ctx:
            mock_ctx.return_value = MagicMock(container=container, db_path=db_path)

            from kora_v2.agents.background.memory_steward_handlers import (
                adhd_profile_refine_step,
            )

            task = _make_task("refine")
            ctx = _make_ctx(task)
            result = await adhd_profile_refine_step(task, ctx)

        assert result.outcome == "complete"
        assert "adhd_profile: refined" in (result.result_summary or "")

        # Verify the profile file was written
        adhd_dir = memory_path / "User Model" / "adhd_profile"
        profile_files = list(adhd_dir.glob("*.md"))
        assert len(profile_files) >= 1

        # Verify frontmatter has correct metadata
        from kora_v2.memory.store import _parse_frontmatter

        text = profile_files[0].read_text(encoding="utf-8")
        meta, body = _parse_frontmatter(text)
        assert meta.get("memory_type") == "user_model"
        assert meta.get("domain") == "adhd_profile"
        assert meta.get("locked_fields") == []

        # Verify body contains profile data
        assert "peak_focus_windows" in body
        assert "afternoon_crash_start" in body
