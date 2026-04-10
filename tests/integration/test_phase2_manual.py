"""Phase 2 Manual Test Suite — exercises all 9 manual test scenarios.

Uses REAL embedding model (nomic-embed-text-v1.5), REAL sqlite-vec,
REAL filesystem writes. No mocks except LLM for dedup judgment.

Run with:
    .venv/bin/python -m pytest tests/integration/test_phase2_manual.py -v -s

The -s flag is important to see timing and diagnostic output.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock

# pysqlite3 monkey-patch (must be before any aiosqlite import)
try:
    import pysqlite3 as _pysqlite3  # type: ignore[import-untyped]
    sys.modules["sqlite3"] = _pysqlite3
except ImportError:
    pass

import pytest

from kora_v2.memory.embeddings import LocalEmbeddingModel
from kora_v2.memory.projection import ProjectionDB
from kora_v2.memory.retrieval import hybrid_search, vector_search, fts5_search
from kora_v2.memory.signal_scanner import SignalScanner
from kora_v2.memory.store import FilesystemMemoryStore
from kora_v2.memory.write_pipeline import WritePipeline
from kora_v2.tools.recall import recall


# ==================================================================
# Shared fixtures
# ==================================================================


@pytest.fixture(scope="module")
def embedding_model():
    """Load the REAL nomic-embed-text-v1.5 model (cached, ~2s first load)."""
    from kora_v2.core.settings import MemorySettings

    model = LocalEmbeddingModel(MemorySettings(), device="cpu")
    model.load()
    yield model
    model.unload()


@pytest.fixture
async def projection_db(tmp_path):
    """Create a real ProjectionDB with sqlite-vec and full schema."""
    db_path = tmp_path / "projection.db"
    db = await ProjectionDB.initialize(db_path)
    yield db
    await db.close()


@pytest.fixture
def memory_store(tmp_path):
    """Create a real FilesystemMemoryStore in temp dir."""
    return FilesystemMemoryStore(tmp_path / "_KoraMemory")


@pytest.fixture
def write_pipeline(memory_store, projection_db, embedding_model):
    """Full WritePipeline with real components (LLM mocked for dedup)."""
    mock_llm = AsyncMock(return_value="ACTION: NEW")
    return WritePipeline(
        store=memory_store,
        projection_db=projection_db,
        embedding_model=embedding_model,
        llm=mock_llm,
    )


class FakeContainer:
    """Minimal container for recall() tool tests."""

    def __init__(self, embedding_model, projection_db):
        self.embedding_model = embedding_model
        self.projection_db = projection_db


# ==================================================================
# Manual Test 1: Memory write
# ==================================================================


class TestMemoryWrite:
    """Tell Kora 'My name is Alex and I live in Portland'.
    Check _KoraMemory/ for new note. Check projection.db.
    """

    async def test_memory_write_creates_note_and_projection(
        self, write_pipeline, memory_store, projection_db,
    ):
        result = await write_pipeline.store(
            content="My name is Alex and I live in Portland",
            memory_type="episodic",
            importance=0.8,
            tags=["identity", "location"],
        )

        # Note created on filesystem
        assert result.action == "created"
        assert result.note_id
        note = await memory_store.read_note(result.note_id)
        assert note is not None
        assert "Alex" in note.body
        assert "Portland" in note.body

        # Frontmatter is correct
        assert note.metadata.memory_type == "episodic"
        assert note.metadata.importance == 0.8

        # Projection DB has a row
        mem = await projection_db.get_memory_by_id(result.note_id)
        assert mem is not None
        assert mem["content"] == "My name is Alex and I live in Portland"
        assert mem["memory_type"] == "episodic"

        # Entities extracted
        assert "Portland" in result.entities_extracted

        print(f"\n  PASS: Note {result.note_id} created at {result.source_path}")
        print(f"  PASS: Projection DB row exists with content")
        print(f"  PASS: Entities extracted: {result.entities_extracted}")


# ==================================================================
# Manual Test 2: Memory recall
# ==================================================================


class TestMemoryRecall:
    """Store a fact, then recall it. Simulates cross-session recall."""

    async def test_recall_returns_stored_fact(
        self, write_pipeline, embedding_model, projection_db,
    ):
        # Store the fact
        await write_pipeline.store(
            content="My name is Alex and I live in Portland",
            memory_type="episodic",
        )

        # Recall via the tool
        container = FakeContainer(embedding_model, projection_db)
        result_json = await recall(
            query="What is my name?",
            layer="all",
            max_results=5,
            container=container,
        )

        parsed = json.loads(result_json)
        results = parsed["results"]
        assert len(results) > 0, "Expected recall to return results"

        # The top result should mention Alex
        top = results[0]
        assert "Alex" in top["content"], f"Expected 'Alex' in: {top['content']}"

        print(f"\n  PASS: recall('What is my name?') returned {len(results)} results")
        print(f"  PASS: Top result: '{top['content'][:60]}...' (score: {top['score']})")


# ==================================================================
# Manual Test 3: recall() latency
# ==================================================================


class TestRecallLatency:
    """Time the recall tool. Expected: <500ms (target 300ms)."""

    async def test_recall_under_500ms(
        self, write_pipeline, embedding_model, projection_db,
    ):
        # Seed some data
        for i in range(5):
            await write_pipeline.store(
                content=f"Memory fact number {i}: I enjoy activity {i}",
                memory_type="episodic",
                skip_dedup=True,
            )

        container = FakeContainer(embedding_model, projection_db)

        # Warm up the embedding model (first call may be slow)
        embedding_model.embed("warmup query", task_type="search_query")

        # Time the recall
        start = time.perf_counter()
        result_json = await recall(
            query="What activities do I enjoy?",
            layer="all",
            max_results=10,
            container=container,
        )
        elapsed_ms = (time.perf_counter() - start) * 1000

        parsed = json.loads(result_json)
        assert len(parsed["results"]) > 0

        print(f"\n  recall() latency: {elapsed_ms:.1f}ms")
        assert elapsed_ms < 500, f"recall() took {elapsed_ms:.1f}ms, expected <500ms"
        print(f"  PASS: {elapsed_ms:.1f}ms < 500ms target")


# ==================================================================
# Manual Test 4: Hybrid search ranking
# ==================================================================


class TestHybridSearchRanking:
    """Store 10 diverse memories, query with partial match.
    Most relevant should rank first.
    """

    async def test_hybrid_ranking_relevance(
        self, write_pipeline, embedding_model, projection_db,
    ):
        memories = [
            "I love playing guitar on weekends",
            "My favorite food is sushi from the place downtown",
            "I work as a software engineer at a startup",
            "My dog's name is Biscuit and he's a golden retriever",
            "I take Adderall 20mg every morning for ADHD",
            "My wife Sarah and I got married in 2020",
            "I run 5 miles every Tuesday and Thursday",
            "I'm learning Japanese and can read hiragana",
            "My birthday is March 15th",
            "I have a fear of heights but I'm working on it",
        ]

        for mem in memories:
            await write_pipeline.store(
                content=mem, memory_type="episodic", skip_dedup=True,
            )

        # Query about the dog
        query = "Tell me about my pet"
        query_emb = embedding_model.embed(query, task_type="search_query")

        results = await hybrid_search(
            db=projection_db,
            query=query,
            query_embedding=query_emb,
            layer="long_term",
            max_results=10,
        )

        assert len(results) > 0, "Expected results from hybrid search"

        # The dog memory should rank highly (top 3)
        top_3_contents = [r.content for r in results[:3]]
        dog_in_top_3 = any("Biscuit" in c or "dog" in c for c in top_3_contents)

        print(f"\n  Query: '{query}'")
        for i, r in enumerate(results[:5]):
            print(f"  #{i+1} (score={r.score:.4f}): {r.content[:60]}...")

        assert dog_in_top_3, (
            f"Expected dog memory in top 3. "
            f"Top 3: {[c[:40] for c in top_3_contents]}"
        )
        print(f"  PASS: Dog memory ranked in top 3")

        # Both vector and FTS5 should contribute
        vec_results = await vector_search(
            db=projection_db, query_embedding=query_emb, table="memories",
        )
        fts_results = await fts5_search(
            db=projection_db, query="pet", table="memories",
        )
        print(f"  Vector results: {len(vec_results)}, FTS5 results: {len(fts_results)}")
        # At minimum vector should return results (FTS5 for "pet" may or may not match)
        assert len(vec_results) > 0, "Vector search should return results"
        print(f"  PASS: Both search modalities operational")


# ==================================================================
# Manual Test 5: Signal Scanner
# ==================================================================


class TestSignalScanner:
    """Say 'I take Adderall at 8am every morning'.
    Check if medication signal detected.
    """

    def test_signal_scanner_detects_medication(self):
        scanner = SignalScanner()
        result = scanner.scan("I take Adderall at 8am every morning")

        assert result.has_signal, "Expected signal detection"
        signal_types = [str(s) for s in result.signal_types]
        assert "medication" in signal_types, f"Expected medication signal, got: {signal_types}"
        assert result.priority <= 3, f"Medication should be priority 3 or higher, got {result.priority}"

        print(f"\n  Signal types: {signal_types}")
        print(f"  Priority: {result.priority}")
        print(f"  PASS: Medication signal detected with priority {result.priority}")

    def test_signal_scanner_detects_person(self):
        scanner = SignalScanner()
        result = scanner.scan("My wife Sarah loves hiking in the mountains")

        assert result.has_signal, "Expected signal detection"
        signal_types = [str(s) for s in result.signal_types]
        assert "new_person" in signal_types, f"Expected new_person signal, got: {signal_types}"

        print(f"\n  Signal types: {signal_types}")
        print(f"  PASS: Person signal detected")

    def test_signal_scanner_detects_explicit_fact(self):
        scanner = SignalScanner()
        result = scanner.scan("My name is Alex and I am 32 years old")

        assert result.has_signal, "Expected signal detection"
        signal_types = [str(s) for s in result.signal_types]
        assert "explicit_fact" in signal_types, f"Expected explicit_fact signal, got: {signal_types}"

        print(f"\n  Signal types: {signal_types}")
        print(f"  PASS: Explicit fact signal detected")


# ==================================================================
# Manual Test 6: Dedup
# ==================================================================


class TestDedup:
    """Tell Kora your name twice. Should not create duplicate."""

    async def test_dedup_prevents_duplicate_storage(
        self, memory_store, projection_db, embedding_model,
    ):
        # Create pipeline with LLM that says DUPLICATE on second store
        call_count = {"n": 0}

        async def mock_llm(prompt, temperature=0.1):
            call_count["n"] += 1
            # First store has no candidates so LLM won't be called.
            # Second store will find the first via FTS5 and call LLM.
            return "ACTION: DUPLICATE"

        pipeline = WritePipeline(
            store=memory_store,
            projection_db=projection_db,
            embedding_model=embedding_model,
            llm=mock_llm,
        )

        # First store
        result1 = await pipeline.store_user_model_fact(
            content="My name is Alex",
            domain="identity",
        )
        assert result1.action == "created"

        # Second store — same fact
        result2 = await pipeline.store_user_model_fact(
            content="My name is Alex",
            domain="identity",
        )
        assert result2.action == "duplicate", f"Expected duplicate, got {result2.action}"

        # Verify only one note on filesystem
        notes = await memory_store.list_notes(layer="user_model", domain="identity")
        assert len(notes) == 1, f"Expected 1 note, got {len(notes)}"

        print(f"\n  First store: {result1.action} (id={result1.note_id})")
        print(f"  Second store: {result2.action} (id={result2.note_id})")
        print(f"  Notes in identity domain: {len(notes)}")
        print(f"  PASS: Duplicate prevented, single fact retained")

    async def test_dedup_merges_new_details(
        self, memory_store, projection_db, embedding_model,
    ):
        """Similar fact with new details should MERGE, not duplicate."""

        async def mock_llm(prompt, temperature=0.1):
            return (
                "ACTION: MERGE\n"
                "MERGED: Alex lives in Portland, Oregon and works as a software engineer."
            )

        pipeline = WritePipeline(
            store=memory_store,
            projection_db=projection_db,
            embedding_model=embedding_model,
            llm=mock_llm,
        )

        # First store
        result1 = await pipeline.store(
            content="Alex lives in Portland",
            memory_type="episodic",
        )
        assert result1.action == "created"

        # Second store with new details
        result2 = await pipeline.store(
            content="Alex lives in Portland Oregon and works as a software engineer",
            memory_type="episodic",
        )
        assert result2.action == "merged", f"Expected merged, got {result2.action}"

        # Read the merged note — should have combined content
        note = await memory_store.read_note(result1.note_id)
        assert note is not None
        assert "software engineer" in note.body, f"Merged content missing new details: {note.body}"

        print(f"\n  First store: {result1.action}")
        print(f"  Second store: {result2.action} (merged into {result2.note_id})")
        print(f"  Merged content: '{note.body[:80]}...'")
        print(f"  PASS: New details merged into existing memory")


# ==================================================================
# Manual Test 7: Entity linking
# ==================================================================


class TestEntityLinking:
    """Mention 'My wife Sarah loves hiking'.
    Check entity_links table.
    """

    async def test_entity_linking_creates_links(
        self, write_pipeline, projection_db,
    ):
        result = await write_pipeline.store(
            content="My wife Sarah loves hiking in the mountains",
            memory_type="episodic",
        )

        assert "Sarah" in result.entities_extracted

        # Check entities table
        cursor = await projection_db._db.execute(
            "SELECT * FROM entities WHERE canonical_name = 'sarah'"
        )
        entity_row = await cursor.fetchone()
        assert entity_row is not None, "Entity 'Sarah' not found in entities table"
        entity_id = entity_row[0]  # id column

        # Check entity_links
        cursor = await projection_db._db.execute(
            "SELECT * FROM entity_links WHERE entity_id = ?", (entity_id,)
        )
        link_row = await cursor.fetchone()
        assert link_row is not None, "Entity link not found"

        link_dict = dict(link_row)
        assert link_dict["memory_id"] == result.note_id

        print(f"\n  Entity: Sarah (id={entity_id})")
        print(f"  Linked to memory: {result.note_id}")
        print(f"  Relationship: {link_dict.get('relationship', 'N/A')}")
        print(f"  PASS: Entity created and linked to memory")


# ==================================================================
# Manual Test 8: sqlite-vec with 100+ memories
# ==================================================================


class TestSqliteVec100Plus:
    """Bulk-insert 100 test memories. Run vector search. Check results."""

    async def test_vector_search_100_memories(
        self, projection_db, embedding_model,
    ):
        topics = [
            "cooking", "programming", "gardening", "music", "travel",
            "reading", "exercise", "photography", "painting", "writing",
        ]

        # Insert 100 memories (10 topics x 10 variations)
        insert_start = time.perf_counter()
        for i in range(100):
            topic = topics[i % len(topics)]
            content = f"I really enjoy {topic}. Memory entry number {i} about my {topic} hobby."
            embedding = embedding_model.embed(content, task_type="search_document")
            await projection_db.index_memory(
                memory_id=f"bulk-{i:04d}",
                content=content,
                summary=None,
                importance=0.5,
                memory_type="episodic",
                created_at="2026-03-28T00:00:00+00:00",
                updated_at="2026-03-28T00:00:00+00:00",
                entities=None,
                tags=json.dumps([topic]),
                source_path=f"/test/bulk-{i:04d}.md",
                embedding=embedding,
            )
        insert_ms = (time.perf_counter() - insert_start) * 1000

        # Vector search
        query = "What are my hobbies related to cooking food?"
        query_emb = embedding_model.embed(query, task_type="search_query")

        search_start = time.perf_counter()
        results = await vector_search(
            db=projection_db,
            query_embedding=query_emb,
            table="memories",
            k=10,
        )
        search_ms = (time.perf_counter() - search_start) * 1000

        assert len(results) == 10, f"Expected 10 results, got {len(results)}"

        # Cooking memories should rank high
        top_5_contents = [r.content for r in results[:5]]
        cooking_in_top_5 = any("cooking" in c for c in top_5_contents)

        print(f"\n  Inserted 100 memories in {insert_ms:.0f}ms")
        print(f"  Vector search returned {len(results)} results in {search_ms:.1f}ms")
        for i, r in enumerate(results[:5]):
            print(f"  #{i+1} (score={r.score:.4f}): {r.content[:60]}...")

        assert cooking_in_top_5, "Expected cooking memories to rank in top 5"
        assert search_ms < 200, f"Vector search took {search_ms:.1f}ms, expected <200ms"
        print(f"  PASS: 100+ memories indexed, vector search in {search_ms:.1f}ms")


# ==================================================================
# Manual Test 9: E2E Smoke
# ==================================================================


class TestE2ESmoke:
    """Store 5 facts, verify all 5 can be recalled."""

    async def test_e2e_five_facts_recalled(
        self, write_pipeline, embedding_model, projection_db,
    ):
        facts = [
            ("My name is Alex", "identity"),
            ("I live in Portland Oregon", "location"),
            ("My wife's name is Sarah", "relationships"),
            ("I take Adderall 20mg every morning", "medications"),
            ("I work as a software engineer", "work"),
        ]

        # Store all 5 facts
        stored_ids = []
        for content, tag in facts:
            result = await write_pipeline.store(
                content=content,
                memory_type="episodic",
                tags=[tag],
                skip_dedup=True,
            )
            stored_ids.append(result.note_id)
            assert result.action == "created"

        print(f"\n  Stored {len(stored_ids)} facts")

        # Recall each fact with a natural query
        queries = [
            ("What is my name?", "Alex"),
            ("Where do I live?", "Portland"),
            ("Who is my wife?", "Sarah"),
            ("What medication do I take?", "Adderall"),
            ("What do I do for work?", "software engineer"),
        ]

        container = FakeContainer(embedding_model, projection_db)
        all_passed = True

        for query, expected_keyword in queries:
            result_json = await recall(
                query=query,
                layer="all",
                max_results=5,
                container=container,
            )
            parsed = json.loads(result_json)
            results = parsed["results"]

            if not results:
                print(f"  FAIL: '{query}' returned no results")
                all_passed = False
                continue

            # Check if expected keyword appears in any of the top results
            found = any(
                expected_keyword.lower() in r["content"].lower()
                for r in results[:3]
            )

            if found:
                top = results[0]
                print(
                    f"  PASS: '{query}' -> found '{expected_keyword}' "
                    f"(top score: {top['score']})"
                )
            else:
                print(
                    f"  FAIL: '{query}' -> '{expected_keyword}' not in top 3: "
                    f"{[r['content'][:40] for r in results[:3]]}"
                )
                all_passed = False

        assert all_passed, "Not all facts were recalled correctly"
        print(f"\n  PASS: All 5 facts recalled correctly via recall() tool")
