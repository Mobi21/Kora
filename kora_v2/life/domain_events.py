"""Durable Life OS domain event store."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite

from kora_v2.life.models import DomainEvent

DOMAIN_EVENTS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS domain_events (
    id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    aggregate_type TEXT NOT NULL,
    aggregate_id TEXT,
    source_service TEXT NOT NULL,
    correlation_id TEXT,
    causation_id TEXT,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_domain_events_type_created
    ON domain_events(event_type, created_at);

CREATE INDEX IF NOT EXISTS idx_domain_events_aggregate
    ON domain_events(aggregate_type, aggregate_id, created_at);
"""


def _event_id() -> str:
    return f"de-{uuid.uuid4().hex[:16]}"


def _now() -> datetime:
    return datetime.now(UTC)


class DomainEventStore:
    """Append-only helper for Life OS product-domain proof events."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    async def ensure_schema(self) -> None:
        async with aiosqlite.connect(str(self._db_path)) as db:
            await db.executescript(DOMAIN_EVENTS_SCHEMA_SQL)
            await db.commit()

    async def append(
        self,
        event_type: str,
        *,
        aggregate_type: str,
        aggregate_id: str | None = None,
        source_service: str,
        payload: dict[str, Any] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> DomainEvent:
        await self.ensure_schema()
        event = DomainEvent(
            id=_event_id(),
            event_type=event_type,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            source_service=source_service,
            correlation_id=correlation_id,
            causation_id=causation_id,
            payload=payload or {},
            created_at=_now(),
        )
        async with aiosqlite.connect(str(self._db_path)) as db:
            await db.execute(
                """
                INSERT INTO domain_events
                    (id, event_type, aggregate_type, aggregate_id,
                     source_service, correlation_id, causation_id, payload,
                     created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.id,
                    event.event_type,
                    event.aggregate_type,
                    event.aggregate_id,
                    event.source_service,
                    event.correlation_id,
                    event.causation_id,
                    json.dumps(event.payload, sort_keys=True),
                    event.created_at.isoformat(),
                ),
            )
            await db.commit()
        return event

    async def list_events(
        self,
        *,
        aggregate_type: str | None = None,
        aggregate_id: str | None = None,
        event_type: str | None = None,
    ) -> list[DomainEvent]:
        await self.ensure_schema()
        clauses: list[str] = []
        params: list[str] = []
        if aggregate_type is not None:
            clauses.append("aggregate_type = ?")
            params.append(aggregate_type)
        if aggregate_id is not None:
            clauses.append("aggregate_id = ?")
            params.append(aggregate_id)
        if event_type is not None:
            clauses.append("event_type = ?")
            params.append(event_type)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        async with aiosqlite.connect(str(self._db_path)) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                f"SELECT * FROM domain_events {where} ORDER BY created_at ASC",
                params,
            )
            rows = await cursor.fetchall()
        return [_domain_event_from_row(row) for row in rows]


def _domain_event_from_row(row: aiosqlite.Row) -> DomainEvent:
    return DomainEvent(
        id=row["id"],
        event_type=row["event_type"],
        aggregate_type=row["aggregate_type"],
        aggregate_id=row["aggregate_id"],
        source_service=row["source_service"],
        correlation_id=row["correlation_id"],
        causation_id=row["causation_id"],
        payload=json.loads(row["payload"] or "{}"),
        created_at=datetime.fromisoformat(row["created_at"]),
    )
