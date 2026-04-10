"""Kora V2 — Checkpoint persistence for autonomous sessions.

``CheckpointManager`` writes and reads ``AutonomousCheckpoint`` objects
using the existing ``autonomous_checkpoints`` table in ``operational.db``.
The entire checkpoint is serialised as JSON in the ``plan_json`` column so
that no schema changes are needed.  Other columns (``id``, ``plan_id``,
``completed_steps``, ``current_step``, ``artifacts``, ``elapsed_minutes``,
``reflection``) are also populated for backwards-compatible filtering.
"""

from __future__ import annotations

import json
from pathlib import Path

import aiosqlite
import structlog

from kora_v2.autonomous.state import AutonomousCheckpoint

log = structlog.get_logger(__name__)


class CheckpointManager:
    """Async persistence layer for ``AutonomousCheckpoint`` objects.

    Args:
        db_path: Path to the ``operational.db`` SQLite file.  The
            ``autonomous_checkpoints`` table must already exist (created
            by ``init_operational_db``).
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    # ── Public API ────────────────────────────────────────────────────

    async def save(self, checkpoint: AutonomousCheckpoint) -> str:
        """Write *checkpoint* to the database and return its ID.

        Uses ``INSERT OR REPLACE`` so that calling ``save`` again with
        the same ``checkpoint_id`` overwrites the previous record.

        Args:
            checkpoint: Fully populated ``AutonomousCheckpoint`` instance.

        Returns:
            The ``checkpoint_id`` string.
        """
        plan_json = checkpoint.model_dump_json()
        completed_steps_json = json.dumps(checkpoint.completed_step_ids)
        artifacts_json = json.dumps(checkpoint.produced_artifact_ids)
        elapsed_minutes = checkpoint.elapsed_seconds // 60
        created_at_iso = checkpoint.created_at.isoformat()

        async with aiosqlite.connect(str(self._db_path)) as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO autonomous_checkpoints (
                    id,
                    plan_id,
                    plan_json,
                    completed_steps,
                    current_step,
                    accumulated_context,
                    artifacts,
                    elapsed_minutes,
                    reflection,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    checkpoint.checkpoint_id,
                    checkpoint.plan_id,
                    plan_json,
                    completed_steps_json,
                    checkpoint.state.current_step_id,
                    None,
                    artifacts_json,
                    elapsed_minutes,
                    checkpoint.latest_reflection,
                    created_at_iso,
                ),
            )
            await db.commit()

        log.info(
            "checkpoint_saved",
            checkpoint_id=checkpoint.checkpoint_id,
            session_id=checkpoint.session_id,
            plan_id=checkpoint.plan_id,
            reason=checkpoint.reason,
        )
        return checkpoint.checkpoint_id

    async def load_latest(self, session_id: str) -> AutonomousCheckpoint | None:
        """Return the most recent checkpoint for *session_id*, or ``None``.

        Because the ``autonomous_checkpoints`` table has no ``session_id``
        column, session identity is determined by parsing the ``plan_json``
        field — specifically the ``session_id`` key at the top level of
        the serialised checkpoint.

        Args:
            session_id: The autonomous session identifier.

        Returns:
            The most recent ``AutonomousCheckpoint`` or ``None`` if not found.
        """
        checkpoints = await self._load_all_for_session(session_id, limit=1)
        return checkpoints[0] if checkpoints else None

    async def load_by_id(self, checkpoint_id: str) -> AutonomousCheckpoint | None:
        """Load a specific checkpoint by its primary key.

        Args:
            checkpoint_id: The ``checkpoint_id`` to retrieve.

        Returns:
            Parsed ``AutonomousCheckpoint`` or ``None`` if not found.
        """
        async with aiosqlite.connect(str(self._db_path)) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT plan_json FROM autonomous_checkpoints WHERE id = ?",
                (checkpoint_id,),
            ) as cursor:
                row = await cursor.fetchone()

        if row is None:
            return None
        return self._parse_row(row["plan_json"])

    async def list_session_checkpoints(self, session_id: str) -> list[str]:
        """Return checkpoint IDs for *session_id*, newest first.

        Args:
            session_id: The autonomous session identifier.

        Returns:
            List of ``checkpoint_id`` strings ordered by ``created_at`` DESC.
        """
        checkpoints = await self._load_all_for_session(session_id)
        return [c.checkpoint_id for c in checkpoints]

    # ── Private helpers ───────────────────────────────────────────────

    async def _load_all_for_session(
        self,
        session_id: str,
        limit: int | None = None,
    ) -> list[AutonomousCheckpoint]:
        """Scan all checkpoint rows and return those matching *session_id*.

        We cannot filter by ``session_id`` in SQL because the column does
        not exist in the schema.  Instead we fetch all rows ordered by
        ``created_at`` DESC and parse ``plan_json`` until we find enough
        matches.  For typical usage the total checkpoint count is small.

        Args:
            session_id: Session to filter by.
            limit: Stop after collecting this many matching rows.

        Returns:
            List of matching checkpoints, newest first.
        """
        sql = "SELECT plan_json FROM autonomous_checkpoints ORDER BY created_at DESC"

        results: list[AutonomousCheckpoint] = []
        async with aiosqlite.connect(str(self._db_path)) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(sql) as cursor:
                async for row in cursor:
                    try:
                        cp = self._parse_row(row["plan_json"])
                    except Exception as exc:
                        log.warning("checkpoint_parse_failed", error=str(exc))
                        continue
                    if cp.session_id == session_id:
                        results.append(cp)
                        if limit is not None and len(results) >= limit:
                            break
        return results

    @staticmethod
    def _parse_row(plan_json: str) -> AutonomousCheckpoint:
        """Deserialise a JSON string to an ``AutonomousCheckpoint``."""
        return AutonomousCheckpoint.model_validate_json(plan_json)
