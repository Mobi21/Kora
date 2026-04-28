from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import aiosqlite
import pytest

from kora_v2.core.db import init_operational_db
from kora_v2.life.day_plan import DayPlanService
from kora_v2.life.domain_events import DomainEventStore
from kora_v2.life.ledger import LifeEventLedger
from kora_v2.life.models import (
    ConfirmationState,
    CorrectionInput,
    DayPlanEntryStatus,
    LifeEventSource,
    RealityState,
    RecordLifeEventInput,
)


async def _make_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "operational.db"
    await init_operational_db(db_path)
    service = DayPlanService(db_path)
    await service.ensure_schema()
    return db_path


async def _seed_sources(db_path: Path, day: date) -> None:
    start = datetime.combine(day, datetime.min.time(), tzinfo=UTC)
    async with aiosqlite.connect(str(db_path)) as db:
        await db.execute(
            """
            INSERT INTO calendar_entries
                (id, kind, title, starts_at, ends_at, source, metadata,
                 status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'kora', ?, 'active', ?, ?)
            """,
            (
                "cal-meds",
                "medication_window",
                "Morning meds",
                (start + timedelta(hours=8)).isoformat(),
                (start + timedelta(hours=8, minutes=30)).isoformat(),
                json.dumps({"support_tags": ["medication", "life-maintenance"]}),
                start.isoformat(),
                start.isoformat(),
            ),
        )
        await db.execute(
            """
            INSERT INTO reminders
                (id, title, description, remind_at, recurring, status,
                 session_id, created_at, due_at, repeat_rule, source, metadata)
            VALUES (?, ?, '', ?, '', 'pending', NULL, ?, ?, NULL, 'user', ?)
            """,
            (
                "rem-breakfast",
                "Eat breakfast",
                (start + timedelta(hours=9)).isoformat(),
                start.isoformat(),
                (start + timedelta(hours=9)).isoformat(),
                json.dumps({"support_tags": ["meal"]}),
            ),
        )
        await db.execute(
            """
            INSERT INTO items
                (id, type, owner, title, status, estimated_minutes,
                 context_tags, created_at, updated_at)
            VALUES (?, 'task', 'user', ?, 'planned', 20, ?, ?, ?)
            """,
            (
                "item-form",
                "Finish benefits form",
                json.dumps(["admin", "anxiety-prone"]),
                start.isoformat(),
                start.isoformat(),
            ),
        )
        await db.execute(
            """
            INSERT INTO routines
                (id, name, description, steps_json, low_energy_variant_json,
                 tags, created_at, updated_at)
            VALUES (?, ?, '', '[]', NULL, ?, ?, ?)
            """,
            (
                "routine-shutdown",
                "Evening shutdown",
                json.dumps(["evening"]),
                start.isoformat(),
                start.isoformat(),
            ),
        )
        await db.commit()


@pytest.mark.asyncio
async def test_create_day_plan_from_existing_life_sources(tmp_path: Path) -> None:
    db_path = await _make_db(tmp_path)
    today = date(2026, 4, 28)
    await _seed_sources(db_path, today)

    service = DayPlanService(db_path)
    plan = await service.create_or_refresh_day_plan(today, source="unit_test")

    assert plan.revision == 1
    assert len(plan.entries) == 4
    assert {entry.title for entry in plan.entries} == {
        "Morning meds",
        "Eat breakfast",
        "Finish benefits form",
        "Evening shutdown",
    }

    events = await DomainEventStore(db_path).list_events(
        aggregate_type="day_plan",
        aggregate_id=plan.id,
    )
    assert [event.event_type for event in events] == ["DAY_PLAN_CREATED"]

    ledger_events = await LifeEventLedger(db_path).events_for_day(today)
    assert any(event.event_type == "day_plan_created" for event in ledger_events)


