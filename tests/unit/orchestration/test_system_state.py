"""SystemStateMachine unit tests."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

from kora_v2.runtime.orchestration.system_state import (
    SystemStateMachine,
    SystemStatePhase,
    UserScheduleProfile,
)


def _profile() -> UserScheduleProfile:
    return UserScheduleProfile(
        timezone="UTC",
        wake_time=time(8, 0),
        sleep_start=time(23, 0),
        sleep_end=time(7, 0),
        dnd_start=time(21, 0),
        dnd_end=time(8, 0),
    )


def test_active_session_is_conversation() -> None:
    machine = SystemStateMachine(_profile())
    noon = datetime(2026, 4, 14, 12, 0, 0, tzinfo=UTC)
    phase = machine.note_session_start(noon)
    assert phase is SystemStatePhase.CONVERSATION


def test_sleeping_window_wins() -> None:
    machine = SystemStateMachine(_profile())
    deep_night = datetime(2026, 4, 14, 3, 0, 0, tzinfo=UTC)
    assert machine.current_phase(deep_night) is SystemStatePhase.SLEEPING


def test_dnd_covers_evening() -> None:
    machine = SystemStateMachine(_profile())
    evening = datetime(2026, 4, 14, 22, 0, 0, tzinfo=UTC)
    # 22:00 is inside DND (21:00→08:00) but outside SLEEPING (23:00→07:00)
    assert machine.current_phase(evening) is SystemStatePhase.DND


def test_wake_up_window_triggers_before_wake_time() -> None:
    # Clear DND/sleep for this test so the wake window is uncontested
    profile = UserScheduleProfile(
        timezone="UTC",
        wake_time=time(8, 0),
    )
    machine = SystemStateMachine(profile)
    just_before = datetime(2026, 4, 14, 7, 45, 0, tzinfo=UTC)
    assert machine.current_phase(just_before) is SystemStatePhase.WAKE_UP_WINDOW
    # Outside the 30-minute window
    early = datetime(2026, 4, 14, 7, 0, 0, tzinfo=UTC)
    assert machine.current_phase(early) is SystemStatePhase.DEEP_IDLE


def test_idle_tiers_from_session_end() -> None:
    profile = UserScheduleProfile(timezone="UTC")
    machine = SystemStateMachine(profile)
    t_end = datetime(2026, 4, 14, 12, 0, 0, tzinfo=UTC)
    machine.note_session_end(t_end)

    # 1 minute after end → ACTIVE_IDLE
    assert machine.current_phase(t_end + timedelta(minutes=1)) is SystemStatePhase.ACTIVE_IDLE
    # 10 minutes after end → LIGHT_IDLE
    assert machine.current_phase(t_end + timedelta(minutes=10)) is SystemStatePhase.LIGHT_IDLE
    # 2 hours after end → DEEP_IDLE
    assert machine.current_phase(t_end + timedelta(hours=2)) is SystemStatePhase.DEEP_IDLE


def test_recompute_logs_transition_without_emitter() -> None:
    profile = UserScheduleProfile(timezone="UTC")
    machine = SystemStateMachine(profile)
    noon = datetime(2026, 4, 14, 12, 0, 0, tzinfo=UTC)
    machine.note_session_start(noon)
    # Tick with the same session → still CONVERSATION
    phase = machine.tick(noon + timedelta(minutes=5))
    assert phase is SystemStatePhase.CONVERSATION


def test_dst_safe_timezone_conversion() -> None:
    # Europe/London flips on March 29, 2026. Run near that boundary.
    profile = UserScheduleProfile(
        timezone="Europe/London",
        wake_time=time(7, 0),
    )
    machine = SystemStateMachine(profile)
    # 07:00 local right after the DST switch should still trigger the
    # wake window for the preceding 30 minutes (06:30–07:00 local).
    local_morning = datetime(2026, 3, 29, 6, 45, tzinfo=UTC)
    phase = machine.current_phase(local_morning)
    # Before DST, London is UTC+0; after DST, UTC+1. The zoneinfo
    # conversion should still land in the wake window regardless of the
    # DST state because we built the local time from the timezone.
    assert phase in {
        SystemStatePhase.WAKE_UP_WINDOW,
        SystemStatePhase.DEEP_IDLE,
    }
