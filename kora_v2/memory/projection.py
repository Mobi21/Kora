"""Projection DB — derived SQLite database from filesystem memory notes.

Wraps aiosqlite with sqlite-vec extension loading. Maintains memories,
user_model_facts, FTS5 indexes, and vec0 virtual tables. All writes
go through this module to keep indexes consistent.
"""

# Note: pysqlite3 sys.modules swap is done in kora_v2/__init__.py so it runs
# before aiosqlite is imported anywhere else. Doing it here would be too late —
# aiosqlite.core has already bound `import sqlite3` to stdlib by the time this
# module loads via container.initialize_memory().

from __future__ import annotations

import json
import os
import re
import struct
import uuid
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
import structlog
from pydantic import BaseModel, Field

from kora_v2.core.migrations import MigrationRunner

log = structlog.get_logger(__name__)
_TOKEN_RE = re.compile(r"[a-z0-9]{4,}", re.IGNORECASE)

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_SEARCH_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


# ------------------------------------------------------------------
# Pydantic models for new ProjectionDB methods
# ------------------------------------------------------------------


class MemoryRecord(BaseModel):
    """A memory or user model fact record from the projection DB."""

    id: str
    content: str
    summary: str | None = None
    memory_type: str = "episodic"
    importance: float = 0.5
    source_path: str = ""
    source_table: str = "memories"
    created_at: str = ""
    updated_at: str = ""
    entities: str | None = None
    tags: str | None = None


class MergeCandidateGroup(BaseModel):
    """A group of semantically similar memories that may be consolidated."""

    records: list[MemoryRecord]
    avg_similarity: float = Field(
        default=0.0, description="Average pairwise cosine similarity"
    )


class DuplicatePair(BaseModel):
    """A pair of near-duplicate records."""

    record_a: MemoryRecord
    record_b: MemoryRecord
    similarity: float = Field(
        default=0.0, description="Combined similarity score"
    )


class EntityRecord(BaseModel):
    """An entity with its link counts and date range."""

    id: str
    name: str
    canonical_name: str
    entity_type: str
    active_link_count: int = 0
    first_mention: str | None = None
    last_mention: str | None = None


class EntityRelationship(BaseModel):
    """Two entities that co-occur in the same active memories."""

    entity_id: str
    entity_name: str
    co_occurrence_count: int = 0


class StaleEntry(BaseModel):
    """A projection DB entry whose filesystem note has been modified."""

    record_id: str
    source_path: str
    source_table: str
    db_updated_at: str
    fs_mtime: str


def serialize_float32(vec: list[float]) -> bytes:
    """Serialize a float vector to packed float32 bytes for sqlite-vec."""
    return struct.pack(f"{len(vec)}f", *vec)


