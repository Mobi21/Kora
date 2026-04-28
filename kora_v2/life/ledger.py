"""Life Event Ledger service."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime, time
from pathlib import Path

import aiosqlite

from kora_v2.life.domain_events import DOMAIN_EVENTS_SCHEMA_SQL, DomainEventStore
from kora_v2.life.models import (
    ConfirmationInput,
    ConfirmationState,
    CorrectionInput,
    LifeEvent,
    LifeEventSource,
    RecordLifeEventInput,
)

LIFE_EVENTS_SCHEMA_SQL = (
    DOMAIN_EVENTS_SCHEMA_SQL
    + """
CREATE TABLE IF NOT EXISTS life_events (
    id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    event_time TEXT NOT NULL,
    source TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 1.0,
    confirmation_state TEXT NOT NULL DEFAULT 'confirmed',
    calendar_entry_id TEXT,
    item_id TEXT,
    day_plan_entry_id TEXT,
    support_module TEXT,
    title TEXT,
    details TEXT,
    raw_text TEXT,
    metadata TEXT,
    supersedes_event_id TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_life_events_time
    ON life_events(event_time);
CREATE INDEX IF NOT EXISTS idx_life_events_day_plan_entry
    ON life_events(day_plan_entry_id, event_time);
CREATE INDEX IF NOT EXISTS idx_life_events_supersedes
    ON life_events(supersedes_event_id);
"""
)


def _new_id() -> str:
    return f"le-{uuid.uuid4().hex[:16]}"


def _now() -> datetime:
    return datetime.now(UTC)


class LifeEventLedger:
    """Durable record of confirmed, inferred, and corrected life reality."""

    def __init__(
        self,
        db_path: Path,
        *,
        domain_events: DomainEventStore | None = None,
    ) -> None:
        self._db_path = db_path
        self._domain_events = domain_events or DomainEventStore(db_path)

    async def ensure_schema(self) -> None:
        async with aiosqlite.connect(str(self._db_path)) as db:
            await db.executescript(LIFE_EVENTS_SCHEMA_SQL)
            await db.commit()

    async def record(self, input: RecordLifeEventInput) -> LifeEvent:
        return await self.record_event(input)

    async def record_event(self, input: RecordLifeEventInput) -> LifeEvent:
        await self.ensure_schema()
        now = _now()
        event = LifeEvent(
            id=_new_id(),
            event_type=input.event_type,
            event_time=input.event_time or now,
            source=input.source,
            confidence=input.confidence,
            confirmation_state=input.confirmation_state,
            calendar_entry_id=input.calendar_entry_id,
            item_id=input.item_id,
            day_plan_entry_id=input.day_plan_entry_id,
            support_module=input.support_module,
            title=input.title,
            details=input.details,
            raw_text=input.raw_text,
            metadata=input.metadata,
            supersedes_event_id=input.supersedes_event_id,
            created_at=now,
        )
        async with aiosqlite.connect(str(self._db_path)) as db:
            await _insert_life_event(db, event)
            await db.commit()
        await self._domain_events.append(
            "LIFE_EVENT_RECORDED",
            aggregate_type="life_event",
            aggregate_id=event.id,
            source_service="LifeEventLedger",
            correlation_id=input.correlation_id,
            causation_id=input.supersedes_event_id,
            payload=event.model_dump(mode="json"),
        )
        return event

    async def confirm(
        self, event_id: str, confirmation: ConfirmationInput
    ) -> LifeEvent:
        return await self.confirm_event(event_id, confirmation)

    async def confirm_event(
        self, event_id: str, confirmation: ConfirmationInput
    ) -> LifeEvent:
        original = await self.get_event(event_id)
        if original is None:
            raise ValueError(f"life event not found: {event_id}")

        event = await self.record_event(
            RecordLifeEventInput(
                event_type=original.event_type,
                event_time=original.event_time,
                source=confirmation.source,
                confidence=confirmation.confidence,
                confirmation_state=confirmation.confirmation_state,
                calendar_entry_id=original.calendar_entry_id,
                item_id=original.item_id,
                day_plan_entry_id=original.day_plan_entry_id,
                support_module=original.support_module,
                title=original.title,
                details=confirmation.details or original.details,
                raw_text=confirmation.raw_text or original.raw_text,
                metadata={**original.metadata, **confirmation.metadata},
                supersedes_event_id=original.id,
                correlation_id=confirmation.correlation_id,
            )
        )
        await self._mark_superseded(
            original.id,
            ConfirmationState.CONFIRMED
            if confirmation.confirmation_state == ConfirmationState.CONFIRMED
            else confirmation.confirmation_state,
        )
        return event

    async def correct(self, event_id: str, correction: CorrectionInput) -> LifeEvent:
        return await self.correct_event(event_id, correction)

    async def correct_event(
        self, event_id: str, correction: CorrectionInput
    ) -> LifeEvent:
        original = await self.get_event(event_id)
        if original is None:
            raise ValueError(f"life event not found: {event_id}")

        event = await self.record_event(
            RecordLifeEventInput(
                event_type=correction.event_type or original.event_type,
                event_time=_now(),
                source=correction.source,
                confidence=correction.confidence,
                confirmation_state=correction.confirmation_state,
                calendar_entry_id=original.calendar_entry_id,
                item_id=original.item_id,
                day_plan_entry_id=original.day_plan_entry_id,
                support_module=original.support_module,
                title=correction.title or original.title,
                details=correction.details,
                raw_text=correction.raw_text,
                metadata={**original.metadata, **correction.metadata},
                supersedes_event_id=original.id,
                correlation_id=correction.correlation_id,
            )
        )
        await self._mark_superseded(original.id, ConfirmationState.REJECTED)
        return event

    async def get_event(self, event_id: str) -> LifeEvent | None:
        await self.ensure_schema()
        async with aiosqlite.connect(str(self._db_path)) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM life_events WHERE id = ?",
                (event_id,),
            )
            row = await cursor.fetchone()
        return _life_event_from_row(row) if row else None

    async def events_for_day(self, day: date) -> list[LifeEvent]:
        await self.ensure_schema()
        start = datetime.combine(day, time.min, tzinfo=UTC).isoformat()
        end = datetime.combine(day, time.max, tzinfo=UTC).isoformat()
        async with aiosqlite.connect(str(self._db_path)) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT * FROM life_events
                WHERE event_time >= ? AND event_time <= ?
                ORDER BY event_time ASC, created_at ASC
                """,
                (start, end),
            )
            rows = await cursor.fetchall()
        return [_life_event_from_row(row) for row in rows]

    async def _mark_superseded(
        self, event_id: str, state: ConfirmationState
    ) -> None:
        async with aiosqlite.connect(str(self._db_path)) as db:
            await db.execute(
                """
                UPDATE life_events
                SET confirmation_state = ?
                WHERE id = ?
                """,
                (state.value, event_id),
            )
            await db.commit()


