"""Append-only :class:`WorkLedger` — spec §10.

Every transition the dispatcher makes — task started, task checkpointed,
pipeline completed, trigger fired, decision posted — is written to
``work_ledger`` as a single row. The ledger has no updates and no
deletes; it is the forensic audit trail Kora uses to reconstruct what
happened during a background run.

The ledger interface is intentionally thin: the dispatcher holds a
single instance and calls :meth:`record` with a typed event name plus a
``metadata`` dict. Serialisation of ``metadata`` is handled here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite
import structlog

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class LedgerEvent:
    """One row from ``work_ledger`` in memory form."""

    id: int
    timestamp: datetime
    event_type: str
    pipeline_instance_id: str | None
    worker_task_id: str | None
    trigger_name: str | None
    reason: str | None
    metadata: dict[str, Any]


# Canonical event names used across the orchestration layer. The set is
# not enforced by the writer — new event types can be added freely — but
# keeping the literal strings in one place prevents typos and makes it
# easy to grep for all producers.
class LedgerEventType:
    PIPELINE_STARTED = "pipeline_started"
    PIPELINE_PAUSED = "pipeline_paused"
    PIPELINE_RESUMED = "pipeline_resumed"
    PIPELINE_COMPLETED = "pipeline_completed"
    PIPELINE_FAILED = "pipeline_failed"

    TASK_CREATED = "task_created"
    TASK_STARTED = "task_started"
    TASK_PROGRESS = "task_progress"
    TASK_CHECKPOINTED = "task_checkpointed"
    TASK_PAUSED = "task_paused"
    TASK_RESUMED = "task_resumed"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    TASK_CANCELLED = "task_cancelled"

    TRIGGER_FIRED = "trigger_fired"
    TRIGGER_SUPPRESSED = "trigger_suppressed"

    RATE_LIMIT_REJECTED = "rate_limit_rejected"
    STATE_TRANSITION = "state_transition"

    DECISION_POSED = "decision_posed"
    DECISION_RESOLVED = "decision_resolved"


class WorkLedger:
    """Append-only writer against ``work_ledger``."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    async def record(
        self,
        event_type: str,
        *,
        pipeline_instance_id: str | None = None,
        worker_task_id: str | None = None,
        trigger_name: str | None = None,
        reason: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        now = datetime.now(UTC)
        payload_json = json.dumps(metadata, default=str) if metadata else None
        async with aiosqlite.connect(str(self._db_path)) as db:
            await db.execute(
                "INSERT INTO work_ledger "
                "(timestamp, event_type, pipeline_instance_id, worker_task_id, "
                " trigger_name, reason, metadata_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    now.isoformat(),
                    event_type,
                    pipeline_instance_id,
                    worker_task_id,
                    trigger_name,
                    reason,
                    payload_json,
                ),
            )
            await db.commit()
        log.debug(
            "work_ledger_recorded",
            event_type=event_type,
            pipeline=pipeline_instance_id,
            task=worker_task_id,
            trigger=trigger_name,
        )

    async def read_task_events(self, worker_task_id: str) -> list[LedgerEvent]:
        """Return every row whose ``worker_task_id`` matches, oldest first."""
        async with aiosqlite.connect(str(self._db_path)) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM work_ledger "
                "WHERE worker_task_id = ? "
                "ORDER BY timestamp ASC",
                (worker_task_id,),
            )
            rows = await cursor.fetchall()
        return [self._row_to_event(row) for row in rows]

    async def read_pipeline_events(self, pipeline_instance_id: str) -> list[LedgerEvent]:
        async with aiosqlite.connect(str(self._db_path)) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM work_ledger "
                "WHERE pipeline_instance_id = ? "
                "ORDER BY timestamp ASC",
                (pipeline_instance_id,),
            )
            rows = await cursor.fetchall()
        return [self._row_to_event(row) for row in rows]

    async def read_recent(self, limit: int = 100) -> list[LedgerEvent]:
        async with aiosqlite.connect(str(self._db_path)) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM work_ledger ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            )
            rows = await cursor.fetchall()
        return [self._row_to_event(row) for row in rows]

    @staticmethod
    def _row_to_event(row: aiosqlite.Row) -> LedgerEvent:
        metadata = {}
        if row["metadata_json"]:
            try:
                metadata = json.loads(row["metadata_json"])
            except json.JSONDecodeError:
                metadata = {"_raw": row["metadata_json"]}
        return LedgerEvent(
            id=row["id"],
            timestamp=datetime.fromisoformat(row["timestamp"]),
            event_type=row["event_type"],
            pipeline_instance_id=row["pipeline_instance_id"],
            worker_task_id=row["worker_task_id"],
            trigger_name=row["trigger_name"],
            reason=row["reason"],
            metadata=metadata,
        )
