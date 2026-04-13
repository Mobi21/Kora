"""Unit tests for the ADHD neurodivergent support module (Phase 5)."""

from __future__ import annotations

import re
from datetime import date, datetime

from kora_v2.adhd import (
    ADHDModule,
    ADHDProfile,
)
from kora_v2.adhd.module import (
    BUSY_MORNING_ADJUSTMENT,
    BUSY_MORNING_THRESHOLD,
    CRASH_WINDOW_ADJUSTMENT,
    MEDS_MISSED_ADJUSTMENT,
    MEDS_TAKEN_ADJUSTMENT,
    OPEN_MORNING_ADJUSTMENT,
    PEAK_WINDOW_ADJUSTMENT,
)
from kora_v2.core.calendar_models import CalendarEntry
from kora_v2.core.models import DayContext, MedicationStatus


def _make_profile(**kwargs) -> ADHDProfile:
    return ADHDProfile(**kwargs)


def _make_entry(hour: int, title: str = "Meeting") -> CalendarEntry:
    ts = datetime(2026, 4, 12, hour, 0)
    return CalendarEntry(
        id=f"e{hour}",
        kind="event",
        title=title,
        starts_at=ts,
        ends_at=ts,
        created_at=ts,
        updated_at=ts,
    )


class TestEnergySignals:
    def test_no_data_yields_no_signals(self):
        mod = ADHDModule(_make_profile())
        dc = DayContext(date=date.today(), day_of_week="Mon")
        signals = mod.energy_signals(dc)
        # No med schedule, no meetings, no peak/crash → nothing to say.
        assert signals == []

    def test_meds_taken_contributes_positive_signal(self):
        mod = ADHDModule(_make_profile())
        dc = DayContext(
            date=date.today(),
            day_of_week="Mon",
            medication_status=MedicationStatus(
                taken=[{"name": "Adderall", "taken_at": "08:15"}],
            ),
        )
        signals = mod.energy_signals(dc)
        sources = {s.source for s in signals}
        assert "medication" in sources
        med_sig = next(s for s in signals if s.source == "medication")
        assert med_sig.level_adjustment == MEDS_TAKEN_ADJUSTMENT
        assert "08:15" in med_sig.description

    def test_meds_missed_contributes_negative_signal(self):
        mod = ADHDModule(_make_profile())
        dc = DayContext(
            date=date.today(),
            day_of_week="Mon",
            medication_status=MedicationStatus(
                missed=[
                    {"name": "Adderall IR", "window": "13:00-15:00", "hours_overdue": 2.0}
                ],
            ),
        )
        signals = mod.energy_signals(dc)
        assert any(s.level_adjustment == MEDS_MISSED_ADJUSTMENT for s in signals)

    def test_busy_morning_fires_above_threshold(self):
        mod = ADHDModule(_make_profile())
        schedule = [_make_entry(h) for h in range(8, 8 + BUSY_MORNING_THRESHOLD)]
        dc = DayContext(
            date=date.today(), day_of_week="Mon", schedule=schedule
        )
        signals = mod.energy_signals(dc)
        assert any(
            s.source == "calendar_load"
            and s.level_adjustment == BUSY_MORNING_ADJUSTMENT
            for s in signals
        )

    def test_open_morning_fires_only_before_noon(self):
        mod = ADHDModule(_make_profile())
        dc = DayContext(date=date.today(), day_of_week="Mon", schedule=[])
        # Force "now" to 9am.
        now = datetime(2026, 4, 12, 9, 0)
        signals = mod.energy_signals(dc, now=now)
        assert any(
            s.source == "calendar_load"
            and s.level_adjustment == OPEN_MORNING_ADJUSTMENT
            for s in signals
        )

    def test_peak_window_fires_when_in_range(self):
        profile = _make_profile(peak_windows=[(9, 12)])
        mod = ADHDModule(profile)
        dc = DayContext(date=date.today(), day_of_week="Mon")
        now = datetime(2026, 4, 12, 10, 0)
        signals = mod.energy_signals(dc, now=now)
        assert any(
            s.source == "time_of_day"
            and s.level_adjustment == PEAK_WINDOW_ADJUSTMENT
            for s in signals
        )

    def test_crash_window_fires_when_in_range(self):
        profile = _make_profile(crash_periods=[(14, 16)])
        mod = ADHDModule(profile)
        dc = DayContext(date=date.today(), day_of_week="Mon")
        now = datetime(2026, 4, 12, 15, 0)
        signals = mod.energy_signals(dc, now=now)
        assert any(
            s.source == "time_of_day"
            and s.level_adjustment == CRASH_WINDOW_ADJUSTMENT
            for s in signals
        )


class TestFocusDetection:
    def test_hyperfocus_fires_at_exact_boundary(self):
        mod = ADHDModule(_make_profile())
        state = mod.focus_detection(turns_in_topic=3, session_minutes=45)
        assert state.level == "locked_in"
        assert state.hyperfocus_mode is True

    def test_just_below_hyperfocus_boundary(self):
        mod = ADHDModule(_make_profile())
        state = mod.focus_detection(turns_in_topic=3, session_minutes=44)
        assert state.hyperfocus_mode is False

    def test_scattered_when_session_is_fresh(self):
        mod = ADHDModule(_make_profile())
        state = mod.focus_detection(turns_in_topic=1, session_minutes=3)
        assert state.level == "scattered"


class TestOutputRules:
    def test_banned_phrases_flag(self):
        mod = ADHDModule(_make_profile())
        rules = mod.output_rules()
        banned = next(r for r in rules if r.name == "banned_phrases")
        assert re.search(banned.pattern, "you forgot to log", re.IGNORECASE)

    def test_failure_context_again_flags_failure(self):
        mod = ADHDModule(_make_profile())
        rules = mod.output_rules()
        rule = next(r for r in rules if r.name == "failure_context_again")
        # Failure context → flag.
        assert re.search(
            rule.pattern, "you missed that again", re.IGNORECASE
        )

    def test_failure_context_again_passes_neutral(self):
        mod = ADHDModule(_make_profile())
        rules = mod.output_rules()
        rule = next(r for r in rules if r.name == "failure_context_again")
        # Neutral context → must NOT flag.
        assert not re.search(
            rule.pattern, "tell me that again please", re.IGNORECASE
        )


class TestPlanningAdjustments:
    def test_defaults(self):
        mod = ADHDModule(_make_profile())
        pc = mod.planning_adjustments()
        assert pc.time_correction_factor == 1.5
        assert pc.max_steps_per_plan == 7
        assert pc.first_step_max_minutes == 10
        assert pc.require_micro_step_first is True

    def test_custom_correction_factor_flows_through(self):
        mod = ADHDModule(_make_profile(time_correction_factor=2.0))
        pc = mod.planning_adjustments()
        assert pc.time_correction_factor == 2.0
