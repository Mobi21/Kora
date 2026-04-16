"""Unit tests for Memory Steward — Phase 8b.

Tests for:
- Extract step: drains signal_queue and session_transcripts
- Extract step: marks processed transcripts
- Extract step: handles LLM failures gracefully
- Consolidation step: respects threshold and batch limit
- Consolidation step: rejects >40% shrinkage
- Consolidation step: soft-deletes originals
- Dedup step: keeps richer note, deletes other
- Dedup step: respects dedup_rejected marking
- Entity resolution: merges confirmed duplicates
- Entity resolution: skips unconfirmed pairs
- Vault handoff: emits MEMORY_PIPELINE_COMPLETE
- ADHD profile: respects locked_fields
- ADHD profile: enters merge mode on user edit
- ADHD profile: writes conflict report
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# pysqlite3 monkey-patch
try:
    import pysqlite3 as _pysqlite3  # type: ignore[import-untyped]

    sys.modules["sqlite3"] = _pysqlite3
except ImportError:
    pass

import aiosqlite

from kora_v2.agents.background.memory_steward import (
    compute_shrinkage,
    jaro_winkler_similarity,
    parse_json_response,
    pick_richer_note,
    validate_extracted_facts,
)
from kora_v2.runtime.orchestration.worker_task import (
    StepContext,
    WorkerTask,
    WorkerTaskConfig,
)

# ══════════════════════════════════════════════════════════════════════════
# Mock helpers
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


def _make_task(
    stage_name: str = "run",
    pipeline_instance_id: str | None = "test-pipeline-001",
) -> WorkerTask:
    """Create a minimal WorkerTask for testing."""
    return WorkerTask(
        id="task-test-001",
        pipeline_instance_id=pipeline_instance_id,
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
        goal="test goal",
        system_prompt="",
    )


def _make_step_context(task: WorkerTask) -> StepContext:
    """Create a minimal StepContext for testing."""
    limiter = MagicMock()
    return StepContext(
        task=task,
        limiter=limiter,
        cancellation_flag=lambda: False,
        now=lambda: datetime.now(UTC),
    )


async def _setup_operational_db(db_path: Path) -> None:
    """Initialize operational.db with required tables."""
    from kora_v2.core.db import init_operational_db

    await init_operational_db(db_path)


async def _insert_transcript(
    db_path: Path,
    session_id: str,
    messages: list[dict],
    *,
    processed: bool = False,
) -> None:
    """Insert a test transcript row."""
    now = datetime.now(UTC).isoformat(timespec="seconds")
    async with aiosqlite.connect(str(db_path)) as db:
        await db.execute(
            "INSERT INTO session_transcripts "
            "(session_id, created_at, ended_at, message_count, messages, "
            " processed_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                session_id,
                now,
                now,
                len(messages),
                json.dumps(messages),
                now if processed else None,
            ),
        )
        await db.commit()


async def _insert_signal(
    db_path: Path,
    signal_id: str,
    session_id: str,
    *,
    status: str = "pending",
    priority: int = 1,
) -> None:
    """Insert a test signal_queue row."""
    now = datetime.now(UTC).isoformat(timespec="seconds")
    async with aiosqlite.connect(str(db_path)) as db:
        await db.execute(
            "INSERT INTO signal_queue "
            "(id, session_id, message_text, signal_types, priority, "
            " status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                signal_id,
                session_id,
                "test message",
                json.dumps(["life_event"]),
                priority,
                status,
                now,
            ),
        )
        await db.commit()


def _make_container(
    tmp_path: Path,
    *,
    llm_response: str = "[]",
    db_path: Path | None = None,
) -> MagicMock:
    """Build a mock container with memory services."""
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

    # WritePipeline mock
    container.write_pipeline = AsyncMock()
    container.write_pipeline.store = AsyncMock(
        return_value=MagicMock(
            note_id="note-001",
            action="created",
            source_path=str(memory_path / "test.md"),
        )
    )
    container.write_pipeline.store_user_model_fact = AsyncMock(
        return_value=MagicMock(
            note_id="fact-001",
            action="created",
            source_path=str(memory_path / "test-fact.md"),
        )
    )

    # ProjectionDB mock
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
    container.settings.memory = MagicMock()
    container.settings.memory.kora_memory_path = str(memory_path)

    return container


# ══════════════════════════════════════════════════════════════════════════
# Helper Tests
# ══════════════════════════════════════════════════════════════════════════


class TestParseJsonResponse:
    def test_valid_json_array(self) -> None:
        result = parse_json_response('[{"content": "test"}]')
        assert result == [{"content": "test"}]

    def test_json_with_code_fences(self) -> None:
        text = '```json\n[{"content": "test"}]\n```'
        result = parse_json_response(text)
        assert result == [{"content": "test"}]

    def test_embedded_json(self) -> None:
        text = 'Here is the response: [{"content": "test"}]'
        result = parse_json_response(text)
        assert result == [{"content": "test"}]

    def test_invalid_json(self) -> None:
        result = parse_json_response("not json at all")
        assert result is None


class TestValidateExtractedFacts:
    def test_valid_facts(self) -> None:
        facts = [
            {
                "content": "User likes hiking",
                "memory_type": "user_model",
                "domain": "preferences",
                "importance": 0.8,
                "entities": ["User"],
                "tags": ["hobby"],
            }
        ]
        result = validate_extracted_facts(facts)
        assert len(result) == 1
        assert result[0]["content"] == "User likes hiking"
        assert result[0]["memory_type"] == "user_model"

    def test_filters_empty_content(self) -> None:
        facts = [{"content": "", "memory_type": "episodic"}]
        result = validate_extracted_facts(facts)
        assert len(result) == 0

    def test_corrects_invalid_memory_type(self) -> None:
        facts = [{"content": "test", "memory_type": "invalid"}]
        result = validate_extracted_facts(facts)
        assert result[0]["memory_type"] == "episodic"

    def test_clamps_importance(self) -> None:
        facts = [{"content": "test", "importance": 5.0}]
        result = validate_extracted_facts(facts)
        assert result[0]["importance"] == 1.0

    def test_not_a_list(self) -> None:
        result = validate_extracted_facts("not a list")
        assert result == []


class TestJaroWinklerSimilarity:
    def test_identical(self) -> None:
        assert jaro_winkler_similarity("Sarah", "Sarah") == 1.0

    def test_similar(self) -> None:
        score = jaro_winkler_similarity("Sarah", "Sara")
        assert score > 0.9

    def test_different(self) -> None:
        score = jaro_winkler_similarity("Sarah", "Mark")
        assert score < 0.7

    def test_empty(self) -> None:
        assert jaro_winkler_similarity("", "") == 1.0
        assert jaro_winkler_similarity("test", "") == 0.0


class TestComputeShrinkage:
    def test_no_shrinkage(self) -> None:
        result = compute_shrinkage(["hello", "world"], "hello world plus more")
        assert result == 0.0

    def test_moderate_shrinkage(self) -> None:
        result = compute_shrinkage(["a" * 100, "b" * 100], "c" * 100)
        assert 0.4 < result < 0.6

    def test_empty_original(self) -> None:
        result = compute_shrinkage([], "anything")
        assert result == 0.0


class TestPickRicherNote:
    def test_higher_importance_wins(self) -> None:
        a = {"id": "a", "content": "short", "importance": 0.9, "entities": []}
        b = {"id": "b", "content": "short", "importance": 0.3, "entities": []}
        richer, poorer = pick_richer_note(a, b)
        assert richer["id"] == "a"
        assert poorer["id"] == "b"

    def test_more_entities_wins(self) -> None:
        a = {
            "id": "a",
            "content": "short",
            "importance": 0.5,
            "entities": json.dumps(["Sarah", "Mark"]),
        }
        b = {"id": "b", "content": "short", "importance": 0.5, "entities": "[]"}
        richer, poorer = pick_richer_note(a, b)
        assert richer["id"] == "a"


# ══════════════════════════════════════════════════════════════════════════
# Extract Step Tests
# ══════════════════════════════════════════════════════════════════════════


class TestExtractStep:
    """Tests for the extract_step handler."""

    async def test_drains_signals_and_transcripts(self, tmp_path: Path) -> None:
        """Extract step processes pending signals and unprocessed transcripts."""
        db_path = tmp_path / "operational.db"
        await _setup_operational_db(db_path)

        # Insert test data
        messages = [
            {"role": "user", "content": "I started a new job at Google"},
            {"role": "assistant", "content": "Congratulations!"},
        ]
        await _insert_transcript(db_path, "session-1", messages)
        await _insert_signal(db_path, "sig-1", "session-1")

        llm_response = json.dumps(
            [
                {
                    "content": "User started a new job at Google",
                    "memory_type": "user_model",
                    "domain": "work",
                    "importance": 0.9,
                    "entities": ["Google"],
                    "tags": ["career"],
                }
            ]
        )

        container = _make_container(
            tmp_path, llm_response=llm_response, db_path=db_path
        )

        with patch(
            "kora_v2.agents.background.memory_steward_handlers.get_autonomous_context"
        ) as mock_ctx:
            mock_ctx.return_value = MagicMock(container=container, db_path=db_path)
            from kora_v2.agents.background.memory_steward_handlers import (
                extract_step,
            )

            task = _make_task(stage_name="extract")
            ctx = _make_step_context(task)
            result = await extract_step(task, ctx)

        assert result.outcome == "complete"
        assert "1 facts extracted" in (result.result_summary or "")

        # Verify transcript marked processed
        async with aiosqlite.connect(str(db_path)) as db:
            cursor = await db.execute(
                "SELECT processed_at FROM session_transcripts "
                "WHERE session_id = 'session-1'"
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] is not None  # processed_at is set

        # Verify signal marked extracted
        async with aiosqlite.connect(str(db_path)) as db:
            cursor = await db.execute(
                "SELECT status FROM signal_queue WHERE id = 'sig-1'"
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == "extracted"

    async def test_marks_transcript_processed_on_failure(
        self, tmp_path: Path
    ) -> None:
        """Transcripts are marked processed even on LLM failure."""
        db_path = tmp_path / "operational.db"
        await _setup_operational_db(db_path)

        messages = [{"role": "user", "content": "hello"}]
        await _insert_transcript(db_path, "session-2", messages)

        container = _make_container(tmp_path, db_path=db_path)
        container.llm.chat = AsyncMock(side_effect=RuntimeError("LLM down"))

        with patch(
            "kora_v2.agents.background.memory_steward_handlers.get_autonomous_context"
        ) as mock_ctx:
            mock_ctx.return_value = MagicMock(container=container, db_path=db_path)
            from kora_v2.agents.background.memory_steward_handlers import (
                extract_step,
            )

            task = _make_task(stage_name="extract")
            ctx = _make_step_context(task)
            result = await extract_step(task, ctx)

        assert result.outcome == "complete"

        # Transcript still marked processed
        async with aiosqlite.connect(str(db_path)) as db:
            cursor = await db.execute(
                "SELECT processed_at FROM session_transcripts "
                "WHERE session_id = 'session-2'"
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] is not None

    async def test_handles_no_pending_data(self, tmp_path: Path) -> None:
        """Extract step completes cleanly when there's nothing to process."""
        db_path = tmp_path / "operational.db"
        await _setup_operational_db(db_path)

        container = _make_container(tmp_path, db_path=db_path)

        with patch(
            "kora_v2.agents.background.memory_steward_handlers.get_autonomous_context"
        ) as mock_ctx:
            mock_ctx.return_value = MagicMock(container=container, db_path=db_path)
            from kora_v2.agents.background.memory_steward_handlers import (
                extract_step,
            )

            task = _make_task(stage_name="extract")
            ctx = _make_step_context(task)
            result = await extract_step(task, ctx)

        assert result.outcome == "complete"
        assert "0 facts extracted" in (result.result_summary or "")


