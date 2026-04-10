"""Schema migration runner for aiosqlite databases.

Reads numbered ``.sql`` files from a migrations directory, tracks applied
versions in a ``schema_version`` table, and executes only unapplied
migrations in order.  Each migration runs statement-by-statement so the
runner can tolerate optional-feature failures (e.g. ``vec0`` virtual
tables when sqlite-vec is not loaded) without losing the rest of the
schema or failing to record the migration as applied.
"""

from __future__ import annotations

import re
from pathlib import Path

import aiosqlite
import structlog

log = structlog.get_logger(__name__)

# Regex to extract the version number from filenames like "001_projection_schema.sql"
_VERSION_RE = re.compile(r"^(\d+)")

# Substrings that mark a statement as depending on an OPTIONAL feature.
# If one of these statements fails, the migration is still considered
# successfully applied (with a "partial" record) instead of rolled back.
_OPTIONAL_FEATURE_MARKERS: tuple[str, ...] = (
    "USING vec0",       # sqlite-vec virtual tables
    "using vec0",
)


def _split_sql_statements(sql: str) -> list[str]:
    """Split a SQL script into individual statements.

    Handles triggers, which embed ``;`` inside ``BEGIN … END`` blocks.
    The splitter tracks nesting depth so those inner semicolons don't
    accidentally terminate the outer statement.

    Returns a list of non-empty, stripped statements (no trailing ``;``).
    """
    statements: list[str] = []
    buf: list[str] = []
    depth = 0
    # Walk char-by-char and track whether we're inside a BEGIN...END block.
    # Use a simple lowercase scan of the surrounding 6 chars to detect the
    # BEGIN/END tokens without false-positives on identifiers.
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]

        # Handle string literals to avoid parsing BEGIN/END inside them.
        if ch == "'":
            buf.append(ch)
            i += 1
            while i < n:
                buf.append(sql[i])
                if sql[i] == "'":
                    # Double '' is an escaped quote — continue
                    if i + 1 < n and sql[i + 1] == "'":
                        buf.append(sql[i + 1])
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            continue

        # Handle single-line SQL comments
        if ch == "-" and i + 1 < n and sql[i + 1] == "-":
            while i < n and sql[i] != "\n":
                buf.append(sql[i])
                i += 1
            continue

        if ch == ";" and depth == 0:
            stmt = "".join(buf).strip()
            if stmt:
                statements.append(stmt)
            buf = []
            i += 1
            continue

        # Track BEGIN/END nesting for triggers.
        if ch.isalpha():
            # Look ahead for BEGIN or END keyword at word boundary.
            word_start = i
            while i < n and (sql[i].isalnum() or sql[i] == "_"):
                buf.append(sql[i])
                i += 1
            word = sql[word_start:i].upper()
            if word == "BEGIN":
                depth += 1
            elif word == "END":
                if depth > 0:
                    depth -= 1
            continue

        buf.append(ch)
        i += 1

    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


def _is_optional_feature_error(stmt: str) -> bool:
    """Return True if *stmt* touches an optional feature that may fail gracefully."""
    return any(marker in stmt for marker in _OPTIONAL_FEATURE_MARKERS)


class MigrationRunner:
    """Apply file-based SQL migrations to an aiosqlite database.

    Usage::

        async with aiosqlite.connect("projection.db") as db:
            runner = MigrationRunner()
            await runner.run_migrations(db, Path("kora_v2/memory/migrations"))
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run_migrations(
        self,
        db: aiosqlite.Connection,
        migrations_dir: Path,
    ) -> int:
        """Apply all unapplied migrations from *migrations_dir*.

        Args:
            db: Open aiosqlite connection (caller owns lifecycle).
            migrations_dir: Directory containing numbered ``.sql`` files.

        Returns:
            Number of newly-applied migrations.
        """
        await self._ensure_schema_version_table(db)
        applied = await self._get_applied_versions(db)
        pending = self._discover_migrations(migrations_dir, applied)

        if not pending:
            log.debug("migrations_up_to_date", applied=len(applied))
            return 0

        count = 0
        for version, path in pending:
            sql = path.read_text(encoding="utf-8")
            log.info(
                "applying_migration",
                version=version,
                file=path.name,
            )

            statements = _split_sql_statements(sql)
            skipped_optional: list[str] = []

            # Execute statements one at a time. A failure on an
            # OPTIONAL_FEATURE statement (e.g. ``CREATE VIRTUAL TABLE …
            # USING vec0`` when sqlite-vec is unavailable) is logged and
            # skipped, but the rest of the migration still applies and
            # the version is still recorded so it doesn't rerun forever.
            for stmt in statements:
                try:
                    await db.execute(stmt)
                except Exception as exc:  # noqa: BLE001
                    if _is_optional_feature_error(stmt):
                        log.warning(
                            "migration_optional_statement_skipped",
                            version=version,
                            error=str(exc),
                            statement_preview=stmt[:120],
                        )
                        skipped_optional.append(stmt[:120])
                        continue
                    log.error(
                        "migration_statement_failed",
                        version=version,
                        error=str(exc),
                        statement_preview=stmt[:200],
                    )
                    await db.rollback()
                    raise

            await db.execute(
                "INSERT INTO schema_version (version, applied_at) "
                "VALUES (?, datetime('now'))",
                (version,),
            )
            await db.commit()
            count += 1
            log.info(
                "migration_applied",
                version=version,
                skipped_optional_count=len(skipped_optional),
            )

        log.info("migrations_complete", applied=count, total=len(applied) + count)
        return count

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    async def _ensure_schema_version_table(db: aiosqlite.Connection) -> None:
        """Create the ``schema_version`` table if it does not exist."""
        await db.execute(
            "CREATE TABLE IF NOT EXISTS schema_version ("
            "  version INTEGER PRIMARY KEY,"
            "  applied_at TEXT NOT NULL"
            ")"
        )
        await db.commit()

    @staticmethod
    async def _get_applied_versions(db: aiosqlite.Connection) -> set[int]:
        """Return the set of already-applied migration versions."""
        cursor = await db.execute("SELECT version FROM schema_version")
        rows = await cursor.fetchall()
        return {row[0] for row in rows}

    @staticmethod
    def _discover_migrations(
        migrations_dir: Path,
        applied: set[int],
    ) -> list[tuple[int, Path]]:
        """Find ``.sql`` files whose version number is not in *applied*.

        Returns a list of ``(version, path)`` tuples sorted by version.
        """
        if not migrations_dir.is_dir():
            log.warning("migrations_dir_missing", path=str(migrations_dir))
            return []

        pending: list[tuple[int, Path]] = []
        for sql_file in sorted(migrations_dir.glob("*.sql")):
            match = _VERSION_RE.match(sql_file.name)
            if not match:
                continue
            version = int(match.group(1))
            if version not in applied:
                pending.append((version, sql_file))

        return sorted(pending, key=lambda t: t[0])
