"""Memory write pipeline — single path from content to indexed storage.

Pipeline:
    content → dedup check → write markdown note (FilesystemMemoryStore)
    → index into ProjectionDB → compute embedding → insert into sqlite-vec
    → update FTS5 (auto via triggers) → extract entities → link entities

All memory writes flow through this pipeline to maintain consistency
between filesystem (canonical) and projection DB (derived).
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

import structlog
from pydantic import BaseModel

from kora_v2.core.events import EventEmitter, EventType
from kora_v2.memory.dedup import DedupAction, dedup_check
from kora_v2.memory.embeddings import LocalEmbeddingModel
from kora_v2.memory.projection import ProjectionDB
from kora_v2.memory.store import FilesystemMemoryStore

log = structlog.get_logger(__name__)


# =====================================================================
# Result model
# =====================================================================


class WriteResult(BaseModel):
    """Result of a write pipeline operation."""

    note_id: str
    action: str  # "created", "merged", "duplicate"
    source_path: str = ""
    entities_extracted: list[str] = []
    message: str = ""


# =====================================================================
# Entity extraction (regex-based, no LLM)
# =====================================================================


# Patterns for simple entity extraction from content
_PERSON_PATTERNS = [
    re.compile(
        r"\b(?:my\s+)?(wife|husband|partner|mom|dad|mother|father|brother|sister"
        r"|friend|boss|son|daughter|child|uncle|aunt|cousin|grandma|grandpa"
        r"|girlfriend|boyfriend|coworker|colleague|neighbor)\s+([A-Z][a-z]+)",
    ),
    re.compile(r"\b([A-Z][a-z]+)\s+(?:is|was)\s+my\s+\w+"),
]

_LOCATION_PATTERN = re.compile(
    r"\b(?:I\s+(?:live|lived|moved)\s+(?:in|to)|I'm\s+from|based\s+in)\s+"
    r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)",
)

_MEDICATION_PATTERN = re.compile(
    r"\b(adderall|ritalin|vyvanse|concerta|strattera|lexapro|zoloft"
    r"|wellbutrin|prozac|sertraline|methylphenidate)\b",
    re.IGNORECASE,
)


def _extract_entities(content: str) -> list[tuple[str, str]]:
    """Extract (entity_name, entity_type) pairs from content.

    Uses regex patterns — no LLM call. Returns a list of unique
    entities found in the text.
    """
    entities: dict[str, str] = {}  # name -> type (dedup by name)

    # People
    for pattern in _PERSON_PATTERNS:
        for match in pattern.finditer(content):
            name = match.group(2) if match.lastindex and match.lastindex >= 2 else match.group(1)
            if name and name[0].isupper() and len(name) > 1:
                entities[name] = "person"

    # Locations
    for match in _LOCATION_PATTERN.finditer(content):
        loc = match.group(1).strip()
        if loc:
            entities[loc] = "place"

    # Medications
    for match in _MEDICATION_PATTERN.finditer(content):
        med = match.group(1).strip()
        entities[med.capitalize()] = "medication"

    return list(entities.items())


# =====================================================================
# Write Pipeline
# =====================================================================


class WritePipeline:
    """Orchestrates memory writes: dedup → store → index → embed → link entities.

    Usage::

        pipeline = WritePipeline(store, projection_db, embedding_model)
        result = await pipeline.store(content="My wife Sarah loves hiking")
        result = await pipeline.store_user_model_fact(
            content="User's name is Alex", domain="identity",
        )
    """

    def __init__(
        self,
        store: FilesystemMemoryStore,
        projection_db: ProjectionDB,
        embedding_model: LocalEmbeddingModel,
        llm: Any = None,
        event_emitter: EventEmitter | None = None,
    ) -> None:
        self._store = store
        self._db = projection_db
        self._embed = embedding_model
        self._llm = llm
        self._emitter = event_emitter

    async def store(
        self,
        content: str,
        memory_type: str = "episodic",
        importance: float = 0.5,
        tags: list[str] | None = None,
        skip_dedup: bool = False,
    ) -> WriteResult:
        """Store a long-term memory through the full pipeline.

        Steps:
            1. Dedup check (unless skip_dedup)
            2. Write/update filesystem note
            3. Compute embedding
            4. Index in projection DB
            5. Extract and link entities

        Args:
            content: Memory content to store.
            memory_type: ``episodic``, ``reflective``, or ``procedural``.
            importance: Score 0.0-1.0.
            tags: Optional freeform tags.
            skip_dedup: Skip dedup check (e.g. for bulk import).

        Returns:
            WriteResult with the note ID and action taken.
        """
        # 1. Dedup check
        if not skip_dedup and self._llm is not None:
            dedup_result = await dedup_check(
                content,
                self._db._db,
                self._llm,
                layer="long_term",
                table="memories_fts",
            )

            if dedup_result.action == DedupAction.DUPLICATE:
                log.info("write_pipeline_duplicate", existing_id=dedup_result.existing_id)
                # Increment evidence — read existing, update evidence count
                if dedup_result.existing_id:
                    existing = await self._db.get_memory_by_id(dedup_result.existing_id)
                    if existing:
                        return WriteResult(
                            note_id=dedup_result.existing_id,
                            action="duplicate",
                            source_path=existing.get("source_path", ""),
                            message="Duplicate detected. Evidence noted.",
                        )
                return WriteResult(
                    note_id=dedup_result.existing_id or "",
                    action="duplicate",
                    message="Duplicate detected.",
                )

            if dedup_result.action == DedupAction.MERGE and dedup_result.existing_id:
                return await self._merge_memory(
                    existing_id=dedup_result.existing_id,
                    merged_content=dedup_result.merged_content or content,
                )

        # 2. Extract entities from content
        entity_pairs = _extract_entities(content)
        entity_names = [name for name, _ in entity_pairs]

        # 3. Write filesystem note
        note_meta = await self._store.write_note(
            content=content,
            memory_type=memory_type,
            entities=entity_names,
            tags=tags,
            importance=importance,
        )

        # 4. Compute embedding
        embedding = self._embed.embed(content, task_type="search_document")

        # 5. Index in projection DB
        await self._db.index_memory(
            memory_id=note_meta.id,
            content=content,
            summary=None,
            importance=importance,
            memory_type=memory_type,
            created_at=note_meta.created_at,
            updated_at=note_meta.updated_at,
            entities=json.dumps(entity_names) if entity_names else None,
            tags=json.dumps(tags) if tags else None,
            source_path=note_meta.source_path,
            embedding=embedding,
        )

        # 6. Link entities
        for name, entity_type in entity_pairs:
            entity_id = await self._db.find_or_create_entity(name, entity_type)
            await self._db.link_entity(
                entity_id=entity_id,
                memory_id=note_meta.id,
                fact_id=None,
                relationship="mentioned_in",
            )

        log.info(
            "write_pipeline_stored",
            note_id=note_meta.id,
            memory_type=memory_type,
            entities=entity_names,
        )

        if self._emitter is not None:
            await self._emitter.emit(
                EventType.MEMORY_STORED,
                note_id=note_meta.id,
                memory_type=memory_type,
                layer="long_term",
                importance=importance,
                entities=entity_names,
                source_path=note_meta.source_path,
            )

        return WriteResult(
            note_id=note_meta.id,
            action="created",
            source_path=note_meta.source_path,
            entities_extracted=entity_names,
            message=f"Memory stored as {memory_type}.",
        )

    async def store_user_model_fact(
        self,
        content: str,
        domain: str,
        importance: float = 0.5,
        skip_dedup: bool = False,
    ) -> WriteResult:
        """Store a User Model fact through the pipeline.

        Args:
            content: Fact content.
            domain: User Model domain (e.g. ``identity``, ``preferences``).
            importance: Score 0.0-1.0 (used as initial confidence).
            skip_dedup: Skip dedup check.

        Returns:
            WriteResult with the note ID and action taken.
        """
        # 1. Dedup check
        if not skip_dedup and self._llm is not None:
            dedup_result = await dedup_check(
                content,
                self._db._db,
                self._llm,
                layer="user_model",
                table="user_model_fts",
            )

            if dedup_result.action == DedupAction.DUPLICATE:
                log.info("write_pipeline_fact_duplicate", existing_id=dedup_result.existing_id)
                # Increment evidence count on existing fact
                if dedup_result.existing_id:
                    existing = await self._db.get_fact_by_id(dedup_result.existing_id)
                    if existing:
                        new_evidence = existing.get("evidence_count", 1) + 1
                        new_confidence = new_evidence / (
                            new_evidence + existing.get("contradiction_count", 0) + 2
                        )
                        embedding = self._embed.embed(
                            existing.get("content", content),
                            task_type="search_document",
                        )
                        now = datetime.now(UTC).isoformat(timespec="seconds")
                        await self._db.update_user_model_fact(
                            fact_id=dedup_result.existing_id,
                            content=existing.get("content", content),
                            confidence=new_confidence,
                            evidence_count=new_evidence,
                            updated_at=now,
                            embedding=embedding,
                        )
                        return WriteResult(
                            note_id=dedup_result.existing_id,
                            action="duplicate",
                            source_path=existing.get("source_path", ""),
                            message=f"Duplicate fact. Evidence count → {new_evidence}.",
                        )
                return WriteResult(
                    note_id=dedup_result.existing_id or "",
                    action="duplicate",
                    message="Duplicate fact detected.",
                )

            if dedup_result.action == DedupAction.MERGE and dedup_result.existing_id:
                return await self._merge_fact(
                    existing_id=dedup_result.existing_id,
                    merged_content=dedup_result.merged_content or content,
                    domain=domain,
                )

        # 2. Extract entities
        entity_pairs = _extract_entities(content)
        entity_names = [name for name, _ in entity_pairs]

        # 3. Write filesystem note
        note_meta = await self._store.write_note(
            content=content,
            memory_type="user_model",
            domain=domain,
            entities=entity_names,
            importance=importance,
        )

        # 4. Compute embedding
        embedding = self._embed.embed(content, task_type="search_document")

        # 5. Index in projection DB
        confidence = 1 / (1 + 0 + 2)  # initial: evidence=1, contradictions=0
        await self._db.index_user_model_fact(
            fact_id=note_meta.id,
            domain=domain,
            content=content,
            confidence=confidence,
            evidence_count=1,
            contradiction_count=0,
            created_at=note_meta.created_at,
            updated_at=note_meta.updated_at,
            source_path=note_meta.source_path,
            embedding=embedding,
        )

        # 6. Link entities
        for name, entity_type in entity_pairs:
            entity_id = await self._db.find_or_create_entity(name, entity_type)
            await self._db.link_entity(
                entity_id=entity_id,
                memory_id=None,
                fact_id=note_meta.id,
                relationship="mentioned_in",
            )

        log.info(
            "write_pipeline_fact_stored",
            note_id=note_meta.id,
            domain=domain,
            entities=entity_names,
        )

        if self._emitter is not None:
            await self._emitter.emit(
                EventType.MEMORY_STORED,
                note_id=note_meta.id,
                memory_type="user_model",
                layer="user_model",
                domain=domain,
                importance=importance,
                entities=entity_names,
                source_path=note_meta.source_path,
            )

        return WriteResult(
            note_id=note_meta.id,
            action="created",
            source_path=note_meta.source_path,
            entities_extracted=entity_names,
            message=f"Fact stored in {domain}.",
        )

    # ── Merge helpers ─────────────────────────────────────────────

    async def _merge_memory(
        self,
        existing_id: str,
        merged_content: str,
    ) -> WriteResult:
        """Merge new details into an existing memory note."""
        # Update filesystem note
        updated_meta = await self._store.update_note(existing_id, merged_content)

        # Re-embed and re-index
        embedding = self._embed.embed(merged_content, task_type="search_document")
        now = datetime.now(UTC).isoformat(timespec="seconds")

        await self._db.update_memory_content(
            memory_id=existing_id,
            content=merged_content,
            summary=None,
            updated_at=now,
            embedding=embedding,
        )

        source_path = updated_meta.source_path if updated_meta else ""

        # Re-extract and link new entities
        entity_pairs = _extract_entities(merged_content)
        entity_names = [name for name, _ in entity_pairs]
        for name, entity_type in entity_pairs:
            entity_id = await self._db.find_or_create_entity(name, entity_type)
            await self._db.link_entity(
                entity_id=entity_id,
                memory_id=existing_id,
                fact_id=None,
                relationship="mentioned_in",
            )

        log.info("write_pipeline_merged", existing_id=existing_id, entities=entity_names)

        return WriteResult(
            note_id=existing_id,
            action="merged",
            source_path=source_path,
            entities_extracted=entity_names,
            message="Merged new details into existing memory.",
        )

    async def _merge_fact(
        self,
        existing_id: str,
        merged_content: str,
        domain: str,
    ) -> WriteResult:
        """Merge new details into an existing User Model fact."""
        existing = await self._db.get_fact_by_id(existing_id)

        # Update filesystem note
        updated_meta = await self._store.update_note(existing_id, merged_content)

        # Bump evidence, recalc confidence
        evidence = (existing.get("evidence_count", 1) + 1) if existing else 2
        contradictions = existing.get("contradiction_count", 0) if existing else 0
        confidence = evidence / (evidence + contradictions + 2)

        # Re-embed and re-index
        embedding = self._embed.embed(merged_content, task_type="search_document")
        now = datetime.now(UTC).isoformat(timespec="seconds")

        await self._db.update_user_model_fact(
            fact_id=existing_id,
            content=merged_content,
            confidence=confidence,
            evidence_count=evidence,
            updated_at=now,
            embedding=embedding,
        )

        source_path = updated_meta.source_path if updated_meta else ""

        # Re-extract and link new entities
        entity_pairs = _extract_entities(merged_content)
        entity_names = [name for name, _ in entity_pairs]
        for name, entity_type in entity_pairs:
            entity_id = await self._db.find_or_create_entity(name, entity_type)
            await self._db.link_entity(
                entity_id=entity_id,
                memory_id=None,
                fact_id=existing_id,
                relationship="mentioned_in",
            )

        log.info(
            "write_pipeline_fact_merged",
            existing_id=existing_id,
            evidence=evidence,
            confidence=confidence,
        )

        return WriteResult(
            note_id=existing_id,
            action="merged",
            source_path=source_path,
            entities_extracted=entity_names,
            message=f"Merged new details into existing fact. Evidence → {evidence}.",
        )
