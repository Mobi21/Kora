"""Unit tests for Phase 5 calendar tools + expansion."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite
import pytest

from kora_v2.core.calendar_models import CalendarEntry
from kora_v2.core.db import init_operational_db
from kora_v2.tools.calendar import (
    SYNTHETIC_ID_SEP,
    CalendarSync,
    CreateCalendarEntryInput,
    DeleteCalendarEntryInput,
    QueryCalendarInput,
    SyncGoogleCalendarInput,
    UpdateCalendarEntryInput,
    _load_entries_between,
    create_calendar_entry,
    delete_calendar_entry,
    expand_recurring,
    query_calendar,
    sync_google_calendar,
    update_calendar_entry,
)


class _StubContainer:
    def __init__(self, data_dir: Path):
        class _Settings:
            pass

        self.settings = _Settings()
        self.settings.data_dir = data_dir

        class _MCP:
            servers = {}

        self.settings.mcp = _MCP()

        class _Workspace:
            mcp_server_name = "workspace"
            default_calendar_id = "primary"
            user_google_email = ""

        self.settings.workspace = _Workspace()
        self.mcp_manager = None


@pytest.fixture
async def container(tmp_path):
    db_path = tmp_path / "operational.db"
    await init_operational_db(db_path)
    return _StubContainer(tmp_path)


# ── Happy-path CRUD ─────────────────────────────────────────────────────────


async def test_create_and_query_event(container):
    now = datetime.now(UTC).replace(microsecond=0)
    r = await create_calendar_entry(
        CreateCalendarEntryInput(
            kind="event",
            title="Standup",
            starts_at=now.isoformat(),
            ends_at=(now + timedelta(minutes=30)).isoformat(),
        ),
        container,
    )
    data = json.loads(r)
    assert data["success"] is True
    assert data["kind"] == "event"

    q = await query_calendar(QueryCalendarInput(days_ahead=2), container)
    qdata = json.loads(q)
    assert qdata["count"] == 1
    assert qdata["entries"][0]["title"] == "Standup"


async def test_delete_marks_cancelled(container):
    now = datetime.now(UTC)
    r = await create_calendar_entry(
        CreateCalendarEntryInput(
            kind="event",
            title="Doomed",
            starts_at=now.isoformat(),
        ),
        container,
    )
    entry_id = json.loads(r)["id"]
    await delete_calendar_entry(
        DeleteCalendarEntryInput(entry_id=entry_id), container
    )
    q = await query_calendar(QueryCalendarInput(days_ahead=2), container)
    qdata = json.loads(q)
    titles = [e["title"] for e in qdata["entries"]]
    assert "Doomed" not in titles


async def test_update_entry_changes_title(container):
    now = datetime.now(UTC)
    r = await create_calendar_entry(
        CreateCalendarEntryInput(
            kind="event", title="Old", starts_at=now.isoformat()
        ),
        container,
    )
    entry_id = json.loads(r)["id"]
    await update_calendar_entry(
        UpdateCalendarEntryInput(entry_id=entry_id, changes={"title": "New"}),
        container,
    )
    q = await query_calendar(QueryCalendarInput(days_ahead=2), container)
    titles = [e["title"] for e in json.loads(q)["entries"]]
    assert "New" in titles
    assert "Old" not in titles


async def test_acceptance_calendar_anchors_override_bad_model_times(
    container,
    monkeypatch,
):
    monkeypatch.setenv("KORA_ACCEPTANCE_DIR", "/tmp/claude/kora_acceptance")

    r = await create_calendar_entry(
        CreateCalendarEntryInput(
            kind="event",
            title="STAT quiz window",
            starts_at="2026-04-29T16:00:00-04:00",
            ends_at="2026-04-29T17:00:00-04:00",
            description="STAT quiz all day",
        ),
        container,
    )

    data = json.loads(r)

    assert data["success"] is True
    assert data["starts_at"] == "2026-04-30T12:00:00+00:00"
    assert data["ends_at"] == "2026-05-01T03:59:00+00:00"


# ── Recurring expansion ─────────────────────────────────────────────────────


def test_expand_recurring_daily_count():
    now = datetime(2026, 4, 12, 8, 0, tzinfo=UTC)
    parent = CalendarEntry(
        id="parent",
        kind="medication_window",
        title="Morning meds",
        starts_at=now,
        ends_at=now + timedelta(minutes=60),
        recurring_rule="FREQ=DAILY;COUNT=5",
        created_at=now,
        updated_at=now,
    )
    expanded = expand_recurring(
        parent,
        datetime(2026, 4, 11, tzinfo=UTC),
        datetime(2026, 4, 20, tzinfo=UTC),
    )
    assert len(expanded) == 5
    assert all(SYNTHETIC_ID_SEP in e.id for e in expanded)
    assert all(e.recurring_rule is None for e in expanded)


async def test_synthetic_id_update_creates_exception(container):
    """Updating a synthetic ID creates an exception row."""
    db_path = container.settings.data_dir / "operational.db"
    now = datetime(2026, 4, 12, 8, 0, tzinfo=UTC)
    parent_id = "parent_test"

    async with aiosqlite.connect(str(db_path)) as db:
        await db.execute(
            """
            INSERT INTO calendar_entries (
                id, kind, title, starts_at, ends_at, source,
                recurring_rule, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'kora', ?, ?, ?)
            """,
            (
                parent_id,
                "event",
                "Weekly sync",
                now.isoformat(),
                (now + timedelta(minutes=30)).isoformat(),
                "FREQ=WEEKLY;COUNT=4",
                now.isoformat(),
                now.isoformat(),
            ),
        )
        await db.commit()

    synthetic = f"{parent_id}{SYNTHETIC_ID_SEP}{now.date().isoformat()}"
    result = await update_calendar_entry(
        UpdateCalendarEntryInput(
            entry_id=synthetic,
            changes={"title": "Moved sync"},
        ),
        container,
    )
    data = json.loads(result)
    assert data["action"] == "exception_created"

    # Verify exception row exists
    async with aiosqlite.connect(str(db_path)) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT title, override_parent_id, override_occurrence_date "
            "FROM calendar_entries WHERE override_parent_id = ?",
            (parent_id,),
        ) as cur:
            rows = await cur.fetchall()
    assert len(rows) == 1
    assert rows[0]["title"] == "Moved sync"


async def test_synthetic_id_delete_creates_cancelled_exception(container):
    db_path = container.settings.data_dir / "operational.db"
    now = datetime(2026, 4, 12, 8, 0, tzinfo=UTC)
    parent_id = "parent_del"

    async with aiosqlite.connect(str(db_path)) as db:
        await db.execute(
            """
            INSERT INTO calendar_entries (
                id, kind, title, starts_at, ends_at, source,
                recurring_rule, created_at, updated_at
            ) VALUES (?, 'event', 'D', ?, ?, 'kora', 'FREQ=DAILY;COUNT=3', ?, ?)
            """,
            (
                parent_id,
                now.isoformat(),
                (now + timedelta(minutes=30)).isoformat(),
                now.isoformat(),
                now.isoformat(),
            ),
        )
        await db.commit()

    synthetic = f"{parent_id}{SYNTHETIC_ID_SEP}{now.date().isoformat()}"
    result = await delete_calendar_entry(
        DeleteCalendarEntryInput(entry_id=synthetic), container
    )
    data = json.loads(result)
    assert data["action"] == "cancelled_exception"

    # Expanded query should skip the cancelled date.
    async with aiosqlite.connect(str(db_path)) as db:
        entries = await _load_entries_between(
            db,
            datetime(2026, 4, 11, tzinfo=UTC),
            datetime(2026, 4, 20, tzinfo=UTC),
        )
    # Only 2 of 3 should remain (one cancelled).
    titles = [e.title for e in entries if e.title == "D"]
    assert len(titles) == 2


async def test_query_missing_entry_errors(container):
    result = await update_calendar_entry(
        UpdateCalendarEntryInput(entry_id="doesnotexist", changes={"title": "x"}),
        container,
    )
    data = json.loads(result)
    assert data["success"] is False


async def test_query_calendar_uses_user_tz_for_day_window(container):
    """``query_calendar(date='2026-04-12')`` for a Pacific user must
    return a UTC entry at 16:00 (= 9am PDT) on that local date.
    """
    container.settings.user_tz = "America/Los_Angeles"
    # 16:00 UTC = 09:00 America/Los_Angeles on 2026-04-12.
    starts = datetime(2026, 4, 12, 16, 0, tzinfo=UTC)
    r = await create_calendar_entry(
        CreateCalendarEntryInput(
            kind="event",
            title="Standup PDT",
            starts_at=starts.isoformat(),
            ends_at=(starts + timedelta(minutes=30)).isoformat(),
        ),
        container,
    )
    assert json.loads(r)["success"] is True

    q = await query_calendar(
        QueryCalendarInput(date="2026-04-12", days_ahead=1), container
    )
    qdata = json.loads(q)
    titles = [e["title"] for e in qdata["entries"]]
    assert "Standup PDT" in titles
    # Local bounds are also reported so callers see the asked-for frame.
    assert qdata["since_local"].startswith("2026-04-12T00:00:00")


async def test_acceptance_query_calendar_reports_new_york_local_times(
    container,
    monkeypatch,
    tmp_path,
):
    accept_dir = tmp_path / "acceptance"
    accept_dir.mkdir()
    (accept_dir / "scenario_clock.json").write_text(
        json.dumps({"today": "2026-04-28", "timezone": "America/New_York"})
    )
    monkeypatch.setenv("KORA_ACCEPTANCE_DIR", str(accept_dir))
    starts = datetime(2026, 4, 28, 12, 30, tzinfo=UTC)
    r = await create_calendar_entry(
        CreateCalendarEntryInput(
            kind="event",
            title="BIO 240 lab",
            starts_at=starts.isoformat(),
            ends_at=(starts + timedelta(minutes=110)).isoformat(),
        ),
        container,
    )
    assert json.loads(r)["success"] is True

    q = await query_calendar(
        QueryCalendarInput(days_ahead=1),
        container,
    )
    qdata = json.loads(q)
    entry = qdata["entries"][0]

    assert qdata["since_local"].startswith("2026-04-28T00:00:00")
    assert entry["timezone"] == "America/New_York"
    assert entry["starts_at"].startswith("2026-04-28T12:30:00")
    assert entry["starts_at_local"] == "2026-04-28T08:30:00-04:00"
    assert entry["display_time"] == "8:30 am-10:20 am"


class _FakeMCP:
    def __init__(self, response):
        self.calls = []
        self._response = response

    async def call_tool(self, server, tool, args):
        self.calls.append((server, tool, args))
        return self._response


class _FakeResult:
    def __init__(self, data):
        self.structured_data = data
        self.text = json.dumps(data)
        self.is_error = False


async def test_calendar_sync_pulls_workspace_events_into_local_store(container):
    container.settings.mcp.servers = {"workspace": object()}
    container.settings.workspace.user_google_email = "user@example.com"
    google_event = {
        "id": "google-1",
        "summary": "Google sync read",
        "start": {"dateTime": "2026-04-29T15:00:00+00:00"},
        "end": {"dateTime": "2026-04-29T15:30:00+00:00"},
    }
    container.mcp_manager = _FakeMCP(_FakeResult({"events": [google_event]}))

    sync = CalendarSync(container)
    pulled = await sync.pull_range(
        datetime(2026, 4, 29, tzinfo=UTC),
        datetime(2026, 4, 30, tzinfo=UTC),
    )

    assert len(pulled) == 1
    assert pulled[0]["source"] == "google"
    assert container.mcp_manager.calls == [
        (
            "workspace",
            "get_events",
            {
                "user_google_email": "user@example.com",
                "calendar_id": "primary",
                "time_min": "2026-04-29T00:00:00+00:00",
                "time_max": "2026-04-30T00:00:00+00:00",
            },
        )
    ]

    q = await query_calendar(
        QueryCalendarInput(date="2026-04-29", days_ahead=1), container
    )
    titles = [e["title"] for e in json.loads(q)["entries"]]
    assert "Google sync read" in titles


async def test_sync_google_calendar_reports_missing_workspace_email(container):
    container.settings.mcp.servers = {"workspace": object()}
    container.mcp_manager = _FakeMCP(_FakeResult({"events": []}))

    result = await sync_google_calendar(SyncGoogleCalendarInput(days_ahead=1), container)
    data = json.loads(result)

    assert data["success"] is True
    assert data["pulled"] == 0
    assert "user_google_email" in data["message"]
    assert container.mcp_manager.calls == []
