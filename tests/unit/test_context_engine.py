"""Unit tests for the Phase 5 + Phase 8d context engine."""

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
from kora_v2.core.events import EventEmitter, EventType
from kora_v2.core.models import Insight


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


# ══════════════════════════════════════════════════════════════════════════════
# Phase 8d: Staleness tracking tests
# ══════════════════════════════════════════════════════════════════════════════


class TestStalenessTracking:
    """Tests for Phase 8d staleness tracking and caching."""

    async def test_mark_stale_sets_flag(self, engine):
        """mark_stale() sets _stale = True."""
        engine._stale = False
        engine.mark_stale()
        assert engine._stale is True

    async def test_engine_starts_stale(self, tmp_path):
        """Engine starts in stale state."""
        db_path = tmp_path / "stale.db"
        await init_operational_db(db_path)
        profile = ADHDProfile()
        eng = ContextEngine(db_path, ADHDModule(profile), "UTC")
        assert eng._stale is True
        assert eng._cached_day_context is None

    async def test_build_clears_stale_flag(self, engine):
        """build_day_context clears _stale after building."""
        assert engine._stale is True
        await engine.build_day_context()
        assert engine._stale is False

    async def test_cached_context_returned_when_not_stale(self, engine):
        """Second call returns the same cached object if not stale."""
        dc1 = await engine.build_day_context()
        dc2 = await engine.build_day_context()
        assert dc1 is dc2  # same object, not rebuilt

    async def test_stale_flag_forces_rebuild(self, engine):
        """Marking stale forces a fresh build."""
        dc1 = await engine.build_day_context()
        engine.mark_stale()
        dc2 = await engine.build_day_context()
        # Different object (rebuilt), same data
        assert dc1 is not dc2
        assert dc1.date == dc2.date


# ══════════════════════════════════════════════════════════════════════════════
# Phase 8d: Event subscription tests
# ══════════════════════════════════════════════════════════════════════════════


class TestEventSubscription:
    """Tests for subscribe_events and event-driven staleness."""

    async def test_memory_stored_marks_stale(self, engine):
        """MEMORY_STORED event marks engine stale."""
        emitter = EventEmitter()
        engine.subscribe_events(emitter)
        # Clear stale from init
        engine._stale = False

        await emitter.emit(EventType.MEMORY_STORED, memory_id="test")
        assert engine._stale is True

    async def test_session_end_marks_stale(self, engine):
        """SESSION_END event marks engine stale."""
        emitter = EventEmitter()
        engine.subscribe_events(emitter)
        engine._stale = False

        await emitter.emit(EventType.SESSION_END, session_id="test")
        assert engine._stale is True

    async def test_autonomous_complete_marks_stale(self, engine):
        """AUTONOMOUS_COMPLETE event marks engine stale."""
        emitter = EventEmitter()
        engine.subscribe_events(emitter)
        engine._stale = False

        await emitter.emit(EventType.AUTONOMOUS_COMPLETE, task_id="test")
        assert engine._stale is True

    async def test_emitter_stored_for_publishing(self, engine):
        """subscribe_events stores the emitter for publishing."""
        emitter = EventEmitter()
        engine.subscribe_events(emitter)
        assert engine._emitter is emitter


# ══════════════════════════════════════════════════════════════════════════════
# Phase 8d: DAY_CONTEXT_UPDATED event emission
# ══════════════════════════════════════════════════════════════════════════════


class TestDayContextUpdatedEvent:
    """Tests for DAY_CONTEXT_UPDATED emission on build."""

    async def test_emits_day_context_updated(self, engine):
        """build_day_context emits DAY_CONTEXT_UPDATED when an emitter is set."""
        emitter = EventEmitter()
        engine.subscribe_events(emitter)

        received: list[dict] = []
        async def handler(payload: dict) -> None:
            received.append(payload)

        emitter.on(EventType.DAY_CONTEXT_UPDATED, handler)
        await engine.build_day_context()

        assert len(received) == 1
        assert received[0]["event_type"] == EventType.DAY_CONTEXT_UPDATED
        assert "date" in received[0]

    async def test_no_emission_without_emitter(self, engine):
        """build_day_context works fine without an emitter (no crash)."""
        assert engine._emitter is None
        dc = await engine.build_day_context()
        assert dc is not None

    async def test_cached_return_does_not_emit(self, engine):
        """Returning cached context does not re-emit the event."""
        emitter = EventEmitter()
        engine.subscribe_events(emitter)

        received: list[dict] = []
        async def handler(payload: dict) -> None:
            received.append(payload)

        emitter.on(EventType.DAY_CONTEXT_UPDATED, handler)

        await engine.build_day_context()
        assert len(received) == 1

        await engine.build_day_context()  # cached, should not emit again
        assert len(received) == 1


