"""Life OS day plan service."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import aiosqlite

from kora_v2.life.domain_events import DOMAIN_EVENTS_SCHEMA_SQL, DomainEventStore
from kora_v2.life.ledger import LIFE_EVENTS_SCHEMA_SQL, LifeEventLedger
from kora_v2.life.models import (
    DayPlan,
    DayPlanEntry,
    DayPlanEntryStatus,
    DayPlanStatus,
    RealityState,
    RecordLifeEventInput,
)

DAY_PLAN_SCHEMA_SQL = (
    LIFE_EVENTS_SCHEMA_SQL
    + DOMAIN_EVENTS_SCHEMA_SQL
    + """
CREATE TABLE IF NOT EXISTS day_plans (
    id TEXT PRIMARY KEY,
    plan_date TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'active',
    supersedes_day_plan_id TEXT,
    generated_from TEXT NOT NULL DEFAULT 'conversation',
    load_assessment_id TEXT,
    summary TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_day_plans_date_status
    ON day_plans(plan_date, status);

CREATE TABLE IF NOT EXISTS day_plan_entries (
    id TEXT PRIMARY KEY,
    day_plan_id TEXT NOT NULL,
    calendar_entry_id TEXT,
    item_id TEXT,
    reminder_id TEXT,
    routine_id TEXT,
    title TEXT NOT NULL,
    entry_type TEXT NOT NULL,
    intended_start TEXT,
    intended_end TEXT,
    expected_effort TEXT,
    support_tags TEXT,
    status TEXT NOT NULL DEFAULT 'planned',
    reality_state TEXT NOT NULL DEFAULT 'unknown',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(day_plan_id) REFERENCES day_plans(id)
);

CREATE INDEX IF NOT EXISTS idx_day_plan_entries_plan
    ON day_plan_entries(day_plan_id);
CREATE INDEX IF NOT EXISTS idx_day_plan_entries_calendar
    ON day_plan_entries(calendar_entry_id);
CREATE INDEX IF NOT EXISTS idx_day_plan_entries_item
    ON day_plan_entries(item_id);
"""
)


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:16]}"


def _now() -> datetime:
    return datetime.now(UTC)


class DayPlanService:
    """Creates and updates the current believable plan for a local date."""

    def __init__(
        self,
        db_path: Path,
        *,
        ledger: LifeEventLedger | None = None,
        domain_events: DomainEventStore | None = None,
    ) -> None:
        self._db_path = db_path
        self._domain_events = domain_events or DomainEventStore(db_path)
        self._ledger = ledger or LifeEventLedger(
            db_path, domain_events=self._domain_events
        )

    async def ensure_schema(self) -> None:
        async with aiosqlite.connect(str(self._db_path)) as db:
            await db.executescript(DAY_PLAN_SCHEMA_SQL)
            await _add_column_if_missing(db, "day_plan_entries", "reminder_id", "TEXT")
            await _add_column_if_missing(db, "day_plan_entries", "routine_id", "TEXT")
            await db.commit()

    async def create_or_refresh_day_plan(
        self, day: date, source: str = "conversation"
    ) -> DayPlan:
        """Create a new active day plan revision from current Life OS tables."""
        await self.ensure_schema()
        existing = await self.get_active_day_plan(day)
        now = _now()
        plan_id = _new_id("dp")
        revision = 1 if existing is None else existing.revision + 1
        entries = await self._collect_source_entries(day, plan_id, now)
        summary = _summarize_entries(entries)

        async with aiosqlite.connect(str(self._db_path)) as db:
            if existing is not None:
                await db.execute(
                    """
                    UPDATE day_plans
                    SET status = 'superseded', updated_at = ?
                    WHERE id = ?
                    """,
                    (now.isoformat(), existing.id),
                )
            await db.execute(
                """
                INSERT INTO day_plans
                    (id, plan_date, revision, status, supersedes_day_plan_id,
                     generated_from, load_assessment_id, summary, created_at,
                     updated_at)
                VALUES (?, ?, ?, 'active', ?, ?, NULL, ?, ?, ?)
                """,
                (
                    plan_id,
                    day.isoformat(),
                    revision,
                    existing.id if existing else None,
                    source,
                    summary,
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            for entry in entries:
                await _insert_day_plan_entry(db, entry)
            await db.commit()

        await self._domain_events.append(
            "DAY_PLAN_CREATED",
            aggregate_type="day_plan",
            aggregate_id=plan_id,
            source_service="DayPlanService",
            payload={
                "plan_date": day.isoformat(),
                "revision": revision,
                "source": source,
                "entry_count": len(entries),
                "supersedes_day_plan_id": existing.id if existing else None,
            },
        )
        await self._ledger.record_event(
            RecordLifeEventInput(
                event_type="day_plan_created",
                event_time=now,
                source="tool",
                title="Day plan created",
                details=summary,
                metadata={
                    "day_plan_id": plan_id,
                    "plan_date": day.isoformat(),
                    "revision": revision,
                    "entry_count": len(entries),
                },
            )
        )
        return DayPlan(
            id=plan_id,
            plan_date=day,
            revision=revision,
            status=DayPlanStatus.ACTIVE,
            supersedes_day_plan_id=existing.id if existing else None,
            generated_from=source,
            summary=summary,
            entries=entries,
            created_at=now,
            updated_at=now,
        )

    async def create_reduced_day_plan(self, payload: dict[str, object]) -> DayPlan:
        """Create a stabilization-sized plan from explicit reduced-plan entries."""

        await self.ensure_schema()
        now = _now()
        day = now.date()
        existing = await self.get_active_day_plan(day)
        plan_id = _new_id("dp")
        revision = 1 if existing is None else existing.revision + 1
        raw_entries = payload.get("entries") if isinstance(payload, dict) else None
        if not isinstance(raw_entries, list) or not raw_entries:
            raw_entries = [
                {"title": "Medication or health basics", "kind": "essential"},
                {"title": "Food and hydration", "kind": "essential"},
                {"title": "One required obligation", "kind": "fixed"},
                {"title": "One recovery action", "kind": "recovery"},
            ]

        entries: list[DayPlanEntry] = []
        for raw in raw_entries:
            if not isinstance(raw, dict):
                continue
            title = str(raw.get("title") or "").strip()
            if not title:
                continue
            entries.append(
                DayPlanEntry(
                    id=_new_id("dpe"),
                    day_plan_id=plan_id,
                    title=title,
                    entry_type=str(raw.get("kind") or "stabilization"),
                    support_tags=["stabilization", "low_energy"],
                    created_at=now,
                    updated_at=now,
                )
            )
        summary = _summarize_entries(entries)

        async with aiosqlite.connect(str(self._db_path)) as db:
            if existing is not None:
                await db.execute(
                    """
                    UPDATE day_plans
                    SET status = 'superseded', updated_at = ?
                    WHERE id = ?
                    """,
                    (now.isoformat(), existing.id),
                )
            await db.execute(
                """
                INSERT INTO day_plans
                    (id, plan_date, revision, status, supersedes_day_plan_id,
                     generated_from, load_assessment_id, summary, created_at,
                     updated_at)
                VALUES (?, ?, ?, 'active', ?, 'stabilization', NULL, ?, ?, ?)
                """,
                (
                    plan_id,
                    day.isoformat(),
                    revision,
                    existing.id if existing else None,
                    summary,
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            for entry in entries:
                await _insert_day_plan_entry(db, entry)
            await db.commit()

        await self._domain_events.append(
            "DAY_PLAN_CREATED",
            aggregate_type="day_plan",
            aggregate_id=plan_id,
            source_service="DayPlanService",
            payload={
                "plan_date": day.isoformat(),
                "revision": revision,
                "source": "stabilization",
                "entry_count": len(entries),
                "supersedes_day_plan_id": existing.id if existing else None,
            },
        )
        await self._ledger.record_event(
            RecordLifeEventInput(
                event_type="day_plan_created",
                event_time=now,
                source="tool",
                title="Reduced day plan created",
                details=summary,
                metadata={
                    "day_plan_id": plan_id,
                    "plan_date": day.isoformat(),
                    "revision": revision,
                    "entry_count": len(entries),
                    "mode": "stabilization",
                },
            )
        )
        return DayPlan(
            id=plan_id,
            plan_date=day,
            revision=revision,
            status=DayPlanStatus.ACTIVE,
            supersedes_day_plan_id=existing.id if existing else None,
            generated_from="stabilization",
            summary=summary,
            entries=entries,
            created_at=now,
            updated_at=now,
        )

    async def mark_entry_reality(
        self,
        entry_id: str,
        state: RealityState,
        source_event_id: str,
    ) -> DayPlanEntry:
        """Mark what really happened and mirror obvious source-record status."""
        if isinstance(state, str):
            state = RealityState(state)
        await self.ensure_schema()
        entry = await self._get_entry(entry_id)
        if entry is None:
            raise ValueError(f"day plan entry not found: {entry_id}")

        now = _now()
        status = _status_for_reality(state)
        async with aiosqlite.connect(str(self._db_path)) as db:
            await db.execute(
                """
                UPDATE day_plan_entries
                SET status = ?, reality_state = ?, updated_at = ?
                WHERE id = ?
                """,
                (status.value, state.value, now.isoformat(), entry_id),
            )
            await self._mirror_source_status(db, entry, status, now)
            await db.commit()

        updated = await self._get_entry(entry_id)
        if updated is None:
            raise RuntimeError(f"day plan entry disappeared after update: {entry_id}")

        await self._domain_events.append(
            "DAY_PLAN_ENTRY_REALITY_MARKED",
            aggregate_type="day_plan_entry",
            aggregate_id=entry_id,
            source_service="DayPlanService",
            causation_id=source_event_id,
            payload={
                "day_plan_id": updated.day_plan_id,
                "state": state.value,
                "status": status.value,
                "source_event_id": source_event_id,
                "calendar_entry_id": updated.calendar_entry_id,
                "item_id": updated.item_id,
            },
        )
        return updated

    async def get_active_day_plan(self, day: date) -> DayPlan | None:
        await self.ensure_schema()
        async with aiosqlite.connect(str(self._db_path)) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT * FROM day_plans
                WHERE plan_date = ? AND status = 'active'
                ORDER BY revision DESC
                LIMIT 1
                """,
                (day.isoformat(),),
            )
            plan_row = await cursor.fetchone()
            if plan_row is None:
                return None
            entries_cursor = await db.execute(
                """
                SELECT * FROM day_plan_entries
                WHERE day_plan_id = ?
                ORDER BY COALESCE(intended_start, created_at), created_at
                """,
                (plan_row["id"],),
            )
            entry_rows = await entries_cursor.fetchall()
        return _day_plan_from_rows(plan_row, entry_rows)

    async def stale_entries(
        self, day: date, now: datetime, *, grace: timedelta = timedelta(minutes=30)
    ) -> list[DayPlanEntry]:
        """Return planned/unknown entries whose intended end/start is behind now."""
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        plan = await self.get_active_day_plan(day)
        if plan is None:
            return []
        stale: list[DayPlanEntry] = []
        for entry in plan.entries:
            if entry.status not in {
                DayPlanEntryStatus.PLANNED,
                DayPlanEntryStatus.ACTIVE,
            }:
                continue
            if entry.reality_state != RealityState.UNKNOWN:
                continue
            cutoff = entry.intended_end or entry.intended_start
            if cutoff is not None and cutoff + grace < now:
                stale.append(entry)
        return stale

    async def _collect_source_entries(
        self, day: date, plan_id: str, now: datetime
    ) -> list[DayPlanEntry]:
        async with aiosqlite.connect(str(self._db_path)) as db:
            db.row_factory = aiosqlite.Row
            calendar = await _calendar_entries_for_day(db, day)
            reminders = await _reminders_for_day(db, day)
            items = await _items_for_day(db, day)
            routines = await _routines_for_day(db)

        entries: list[DayPlanEntry] = []
        seen_keys: set[tuple[str, str]] = set()

        def add(entry: DayPlanEntry, key: tuple[str, str]) -> None:
            if key not in seen_keys:
                seen_keys.add(key)
                entries.append(entry)

        for row in calendar:
            add(
                DayPlanEntry(
                    id=_new_id("dpe"),
                    day_plan_id=plan_id,
                    calendar_entry_id=row["id"],
                    title=row["title"],
                    entry_type=row["kind"] or "calendar",
                    intended_start=_parse_dt(row["starts_at"]),
                    intended_end=_parse_dt(row["ends_at"]),
                    support_tags=_support_tags_from_json(row["metadata"]),
                    created_at=now,
                    updated_at=now,
                ),
                ("calendar", row["id"]),
            )
        for row in reminders:
            due = _parse_dt(row["due_at"] or row["remind_at"])
            add(
                DayPlanEntry(
                    id=_new_id("dpe"),
                    day_plan_id=plan_id,
                    reminder_id=row["id"],
                    title=row["title"],
                    entry_type="reminder",
                    intended_start=due,
                    support_tags=["reminder"],
                    created_at=now,
                    updated_at=now,
                ),
                ("reminder", row["id"]),
            )
        for row in items:
            add(
                DayPlanEntry(
                    id=_new_id("dpe"),
                    day_plan_id=plan_id,
                    item_id=row["id"],
                    title=row["title"],
                    entry_type=row["type"] or "task",
                    expected_effort=(
                        str(row["estimated_minutes"])
                        if "estimated_minutes" in row.keys()
                        and row["estimated_minutes"] is not None
                        else None
                    ),
                    support_tags=_json_list(row["context_tags"])
                    if "context_tags" in row.keys()
                    else [],
                    created_at=now,
                    updated_at=now,
                ),
                ("item", row["id"]),
            )
        for row in routines:
            add(
                DayPlanEntry(
                    id=_new_id("dpe"),
                    day_plan_id=plan_id,
                    routine_id=row["id"],
                    title=row["name"],
                    entry_type="routine",
                    support_tags=_json_list(row["tags"]),
                    created_at=now,
                    updated_at=now,
                ),
                ("routine", row["id"]),
            )

        return sorted(
            entries,
            key=lambda e: (
                e.intended_start or datetime.max.replace(tzinfo=UTC),
                e.created_at,
                e.title.lower(),
            ),
        )

    async def _get_entry(self, entry_id: str) -> DayPlanEntry | None:
        async with aiosqlite.connect(str(self._db_path)) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM day_plan_entries WHERE id = ?",
                (entry_id,),
            )
            row = await cursor.fetchone()
        return _day_plan_entry_from_row(row) if row else None

    async def _mirror_source_status(
        self,
        db: aiosqlite.Connection,
        entry: DayPlanEntry,
        status: DayPlanEntryStatus,
        now: datetime,
    ) -> None:
        if entry.calendar_entry_id and await _table_exists(db, "calendar_entries"):
            calendar_status = {
                DayPlanEntryStatus.DONE: "completed",
                DayPlanEntryStatus.SKIPPED: "missed",
                DayPlanEntryStatus.BLOCKED: "missed",
            }.get(status)
            if calendar_status is not None:
                await db.execute(
                    """
                    UPDATE calendar_entries
                    SET status = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        calendar_status,
                        now.isoformat(),
                        entry.calendar_entry_id,
                    ),
                )
        if entry.item_id and await _table_exists(db, "items"):
            item_status = {
                DayPlanEntryStatus.DONE: "completed",
                DayPlanEntryStatus.PARTIAL: "in_progress",
                DayPlanEntryStatus.SKIPPED: "planned",
                DayPlanEntryStatus.BLOCKED: "blocked",
                DayPlanEntryStatus.DROPPED: "cancelled",
            }.get(status)
            if item_status is not None:
                await db.execute(
                    "UPDATE items SET status = ?, updated_at = ? WHERE id = ?",
                    (item_status, now.isoformat(), entry.item_id),
                )
        if entry.reminder_id and await _table_exists(db, "reminders"):
            reminder_status = {
                DayPlanEntryStatus.DONE: "completed",
                DayPlanEntryStatus.SKIPPED: "dismissed",
            }.get(status)
            if reminder_status is not None:
                await db.execute(
                    "UPDATE reminders SET status = ? WHERE id = ?",
                    (reminder_status, entry.reminder_id),
                )


async def _insert_day_plan_entry(
    db: aiosqlite.Connection, entry: DayPlanEntry
) -> None:
    await db.execute(
        """
        INSERT INTO day_plan_entries
            (id, day_plan_id, calendar_entry_id, item_id, reminder_id,
             routine_id, title, entry_type, intended_start, intended_end,
             expected_effort, support_tags, status, reality_state, created_at,
             updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            entry.id,
            entry.day_plan_id,
            entry.calendar_entry_id,
            entry.item_id,
            entry.reminder_id,
            entry.routine_id,
            entry.title,
            entry.entry_type,
            entry.intended_start.isoformat() if entry.intended_start else None,
            entry.intended_end.isoformat() if entry.intended_end else None,
            entry.expected_effort,
            json.dumps(entry.support_tags),
            entry.status.value,
            entry.reality_state.value,
            entry.created_at.isoformat(),
            entry.updated_at.isoformat(),
        ),
    )


