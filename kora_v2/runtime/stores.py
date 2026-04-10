"""Kora V2 — Thin repository layers over operational.db.

Stores are stateless wrappers that each own a single table or domain.

Currently implemented:
- ``ArtifactStore``: persist artifact links for autonomous plan items.
- ``AutonomousUpdateStore``: persist/fetch unread autonomous updates
  so the foreground conversation can surface background progress when
  the user returns from an idle period.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import aiosqlite
import structlog

log = structlog.get_logger(__name__)


class ArtifactStore:
    """Persist artifact links for autonomous plan items."""

    def __init__(self, db: aiosqlite.Connection) -> None:
        self._db = db

    async def record(
        self,
        *,
        item_id: str | None = None,
        artifact_id: str | None = None,
        artifact_type: str,
        uri: str,
        label: str | None = None,
        size_bytes: int | None = None,
        recorded_at: datetime | None = None,
    ) -> None:
        """Insert an artifact link.

        Table: item_artifact_links (columns: item_id, artifact_id, artifact_type,
        uri, label, size_bytes, created_at).
        Gracefully handles the case where the table does not yet exist.
        """
        ts = (recorded_at or datetime.now(UTC)).isoformat()
        try:
            await self._db.execute(
                """
                INSERT INTO item_artifact_links
                    (item_id, artifact_id, artifact_type, uri, label, size_bytes, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item_id,
                    artifact_id,
                    artifact_type,
                    uri,
                    label,
                    size_bytes,
                    ts,
                ),
            )
            await self._db.commit()
            log.debug(
                "artifact_store.record",
                item_id=item_id,
                artifact_id=artifact_id,
                uri=uri,
            )
        except Exception as exc:
            err_str = str(exc).lower()
            if "no such table" in err_str or "no such column" in err_str:
                log.warning(
                    "artifact_store.record.table_missing",
                    error=str(exc),
                    uri=uri,
                )
                # Non-fatal: table not yet created
            else:
                log.error("artifact_store.record.error", error=str(exc), uri=uri)
                raise

    async def list_for_plan(self, plan_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        """Return artifacts for a plan, newest first.

        Joins through items because item_artifact_links has no plan_id column;
        items carry the autonomous_plan_id reference.
        """
        try:
            async with self._db.execute(
                """
                SELECT ial.*
                FROM item_artifact_links ial
                JOIN items i ON i.id = ial.item_id
                WHERE i.autonomous_plan_id = ?
                ORDER BY ial.created_at DESC LIMIT ?
                """,
                (plan_id, limit),
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception as exc:
            log.warning("artifact_store.list_for_plan.error", plan_id=plan_id, error=str(exc))
            return []


class AutonomousUpdateStore:
    """Persist and fetch unread autonomous updates for foreground delivery."""

    def __init__(self, db: aiosqlite.Connection) -> None:
        self._db = db

    async def record(
        self,
        *,
        session_id: str,
        plan_id: str | None,
        update_type: str,  # 'checkpoint' or 'completion'
        summary: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Insert an autonomous update record.

        Gracefully handles the case where the ``autonomous_updates`` table
        does not yet exist (older operational.db schemas).
        """
        try:
            await self._db.execute(
                """
                INSERT INTO autonomous_updates
                    (session_id, plan_id, update_type, summary, payload, delivered, created_at)
                VALUES (?, ?, ?, ?, ?, 0, ?)
                """,
                (
                    session_id,
                    plan_id,
                    update_type,
                    summary,
                    json.dumps(payload) if payload else None,
                    datetime.now(UTC).isoformat(),
                ),
            )
            await self._db.commit()
            log.debug(
                "autonomous_update_store.record",
                session_id=session_id,
                plan_id=plan_id,
                update_type=update_type,
            )
        except Exception as exc:
            err_str = str(exc).lower()
            if "no such table" in err_str or "no such column" in err_str:
                log.warning(
                    "autonomous_update_store.record.table_missing",
                    error=str(exc),
                )
            else:
                log.error(
                    "autonomous_update_store.record.error",
                    error=str(exc),
                    session_id=session_id,
                )
                raise

    async def get_undelivered(self, session_id: str) -> list[dict[str, Any]]:
        """Return all undelivered updates for a session, oldest first."""
        try:
            async with self._db.execute(
                """
                SELECT * FROM autonomous_updates
                WHERE session_id = ? AND delivered = 0
                ORDER BY created_at ASC
                """,
                (session_id,),
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception as exc:
            err_str = str(exc).lower()
            if "no such table" in err_str:
                log.warning(
                    "autonomous_update_store.get_undelivered.table_missing",
                    session_id=session_id,
                )
                return []
            log.warning(
                "autonomous_update_store.get_undelivered.error",
                session_id=session_id,
                error=str(exc),
            )
            return []

    async def mark_delivered(self, session_id: str) -> None:
        """Mark all undelivered updates for *session_id* as delivered."""
        try:
            await self._db.execute(
                "UPDATE autonomous_updates SET delivered = 1 "
                "WHERE session_id = ? AND delivered = 0",
                (session_id,),
            )
            await self._db.commit()
        except Exception as exc:
            err_str = str(exc).lower()
            if "no such table" in err_str:
                log.warning(
                    "autonomous_update_store.mark_delivered.table_missing",
                    session_id=session_id,
                )
                return
            log.warning(
                "autonomous_update_store.mark_delivered.error",
                session_id=session_id,
                error=str(exc),
            )