# ══════════════════════════════════════════════════════════════════════════
# Consolidation Step Tests
# ══════════════════════════════════════════════════════════════════════════


class TestConsolidateStep:
    """Tests for the consolidate_step handler."""

    async def test_respects_batch_limit(self, tmp_path: Path) -> None:
        """Consolidation processes at most MAX_CONSOLIDATION_GROUPS groups."""
        db_path = tmp_path / "operational.db"
        await _setup_operational_db(db_path)

        container = _make_container(tmp_path, db_path=db_path)

        # Mock 5 groups but should only process 3
        from kora_v2.memory.projection import MemoryRecord, MergeCandidateGroup

        groups = []
        for i in range(5):
            groups.append(
                MergeCandidateGroup(
                    records=[
                        MemoryRecord(
                            id=f"note-{i}-a",
                            content=f"Content A for group {i}",
                            source_table="memories",
                        ),
                        MemoryRecord(
                            id=f"note-{i}-b",
                            content=f"Content B for group {i}",
                            source_table="memories",
                        ),
                    ],
                    avg_similarity=0.85,
                )
            )
        container.projection_db.consolidate = AsyncMock(return_value=groups)

        # Mock reading notes
        container.memory_store = MagicMock()
        container.memory_store.read_note = AsyncMock(
            return_value=MagicMock(body="Test content for note", metadata=MagicMock())
        )
        container.memory_store.soft_delete_note = AsyncMock(return_value=True)
        container.memory_store._base = tmp_path / "_KoraMemory"

        container.llm.chat = AsyncMock(
            return_value="Consolidated content preserving all facts."
        )

        with patch(
            "kora_v2.agents.background.memory_steward_handlers.get_autonomous_context"
        ) as mock_ctx:
            mock_ctx.return_value = MagicMock(container=container, db_path=db_path)
            from kora_v2.agents.background.memory_steward_handlers import (
                consolidate_step,
            )

            task = _make_task(stage_name="consolidate")
            ctx = _make_step_context(task)
            result = await consolidate_step(task, ctx)

        assert result.outcome == "complete"
        # Should process at most 3 groups (MAX_CONSOLIDATION_GROUPS)
        assert container.llm.chat.call_count <= 3

    async def test_rejects_excessive_shrinkage(self, tmp_path: Path) -> None:
        """Consolidation rejects output that is >40% shorter than inputs."""
        db_path = tmp_path / "operational.db"
        await _setup_operational_db(db_path)

        container = _make_container(tmp_path, db_path=db_path)

        from kora_v2.memory.projection import MemoryRecord, MergeCandidateGroup

        groups = [
            MergeCandidateGroup(
                records=[
                    MemoryRecord(
                        id="note-a",
                        content="A" * 100,
                        source_table="memories",
                    ),
                    MemoryRecord(
                        id="note-b",
                        content="B" * 100,
                        source_table="memories",
                    ),
                ],
                avg_similarity=0.85,
            )
        ]
        container.projection_db.consolidate = AsyncMock(return_value=groups)

        # Read note returns long content
        container.memory_store = MagicMock()
        container.memory_store.read_note = AsyncMock(
            return_value=MagicMock(body="A" * 100, metadata=MagicMock())
        )
        container.memory_store.soft_delete_note = AsyncMock(return_value=True)
        container.memory_store._base = tmp_path / "_KoraMemory"

        # LLM returns very short consolidation (>40% shrinkage)
        container.llm.chat = AsyncMock(return_value="Short.")

        with patch(
            "kora_v2.agents.background.memory_steward_handlers.get_autonomous_context"
        ) as mock_ctx:
            mock_ctx.return_value = MagicMock(container=container, db_path=db_path)
            from kora_v2.agents.background.memory_steward_handlers import (
                consolidate_step,
            )

            task = _make_task(stage_name="consolidate")
            ctx = _make_step_context(task)
            result = await consolidate_step(task, ctx)

        assert result.outcome == "complete"
        assert "1 rejected" in (result.result_summary or "")
        # WritePipeline.store should NOT have been called
        container.write_pipeline.store.assert_not_called()

    async def test_soft_deletes_originals(self, tmp_path: Path) -> None:
        """After successful consolidation, originals are soft-deleted."""
        db_path = tmp_path / "operational.db"
        await _setup_operational_db(db_path)

        container = _make_container(tmp_path, db_path=db_path)

        from kora_v2.memory.projection import MemoryRecord, MergeCandidateGroup

        groups = [
            MergeCandidateGroup(
                records=[
                    MemoryRecord(
                        id="note-x",
                        content="Facts about X",
                        importance=0.7,
                        source_table="memories",
                    ),
                    MemoryRecord(
                        id="note-y",
                        content="Facts about Y",
                        importance=0.5,
                        source_table="memories",
                    ),
                ],
                avg_similarity=0.85,
            )
        ]
        container.projection_db.consolidate = AsyncMock(return_value=groups)

        container.memory_store = MagicMock()
        container.memory_store.read_note = AsyncMock(
            return_value=MagicMock(
                body="Detailed facts about the subject with lots of context",
                metadata=MagicMock(),
            )
        )
        container.memory_store.soft_delete_note = AsyncMock(return_value=True)
        container.memory_store._base = tmp_path / "_KoraMemory"

        # LLM returns adequate consolidation (no shrinkage)
        container.llm.chat = AsyncMock(
            return_value=(
                "Detailed consolidated facts about X and Y subjects "
                "with lots of context preserving every detail from both"
            )
        )

        with patch(
            "kora_v2.agents.background.memory_steward_handlers.get_autonomous_context"
        ) as mock_ctx:
            mock_ctx.return_value = MagicMock(container=container, db_path=db_path)
            from kora_v2.agents.background.memory_steward_handlers import (
                consolidate_step,
            )

            task = _make_task(stage_name="consolidate")
            ctx = _make_step_context(task)
            result = await consolidate_step(task, ctx)

        assert result.outcome == "complete"
        assert "1 groups merged" in (result.result_summary or "")

        # Both originals should be soft-deleted
        assert container.memory_store.soft_delete_note.call_count == 2
        # MEMORY_SOFT_DELETED events emitted
        assert container.event_emitter.emit.call_count >= 2