async def _calendar_entries_for_day(
    db: aiosqlite.Connection, day: date
) -> list[aiosqlite.Row]:
    if not await _table_exists(db, "calendar_entries"):
        return []
    start, end = _day_bounds(day)
    cursor = await db.execute(
        """
        SELECT * FROM calendar_entries
        WHERE status != 'cancelled'
          AND starts_at >= ?
          AND starts_at <= ?
          AND (
              kind != 'buffer'
              OR title != 'Transition buffer'
              OR id IN (
                  SELECT id FROM calendar_entries
                  WHERE status != 'cancelled'
                    AND kind = 'buffer'
                    AND title = 'Transition buffer'
                    AND starts_at >= ?
                    AND starts_at <= ?
                  ORDER BY starts_at ASC
                  LIMIT 3
              )
          )
        ORDER BY starts_at ASC
        """,
        (start.isoformat(), end.isoformat(), start.isoformat(), end.isoformat()),
    )
    return await cursor.fetchall()


async def _reminders_for_day(
    db: aiosqlite.Connection, day: date
) -> list[aiosqlite.Row]:
    if not await _table_exists(db, "reminders"):
        return []
    columns = await _columns(db, "reminders")
    date_expr = "due_at" if "due_at" in columns else "remind_at"
    if date_expr not in columns:
        return []
    start, end = _day_bounds(day)
    cursor = await db.execute(
        f"""
        SELECT * FROM reminders
        WHERE status IN ('pending', 'delivered')
          AND {date_expr} >= ?
          AND {date_expr} <= ?
        ORDER BY {date_expr} ASC
        """,
        (start.isoformat(), end.isoformat()),
    )
    return await cursor.fetchall()


async def _items_for_day(
    db: aiosqlite.Connection, day: date
) -> list[aiosqlite.Row]:
    if not await _table_exists(db, "items"):
        return []
    columns = await _columns(db, "items")
    status_filter = "status NOT IN ('completed', 'cancelled', 'dropped')"
    if "due_at" in columns:
        start, end = _day_bounds(day)
        query = f"""
            SELECT * FROM items
            WHERE {status_filter}
              AND (due_at IS NULL OR (due_at >= ? AND due_at <= ?))
            ORDER BY COALESCE(due_at, created_at) ASC
        """
        params: tuple[str, ...] = (start.isoformat(), end.isoformat())
    else:
        query = f"""
            SELECT * FROM items
            WHERE {status_filter}
            ORDER BY created_at ASC
        """
        params = ()
    cursor = await db.execute(query, params)
    return await cursor.fetchall()


async def _routines_for_day(db: aiosqlite.Connection) -> list[aiosqlite.Row]:
    if not await _table_exists(db, "routines"):
        return []
    cursor = await db.execute("SELECT * FROM routines ORDER BY name ASC")
    return await cursor.fetchall()


async def _table_exists(db: aiosqlite.Connection, table: str) -> bool:
    cursor = await db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
        (table,),
    )
    return await cursor.fetchone() is not None


