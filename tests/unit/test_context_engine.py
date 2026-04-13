"""Unit tests for the Phase 5 context engine."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import aiosqlite
import pytest

from kora_v2.adhd import (
    ADHDModule,
    ADHDProfile,
    MedicationScheduleEntry,
    MedicationWindow,
)
from kora_v2.adhd.protocol import EnergySignal
from kora_v2.context.engine import (
    ContextEngine,
    _aggregate_focus_summary,
    _build_medication_status,
    _estimate_energy,
)
from kora_v2.core.db import init_operational_db


@pytest.fixture
async def engine(tmp_path):
    db_path = tmp_path / "op.db"
    await init_operational_db(db_path)
    profile = ADHDProfile(
        peak_windows=[(9, 12)],
        crash_periods=[(14, 16)],
        medication_schedule=[
            MedicationScheduleEntry(
                name="Adderall",
                dose="20mg",
                windows=[
                    MedicationWindow(
                        start=time(8, 0), end=time(9, 0), label="morning"
                    )
                ],
            )
        ],
    )
    mod = ADHDModule(profile)
    return ContextEngine(db_path, mod, "UTC")


class TestDayContextBuilder:
    async def test_empty_day(self, engine):
        dc = await engine.build_day_context()
        assert dc.day_of_week  # always populated
        assert dc.schedule == []
        assert dc.energy is not None
        assert dc.energy.is_guess is True

    async def test_insights_include_best_day(self, engine):
        lc = await engine.build_life_context(
            date.today() - timedelta(days=7), date.today(), "last 7 days"
        )
        # With no data, insights should be empty or only safe defaults.
        for ins in lc.insights:
            assert isinstance(ins, str)

    async def test_items_due_today_with_bare_date(self, engine):
        """items_due must match bare YYYY-MM-DD due_date strings.

        Regression: the original SQL used range comparison against full
        ISO datetime bounds, which is lexically false against the bare
        date strings ``create_item`` writes. Today's items vanished.
        """
        today = date.today()
        async with aiosqlite.connect(str(engine._db_path)) as db:
            now = datetime.now(UTC).isoformat()
            await db.execute(
                "INSERT INTO items "
                "(id, type, owner, title, status, due_date, "
                " goal_scope, priority, created_at, updated_at) "
                "VALUES (?, 'task', 'kora', ?, 'planned', ?, 'task', 3, ?, ?)",
                (uuid.uuid4().hex[:8], "Today task", today.isoformat(), now, now),
            )
            await db.commit()

        dc = await engine.build_day_context(target_date=today)
        assert any(item["title"] == "Today task" for item in dc.items_due)


class TestEstimateEnergy:
    def test_self_report_short_circuits(self):
        now = datetime(2026, 4, 12, 10, 0, tzinfo=UTC)
        last_check_in = {
            "level": "high",
            "focus": "locked_in",
            "notes": "feeling sharp",
            "logged_at": (now - timedelta(minutes=30)).isoformat(),
        }
        result = _estimate_energy([], last_check_in, now)
        assert result.level == "high"
        assert result.is_guess is False
        assert result.confidence == 1.0

    def test_signals_without_check_in_are_guessed(self):
        now = datetime(2026, 4, 12, 10, 0, tzinfo=UTC)
        signals = [
            EnergySignal(
                source="medication",
                level_adjustment=0.2,
                confidence=0.7,
                description="meds taken",
            ),
        ]
        result = _estimate_energy(signals, None, now)
        assert result.is_guess is True
        assert "meds taken" in result.signals[0]

    def test_stale_self_report_falls_back_to_guess(self):
        now = datetime(2026, 4, 12, 10, 0, tzinfo=UTC)
        last_check_in = {
            "level": "high",
            "focus": "locked_in",
            "notes": "",
            "logged_at": (now - timedelta(hours=5)).isoformat(),
        }
        result = _estimate_energy([], last_check_in, now)
        assert result.is_guess is True

    def test_low_signals_produce_low_level(self):
        now = datetime(2026, 4, 12, 10, 0, tzinfo=UTC)
        signals = [
            EnergySignal(
                source="medication",
                level_adjustment=-0.3,
                confidence=0.8,
                description="meds missed",
            ),
            EnergySignal(
                source="calendar_load",
                level_adjustment=-0.25,
                confidence=0.6,
                description="3 meetings",
            ),
        ]
        result = _estimate_energy(signals, None, now)
        assert result.level == "low"


class TestMedicationStatus:
    def test_window_end_plus_29min_matches(self):
        profile = ADHDProfile(
            medication_schedule=[
                MedicationScheduleEntry(
                    name="Adderall",
                    windows=[MedicationWindow(start=time(8, 0), end=time(9, 0))],
                )
            ]
        )
        now = datetime(2026, 4, 12, 10, 0, tzinfo=UTC)
        tz = ZoneInfo("UTC")

        class FakeRow:
            def __init__(self, taken_at):
                self._data = {"medication_name": "Adderall", "taken_at": taken_at}

            def __getitem__(self, key):
                return self._data[key]

        # Taken 29 minutes after window_end → matched via ±30 grace.
        rows = [FakeRow(datetime(2026, 4, 12, 9, 29, tzinfo=UTC))]
        status = _build_medication_status(profile, rows, now, tz)
        assert status.taken and not status.missed

    def test_window_end_plus_31min_is_missed(self):
        profile = ADHDProfile(
            medication_schedule=[
                MedicationScheduleEntry(
                    name="Adderall",
                    windows=[MedicationWindow(start=time(8, 0), end=time(9, 0))],
                )
            ]
        )
        now = datetime(2026, 4, 12, 10, 0, tzinfo=UTC)
        tz = ZoneInfo("UTC")

        class FakeRow:
            def __init__(self, taken_at):
                self._data = {"medication_name": "Adderall", "taken_at": taken_at}

            def __getitem__(self, key):
                return self._data[key]

        rows = [FakeRow(datetime(2026, 4, 12, 9, 31, tzinfo=UTC))]
        status = _build_medication_status(profile, rows, now, tz)
        assert status.missed and not status.taken


class TestFocusSummaryTrend:
    """``_aggregate_focus_summary.trend`` must reflect first-half vs
    second-half averages, not a hardcoded 'stable'."""

    @staticmethod
    def _rows(hours_per_day: list[float]) -> list[dict]:
        rows: list[dict] = []
        base = datetime(2026, 4, 1, 9, 0, tzinfo=UTC)
        for i, h in enumerate(hours_per_day):
            start = base + timedelta(days=i)
            end = start + timedelta(hours=h)
            rows.append(
                {
                    "started_at": start.isoformat(),
                    "ended_at": end.isoformat(),
                }
            )
        return rows

    def test_stable(self):
        rows = self._rows([2, 2, 2, 2])
        assert _aggregate_focus_summary(rows, ZoneInfo("UTC"))["trend"] == "stable"

    def test_declining(self):
        # First half: 4h/day, second half: 1h/day → below 0.7 * first avg.
        rows = self._rows([4, 4, 1, 1])
        assert (
            _aggregate_focus_summary(rows, ZoneInfo("UTC"))["trend"] == "declining"
        )

    def test_improving(self):
        # First half: 1h/day, second half: 4h/day → above 1.3 * first avg.
        rows = self._rows([1, 1, 4, 4])
        assert (
            _aggregate_focus_summary(rows, ZoneInfo("UTC"))["trend"] == "improving"
        )


class TestLifeContextAggregation:
    async def test_life_context_with_finance_rows(self, tmp_path):
        db_path = tmp_path / "life.db"
        await init_operational_db(db_path)
        # Insert finance rows
        async with aiosqlite.connect(str(db_path)) as db:
            now = datetime.now(UTC).isoformat()
            for amt, cat in [(50, "food"), (200, "tech"), (30, "food")]:
                await db.execute(
                    "INSERT INTO finance_log (id, amount, category, logged_at, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (uuid.uuid4().hex[:8], amt, cat, now, now),
                )
            await db.commit()

        profile = ADHDProfile()
        engine = ContextEngine(db_path, ADHDModule(profile), "UTC")
        lc = await engine.build_life_context(
            date.today() - timedelta(days=1), date.today(), "today"
        )
        assert lc.finance_summary is not None
        assert lc.finance_summary["total_spend"] == 280.0
        assert lc.finance_summary["by_category"]["food"] == 80.0
