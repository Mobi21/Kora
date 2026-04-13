"""Unit tests for Phase 5 planning tools."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from kora_v2.adhd import ADHDModule, ADHDProfile
from kora_v2.context.engine import ContextEngine
from kora_v2.core.db import init_operational_db
from kora_v2.tools.calendar import (
    CreateCalendarEntryInput,
    create_calendar_entry,
)
from kora_v2.tools.planning import (
    CompleteItemInput,
    CreateItemInput,
    DeferItemInput,
    QueryItemsInput,
    UpdatePlanInput,
    _parse_scope_window,
    apply_time_correction,
    complete_item,
    create_item,
    defer_item,
    query_items,
    update_plan,
)


class _StubContainer:
    def __init__(self, data_dir: Path, profile: ADHDProfile | None = None):
        class _Settings:
            pass

        self.settings = _Settings()
        self.settings.data_dir = data_dir

        class _MCP:
            servers = {}

        self.settings.mcp = _MCP()
        self.adhd_profile = profile or ADHDProfile()
        mod = ADHDModule(self.adhd_profile)
        self.context_engine = ContextEngine(
            data_dir / "operational.db", mod, "UTC"
        )


@pytest.fixture
async def container(tmp_path):
    db_path = tmp_path / "operational.db"
    await init_operational_db(db_path)
    return _StubContainer(tmp_path)


# ── Time correction ─────────────────────────────────────────────────────────


class TestTimeCorrection:
    def test_applies_1_5x(self):
        profile = ADHDProfile(time_correction_factor=1.5)
        assert apply_time_correction(30, profile) == 45
        assert apply_time_correction(10, profile) == 15
        assert apply_time_correction(0, profile) == 0

    def test_alternate_factor(self):
        profile = ADHDProfile(time_correction_factor=2.0)
        assert apply_time_correction(30, profile) == 60


# ── Scope parsing ───────────────────────────────────────────────────────────


class TestScopeParsing:
    def test_today(self):
        since, until, label = _parse_scope_window("today")
        assert since == until
        assert label == "today"

    def test_this_week(self):
        since, until, label = _parse_scope_window("this week")
        assert (until - since).days == 6
        assert label == "this week"

    def test_last_n_days(self):
        since, until, label = _parse_scope_window("last 14 days")
        assert (until - since).days == 14
        assert label == "last 14 days"

    def test_until_weekday(self):
        since, until, label = _parse_scope_window("until friday")
        assert label == "until friday"
        assert until >= since


# ── Item CRUD ───────────────────────────────────────────────────────────────


class TestItemCRUD:
    async def test_create_applies_correction(self, container):
        result = await create_item(
            CreateItemInput(
                title="Write tests",
                estimated_minutes=20,
                goal_scope="weekly_goal",
            ),
            container,
        )
        data = json.loads(result)
        assert data["estimated_minutes_raw"] == 20
        assert data["estimated_minutes_corrected"] == 30
        assert data["goal_scope"] == "weekly_goal"

    async def test_query_respects_status_filter(self, container):
        await create_item(
            CreateItemInput(title="A", goal_scope="task"), container
        )
        r = await query_items(QueryItemsInput(status="planned"), container)
        data = json.loads(r)
        assert data["count"] >= 1

    async def test_complete_transitions_status(self, container):
        r = await create_item(
            CreateItemInput(title="Finish", goal_scope="task"), container
        )
        item_id = json.loads(r)["id"]
        await complete_item(CompleteItemInput(item_id=item_id), container)
        rows = json.loads(
            await query_items(QueryItemsInput(status="done"), container)
        )
        assert any(i["id"] == item_id for i in rows["items"])

    async def test_defer_sets_due_date(self, container):
        r = await create_item(
            CreateItemInput(title="Later", goal_scope="task"), container
        )
        item_id = json.loads(r)["id"]
        r = await defer_item(
            DeferItemInput(item_id=item_id, to_when="tomorrow"), container
        )
        data = json.loads(r)
        assert data["status"] == "deferred"
        assert data["due_date"]

    async def test_complete_missing_item_errors(self, container):
        r = await complete_item(CompleteItemInput(item_id="nope"), container)
        assert json.loads(r)["success"] is False


# ── update_plan ripple analysis ─────────────────────────────────────────────


class TestUpdatePlan:
    async def test_delete_action_marks_cancelled(self, container):
        now = datetime.now(UTC).replace(microsecond=0)
        r = await create_calendar_entry(
            CreateCalendarEntryInput(
                kind="event",
                title="Meeting",
                starts_at=now.isoformat(),
                ends_at=(now + timedelta(minutes=30)).isoformat(),
            ),
            container,
        )
        entry_id = json.loads(r)["id"]

        r = await update_plan(
            UpdatePlanInput(
                summary="canceled meeting",
                affected_entry_ids=[entry_id],
                action="delete",
            ),
            container,
        )
        data = json.loads(r)
        assert data["moved"][0]["action"] == "cancelled"

    async def test_reschedule_into_crash_window_warns(self, container):
        """Crash window check must respect the user's local timezone:
        a 3pm-PDT reschedule (= 22:00 UTC) must still match a local
        [14, 16] crash window.
        """
        from zoneinfo import ZoneInfo

        profile = ADHDProfile(crash_periods=[(14, 16)])
        container.adhd_profile = profile
        container.settings.user_tz = "America/Los_Angeles"
        la = ZoneInfo("America/Los_Angeles")

        morning_local = datetime(2026, 4, 12, 9, 0, tzinfo=la)
        morning_utc = morning_local.astimezone(UTC)
        r = await create_calendar_entry(
            CreateCalendarEntryInput(
                kind="event",
                title="Coding",
                starts_at=morning_utc.isoformat(),
                ends_at=(morning_utc + timedelta(minutes=60)).isoformat(),
            ),
            container,
        )
        entry_id = json.loads(r)["id"]

        afternoon_local = datetime(2026, 4, 12, 15, 0, tzinfo=la)
        afternoon_utc = afternoon_local.astimezone(UTC)
        r = await update_plan(
            UpdatePlanInput(
                summary="moved to afternoon",
                affected_entry_ids=[entry_id],
                action="reschedule",
                reschedule_to=afternoon_utc.isoformat(),
            ),
            container,
        )
        data = json.loads(r)
        assert any("crash" in w for w in data["warnings"])

    async def test_shrink_action_reduces_duration(self, container):
        now = datetime.now(UTC).replace(microsecond=0)
        r = await create_calendar_entry(
            CreateCalendarEntryInput(
                kind="event",
                title="Long meeting",
                starts_at=now.isoformat(),
                ends_at=(now + timedelta(minutes=60)).isoformat(),
            ),
            container,
        )
        entry_id = json.loads(r)["id"]

        r = await update_plan(
            UpdatePlanInput(
                summary="tighter",
                affected_entry_ids=[entry_id],
                action="shrink",
                shrink_to_minutes=15,
            ),
            container,
        )
        data = json.loads(r)
        assert data["moved"][0]["action"] == "shrunk"

    async def test_missing_reschedule_to_errors(self, container):
        r = await update_plan(
            UpdatePlanInput(
                summary="",
                affected_entry_ids=["doesnotexist"],
                action="reschedule",
            ),
            container,
        )
        assert json.loads(r)["success"] is False
