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

import struct
import uuid
from pathlib import Path

import aiosqlite
import structlog

from kora_v2.core.migrations import MigrationRunner

log = structlog.get_logger(__name__)

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"


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
        cursor = await self._db.execute(
            "INSERT INTO memories "
            "(id, content, summary, importance, memory_type, "
            " created_at, updated_at, entities, tags, source_path) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                memory_id, content, summary, importance, memory_type,
                created_at, updated_at, entities, tags, source_path,
            ),
        )
        rowid = cursor.lastrowid

        if self._vector_available:
            await self._db.execute(
                "INSERT INTO memories_vec (rowid, embedding) VALUES (?, ?)",
                (rowid, serialize_float32(embedding)),
            )

        await self._db.commit()

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
        cursor = await self._db.execute(
            "INSERT INTO user_model_facts "
            "(id, domain, content, confidence, evidence_count, "
            " contradiction_count, created_at, updated_at, source_path) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                fact_id, domain, content, confidence, evidence_count,
                contradiction_count, created_at, updated_at, source_path,
            ),
        )
        rowid = cursor.lastrowid

        if self._vector_available:
            await self._db.execute(
                "INSERT INTO user_model_vec (rowid, embedding) VALUES (?, ?)",
                (rowid, serialize_float32(embedding)),
            )

        await self._db.commit()

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

    async def get_memory_by_id(self, memory_id: str) -> dict | None:
        """Fetch a single memory by its ID.

        Returns:
            Dict with all memory columns, or None if not found.
        """
        cursor = await self._db.execute(
            "SELECT * FROM memories WHERE id = ?", (memory_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return dict(row)

    async def get_fact_by_id(self, fact_id: str) -> dict | None:
        """Fetch a single user model fact by its ID.

        Returns:
            Dict with all fact columns, or None if not found.
        """
        cursor = await self._db.execute(
            "SELECT * FROM user_model_facts WHERE id = ?", (fact_id,)
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
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Close the underlying database connection."""
        await self._db.close()
        log.info("projection_db_closed")