async def _insert_life_event(
    db: aiosqlite.Connection, event: LifeEvent
) -> None:
    await db.execute(
        """
        INSERT INTO life_events
            (id, event_type, event_time, source, confidence,
             confirmation_state, calendar_entry_id, item_id, day_plan_entry_id,
             support_module, title, details, raw_text, metadata,
             supersedes_event_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event.id,
            event.event_type,
            event.event_time.isoformat(),
            event.source.value,
            event.confidence,
            event.confirmation_state.value,
            event.calendar_entry_id,
            event.item_id,
            event.day_plan_entry_id,
            event.support_module,
            event.title,
            event.details,
            event.raw_text,
            json.dumps(event.metadata, sort_keys=True),
            event.supersedes_event_id,
            event.created_at.isoformat(),
        ),
    )


def _life_event_from_row(row: aiosqlite.Row) -> LifeEvent:
    return LifeEvent(
        id=row["id"],
        event_type=row["event_type"],
        event_time=datetime.fromisoformat(row["event_time"]),
        source=LifeEventSource(row["source"]),
        confidence=row["confidence"],
        confirmation_state=ConfirmationState(row["confirmation_state"]),
        calendar_entry_id=row["calendar_entry_id"],
        item_id=row["item_id"],
        day_plan_entry_id=row["day_plan_entry_id"],
        support_module=row["support_module"],
        title=row["title"],
        details=row["details"],
        raw_text=row["raw_text"],
        metadata=json.loads(row["metadata"] or "{}"),
        supersedes_event_id=row["supersedes_event_id"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )
