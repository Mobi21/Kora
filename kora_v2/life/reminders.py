"""Reminder subsystem -- Phase 8e.

Provides ``ReminderStore`` for creating, querying, and managing
time-based reminders. Used by the ``continuity_check`` pipeline to
poll for due reminders and by life management tools to create them.

The underlying table is ``reminders`` in ``operational.db``, extended
with Phase 8e columns (``due_at``, ``repeat_rule``, ``source``,
``delivered_at``, ``dismissed_at``, ``metadata``).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import aiosqlite
import structlog
from pydantic import BaseModel

log = structlog.get_logger(__name__)


# -- Models ------------------------------------------------------------------


class Reminder(BaseModel):
    """A single reminder entry."""

    id: str
    title: str
    description: str = ""
    due_at: datetime
    repeat_rule: str | None = None  # "daily", "weekly", etc.
    source: str = "user"  # user, routine, medication, calendar
    status: str = "pending"  # pending, delivered, dismissed
    delivered_at: datetime | None = None
    metadata: dict[str, Any] = {}


# -- Store -------------------------------------------------------------------


class ReminderStore:
    """Manages reminders in the operational database.

    All methods open their own connection so callers do not need to
    manage connection lifecycle.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    async def create_reminder(
        self,
        title: str,
        description: str = "",
        due_at: datetime | None = None,
        repeat_rule: str | None = None,
        source: str = "user",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Create a new reminder. Returns the reminder ID."""
        reminder_id = f"rem-{uuid.uuid4().hex[:12]}"
        now = datetime.now(UTC)
        if due_at is None:
            due_at = now

        async with aiosqlite.connect(str(self._db_path)) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute(
                "INSERT INTO reminders "
                "(id, title, description, due_at, repeat_rule, source, "
                " status, metadata, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
                (
                    reminder_id,
                    title,
                    description,
                    due_at.isoformat(),
                    repeat_rule,
                    source,
                    json.dumps(metadata or {}),
                    now.isoformat(),
                ),
            )
            await db.commit()

        log.info(
            "reminder.created",
            reminder_id=reminder_id,
            title=title,
            due_at=due_at.isoformat(),
            source=source,
        )
        return reminder_id

    async def get_due_reminders(
        self,
        window: timedelta = timedelta(minutes=15),
        look_back: timedelta = timedelta(hours=2),
    ) -> list[Reminder]:
        """Return pending reminders due within *window* of now.

        Reminders older than *look_back* are considered stale and are
        excluded from the result. This prevents burst-delivery on outage
        recovery: if the daemon was down for a day, every accumulated
        past-due reminder would otherwise fire at once.

        Callers that need to surface or clean up stale reminders should
        query separately (e.g. via :meth:`get_pending`) and decide what
        to do with them.

        Args:
            window: Future window — return items due up to this far ahead.
            look_back: Past window — exclude items older than now -
                ``look_back``. Pass a very large timedelta to disable.
        """
        now = datetime.now(UTC)
        future_cutoff = (now + window).isoformat()
        past_cutoff = (now - look_back).isoformat()

        async with aiosqlite.connect(str(self._db_path)) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT id, title, description, due_at, repeat_rule, "
                "       source, status, delivered_at, metadata "
                "FROM reminders "
                "WHERE status = 'pending' "
                "  AND due_at <= ? "
                "  AND due_at >= ? "
                "ORDER BY due_at ASC",
                (future_cutoff, past_cutoff),
            )
            rows = await cursor.fetchall()

        return [_reminder_from_row(row) for row in rows]

    async def mark_delivered(self, reminder_id: str) -> None:
        """Mark a reminder as delivered."""
        now = datetime.now(UTC).isoformat()
        async with aiosqlite.connect(str(self._db_path)) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute(
                "UPDATE reminders SET status = 'delivered', delivered_at = ? "
                "WHERE id = ?",
                (now, reminder_id),
            )
            await db.commit()
        log.debug("reminder.delivered", reminder_id=reminder_id)

    async def mark_dismissed(self, reminder_id: str) -> None:
        """Mark a reminder as dismissed."""
        now = datetime.now(UTC).isoformat()
        async with aiosqlite.connect(str(self._db_path)) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute(
                "UPDATE reminders SET status = 'dismissed', dismissed_at = ? "
                "WHERE id = ?",
                (now, reminder_id),
            )
            await db.commit()
        log.debug("reminder.dismissed", reminder_id=reminder_id)

    async def get_pending(self) -> list[Reminder]:
        """Return all pending reminders regardless of due time."""
        async with aiosqlite.connect(str(self._db_path)) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT id, title, description, due_at, repeat_rule, "
                "       source, status, delivered_at, metadata "
                "FROM reminders "
                "WHERE status = 'pending' "
                "ORDER BY due_at ASC",
            )
            rows = await cursor.fetchall()

        return [_reminder_from_row(row) for row in rows]

    async def reschedule_recurring(self, reminder_id: str) -> str | None:
        """If a reminder has a repeat_rule, create the next occurrence.

        Returns the new reminder ID or None if not recurring.
        """
        async with aiosqlite.connect(str(self._db_path)) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT id, title, description, due_at, repeat_rule, "
                "       source, metadata "
                "FROM reminders WHERE id = ?",
                (reminder_id,),
            )
            row = await cursor.fetchone()

        if row is None or not row["repeat_rule"]:
            return None

        old_due = datetime.fromisoformat(row["due_at"])
        rule = row["repeat_rule"]
        delta = _repeat_delta(rule)
        if delta is None:
            return None

        new_due = old_due + delta
        metadata = json.loads(row["metadata"] or "{}") if row["metadata"] else {}

        return await self.create_reminder(
            title=row["title"],
            description=row["description"] or "",
            due_at=new_due,
            repeat_rule=rule,
            source=row["source"] or "user",
            metadata=metadata,
        )

    async def deliver_and_reschedule(
        self, reminder_id: str
    ) -> str | None:
        """Atomically mark a reminder delivered and schedule its next run.

        Both operations execute in a single transaction on a single
        connection so a process crash cannot leave a recurring reminder
        delivered-but-never-rescheduled (which would silently drop the
        next occurrence).

        Returns:
            The new reminder ID for recurring reminders, or ``None`` for
            one-shot reminders (or unknown reminders / unmapped repeat
            rules).
        """
        now = datetime.now(UTC)
        new_id: str | None = None

        async with aiosqlite.connect(str(self._db_path)) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            db.row_factory = aiosqlite.Row

            # Fetch the current row first.
            cursor = await db.execute(
                "SELECT id, title, description, due_at, repeat_rule, "
                "       source, metadata "
                "FROM reminders WHERE id = ?",
                (reminder_id,),
            )
            row = await cursor.fetchone()

            if row is None:
                # Nothing to do; commit the no-op so we exit cleanly.
                await db.commit()
                return None

            # Mark delivered.
            await db.execute(
                "UPDATE reminders SET status = 'delivered', "
                "delivered_at = ? WHERE id = ?",
                (now.isoformat(), reminder_id),
            )

            # Schedule the next occurrence in the same transaction.
            rule = row["repeat_rule"]
            if rule:
                delta = _repeat_delta(rule)
                if delta is not None:
                    old_due = datetime.fromisoformat(row["due_at"])
                    new_due = old_due + delta
                    new_id = f"rem-{uuid.uuid4().hex[:12]}"
                    metadata_raw = row["metadata"] or "{}"
                    await db.execute(
                        "INSERT INTO reminders "
                        "(id, title, description, due_at, repeat_rule, "
                        " source, status, metadata, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
                        (
                            new_id,
                            row["title"],
                            row["description"] or "",
                            new_due.isoformat(),
                            rule,
                            row["source"] or "user",
                            metadata_raw,
                            now.isoformat(),
                        ),
                    )

            await db.commit()

        log.debug(
            "reminder.delivered_and_rescheduled",
            reminder_id=reminder_id,
            new_id=new_id,
        )
        return new_id


# -- Helpers -----------------------------------------------------------------


def _repeat_delta(rule: str) -> timedelta | None:
    """Convert a repeat_rule string to a timedelta."""
    mapping = {
        "daily": timedelta(days=1),
        "weekly": timedelta(weeks=1),
        "biweekly": timedelta(weeks=2),
        "monthly": timedelta(days=30),
        "hourly": timedelta(hours=1),
    }
    return mapping.get(rule.lower())


def _reminder_from_row(row: aiosqlite.Row) -> Reminder:
    """Convert a database row to a Reminder model."""
    due_at_raw = row["due_at"]
    due_at = datetime.fromisoformat(due_at_raw) if due_at_raw else datetime.now(UTC)
    if due_at.tzinfo is None:
        due_at = due_at.replace(tzinfo=UTC)

    delivered_at: datetime | None = None
    delivered_raw = row["delivered_at"]
    if delivered_raw:
        delivered_at = datetime.fromisoformat(delivered_raw)
        if delivered_at.tzinfo is None:
            delivered_at = delivered_at.replace(tzinfo=UTC)

    metadata_raw = row["metadata"]
    metadata: dict[str, Any] = {}
    if metadata_raw:
        try:
            metadata = json.loads(metadata_raw)
        except (json.JSONDecodeError, TypeError):
            pass

    return Reminder(
        id=row["id"],
        title=row["title"],
        description=row["description"] or "",
        due_at=due_at,
        repeat_rule=row["repeat_rule"],
        source=row["source"] or "user",
        status=row["status"],
        delivered_at=delivered_at,
        metadata=metadata,
    )
