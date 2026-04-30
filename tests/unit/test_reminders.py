"""Unit tests for the Reminder subsystem -- Phase 8e.

Tests for:
- ReminderStore.create_reminder persists rows correctly
- get_due_reminders returns due items in order
- get_due_reminders excludes future items outside the window
- mark_delivered / mark_dismissed update status
- reschedule_recurring creates next occurrence (daily/weekly)
- reschedule_recurring returns None for non-recurring
- get_pending returns all pending regardless of due time
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

# pysqlite3 monkey-patch
try:
    import pysqlite3 as _pysqlite3  # type: ignore[import-untyped]

    sys.modules["sqlite3"] = _pysqlite3
except ImportError:
    pass

import aiosqlite
import pytest

from kora_v2.core.db import init_operational_db
from kora_v2.life.reminders import Reminder, ReminderStore


async def _make_store(tmp_path: Path) -> tuple[ReminderStore, Path]:
    db_path = tmp_path / "operational.db"
    await init_operational_db(db_path)
    return ReminderStore(db_path), db_path


class TestCreateReminder:
    async def test_persists_to_db(self, tmp_path: Path) -> None:
        store, db_path = await _make_store(tmp_path)
        due = datetime.now(UTC) + timedelta(hours=1)

        rid = await store.create_reminder(
            title="Take meds",
            description="Morning dose",
            due_at=due,
            source="medication",
        )

        assert rid.startswith("rem-")

        async with aiosqlite.connect(str(db_path)) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT title, description, due_at, source, status "
                "FROM reminders WHERE id = ?",
                (rid,),
            )
            row = await cursor.fetchone()

        assert row is not None
        assert row["title"] == "Take meds"
        assert row["description"] == "Morning dose"
        assert row["source"] == "medication"
        assert row["status"] == "pending"

    async def test_default_due_at_is_now(self, tmp_path: Path) -> None:
        store, _db = await _make_store(tmp_path)
        rid = await store.create_reminder(title="Check in")

        pending = await store.get_pending()
        match = next((r for r in pending if r.id == rid), None)
        assert match is not None
        # Should be roughly "now" (within a minute)
        now = datetime.now(UTC)
        assert abs((match.due_at - now).total_seconds()) < 60


class TestGetDueReminders:
    async def test_returns_due_items_in_order(self, tmp_path: Path) -> None:
        store, _db = await _make_store(tmp_path)
        now = datetime.now(UTC)

        # Create 3 reminders with different due times (all within window)
        r_late = await store.create_reminder(
            title="Late", due_at=now + timedelta(minutes=10)
        )
        r_early = await store.create_reminder(
            title="Early", due_at=now - timedelta(minutes=5)
        )
        r_mid = await store.create_reminder(
            title="Mid", due_at=now + timedelta(minutes=5)
        )

        due = await store.get_due_reminders(window=timedelta(minutes=15))

        ids = [r.id for r in due]
        assert r_early in ids
        assert r_mid in ids
        assert r_late in ids
        # Should be ordered by due_at ASC
        assert ids.index(r_early) < ids.index(r_mid)
        assert ids.index(r_mid) < ids.index(r_late)

    async def test_excludes_future_items_outside_window(
        self, tmp_path: Path
    ) -> None:
        store, _db = await _make_store(tmp_path)
        now = datetime.now(UTC)

        r_soon = await store.create_reminder(
            title="Soon", due_at=now + timedelta(minutes=5)
        )
        _r_far = await store.create_reminder(
            title="Far", due_at=now + timedelta(hours=5)
        )

        due = await store.get_due_reminders(window=timedelta(minutes=15))
        ids = [r.id for r in due]
        assert r_soon in ids
        # Far-future item must NOT appear
        for r in due:
            if r.title == "Far":
                raise AssertionError(
                    f"Far-future reminder {r.id} should not be returned"
                )

    async def test_excludes_non_pending(self, tmp_path: Path) -> None:
        store, _db = await _make_store(tmp_path)
        now = datetime.now(UTC)

        rid = await store.create_reminder(
            title="Delivered", due_at=now - timedelta(minutes=1)
        )
        await store.mark_delivered(rid)

        due = await store.get_due_reminders()
        assert rid not in [r.id for r in due]

    async def test_excludes_stale_reminders_outside_look_back(
        self, tmp_path: Path
    ) -> None:
        """Reminders older than ``look_back`` must not fire on outage recovery."""
        store, _db = await _make_store(tmp_path)
        now = datetime.now(UTC)

        # Two reminders: one fresh-overdue, one ancient (3 hours ago)
        rid_fresh = await store.create_reminder(
            title="Fresh", due_at=now - timedelta(minutes=10)
        )
        rid_stale = await store.create_reminder(
            title="Stale", due_at=now - timedelta(hours=3)
        )

        # Default look_back = 2h, so the stale one must be excluded
        due = await store.get_due_reminders()
        ids = [r.id for r in due]
        assert rid_fresh in ids
        assert rid_stale not in ids

        # A wider look_back should include the stale one
        due_wide = await store.get_due_reminders(
            look_back=timedelta(days=1)
        )
        ids_wide = [r.id for r in due_wide]
        assert rid_stale in ids_wide


class TestMarkDeliveredDismissed:
    async def test_mark_delivered_updates_status(self, tmp_path: Path) -> None:
        store, db_path = await _make_store(tmp_path)
        rid = await store.create_reminder(title="Ping")

        await store.mark_delivered(rid)

        async with aiosqlite.connect(str(db_path)) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT status, delivered_at FROM reminders WHERE id = ?",
                (rid,),
            )
            row = await cursor.fetchone()

        assert row is not None
        assert row["status"] == "delivered"
        assert row["delivered_at"] is not None

    async def test_mark_delivered_uses_due_time_in_acceptance_mode(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        store, db_path = await _make_store(tmp_path)
        due_at = datetime.now(UTC) - timedelta(hours=3)
        rid = await store.create_reminder(title="Simulated due", due_at=due_at)
        monkeypatch.setenv("KORA_ACCEPTANCE_DIR", str(tmp_path / "acceptance"))

        await store.mark_delivered(rid)

        async with aiosqlite.connect(str(db_path)) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT delivered_at FROM reminders WHERE id = ?",
                (rid,),
            )
            row = await cursor.fetchone()

        assert row is not None
        assert datetime.fromisoformat(row["delivered_at"]) == due_at

    async def test_mark_dismissed_updates_status(self, tmp_path: Path) -> None:
        store, db_path = await _make_store(tmp_path)
        rid = await store.create_reminder(title="Ping")

        await store.mark_dismissed(rid)

        async with aiosqlite.connect(str(db_path)) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT status, dismissed_at FROM reminders WHERE id = ?",
                (rid,),
            )
            row = await cursor.fetchone()

        assert row is not None
        assert row["status"] == "dismissed"
        assert row["dismissed_at"] is not None


class TestRescheduleRecurring:
    async def test_daily_reschedules_next_day(self, tmp_path: Path) -> None:
        store, _db = await _make_store(tmp_path)
        now = datetime.now(UTC)
        original_due = now - timedelta(minutes=1)

        rid = await store.create_reminder(
            title="Daily dose",
            due_at=original_due,
            repeat_rule="daily",
            source="medication",
        )

        new_rid = await store.reschedule_recurring(rid)
        assert new_rid is not None
        assert new_rid != rid

        # Fetch the new reminder
        pending = await store.get_pending()
        new_reminder = next((r for r in pending if r.id == new_rid), None)
        assert new_reminder is not None
        # New due time should be ~1 day after original due
        delta = new_reminder.due_at - original_due
        assert abs(delta - timedelta(days=1)) < timedelta(seconds=5)
        assert new_reminder.repeat_rule == "daily"
        assert new_reminder.source == "medication"
        assert new_reminder.title == "Daily dose"

    async def test_weekly_reschedules_next_week(
        self, tmp_path: Path
    ) -> None:
        store, _db = await _make_store(tmp_path)
        original_due = datetime.now(UTC) - timedelta(hours=1)

        rid = await store.create_reminder(
            title="Weekly check",
            due_at=original_due,
            repeat_rule="weekly",
        )

        new_rid = await store.reschedule_recurring(rid)
        assert new_rid is not None
        pending = await store.get_pending()
        new_reminder = next((r for r in pending if r.id == new_rid), None)
        assert new_reminder is not None
        delta = new_reminder.due_at - original_due
        assert abs(delta - timedelta(weeks=1)) < timedelta(seconds=5)

    async def test_no_repeat_rule_returns_none(
        self, tmp_path: Path
    ) -> None:
        store, _db = await _make_store(tmp_path)
        rid = await store.create_reminder(
            title="One-off",
            due_at=datetime.now(UTC),
            repeat_rule=None,
        )

        new_rid = await store.reschedule_recurring(rid)
        assert new_rid is None

    async def test_unknown_rule_returns_none(
        self, tmp_path: Path
    ) -> None:
        store, _db = await _make_store(tmp_path)
        rid = await store.create_reminder(
            title="Mystery cadence",
            due_at=datetime.now(UTC),
            repeat_rule="quarterly",  # not mapped
        )

        new_rid = await store.reschedule_recurring(rid)
        assert new_rid is None

    async def test_missing_reminder_returns_none(
        self, tmp_path: Path
    ) -> None:
        store, _db = await _make_store(tmp_path)
        new_rid = await store.reschedule_recurring("rem-does-not-exist")
        assert new_rid is None


class TestDeliverAndReschedule:
    async def test_atomic_delivery_and_reschedule_recurring(
        self, tmp_path: Path
    ) -> None:
        store, db_path = await _make_store(tmp_path)
        original_due = datetime.now(UTC) - timedelta(minutes=1)

        rid = await store.create_reminder(
            title="Daily vitamins",
            due_at=original_due,
            repeat_rule="daily",
            source="routine",
        )

        new_id = await store.deliver_and_reschedule(rid)
        assert new_id is not None
        assert new_id != rid

        # Original should be delivered
        async with aiosqlite.connect(str(db_path)) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT status, delivered_at FROM reminders WHERE id = ?",
                (rid,),
            )
            row = await cursor.fetchone()
        assert row is not None
        assert row["status"] == "delivered"
        assert row["delivered_at"] is not None

        # New occurrence should exist with delta == 1 day
        pending = await store.get_pending()
        new_reminder = next((r for r in pending if r.id == new_id), None)
        assert new_reminder is not None
        delta = new_reminder.due_at - original_due
        assert abs(delta - timedelta(days=1)) < timedelta(seconds=5)
        assert new_reminder.repeat_rule == "daily"
        assert new_reminder.source == "routine"

    async def test_one_shot_marks_delivered_and_returns_none(
        self, tmp_path: Path
    ) -> None:
        store, db_path = await _make_store(tmp_path)
        rid = await store.create_reminder(
            title="One-off", due_at=datetime.now(UTC) - timedelta(minutes=1)
        )

        new_id = await store.deliver_and_reschedule(rid)
        assert new_id is None

        async with aiosqlite.connect(str(db_path)) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT status FROM reminders WHERE id = ?",
                (rid,),
            )
            row = await cursor.fetchone()
        assert row is not None
        assert row["status"] == "delivered"

    async def test_missing_reminder_returns_none(
        self, tmp_path: Path
    ) -> None:
        store, _db = await _make_store(tmp_path)
        new_id = await store.deliver_and_reschedule("rem-missing")
        assert new_id is None


class TestGetPending:
    async def test_returns_all_pending(self, tmp_path: Path) -> None:
        store, _db = await _make_store(tmp_path)
        now = datetime.now(UTC)

        rid_near = await store.create_reminder(
            title="Near", due_at=now + timedelta(minutes=10)
        )
        rid_far = await store.create_reminder(
            title="Far", due_at=now + timedelta(days=2)
        )
        rid_delivered = await store.create_reminder(
            title="Gone", due_at=now + timedelta(minutes=5)
        )
        await store.mark_delivered(rid_delivered)

        pending = await store.get_pending()
        ids = {r.id for r in pending}
        assert rid_near in ids
        assert rid_far in ids
        assert rid_delivered not in ids


class TestReminderModel:
    async def test_round_trip_metadata(self, tmp_path: Path) -> None:
        store, _db = await _make_store(tmp_path)
        rid = await store.create_reminder(
            title="With meta",
            metadata={"routine_id": "abc", "urgency": "high"},
        )

        pending = await store.get_pending()
        reminder = next(r for r in pending if r.id == rid)
        assert isinstance(reminder, Reminder)
        assert reminder.metadata == {"routine_id": "abc", "urgency": "high"}