# ══════════════════════════════════════════════════════════════════════════════
# Phase 8d: Insight generation tests
# ══════════════════════════════════════════════════════════════════════════════


async def _make_insight_engine(tmp_path, crash_periods=None, peak_windows=None):
    """Helper: create an engine with a populated DB for insight tests."""
    db_path = tmp_path / "insights.db"
    await init_operational_db(db_path)
    profile = ADHDProfile(
        crash_periods=crash_periods or [(14, 16)],
        peak_windows=peak_windows or [(9, 12)],
    )
    return ContextEngine(db_path, ADHDModule(profile), "UTC"), db_path


class TestInsightGeneration:
    """Tests for get_insights and the 5 insight rules."""

    async def test_empty_db_returns_no_insights(self, tmp_path):
        """No data => no insights."""
        engine, _ = await _make_insight_engine(tmp_path)
        insights = await engine.get_insights(window_days=7, min_confidence=0.5)
        assert insights == []

    async def test_energy_calendar_mismatch(self, tmp_path):
        """Rule 1: Events during crash periods produce an insight."""
        engine, db_path = await _make_insight_engine(
            tmp_path, crash_periods=[(14, 16)]
        )

        now = datetime.now(UTC)
        today = now.date()

        async with aiosqlite.connect(str(db_path)) as db:
            # Insert calendar entries during crash period (14:00-16:00)
            for i in range(3):
                day = today - timedelta(days=i)
                starts = datetime.combine(
                    day, time(14, 30), tzinfo=UTC
                )
                ends = starts + timedelta(hours=1)
                await db.execute(
                    "INSERT INTO calendar_entries "
                    "(id, kind, title, starts_at, ends_at, source) "
                    "VALUES (?, 'event', ?, ?, ?, 'kora')",
                    (
                        uuid.uuid4().hex[:8],
                        f"Meeting {i}",
                        starts.isoformat(),
                        ends.isoformat(),
                    ),
                )
            await db.commit()

        insights = await engine.get_insights(window_days=7, min_confidence=0.3)
        mismatch = [i for i in insights if i.type == "energy_calendar_mismatch"]
        assert len(mismatch) == 1
        assert mismatch[0].domain == "adhd"
        assert len(mismatch[0].evidence) > 0

    async def test_routine_adherence_trend(self, tmp_path):
        """Rule 3: Routine completion declining triggers insight."""
        engine, db_path = await _make_insight_engine(tmp_path)

        now = datetime.now(UTC)
        today = now.date()

        async with aiosqlite.connect(str(db_path)) as db:
            # Create a routine
            routine_id = uuid.uuid4().hex[:8]
            now_iso = now.isoformat()
            await db.execute(
                "INSERT INTO routines (id, name, steps_json, created_at, updated_at) "
                "VALUES (?, 'Morning', '[]', ?, ?)",
                (routine_id, now_iso, now_iso),
            )
            # First half of the week: all completed
            for i in range(6, 3, -1):
                day = today - timedelta(days=i)
                started = datetime.combine(day, time(8, 0), tzinfo=UTC)
                await db.execute(
                    "INSERT INTO routine_sessions "
                    "(id, routine_id, status, started_at) "
                    "VALUES (?, ?, 'completed', ?)",
                    (uuid.uuid4().hex[:8], routine_id, started.isoformat()),
                )
            # Second half: all abandoned
            for i in range(3, 0, -1):
                day = today - timedelta(days=i)
                started = datetime.combine(day, time(8, 0), tzinfo=UTC)
                await db.execute(
                    "INSERT INTO routine_sessions "
                    "(id, routine_id, status, started_at) "
                    "VALUES (?, ?, 'abandoned', ?)",
                    (uuid.uuid4().hex[:8], routine_id, started.isoformat()),
                )
            await db.commit()

        insights = await engine.get_insights(window_days=7, min_confidence=0.3)
        trend = [i for i in insights if i.type == "routine_trend"]
        assert len(trend) == 1
        assert trend[0].domain == "productivity"

    async def test_confidence_filtering(self, tmp_path):
        """get_insights respects min_confidence — high threshold filters out."""
        engine, db_path = await _make_insight_engine(
            tmp_path, crash_periods=[(14, 16)]
        )

        now = datetime.now(UTC)
        today = now.date()

        async with aiosqlite.connect(str(db_path)) as db:
            # Only 1 mismatch event out of many -> low confidence
            starts = datetime.combine(today, time(14, 30), tzinfo=UTC)
            ends = starts + timedelta(hours=1)
            await db.execute(
                "INSERT INTO calendar_entries "
                "(id, kind, title, starts_at, ends_at, source) "
                "VALUES (?, 'event', 'Meeting', ?, ?, 'kora')",
                (uuid.uuid4().hex[:8], starts.isoformat(), ends.isoformat()),
            )
            # Add many events outside crash period
            for i in range(10):
                s = datetime.combine(
                    today - timedelta(days=i % 5), time(10, 0), tzinfo=UTC
                )
                e = s + timedelta(hours=1)
                await db.execute(
                    "INSERT INTO calendar_entries "
                    "(id, kind, title, starts_at, ends_at, source) "
                    "VALUES (?, 'event', ?, ?, ?, 'kora')",
                    (uuid.uuid4().hex[:8], f"Normal {i}", s.isoformat(), e.isoformat()),
                )
            await db.commit()

        # With very high threshold, mismatch insight should be filtered
        insights = await engine.get_insights(window_days=7, min_confidence=0.99)
        mismatch = [i for i in insights if i.type == "energy_calendar_mismatch"]
        assert len(mismatch) == 0

    async def test_medication_focus_correlation(self, tmp_path):
        """Rule 2: Focus block quality correlates with medication timing."""
        engine, db_path = await _make_insight_engine(tmp_path)

        now = datetime.now(UTC)
        today = now.date()

        async with aiosqlite.connect(str(db_path)) as db:
            # Medicated days with long focus blocks
            for i in range(3):
                day = today - timedelta(days=i + 1)
                taken_at = datetime.combine(day, time(8, 0), tzinfo=UTC)
                await db.execute(
                    "INSERT INTO medication_log (id, medication_name, taken_at, created_at) "
                    "VALUES (?, 'Adderall', ?, ?)",
                    (uuid.uuid4().hex[:8], taken_at.isoformat(), taken_at.isoformat()),
                )
                # Long focus block on med day
                fb_start = datetime.combine(day, time(9, 0), tzinfo=UTC)
                fb_end = fb_start + timedelta(hours=2)
                await db.execute(
                    "INSERT INTO focus_blocks (id, label, started_at, ended_at, created_at) "
                    "VALUES (?, 'Deep work', ?, ?, datetime('now'))",
                    (uuid.uuid4().hex[:8], fb_start.isoformat(), fb_end.isoformat()),
                )

            # Unmedicated days with short focus blocks
            for i in range(3):
                day = today - timedelta(days=i + 4)
                fb_start = datetime.combine(day, time(9, 0), tzinfo=UTC)
                fb_end = fb_start + timedelta(minutes=30)
                await db.execute(
                    "INSERT INTO focus_blocks (id, label, started_at, ended_at, created_at) "
                    "VALUES (?, 'Attempt', ?, ?, datetime('now'))",
                    (uuid.uuid4().hex[:8], fb_start.isoformat(), fb_end.isoformat()),
                )
            await db.commit()

        insights = await engine.get_insights(window_days=7, min_confidence=0.3)
        med_focus = [i for i in insights if i.type == "medication_focus"]
        assert len(med_focus) == 1
        assert med_focus[0].domain == "adhd"

    async def test_insight_available_event_emission(self, tmp_path):
        """INSIGHT_AVAILABLE events are emitted for each generated insight."""
        engine, db_path = await _make_insight_engine(
            tmp_path, crash_periods=[(14, 16)]
        )
        emitter = EventEmitter()
        engine.subscribe_events(emitter)

        received: list[dict] = []
        async def handler(payload: dict) -> None:
            received.append(payload)

        emitter.on(EventType.INSIGHT_AVAILABLE, handler)

        now = datetime.now(UTC)
        today = now.date()

        async with aiosqlite.connect(str(db_path)) as db:
            for i in range(3):
                day = today - timedelta(days=i)
                starts = datetime.combine(day, time(14, 30), tzinfo=UTC)
                ends = starts + timedelta(hours=1)
                await db.execute(
                    "INSERT INTO calendar_entries "
                    "(id, kind, title, starts_at, ends_at, source) "
                    "VALUES (?, 'event', ?, ?, ?, 'kora')",
                    (
                        uuid.uuid4().hex[:8],
                        f"Meeting {i}",
                        starts.isoformat(),
                        ends.isoformat(),
                    ),
                )
            await db.commit()

        insights = await engine.get_insights(window_days=7, min_confidence=0.3)
        assert len(received) == len(insights)
        assert all(
            r["event_type"] == EventType.INSIGHT_AVAILABLE for r in received
        )

    async def test_get_insights_returns_empty_no_patterns(self, tmp_path):
        """get_insights returns empty list when no patterns detected."""
        engine, db_path = await _make_insight_engine(tmp_path)

        # Insert some data that doesn't trigger any rules
        async with aiosqlite.connect(str(db_path)) as db:
            now = datetime.now(UTC)
            today = now.date()
            # Event in peak window (not crash period)
            starts = datetime.combine(today, time(10, 0), tzinfo=UTC)
            ends = starts + timedelta(hours=1)
            await db.execute(
                "INSERT INTO calendar_entries "
                "(id, kind, title, starts_at, ends_at, source) "
                "VALUES (?, 'event', 'OK Meeting', ?, ?, 'kora')",
                (uuid.uuid4().hex[:8], starts.isoformat(), ends.isoformat()),
            )
            await db.commit()

        insights = await engine.get_insights(window_days=7, min_confidence=0.5)
        assert insights == []

    async def test_emotional_pattern_detection(self, tmp_path):
        """Rule 4: Repeated low energy at same time of day."""
        engine, db_path = await _make_insight_engine(tmp_path)

        now = datetime.now(UTC)
        today = now.date()

        async with aiosqlite.connect(str(db_path)) as db:
            # Insert several afternoon low-energy self-reports
            for i in range(5):
                day = today - timedelta(days=i)
                logged = datetime.combine(day, time(15, 0), tzinfo=UTC)
                await db.execute(
                    "INSERT INTO energy_log (id, level, focus, source, logged_at) "
                    "VALUES (?, 'low', 'scattered', 'self_report', ?)",
                    (uuid.uuid4().hex[:8], logged.isoformat()),
                )
            await db.commit()

        insights = await engine.get_insights(window_days=7, min_confidence=0.3)
        emotional = [i for i in insights if i.type == "emotional_pattern"]
        assert len(emotional) >= 1
        assert emotional[0].domain == "emotional"

    async def test_sleep_energy_correlation(self, tmp_path):
        """Rule 5: Late nights correlate with low morning energy."""
        engine, db_path = await _make_insight_engine(tmp_path)

        now = datetime.now(UTC)
        today = now.date()

        async with aiosqlite.connect(str(db_path)) as db:
            # Late night activity + next morning low energy
            for i in range(3):
                day = today - timedelta(days=i + 1)
                # Late night entry
                late = datetime.combine(day, time(23, 30), tzinfo=UTC)
                await db.execute(
                    "INSERT INTO energy_log (id, level, focus, source, logged_at) "
                    "VALUES (?, 'medium', 'normal', 'self_report', ?)",
                    (uuid.uuid4().hex[:8], late.isoformat()),
                )
                # Next morning low energy
                next_day = day + timedelta(days=1)
                morning = datetime.combine(next_day, time(8, 0), tzinfo=UTC)
                await db.execute(
                    "INSERT INTO energy_log (id, level, focus, source, logged_at) "
                    "VALUES (?, 'low', 'scattered', 'self_report', ?)",
                    (uuid.uuid4().hex[:8], morning.isoformat()),
                )

            # Normal nights with good morning energy
            for i in range(3):
                day = today - timedelta(days=i + 4)
                morning = datetime.combine(day, time(8, 0), tzinfo=UTC)
                await db.execute(
                    "INSERT INTO energy_log (id, level, focus, source, logged_at) "
                    "VALUES (?, 'high', 'normal', 'self_report', ?)",
                    (uuid.uuid4().hex[:8], morning.isoformat()),
                )
            await db.commit()

        insights = await engine.get_insights(window_days=7, min_confidence=0.3)
        sleep = [i for i in insights if i.type == "sleep_energy"]
        assert len(sleep) == 1
        assert sleep[0].domain == "health"

    async def test_insight_model_fields(self, tmp_path):
        """Insight model has all required fields."""
        insight = Insight(
            type="test",
            title="Test insight",
            description="A test",
            confidence=0.8,
            domain="adhd",
            evidence=["evidence 1"],
        )
        assert insight.type == "test"
        assert insight.confidence == 0.8
        assert insight.generated_at is not None