# ══════════════════════════════════════════════════════════════════════════
# Dedup Step Tests
# ══════════════════════════════════════════════════════════════════════════


class TestDedupStep:
    """Tests for the dedup_step handler."""

    async def test_keeps_richer_note(self, tmp_path: Path) -> None:
        """Dedup keeps the note with higher importance/entities."""
        db_path = tmp_path / "operational.db"
        await _setup_operational_db(db_path)

        container = _make_container(tmp_path, db_path=db_path)

        from kora_v2.memory.projection import DuplicatePair, MemoryRecord

        pairs = [
            DuplicatePair(
                record_a=MemoryRecord(
                    id="rich-note",
                    content="Detailed note about hiking with Sarah",
                    importance=0.9,
                    entities=json.dumps(["Sarah"]),
                    source_table="memories",
                ),
                record_b=MemoryRecord(
                    id="poor-note",
                    content="I like hiking",
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
                MagicMock(body="Detailed note about hiking with Sarah"),
                MagicMock(body="I like hiking"),
            ]
        )
        container.memory_store.soft_delete_note = AsyncMock(return_value=True)
        container.memory_store.update_frontmatter = AsyncMock(return_value=MagicMock())

        # LLM confirms duplicate
        container.llm.chat = AsyncMock(
            return_value='{"is_duplicate": true, "reasoning": "Same fact"}'
        )

        with patch(
            "kora_v2.agents.background.memory_steward_handlers.get_autonomous_context"
        ) as mock_ctx:
            mock_ctx.return_value = MagicMock(container=container, db_path=db_path)
            from kora_v2.agents.background.memory_steward_handlers import (
                dedup_step,
            )

            task = _make_task(stage_name="dedup")
            ctx = _make_step_context(task)
            result = await dedup_step(task, ctx)

        assert result.outcome == "complete"
        assert "1 duplicates removed" in (result.result_summary or "")

        # The poor note should be soft-deleted
        container.memory_store.soft_delete_note.assert_called_once()
        call_args = container.memory_store.soft_delete_note.call_args
        assert call_args[1]["note_id"] == "poor-note" or call_args[0][0] == "poor-note"

    async def test_respects_distinct_marking(self, tmp_path: Path) -> None:
        """When LLM says notes are distinct, they are skipped."""
        db_path = tmp_path / "operational.db"
        await _setup_operational_db(db_path)

        container = _make_container(tmp_path, db_path=db_path)

        from kora_v2.memory.projection import DuplicatePair, MemoryRecord

        pairs = [
            DuplicatePair(
                record_a=MemoryRecord(
                    id="note-1",
                    content="I like hiking",
                    source_table="memories",
                ),
                record_b=MemoryRecord(
                    id="note-2",
                    content="I went hiking yesterday",
                    source_table="memories",
                ),
                similarity=0.93,
            )
        ]
        container.projection_db.deduplicate = AsyncMock(return_value=pairs)

        container.memory_store = MagicMock()
        container.memory_store.read_note = AsyncMock(
            side_effect=[
                MagicMock(body="I like hiking"),
                MagicMock(body="I went hiking yesterday"),
            ]
        )
        container.memory_store.soft_delete_note = AsyncMock()

        # LLM says distinct
        container.llm.chat = AsyncMock(
            return_value='{"is_duplicate": false, "reasoning": "Different facts"}'
        )

        with patch(
            "kora_v2.agents.background.memory_steward_handlers.get_autonomous_context"
        ) as mock_ctx:
            mock_ctx.return_value = MagicMock(container=container, db_path=db_path)
            from kora_v2.agents.background.memory_steward_handlers import (
                dedup_step,
            )

            task = _make_task(stage_name="dedup")
            ctx = _make_step_context(task)
            result = await dedup_step(task, ctx)

        assert result.outcome == "complete"
        assert "1 distinct" in (result.result_summary or "")
        # Soft delete should NOT have been called
        assert container.memory_store.soft_delete_note.call_count == 0


# ══════════════════════════════════════════════════════════════════════════
# Entity Resolution Step Tests
# ══════════════════════════════════════════════════════════════════════════


class TestEntitiesStep:
    """Tests for the entities_step handler."""

    async def test_merges_confirmed_duplicates(self, tmp_path: Path) -> None:
        """Entity resolution merges entities the LLM confirms are the same."""
        db_path = tmp_path / "operational.db"
        await _setup_operational_db(db_path)

        container = _make_container(tmp_path, db_path=db_path)

        from kora_v2.memory.projection import EntityRecord, MemoryRecord

        entities = [
            EntityRecord(
                id="e1",
                name="Sarah",
                canonical_name="sarah",
                entity_type="person",
                active_link_count=5,
            ),
            EntityRecord(
                id="e2",
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
                MemoryRecord(id="m1", content="Sarah is my friend")
            ]
        )

        # LLM confirms they are the same
        container.llm.chat = AsyncMock(
            return_value='{"is_same": true, "reasoning": "Same person"}'
        )

        with patch(
            "kora_v2.agents.background.memory_steward_handlers.get_autonomous_context"
        ) as mock_ctx:
            mock_ctx.return_value = MagicMock(container=container, db_path=db_path)
            from kora_v2.agents.background.memory_steward_handlers import (
                entities_step,
            )

            task = _make_task(stage_name="entities")
            ctx = _make_step_context(task)
            result = await entities_step(task, ctx)

        assert result.outcome == "complete"
        assert "1 merged" in (result.result_summary or "")

        # merge_entities should be called: Sara merged into Sarah
        container.projection_db.merge_entities.assert_called_once()
        call_args = container.projection_db.merge_entities.call_args
        assert call_args[1]["source_id"] == "e2"  # Sara (fewer links)
        assert call_args[1]["target_id"] == "e1"  # Sarah (more links)

    async def test_skips_unconfirmed_pairs(self, tmp_path: Path) -> None:
        """Entity resolution skips pairs the LLM says are distinct."""
        db_path = tmp_path / "operational.db"
        await _setup_operational_db(db_path)

        container = _make_container(tmp_path, db_path=db_path)

        from kora_v2.memory.projection import EntityRecord

        entities = [
            EntityRecord(
                id="e1",
                name="Mark",
                canonical_name="mark",
                entity_type="person",
                active_link_count=3,
            ),
            EntityRecord(
                id="e2",
                name="Mars",
                canonical_name="mars",
                entity_type="person",
                active_link_count=1,
            ),
        ]
        # Only return entities for "person" type
        container.projection_db.get_entities_by_type = AsyncMock(
            side_effect=lambda t: entities if t == "person" else []
        )
        container.projection_db.get_memories_by_entity = AsyncMock(return_value=[])

        # LLM says they are different
        container.llm.chat = AsyncMock(
            return_value='{"is_same": false, "reasoning": "Different entities"}'
        )

        with patch(
            "kora_v2.agents.background.memory_steward_handlers.get_autonomous_context"
        ) as mock_ctx:
            mock_ctx.return_value = MagicMock(container=container, db_path=db_path)
            from kora_v2.agents.background.memory_steward_handlers import (
                entities_step,
            )

            task = _make_task(stage_name="entities")
            ctx = _make_step_context(task)

            # Check if Mark/Mars pass the Jaro-Winkler threshold
            from kora_v2.agents.background.memory_steward import (
                jaro_winkler_similarity,
            )

            sim = jaro_winkler_similarity("mark", "mars")
            if sim >= 0.85:
                result = await entities_step(task, ctx)
                assert result.outcome == "complete"
                container.projection_db.merge_entities.assert_not_called()
            else:
                # Mark/Mars are below threshold, so no LLM call needed
                result = await entities_step(task, ctx)
                assert result.outcome == "complete"
                assert "0 merged" in (result.result_summary or "")


# ══════════════════════════════════════════════════════════════════════════
# Vault Handoff Step Tests
# ══════════════════════════════════════════════════════════════════════════


class TestVaultHandoffStep:
    """Tests for the vault_handoff_step handler."""

    async def test_emits_pipeline_complete(self, tmp_path: Path) -> None:
        """Vault handoff emits MEMORY_PIPELINE_COMPLETE event."""
        db_path = tmp_path / "operational.db"
        await _setup_operational_db(db_path)

        container = _make_container(tmp_path, db_path=db_path)

        with patch(
            "kora_v2.agents.background.memory_steward_handlers.get_autonomous_context"
        ) as mock_ctx:
            mock_ctx.return_value = MagicMock(container=container, db_path=db_path)
            from kora_v2.agents.background.memory_steward_handlers import (
                vault_handoff_step,
            )

            task = _make_task(stage_name="vault_handoff")
            ctx = _make_step_context(task)
            result = await vault_handoff_step(task, ctx)

        assert result.outcome == "complete"
        assert "PIPELINE_COMPLETE" in (result.result_summary or "")

        # Event should have been emitted
        from kora_v2.core.events import EventType

        container.event_emitter.emit.assert_called_once()
        call_args = container.event_emitter.emit.call_args
        assert call_args[0][0] == EventType.PIPELINE_COMPLETE


# ══════════════════════════════════════════════════════════════════════════
# ADHD Profile Refinement Tests
# ══════════════════════════════════════════════════════════════════════════


class TestADHDProfileRefineStep:
    """Tests for the adhd_profile_refine_step handler."""

    async def test_respects_locked_fields(self, tmp_path: Path) -> None:
        """ADHD profile refinement never overwrites locked fields."""
        db_path = tmp_path / "operational.db"
        await _setup_operational_db(db_path)

        container = _make_container(tmp_path, db_path=db_path)
        memory_path = tmp_path / "_KoraMemory"
        from kora_v2.memory.store import FilesystemMemoryStore

        store = FilesystemMemoryStore(memory_path)
        container.memory_store = store

        # Create profile with locked field
        adhd_dir = memory_path / "User Model" / "adhd_profile"
        adhd_dir.mkdir(parents=True, exist_ok=True)
        profile_path = adhd_dir / "adhd-profile.md"
        profile_content = (
            "---\n"
            "id: adhd-profile\n"
            "memory_type: user_model\n"
            "domain: adhd_profile\n"
            "locked_fields:\n"
            "  - peak_focus_windows\n"
            "created_at: '2024-01-01T00:00:00+00:00'\n"
            "updated_at: '2024-01-01T00:00:00+00:00'\n"
            "---\n\n"
            "peak_focus_windows:\n"
            "  - '09:00-11:30'\n"
            "  - '14:00-16:00'\n"
            "afternoon_crash_start: '13:00'\n"
            "time_estimation_factor: 1.5\n"
        )
        profile_path.write_text(profile_content, encoding="utf-8")

        # LLM tries to change locked field
        llm_response = (
            "peak_focus_windows:\n"
            "  - '10:00-12:00'\n"  # Different from user's locked value
            "afternoon_crash_start: '13:30'\n"
            "time_estimation_factor: 1.3\n"
        )
        container.llm.chat = AsyncMock(return_value=llm_response)

        with patch(
            "kora_v2.agents.background.memory_steward_handlers.get_autonomous_context"
        ) as mock_ctx:
            mock_ctx.return_value = MagicMock(container=container, db_path=db_path)
            from kora_v2.agents.background.memory_steward_handlers import (
                adhd_profile_refine_step,
            )

            task = _make_task(stage_name="refine")
            ctx = _make_step_context(task)
            result = await adhd_profile_refine_step(task, ctx)

        assert result.outcome == "complete"

        # Read the written profile and verify locked field preserved
        import yaml

        written = profile_path.read_text(encoding="utf-8")
        from kora_v2.memory.store import _parse_frontmatter

        meta, body = _parse_frontmatter(written)
        parsed = yaml.safe_load(body) or {}

        # peak_focus_windows should be user's original value, not LLM's
        if isinstance(parsed, dict) and "peak_focus_windows" in parsed:
            assert parsed["peak_focus_windows"] == ["09:00-11:30", "14:00-16:00"]

    async def test_enters_merge_mode_on_user_edit(self, tmp_path: Path) -> None:
        """ADHD profile enters merge mode when file was edited since last fire."""
        db_path = tmp_path / "operational.db"
        await _setup_operational_db(db_path)

        container = _make_container(tmp_path, db_path=db_path)
        memory_path = tmp_path / "_KoraMemory"
        from kora_v2.memory.store import FilesystemMemoryStore

        store = FilesystemMemoryStore(memory_path)
        container.memory_store = store

        # Create profile
        adhd_dir = memory_path / "User Model" / "adhd_profile"
        adhd_dir.mkdir(parents=True, exist_ok=True)
        profile_path = adhd_dir / "adhd-profile.md"
        profile_content = (
            "---\n"
            "id: adhd-profile\n"
            "memory_type: user_model\n"
            "domain: adhd_profile\n"
            "locked_fields: []\n"
            "created_at: '2024-01-01T00:00:00+00:00'\n"
            "updated_at: '2024-01-01T00:00:00+00:00'\n"
            "---\n\n"
            "afternoon_crash_start: '13:00'\n"
            "time_estimation_factor: 1.5\n"
        )
        profile_path.write_text(profile_content, encoding="utf-8")

        # Set trigger_state to last_fired_at in the past (before file mtime)
        past = (datetime.now(UTC) - timedelta(days=2)).isoformat()
        async with aiosqlite.connect(str(db_path)) as db:
            try:
                await db.execute(
                    "INSERT INTO trigger_state (trigger_id, last_fired_at) "
                    "VALUES (?, ?)",
                    ("weekly_adhd_profile", past),
                )
                await db.commit()
            except aiosqlite.OperationalError:
                pass  # Table may not exist in test

        # LLM proposes different values
        llm_response = (
            "afternoon_crash_start: '14:00'\n"
            "time_estimation_factor: 1.3\n"
        )
        container.llm.chat = AsyncMock(return_value=llm_response)

        with patch(
            "kora_v2.agents.background.memory_steward_handlers.get_autonomous_context"
        ) as mock_ctx:
            mock_ctx.return_value = MagicMock(container=container, db_path=db_path)
            from kora_v2.agents.background.memory_steward_handlers import (
                adhd_profile_refine_step,
            )

            task = _make_task(stage_name="refine")
            ctx = _make_step_context(task)
            result = await adhd_profile_refine_step(task, ctx)

        assert result.outcome == "complete"
        assert "merge mode" in (result.result_summary or "")

    async def test_writes_conflict_report(self, tmp_path: Path) -> None:
        """ADHD profile writes conflict report when merge mode detects changes."""
        db_path = tmp_path / "operational.db"
        await _setup_operational_db(db_path)

        container = _make_container(tmp_path, db_path=db_path)
        memory_path = tmp_path / "_KoraMemory"
        from kora_v2.memory.store import FilesystemMemoryStore

        store = FilesystemMemoryStore(memory_path)
        container.memory_store = store

        # Create profile with a value
        adhd_dir = memory_path / "User Model" / "adhd_profile"
        adhd_dir.mkdir(parents=True, exist_ok=True)
        profile_path = adhd_dir / "adhd-profile.md"
        profile_content = (
            "---\n"
            "id: adhd-profile\n"
            "memory_type: user_model\n"
            "domain: adhd_profile\n"
            "locked_fields: []\n"
            "created_at: '2024-01-01T00:00:00+00:00'\n"
            "updated_at: '2024-01-01T00:00:00+00:00'\n"
            "---\n\n"
            "afternoon_crash_start: '13:00'\n"
            "time_estimation_factor: 1.5\n"
        )
        profile_path.write_text(profile_content, encoding="utf-8")

        # Force merge mode: set last_fired_at in the distant past
        past = (datetime.now(UTC) - timedelta(days=10)).isoformat()
        async with aiosqlite.connect(str(db_path)) as db:
            try:
                await db.execute(
                    "INSERT INTO trigger_state (trigger_id, last_fired_at) "
                    "VALUES (?, ?)",
                    ("weekly_adhd_profile", past),
                )
                await db.commit()
            except aiosqlite.OperationalError:
                pass

        # LLM proposes different values
        llm_response = (
            "afternoon_crash_start: '14:30'\n"
            "time_estimation_factor: 1.2\n"
        )
        container.llm.chat = AsyncMock(return_value=llm_response)

        with patch(
            "kora_v2.agents.background.memory_steward_handlers.get_autonomous_context"
        ) as mock_ctx:
            mock_ctx.return_value = MagicMock(container=container, db_path=db_path)
            from kora_v2.agents.background.memory_steward_handlers import (
                adhd_profile_refine_step,
            )

            task = _make_task(stage_name="refine")
            ctx = _make_step_context(task)
            result = await adhd_profile_refine_step(task, ctx)

        assert result.outcome == "complete"

        # Check for conflict report in Inbox
        inbox = memory_path / "Inbox"
        conflict_files = list(inbox.glob("adhd-profile-conflicts-*.md"))
        assert len(conflict_files) >= 1, "Conflict report should be written"

        # Read conflict report content
        report = conflict_files[0].read_text(encoding="utf-8")
        assert "Conflict Report" in report