async def _columns(db: aiosqlite.Connection, table: str) -> set[str]:
    cursor = await db.execute(f"PRAGMA table_info({table})")
    rows = await cursor.fetchall()
    return {row[1] for row in rows}


async def _add_column_if_missing(
    db: aiosqlite.Connection, table: str, column: str, definition: str
) -> None:
    columns = await _columns(db, table)
    if column not in columns:
        await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    return (
        datetime.combine(day, time.min, tzinfo=UTC),
        datetime.combine(day, time.max, tzinfo=UTC),
    )


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _json_list(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    if isinstance(parsed, list):
        return [str(item) for item in parsed]
    return []


def _support_tags_from_json(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    if isinstance(parsed, dict):
        tags = parsed.get("support_tags") or parsed.get("tags") or []
        if isinstance(tags, list):
            return [str(tag) for tag in tags]
    return []


def _status_for_reality(state: RealityState) -> DayPlanEntryStatus:
    return {
        RealityState.CONFIRMED_DONE: DayPlanEntryStatus.DONE,
        RealityState.INFERRED_DONE: DayPlanEntryStatus.DONE,
        RealityState.CONFIRMED_PARTIAL: DayPlanEntryStatus.PARTIAL,
        RealityState.CONFIRMED_SKIPPED: DayPlanEntryStatus.SKIPPED,
        RealityState.CONFIRMED_BLOCKED: DayPlanEntryStatus.BLOCKED,
        RealityState.REJECTED_INFERENCE: DayPlanEntryStatus.PLANNED,
        RealityState.NEEDS_CONFIRMATION: DayPlanEntryStatus.ACTIVE,
        RealityState.UNKNOWN: DayPlanEntryStatus.PLANNED,
    }[state]


def _summarize_entries(entries: list[DayPlanEntry]) -> str:
    if not entries:
        return "No planned entries found for this day."
    by_type: dict[str, int] = {}
    for entry in entries:
        by_type[entry.entry_type] = by_type.get(entry.entry_type, 0) + 1
    parts = [f"{count} {kind}" for kind, count in sorted(by_type.items())]
    return f"Planned {len(entries)} entries: {', '.join(parts)}."


def _day_plan_entry_from_row(row: aiosqlite.Row) -> DayPlanEntry:
    return DayPlanEntry(
        id=row["id"],
        day_plan_id=row["day_plan_id"],
        calendar_entry_id=row["calendar_entry_id"],
        item_id=row["item_id"],
        reminder_id=row["reminder_id"] if "reminder_id" in row.keys() else None,
        routine_id=row["routine_id"] if "routine_id" in row.keys() else None,
        title=row["title"],
        entry_type=row["entry_type"],
        intended_start=_parse_dt(row["intended_start"]),
        intended_end=_parse_dt(row["intended_end"]),
        expected_effort=row["expected_effort"],
        support_tags=_json_list(row["support_tags"]),
        status=DayPlanEntryStatus(row["status"]),
        reality_state=RealityState(row["reality_state"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def _day_plan_from_rows(
    plan_row: aiosqlite.Row, entry_rows: list[aiosqlite.Row]
) -> DayPlan:
    return DayPlan(
        id=plan_row["id"],
        plan_date=date.fromisoformat(plan_row["plan_date"]),
        revision=plan_row["revision"],
        status=DayPlanStatus(plan_row["status"]),
        supersedes_day_plan_id=plan_row["supersedes_day_plan_id"],
        generated_from=plan_row["generated_from"],
        load_assessment_id=plan_row["load_assessment_id"],
        summary=plan_row["summary"],
        entries=[_day_plan_entry_from_row(row) for row in entry_rows],
        created_at=datetime.fromisoformat(plan_row["created_at"]),
        updated_at=datetime.fromisoformat(plan_row["updated_at"]),
    )