class ProjectionDB:
    """Async wrapper around the projection SQLite database.

    Loads sqlite-vec, runs schema migrations, and exposes typed
    insert/update/delete/query methods for memories, user model facts,
    entities, and vector embeddings.

    Usage::

        db = await ProjectionDB.initialize(Path("data/projection.db"))
        rowid = await db.index_memory(...)
        await db.close()
    """

    def __init__(self, db: aiosqlite.Connection, *, vector_available: bool = True) -> None:
        self._db = db
        self._vector_available = vector_available

    @property
    def capabilities(self) -> dict[str, bool]:
        """Report available search capabilities."""
        return {"vector_search": self._vector_available, "fts5": True}

    async def search(self, query: str, limit: int = 5) -> list[MemoryRecord]:
        """Lightweight FTS5 search used by background proactive handlers.

        The full retrieval module owns vector/hybrid ranking, but several
        background handlers need a small provider-free lookup surface while
        running inside orchestration tasks. Return ``MemoryRecord`` values
        so callers can read ``.content`` and ``.created_at`` consistently.
        """
        terms = _SEARCH_TOKEN_RE.findall(query or "")
        if not terms:
            return []
        match = " OR ".join(terms[:8])
        rows: list[MemoryRecord] = []

        try:
            cur = await self._db.execute(
                """
                SELECT m.id, m.content, m.summary, m.importance,
                       m.memory_type, m.source_path, m.created_at,
                       m.updated_at, m.entities, m.tags
                FROM memories_fts f
                JOIN memories m ON m.rowid = f.rowid
                WHERE memories_fts MATCH ? AND m.status = 'active'
                LIMIT ?
                """,
                (match, limit),
            )
            for row in await cur.fetchall():
                rd = dict(row)
                rows.append(
                    MemoryRecord(
                        id=rd.get("id", ""),
                        content=rd.get("content", ""),
                        summary=rd.get("summary"),
                        importance=rd.get("importance", 0.5),
                        memory_type=rd.get("memory_type", "episodic"),
                        source_path=rd.get("source_path", ""),
                        source_table="memories",
                        created_at=rd.get("created_at", ""),
                        updated_at=rd.get("updated_at", ""),
                        entities=rd.get("entities"),
                        tags=rd.get("tags"),
                    )
                )
        except Exception:
            log.debug("projection_search_memories_failed", exc_info=True)

        remaining = max(0, limit - len(rows))
        if remaining:
            try:
                cur = await self._db.execute(
                    """
                    SELECT f.id, f.content, f.domain, f.confidence,
                           f.source_path, f.created_at, f.updated_at
                    FROM user_model_fts u
                    JOIN user_model_facts f ON f.rowid = u.rowid
                    WHERE user_model_fts MATCH ? AND f.status = 'active'
                    LIMIT ?
                    """,
                    (match, remaining),
                )
                for row in await cur.fetchall():
                    rd = dict(row)
                    rows.append(
                        MemoryRecord(
                            id=rd.get("id", ""),
                            content=rd.get("content", ""),
                            summary=rd.get("domain"),
                            importance=rd.get("confidence", 0.5),
                            memory_type="user_model",
                            source_path=rd.get("source_path", ""),
                            source_table="user_model_facts",
                            created_at=rd.get("created_at", ""),
                            updated_at=rd.get("updated_at", ""),
                        )
                    )
            except Exception:
                log.debug("projection_search_user_model_failed", exc_info=True)

        return rows[:limit]

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    async def initialize(cls, db_path: Path) -> ProjectionDB:
        """Open the database, load sqlite-vec, run migrations, return instance.

        Args:
            db_path: Path to the SQLite database file.  Parent directory
                is created if it does not exist.

        Returns:
            Ready-to-use ProjectionDB instance.
        """
        db_path.parent.mkdir(parents=True, exist_ok=True)

        db = await aiosqlite.connect(str(db_path), check_same_thread=False)
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("SELECT 1")  # ensure connection works
        db.row_factory = aiosqlite.Row

        # Load sqlite-vec extension (optional — falls back to FTS5-only)
        vector_available = False
        try:
            import sqlite_vec

            conn = db._connection  # underlying sqlite3 connection
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
            vector_available = True
            log.info("sqlite_vec_loaded")
        except Exception as exc:
            log.warning(
                "sqlite_vec_unavailable",
                error=str(exc),
                fallback="fts5_only",
            )

        # Run schema migrations. MigrationRunner now executes statements
        # one at a time and transparently skips ``USING vec0`` statements
        # when sqlite-vec is unavailable, so the migration still applies
        # (and is recorded in schema_version) on FTS5-only installs.
        runner = MigrationRunner()
        try:
            await runner.run_migrations(db, _MIGRATIONS_DIR)
        except Exception:
            await db.close()
            raise

        log.info(
            "projection_db_initialized",
            path=str(db_path),
            vector_search=vector_available,
        )
        return cls(db, vector_available=vector_available)

    # ------------------------------------------------------------------
    # Memory CRUD
    # ------------------------------------------------------------------

    async def index_memory(
        self,
        memory_id: str,
        content: str,
        summary: str | None,
        importance: float,
        memory_type: str,
        created_at: str,
        updated_at: str,
        entities: str | None,
        tags: str | None,
        source_path: str,
        embedding: list[float],
    ) -> int:
        """Insert a memory and its vector embedding.

        Returns:
            The rowid of the inserted memory.
        """
        try:
            cursor = await self._db.execute(
                "INSERT INTO memories "
                "(id, content, summary, importance, memory_type, "
                " created_at, updated_at, entities, tags, source_path) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "content = excluded.content, "
                "summary = excluded.summary, "
                "importance = excluded.importance, "
                "memory_type = excluded.memory_type, "
                "updated_at = excluded.updated_at, "
                "entities = excluded.entities, "
                "tags = excluded.tags, "
                "source_path = excluded.source_path",
                (
                    memory_id, content, summary, importance, memory_type,
                    created_at, updated_at, entities, tags, source_path,
                ),
            )
            cursor = await self._db.execute(
                "SELECT rowid FROM memories WHERE id = ?", (memory_id,)
            )
            row = await cursor.fetchone()
            rowid = row[0] if row is not None else None

            if self._vector_available and rowid is not None:
                await self._db.execute(
                    "DELETE FROM memories_vec WHERE rowid = ?", (rowid,)
                )
                await self._db.execute(
                    "INSERT INTO memories_vec (rowid, embedding) VALUES (?, ?)",
                    (rowid, serialize_float32(embedding)),
                )

            await self._db.commit()
        except Exception:
            await self._db.rollback()
            raise

        log.debug("memory_indexed", id=memory_id, rowid=rowid, vector=self._vector_available)
        return rowid  # type: ignore[return-value]

    async def index_user_model_fact(
        self,
        fact_id: str,
        domain: str,
        content: str,
        confidence: float,
        evidence_count: int,
        contradiction_count: int,
        created_at: str,
        updated_at: str,
        source_path: str,
        embedding: list[float],
    ) -> int:
        """Insert a user model fact and its vector embedding.

        Returns:
            The rowid of the inserted fact.
        """
        try:
            cursor = await self._db.execute(
                "INSERT INTO user_model_facts "
                "(id, domain, content, confidence, evidence_count, "
                " contradiction_count, created_at, updated_at, source_path) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "domain = excluded.domain, "
                "content = excluded.content, "
                "confidence = excluded.confidence, "
                "evidence_count = excluded.evidence_count, "
                "contradiction_count = excluded.contradiction_count, "
                "updated_at = excluded.updated_at, "
                "source_path = excluded.source_path",
                (
                    fact_id, domain, content, confidence, evidence_count,
                    contradiction_count, created_at, updated_at, source_path,
                ),
            )
            cursor = await self._db.execute(
                "SELECT rowid FROM user_model_facts WHERE id = ?", (fact_id,)
            )
            row = await cursor.fetchone()
            rowid = row[0] if row is not None else None

            if self._vector_available and rowid is not None:
                await self._db.execute(
                    "DELETE FROM user_model_vec WHERE rowid = ?", (rowid,)
                )
                await self._db.execute(
                    "INSERT INTO user_model_vec (rowid, embedding) VALUES (?, ?)",
                    (rowid, serialize_float32(embedding)),
                )

            await self._db.commit()
        except Exception:
            await self._db.rollback()
            raise

        log.debug("user_model_fact_indexed", id=fact_id, rowid=rowid, vector=self._vector_available)
        return rowid  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Updates (for dedup merge)
    # ------------------------------------------------------------------

    async def update_memory_content(
        self,
        memory_id: str,
        content: str,
        summary: str | None,
        updated_at: str,
        embedding: list[float],
    ) -> None:
        """Update memory content/summary and re-index its embedding."""
        # Get the rowid before updating
        cursor = await self._db.execute(
            "SELECT rowid FROM memories WHERE id = ?", (memory_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            log.warning("update_memory_not_found", id=memory_id)
            return

        rowid = row[0]

        await self._db.execute(
            "UPDATE memories SET content = ?, summary = ?, updated_at = ? "
            "WHERE id = ?",
            (content, summary, updated_at, memory_id),
        )

        if self._vector_available:
            # Re-index embedding: delete old, insert new
            await self._db.execute(
                "DELETE FROM memories_vec WHERE rowid = ?", (rowid,)
            )
            await self._db.execute(
                "INSERT INTO memories_vec (rowid, embedding) VALUES (?, ?)",
                (rowid, serialize_float32(embedding)),
            )

        await self._db.commit()

    async def update_user_model_fact(
        self,
        fact_id: str,
        content: str,
        confidence: float,
        evidence_count: int,
        updated_at: str,
        embedding: list[float],
    ) -> None:
        """Update a user model fact and re-index its embedding."""
        cursor = await self._db.execute(
            "SELECT rowid FROM user_model_facts WHERE id = ?", (fact_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            log.warning("update_fact_not_found", id=fact_id)
            return

        rowid = row[0]

        await self._db.execute(
            "UPDATE user_model_facts "
            "SET content = ?, confidence = ?, evidence_count = ?, "
            "    updated_at = ? "
            "WHERE id = ?",
            (content, confidence, evidence_count, updated_at, fact_id),
        )

        if self._vector_available:
            await self._db.execute(
                "DELETE FROM user_model_vec WHERE rowid = ?", (rowid,)
            )
            await self._db.execute(
                "INSERT INTO user_model_vec (rowid, embedding) VALUES (?, ?)",
                (rowid, serialize_float32(embedding)),
            )

        await self._db.commit()

    # ------------------------------------------------------------------
    # Deletes
    # ------------------------------------------------------------------

    async def delete_memory(self, memory_id: str) -> None:
        """Delete a memory and its vector embedding."""
        cursor = await self._db.execute(
            "SELECT rowid FROM memories WHERE id = ?", (memory_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return

        rowid = row[0]

        await self._db.execute(
            "DELETE FROM memories WHERE id = ?", (memory_id,)
        )
        if self._vector_available:
            await self._db.execute(
                "DELETE FROM memories_vec WHERE rowid = ?", (rowid,)
            )
        # Clean up entity links
        await self._db.execute(
            "DELETE FROM entity_links WHERE memory_id = ?", (memory_id,)
        )
        await self._db.commit()
        log.debug("memory_deleted", id=memory_id)

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def get_memory_by_id(
        self,
        memory_id: str,
        *,
        include_soft_deleted: bool = False,
    ) -> dict | None:
        """Fetch a single memory by its ID.

        Args:
            memory_id: The memory ID to look up.
            include_soft_deleted: If True, return the record even if
                its status is not ``active``.

        Returns:
            Dict with all memory columns, or None if not found.
        """
        if include_soft_deleted:
            cursor = await self._db.execute(
                "SELECT * FROM memories WHERE id = ?", (memory_id,)
            )
        else:
            cursor = await self._db.execute(
                "SELECT * FROM memories WHERE id = ? AND status = 'active'",
                (memory_id,),
            )
        row = await cursor.fetchone()
        if row is None:
            return None
        return dict(row)

    async def get_fact_by_id(
        self,
        fact_id: str,
        *,
        include_soft_deleted: bool = False,
    ) -> dict | None:
        """Fetch a single user model fact by its ID.

        Args:
            fact_id: The fact ID to look up.
            include_soft_deleted: If True, return the record even if
                its status is not ``active``.

        Returns:
            Dict with all fact columns, or None if not found.
        """
        if include_soft_deleted:
            cursor = await self._db.execute(
                "SELECT * FROM user_model_facts WHERE id = ?", (fact_id,)
            )
        else:
            cursor = await self._db.execute(
                "SELECT * FROM user_model_facts WHERE id = ? AND status = 'active'",
                (fact_id,),
            )
        row = await cursor.fetchone()
        if row is None:
            return None
        return dict(row)

    # ------------------------------------------------------------------
    # Entities
    # ------------------------------------------------------------------

    async def find_or_create_entity(
        self,
        name: str,
        entity_type: str,
    ) -> str:
        """Find an entity by canonical name or create it.

        Args:
            name: Display name of the entity.
            entity_type: Type classification (person, place, etc.).

        Returns:
            The entity's ID (existing or newly created).
        """
        canonical = name.strip().lower()
        cursor = await self._db.execute(
            "SELECT id FROM entities WHERE canonical_name = ? AND entity_type = ?",
            (canonical, entity_type),
        )
        row = await cursor.fetchone()
        if row is not None:
            return row[0]

        entity_id = str(uuid.uuid4())
        await self._db.execute(
            "INSERT INTO entities (id, name, canonical_name, entity_type) "
            "VALUES (?, ?, ?, ?)",
            (entity_id, name, canonical, entity_type),
        )
        await self._db.commit()
        log.debug("entity_created", id=entity_id, name=name, type=entity_type)
        return entity_id

    async def link_entity(
        self,
        entity_id: str,
        memory_id: str | None,
        fact_id: str | None,
        relationship: str,
    ) -> None:
        """Create a link between an entity and a memory or fact.

        Args:
            entity_id: The entity to link.
            memory_id: Memory ID (mutually exclusive with fact_id, at
                least one must be provided).
            fact_id: User model fact ID.
            relationship: Description of the relationship.
        """
        await self._db.execute(
            "INSERT INTO entity_links "
            "(entity_id, memory_id, user_model_fact_id, relationship) "
            "VALUES (?, ?, ?, ?)",
            (entity_id, memory_id, fact_id, relationship),
        )
        await self._db.commit()

    # ------------------------------------------------------------------
    # Soft-delete
    # ------------------------------------------------------------------

    async def soft_delete(
        self,
        table: str,
        record_id: str,
        successor_id: str | None,
        reason: str,
    ) -> None:
        """Mark a projection DB row as soft-deleted.

        Sets ``status = 'soft_deleted'``, ``deleted_at`` to now, and
        ``consolidated_into`` to *successor_id* if provided. Runs in
        an atomic transaction.

        Args:
            table: Either ``memories`` or ``user_model_facts``.
            record_id: ID of the record to soft-delete.
            successor_id: Optional ID of the record that replaces this one.
            reason: Reason for soft-deletion (e.g. ``consolidated``, ``duplicate``).
        """
        if table not in ("memories", "user_model_facts"):
            raise ValueError(f"Invalid table for soft_delete: {table!r}")

        now = datetime.now(UTC).isoformat(timespec="seconds")

        # Determine the new status based on reason
        status = "merged" if reason in ("consolidated", "duplicate", "merged") else "soft_deleted"

        await self._db.execute(
            f"UPDATE {table} SET status = ?, consolidated_into = ?, "  # noqa: S608
            f"deleted_at = ? WHERE id = ?",
            (status, successor_id, now, record_id),
        )
        await self._db.commit()
        log.debug(
            "record_soft_deleted",
            table=table,
            id=record_id,
            successor=successor_id,
            reason=reason,
        )

    # ------------------------------------------------------------------
    # Consolidation & deduplication
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_memory_record(row: aiosqlite.Row, table: str) -> MemoryRecord:
        rd = dict(row)
        return MemoryRecord(
            id=rd.get("id", ""),
            content=rd.get("content", ""),
            summary=rd.get("summary"),
            memory_type=rd.get("memory_type", "semantic"),
            importance=rd.get("importance", rd.get("confidence", 0.5)),
            source_path=rd.get("source_path", ""),
            source_table=table,
            created_at=rd.get("created_at", ""),
            updated_at=rd.get("updated_at", ""),
            entities=rd.get("entities"),
            tags=rd.get("tags"),
        )

    @staticmethod
    def _text_similarity(a: str, b: str) -> float:
        a_tokens = set(_TOKEN_RE.findall(a.lower()))
        b_tokens = set(_TOKEN_RE.findall(b.lower()))
        if not a_tokens or not b_tokens:
            return 0.0
        return len(a_tokens & b_tokens) / len(a_tokens | b_tokens)

    async def _active_records_for_table(self, table: str) -> list[MemoryRecord]:
        cursor = await self._db.execute(
            f"SELECT * FROM {table} WHERE status = 'active'"  # noqa: S608
        )
        return [
            self._row_to_memory_record(row, table)
            for row in await cursor.fetchall()
        ]

    async def _fallback_consolidation_groups(
        self,
        threshold: float,
    ) -> list[MergeCandidateGroup]:
        groups: list[MergeCandidateGroup] = []
        seen: set[str] = set()
        for table in ("memories", "user_model_facts"):
            records = await self._active_records_for_table(table)
            for idx, rec in enumerate(records):
                if rec.id in seen:
                    continue
                related = [rec]
                for other in records[idx + 1:]:
                    if other.id in seen:
                        continue
                    similarity = self._text_similarity(rec.content, other.content)
                    # Exact duplicates are left for the stricter dedup stage.
                    if threshold <= similarity < 0.96:
                        related.append(other)
                if len(related) >= 2:
                    seen.update(r.id for r in related)
                    groups.append(
                        MergeCandidateGroup(
                            records=related,
                            avg_similarity=threshold,
                        )
                    )
        return groups

    async def _fallback_duplicate_pairs(
        self,
        threshold: float,
        excluded_pairs: set[tuple[str, str]],
    ) -> list[DuplicatePair]:
        pairs: list[DuplicatePair] = []
        for table in ("memories", "user_model_facts"):
            records = await self._active_records_for_table(table)
            for idx, rec in enumerate(records):
                for other in records[idx + 1:]:
                    pair_key = tuple(sorted([rec.id, other.id]))
                    if pair_key in excluded_pairs:
                        continue
                    similarity = self._text_similarity(rec.content, other.content)
                    if similarity >= threshold:
                        excluded_pairs.add(pair_key)
                        pairs.append(
                            DuplicatePair(
                                record_a=rec,
                                record_b=other,
                                similarity=similarity,
                            )
                        )
        return pairs

    @staticmethod
    def _append_unique_duplicate_pair(
        pairs: list[DuplicatePair],
        seen_pairs: set[tuple[str, str]],
        pair: DuplicatePair,
    ) -> None:
        pair_key = tuple(sorted([pair.record_a.id, pair.record_b.id]))
        if pair_key in seen_pairs:
            return
        seen_pairs.add(pair_key)
        pairs.append(pair)

    async def consolidate(
        self,
        threshold: float = 0.82,
    ) -> list[MergeCandidateGroup]:
        """Find semantically similar active memories using embedding cosine similarity.

        Queries both ``memories`` and ``user_model_facts`` tables, filtered
        to ``status = 'active'``. Returns groups of merge candidates whose
        pairwise cosine similarity exceeds *threshold*.

        Args:
            threshold: Minimum cosine similarity to consider records related.

        Returns:
            List of MergeCandidateGroup with records and avg similarity.
        """
        if not self._vector_available:
            log.warning("consolidate_skipped_no_vector")
            return await self._fallback_consolidation_groups(threshold)

        groups: list[MergeCandidateGroup] = []

        for table, vec_table in [
            ("memories", "memories_vec"),
            ("user_model_facts", "user_model_vec"),
        ]:
            # Get all active records with their embeddings
            cursor = await self._db.execute(
                f"SELECT m.id, m.rowid FROM {table} m "  # noqa: S608
                f"WHERE m.status = 'active'"
            )
            rows = await cursor.fetchall()
            if len(rows) < 2:
                continue

            # For each record, find similar records using vector search
            seen_pairs: set[tuple[str, str]] = set()
            pair_groups: dict[str, list[str]] = {}

            for row in rows:
                record_id = row[0]
                rowid = row[1]

                # Get this record's embedding
                emb_cursor = await self._db.execute(
                    f"SELECT embedding FROM {vec_table} WHERE rowid = ?",  # noqa: S608
                    (rowid,),
                )
                emb_row = await emb_cursor.fetchone()
                if emb_row is None:
                    continue

                # Search for similar vectors
                query_bytes = emb_row[0]
                sim_cursor = await self._db.execute(
                    f"SELECT v.rowid, v.distance FROM {vec_table} v "  # noqa: S608
                    f"WHERE v.embedding MATCH ? AND k = 10 "
                    f"ORDER BY v.distance",
                    (query_bytes,),
                )
                sim_rows = await sim_cursor.fetchall()

                for sim_row in sim_rows:
                    sim_rowid = sim_row[0]
                    distance = sim_row[1]
                    similarity = 1.0 - distance

                    if similarity < threshold:
                        continue
                    if sim_rowid == rowid:
                        continue

                    # Get the ID of the similar record
                    id_cursor = await self._db.execute(
                        f"SELECT id FROM {table} WHERE rowid = ? AND status = 'active'",  # noqa: S608
                        (sim_rowid,),
                    )
                    id_row = await id_cursor.fetchone()
                    if id_row is None:
                        continue

                    other_id = id_row[0]
                    pair_key = tuple(sorted([record_id, other_id]))
                    if pair_key in seen_pairs:
                        continue
                    seen_pairs.add(pair_key)

                    # Group connected records
                    group_key = record_id
                    for k, members in pair_groups.items():
                        if record_id in members or other_id in members:
                            group_key = k
                            break

                    if group_key not in pair_groups:
                        pair_groups[group_key] = [record_id]
                    if other_id not in pair_groups[group_key]:
                        pair_groups[group_key].append(other_id)
                    if record_id not in pair_groups[group_key]:
                        pair_groups[group_key].append(record_id)

            # Build MergeCandidateGroups from the grouped IDs
            for _key, member_ids in pair_groups.items():
                if len(member_ids) < 2:
                    continue

                records: list[MemoryRecord] = []
                for mid in member_ids:
                    rec_cursor = await self._db.execute(
                        f"SELECT * FROM {table} WHERE id = ? AND status = 'active'",  # noqa: S608
                        (mid,),
                    )
                    rec_row = await rec_cursor.fetchone()
                    if rec_row is None:
                        continue
                    rd = dict(rec_row)
                    records.append(
                        MemoryRecord(
                            id=rd.get("id", ""),
                            content=rd.get("content", ""),
                            summary=rd.get("summary"),
                            memory_type=rd.get("memory_type", "episodic"),
                            importance=rd.get("importance", rd.get("confidence", 0.5)),
                            source_path=rd.get("source_path", ""),
                            source_table=table,
                            created_at=rd.get("created_at", ""),
                            updated_at=rd.get("updated_at", ""),
                            entities=rd.get("entities"),
                            tags=rd.get("tags"),
                        )
                    )

                if len(records) >= 2:
                    groups.append(
                        MergeCandidateGroup(
                            records=records,
                            avg_similarity=threshold,
                        )
                    )

        if not groups:
            groups = await self._fallback_consolidation_groups(threshold)
        return groups

    async def deduplicate(
        self,
        threshold: float = 0.92,
        excluded_pairs: set[tuple[str, str]] | None = None,
    ) -> list[DuplicatePair]:
        """Find near-duplicate active notes using embedding similarity.

        Higher threshold than consolidation -- these are true duplicates.

        Args:
            threshold: Minimum cosine similarity to consider records duplicates.
            excluded_pairs: Set of ``(id_a, id_b)`` tuples (sorted) that have
                previously been rejected as distinct by the LLM. These pairs
                are skipped so they are not re-evaluated every session.

        Returns:
            List of DuplicatePair with similarity scores.
        """
        if not self._vector_available:
            log.warning("deduplicate_skipped_no_vector")
            return await self._fallback_duplicate_pairs(
                threshold,
                set(excluded_pairs or set()),
            )

        # Always include deterministic text matches first. Vector rows can lag
        # behind projection rows during acceptance/startup cleanup, and exact
        # duplicates should not be missed just because another vector pair was
        # found earlier in the same pass.
        seen_pairs: set[tuple[str, str]] = set(excluded_pairs or set())
        pairs = await self._fallback_duplicate_pairs(threshold, seen_pairs)

        for table, vec_table in [
            ("memories", "memories_vec"),
            ("user_model_facts", "user_model_vec"),
        ]:
            cursor = await self._db.execute(
                f"SELECT m.id, m.rowid FROM {table} m "  # noqa: S608
                f"WHERE m.status = 'active'"
            )
            rows = await cursor.fetchall()
            if len(rows) < 2:
                continue

            for row in rows:
                record_id = row[0]
                rowid = row[1]

                emb_cursor = await self._db.execute(
                    f"SELECT embedding FROM {vec_table} WHERE rowid = ?",  # noqa: S608
                    (rowid,),
                )
                emb_row = await emb_cursor.fetchone()
                if emb_row is None:
                    continue

                query_bytes = emb_row[0]
                sim_cursor = await self._db.execute(
                    f"SELECT v.rowid, v.distance FROM {vec_table} v "  # noqa: S608
                    f"WHERE v.embedding MATCH ? AND k = 5 "
                    f"ORDER BY v.distance",
                    (query_bytes,),
                )
                sim_rows = await sim_cursor.fetchall()

                for sim_row in sim_rows:
                    sim_rowid = sim_row[0]
                    distance = sim_row[1]
                    similarity = 1.0 - distance

                    if similarity < threshold or sim_rowid == rowid:
                        continue

                    id_cursor = await self._db.execute(
                        f"SELECT * FROM {table} WHERE rowid = ? AND status = 'active'",  # noqa: S608
                        (sim_rowid,),
                    )
                    id_row = await id_cursor.fetchone()
                    if id_row is None:
                        continue

                    other_id = id_row[0]
                    pair_key = tuple(sorted([record_id, other_id]))
                    if pair_key in seen_pairs:
                        continue

                    # Build both records
                    rec_a_cursor = await self._db.execute(
                        f"SELECT * FROM {table} WHERE id = ? AND status = 'active'",  # noqa: S608
                        (record_id,),
                    )
                    rec_a_row = await rec_a_cursor.fetchone()
                    if rec_a_row is None:
                        continue

                    rd_a = dict(rec_a_row)
                    rd_b = dict(id_row)

                    self._append_unique_duplicate_pair(
                        pairs,
                        seen_pairs,
                        DuplicatePair(
                            record_a=MemoryRecord(
                                id=rd_a.get("id", ""),
                                content=rd_a.get("content", ""),
                                summary=rd_a.get("summary"),
                                memory_type=rd_a.get("memory_type", "episodic"),
                                importance=rd_a.get("importance", rd_a.get("confidence", 0.5)),
                                source_path=rd_a.get("source_path", ""),
                                source_table=table,
                                created_at=rd_a.get("created_at", ""),
                                updated_at=rd_a.get("updated_at", ""),
                            ),
                            record_b=MemoryRecord(
                                id=rd_b.get("id", ""),
                                content=rd_b.get("content", ""),
                                summary=rd_b.get("summary"),
                                memory_type=rd_b.get("memory_type", "episodic"),
                                importance=rd_b.get("importance", rd_b.get("confidence", 0.5)),
                                source_path=rd_b.get("source_path", ""),
                                source_table=table,
                                created_at=rd_b.get("created_at", ""),
                                updated_at=rd_b.get("updated_at", ""),
                            ),
                            similarity=similarity,
                        ),
                    )

        return pairs

    # ------------------------------------------------------------------
    # Entity queries
    # ------------------------------------------------------------------

    async def get_memories_by_entity(
        self,
        entity_name: str,
    ) -> list[MemoryRecord]:
        """Return all active memories/facts linked to an entity.

        Joins through the ``entities`` table using canonical name matching.
        Filters ``status = 'active'``.

        Args:
            entity_name: Display or canonical name of the entity.

        Returns:
            List of MemoryRecord linked to the entity.
        """
        canonical = entity_name.strip().lower()
        results: list[MemoryRecord] = []

        # Memories linked via entity_links
        cursor = await self._db.execute(
            "SELECT m.* FROM memories m "
            "INNER JOIN entity_links el ON el.memory_id = m.id "
            "INNER JOIN entities e ON e.id = el.entity_id "
            "WHERE e.canonical_name = ? AND m.status = 'active'",
            (canonical,),
        )
        for row in await cursor.fetchall():
            rd = dict(row)
            results.append(
                MemoryRecord(
                    id=rd.get("id", ""),
                    content=rd.get("content", ""),
                    summary=rd.get("summary"),
                    memory_type=rd.get("memory_type", "episodic"),
                    importance=rd.get("importance", 0.5),
                    source_path=rd.get("source_path", ""),
                    source_table="memories",
                    created_at=rd.get("created_at", ""),
                    updated_at=rd.get("updated_at", ""),
                    entities=rd.get("entities"),
                    tags=rd.get("tags"),
                )
            )

        # User model facts linked via entity_links
        cursor = await self._db.execute(
            "SELECT f.* FROM user_model_facts f "
            "INNER JOIN entity_links el ON el.user_model_fact_id = f.id "
            "INNER JOIN entities e ON e.id = el.entity_id "
            "WHERE e.canonical_name = ? AND f.status = 'active'",
            (canonical,),
        )
        for row in await cursor.fetchall():
            rd = dict(row)
            results.append(
                MemoryRecord(
                    id=rd.get("id", ""),
                    content=rd.get("content", ""),
                    summary=None,
                    memory_type="user_model",
                    importance=rd.get("confidence", 0.5),
                    source_path=rd.get("source_path", ""),
                    source_table="user_model_facts",
                    created_at=rd.get("created_at", ""),
                    updated_at=rd.get("updated_at", ""),
                )
            )

        return results

    async def get_entities_by_type(
        self,
        entity_type: str,
    ) -> list[EntityRecord]:
        """Return all entities of a given type with active link counts.

        Args:
            entity_type: Type classification (e.g. ``person``, ``place``).

        Returns:
            List of EntityRecord with link counts and date ranges.
        """
        cursor = await self._db.execute(
            "SELECT e.id, e.name, e.canonical_name, e.entity_type, "
            "  (SELECT COUNT(*) FROM entity_links el "
            "   LEFT JOIN memories m ON m.id = el.memory_id "
            "   LEFT JOIN user_model_facts f ON f.id = el.user_model_fact_id "
            "   WHERE el.entity_id = e.id "
            "     AND (m.status = 'active' OR f.status = 'active')"
            "  ) AS active_link_count, "
            "  (SELECT MIN(COALESCE(m2.created_at, f2.created_at)) "
            "   FROM entity_links el2 "
            "   LEFT JOIN memories m2 ON m2.id = el2.memory_id AND m2.status = 'active' "
            "   LEFT JOIN user_model_facts f2 ON f2.id = el2.user_model_fact_id AND f2.status = 'active' "
            "   WHERE el2.entity_id = e.id"
            "  ) AS first_mention, "
            "  (SELECT MAX(COALESCE(m3.created_at, f3.created_at)) "
            "   FROM entity_links el3 "
            "   LEFT JOIN memories m3 ON m3.id = el3.memory_id AND m3.status = 'active' "
            "   LEFT JOIN user_model_facts f3 ON f3.id = el3.user_model_fact_id AND f3.status = 'active' "
            "   WHERE el3.entity_id = e.id"
            "  ) AS last_mention "
            "FROM entities e WHERE e.entity_type = ?",
            (entity_type,),
        )
        rows = await cursor.fetchall()
        results: list[EntityRecord] = []
        for row in rows:
            rd = dict(row)
            results.append(
                EntityRecord(
                    id=rd.get("id", ""),
                    name=rd.get("name", ""),
                    canonical_name=rd.get("canonical_name", ""),
                    entity_type=rd.get("entity_type", ""),
                    active_link_count=rd.get("active_link_count", 0),
                    first_mention=rd.get("first_mention"),
                    last_mention=rd.get("last_mention"),
                )
            )
        return results

    async def get_entity_relationships(
        self,
        entity_id: str,
    ) -> list[EntityRelationship]:
        """Return entities that co-occur in the same active memories.

        Builds the relationship graph by finding entities that share
        active memory or fact links with the given entity.

        Args:
            entity_id: ID of the entity to find relationships for.

        Returns:
            List of EntityRelationship with co-occurrence counts.
        """
        # Find all active memory IDs linked to this entity
        cursor = await self._db.execute(
            "SELECT el.memory_id, el.user_model_fact_id FROM entity_links el "
            "WHERE el.entity_id = ?",
            (entity_id,),
        )
        link_rows = await cursor.fetchall()

        active_memory_ids: list[str] = []
        active_fact_ids: list[str] = []

        for lr in link_rows:
            rd = dict(lr)
            mid = rd.get("memory_id")
            fid = rd.get("user_model_fact_id")
            if mid:
                # Check if active
                check = await self._db.execute(
                    "SELECT 1 FROM memories WHERE id = ? AND status = 'active'",
                    (mid,),
                )
                if await check.fetchone():
                    active_memory_ids.append(mid)
            if fid:
                check = await self._db.execute(
                    "SELECT 1 FROM user_model_facts WHERE id = ? AND status = 'active'",
                    (fid,),
                )
                if await check.fetchone():
                    active_fact_ids.append(fid)

        if not active_memory_ids and not active_fact_ids:
            return []

        # Find other entities linked to the same active records
        co_occurrences: dict[str, int] = {}
        entity_names: dict[str, str] = {}

        for mid in active_memory_ids:
            cursor = await self._db.execute(
                "SELECT el.entity_id, e.name FROM entity_links el "
                "INNER JOIN entities e ON e.id = el.entity_id "
                "WHERE el.memory_id = ? AND el.entity_id != ?",
                (mid, entity_id),
            )
            for row in await cursor.fetchall():
                rd = dict(row)
                eid = rd["entity_id"]
                co_occurrences[eid] = co_occurrences.get(eid, 0) + 1
                entity_names[eid] = rd["name"]

        for fid in active_fact_ids:
            cursor = await self._db.execute(
                "SELECT el.entity_id, e.name FROM entity_links el "
                "INNER JOIN entities e ON e.id = el.entity_id "
                "WHERE el.user_model_fact_id = ? AND el.entity_id != ?",
                (fid, entity_id),
            )
            for row in await cursor.fetchall():
                rd = dict(row)
                eid = rd["entity_id"]
                co_occurrences[eid] = co_occurrences.get(eid, 0) + 1
                entity_names[eid] = rd["name"]

        return [
            EntityRelationship(
                entity_id=eid,
                entity_name=entity_names[eid],
                co_occurrence_count=count,
            )
            for eid, count in sorted(
                co_occurrences.items(), key=lambda x: x[1], reverse=True
            )
        ]

    async def merge_entities(
        self,
        source_id: str,
        target_id: str,
    ) -> None:
        """Merge source entity into target.

        Re-links all ``entity_links`` from source to target, then deletes
        the source entity record.

        Args:
            source_id: Entity to merge away (will be deleted).
            target_id: Entity to merge into (preserved).
        """
        source_cur = await self._db.execute(
            "SELECT id, name, canonical_name, entity_type FROM entities WHERE id = ?",
            (source_id,),
        )
        source_row = await source_cur.fetchone()
        target_cur = await self._db.execute(
            "SELECT metadata FROM entities WHERE id = ?", (target_id,)
        )
        target_row = await target_cur.fetchone()

        metadata: dict[str, object] = {}
        if target_row is not None and target_row[0]:
            try:
                parsed = json.loads(target_row[0])
                if isinstance(parsed, dict):
                    metadata = parsed
            except json.JSONDecodeError:
                metadata = {}

        merged_from = metadata.get("merged_from")
        if not isinstance(merged_from, list):
            merged_from = []
        if source_row is not None:
            merged_from.append(
                {
                    "id": source_row["id"],
                    "name": source_row["name"],
                    "canonical_name": source_row["canonical_name"],
                    "entity_type": source_row["entity_type"],
                    "merged_at": datetime.now(UTC).isoformat(timespec="seconds"),
                }
            )
        metadata["merged_from"] = merged_from

        await self._db.execute(
            "UPDATE entity_links SET entity_id = ? WHERE entity_id = ?",
            (target_id, source_id),
        )
        await self._db.execute(
            "UPDATE entities SET metadata = ? WHERE id = ?",
            (json.dumps(metadata, sort_keys=True), target_id),
        )
        await self._db.execute("DELETE FROM entities WHERE id = ?", (source_id,))
        await self._db.commit()
        log.debug("entities_merged", source=source_id, target=target_id)

    # ------------------------------------------------------------------
    # Stale entry detection
    # ------------------------------------------------------------------

    async def detect_stale_entries(self) -> list[StaleEntry]:
        """Compare projection DB updated_at against filesystem note mtime.

        Uses ``os.stat()`` as a fast filter, then returns entries whose
        filesystem modification time is newer than the indexed timestamp.

        Returns:
            List of StaleEntry needing re-indexing.
        """
        stale: list[StaleEntry] = []

        for table in ("memories", "user_model_facts"):
            cursor = await self._db.execute(
                f"SELECT id, source_path, updated_at FROM {table} "  # noqa: S608
                f"WHERE status = 'active'"
            )
            rows = await cursor.fetchall()

            for row in rows:
                rd = dict(row)
                record_id = rd["id"]
                source_path = rd.get("source_path", "")
                db_updated_at = rd.get("updated_at", "")

                if not source_path:
                    continue

                try:
                    stat = os.stat(source_path)
                    fs_mtime = datetime.fromtimestamp(
                        stat.st_mtime, tz=UTC
                    ).isoformat(timespec="seconds")
                except OSError:
                    # File doesn't exist -- might be stale
                    stale.append(
                        StaleEntry(
                            record_id=record_id,
                            source_path=source_path,
                            source_table=table,
                            db_updated_at=db_updated_at,
                            fs_mtime="missing",
                        )
                    )
                    continue

                # Compare timestamps: if FS is newer than DB, it's stale
                if not db_updated_at:
                    stale.append(
                        StaleEntry(
                            record_id=record_id,
                            source_path=source_path,
                            source_table=table,
                            db_updated_at=db_updated_at,
                            fs_mtime=fs_mtime,
                        )
                    )
                    continue

                try:
                    db_dt = datetime.fromisoformat(db_updated_at)
                    if db_dt.tzinfo is None:
                        db_dt = db_dt.replace(tzinfo=UTC)
                    fs_dt = datetime.fromisoformat(fs_mtime)
                    if fs_dt > db_dt:
                        stale.append(
                            StaleEntry(
                                record_id=record_id,
                                source_path=source_path,
                                source_table=table,
                                db_updated_at=db_updated_at,
                                fs_mtime=fs_mtime,
                            )
                        )
                except ValueError:
                    stale.append(
                        StaleEntry(
                            record_id=record_id,
                            source_path=source_path,
                            source_table=table,
                            db_updated_at=db_updated_at,
                            fs_mtime=fs_mtime,
                        )
                    )

        return stale

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Close the underlying database connection."""
        await self._db.close()
        log.info("projection_db_closed")