@pytest.mark.asyncio
async def test_refresh_supersedes_active_revision(tmp_path: Path) -> None:
    db_path = await _make_db(tmp_path)
    today = date(2026, 4, 28)
    await _seed_sources(db_path, today)

    service = DayPlanService(db_path)
    first = await service.create_or_refresh_day_plan(today, source="first")
    second = await service.create_or_refresh_day_plan(today, source="refresh")

    assert second.revision == 2
    assert second.supersedes_day_plan_id == first.id

    async with aiosqlite.connect(str(db_path)) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT id, status FROM day_plans WHERE plan_date = ?",
            (today.isoformat(),),
        )
        rows = await cursor.fetchall()

    by_id = {row["id"]: row["status"] for row in rows}
    assert by_id[first.id] == "superseded"
    assert by_id[second.id] == "active"


@pytest.mark.asyncio
async def test_life_event_confirm_and_correct_preserve_history(
    tmp_path: Path,
) -> None:
    db_path = await _make_db(tmp_path)
    ledger = LifeEventLedger(db_path)

    inferred = await ledger.record(
        RecordLifeEventInput(
            event_type="task_completed",
            event_time=datetime(2026, 4, 28, 12, tzinfo=UTC),
            source=LifeEventSource.ASSISTANT_INFERRED,
            confirmation_state=ConfirmationState.NEEDS_CONFIRMATION,
            title="Finish benefits form",
            raw_text="looks done",
            metadata={"guess": True},
        )
    )
    corrected = await ledger.correct(
        inferred.id,
        CorrectionInput(
            event_type="task_partial",
            details="Only half the form is done.",
            raw_text="I only did half the form",
        ),
    )

    assert corrected.supersedes_event_id == inferred.id
    assert corrected.confirmation_state == ConfirmationState.CORRECTED

    original_after = await ledger.get_event(inferred.id)
    assert original_after is not None
    assert original_after.confirmation_state == ConfirmationState.REJECTED

    events = await DomainEventStore(db_path).list_events(
        aggregate_type="life_event"
    )
    assert [event.event_type for event in events] == [
        "LIFE_EVENT_RECORDED",
        "LIFE_EVENT_RECORDED",
    ]


@pytest.mark.asyncio
async def test_mark_entry_reality_updates_plan_source_and_domain_event(
    tmp_path: Path,
) -> None:
    db_path = await _make_db(tmp_path)
    today = date(2026, 4, 28)
    await _seed_sources(db_path, today)
    service = DayPlanService(db_path)
    plan = await service.create_or_refresh_day_plan(today, source="unit_test")
    med_entry = next(entry for entry in plan.entries if entry.title == "Morning meds")

    life_event = await LifeEventLedger(db_path).record(
        RecordLifeEventInput(
            event_type="medication_taken",
            event_time=datetime(2026, 4, 28, 8, 5, tzinfo=UTC),
            source=LifeEventSource.USER_CONFIRMED,
            day_plan_entry_id=med_entry.id,
            calendar_entry_id=med_entry.calendar_entry_id,
            title="Morning meds taken",
            raw_text="I took my meds",
        )
    )
    updated = await service.mark_entry_reality(
        med_entry.id,
        RealityState.CONFIRMED_DONE,
        life_event.id,
    )

    assert updated.status == DayPlanEntryStatus.DONE
    assert updated.reality_state == RealityState.CONFIRMED_DONE

    async with aiosqlite.connect(str(db_path)) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT status FROM calendar_entries WHERE id = 'cal-meds'"
        )
        row = await cursor.fetchone()
    assert row["status"] == "completed"

    events = await DomainEventStore(db_path).list_events(
        aggregate_type="day_plan_entry",
        aggregate_id=med_entry.id,
    )
    assert events[-1].event_type == "DAY_PLAN_ENTRY_REALITY_MARKED"
    assert events[-1].causation_id == life_event.id


@pytest.mark.asyncio
async def test_stale_entries_returns_unknown_past_entries(tmp_path: Path) -> None:
    db_path = await _make_db(tmp_path)
    today = date(2026, 4, 28)
    await _seed_sources(db_path, today)
    service = DayPlanService(db_path)
    await service.create_or_refresh_day_plan(today, source="unit_test")

    stale = await service.stale_entries(
        today,
        datetime(2026, 4, 28, 10, 15, tzinfo=UTC),
        grace=timedelta(minutes=15),
    )

    assert {entry.title for entry in stale} >= {"Morning meds", "Eat breakfast"}
