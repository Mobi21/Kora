"""Trigger factory + composition unit tests."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

from kora_v2.runtime.orchestration.triggers import (
    TriggerContext,
    TriggerKind,
    all_of,
    any_of,
    condition,
    event,
    interval,
    sequence_complete,
    time_of_day,
    user_action,
)


def _ctx(now: datetime, **kwargs) -> TriggerContext:
    return TriggerContext(now=now, **kwargs)


def test_interval_fires_initially_and_after_interval() -> None:
    t0 = datetime(2026, 4, 14, 12, 0, 0, tzinfo=UTC)
    trig = interval("p", every=timedelta(minutes=10))
    assert trig.should_fire(_ctx(t0)) is True
    trig.mark_fired(t0)
    # Immediately after firing -> not ready
    assert trig.should_fire(_ctx(t0 + timedelta(minutes=5))) is False
    # After the interval -> ready again
    assert trig.should_fire(_ctx(t0 + timedelta(minutes=11))) is True


def test_interval_honours_cooldown() -> None:
    t0 = datetime(2026, 4, 14, 12, 0, 0, tzinfo=UTC)
    trig = interval(
        "p",
        every=timedelta(minutes=5),
        cooldown=timedelta(minutes=10),
    )
    trig.mark_fired(t0)
    # After interval window but still in cooldown -> suppressed
    assert trig.should_fire(_ctx(t0 + timedelta(minutes=6))) is False
    assert trig.should_fire(_ctx(t0 + timedelta(minutes=11))) is True


def test_event_trigger_requires_payload() -> None:
    t0 = datetime(2026, 4, 14, 12, 0, 0, tzinfo=UTC)
    trig = event("p", event_type="memory_stored")
    assert trig.should_fire(_ctx(t0)) is False
    ctx = _ctx(t0, last_event_payloads={"memory_stored": {"id": "m1"}})
    assert trig.should_fire(ctx) is True


def test_condition_trigger_uses_predicate() -> None:
    t0 = datetime(2026, 4, 14, 12, 0, 0, tzinfo=UTC)
    trig = condition("p", predicate=lambda now, ctx: ctx.get("flag", False))
    assert trig.should_fire(_ctx(t0)) is False
    assert trig.should_fire(_ctx(t0, extra={"flag": True})) is True


def test_time_of_day_fires_once_per_day() -> None:
    target = time(9, 0)
    trig = time_of_day("p", at=target)
    morning = datetime(2026, 4, 14, 9, 0, 0, tzinfo=UTC)
    assert trig.should_fire(_ctx(morning)) is True
    trig.mark_fired(morning)
    # Same day, later: do not re-fire
    assert trig.should_fire(_ctx(morning + timedelta(hours=2))) is False
    # Next day: fires
    next_day = morning + timedelta(days=1)
    assert trig.should_fire(_ctx(next_day)) is True


def test_sequence_and_user_action_triggers() -> None:
    t0 = datetime(2026, 4, 14, 12, 0, 0, tzinfo=UTC)
    seq = sequence_complete("p", sequence_name="morning_routine")
    ua = user_action("p", action_name="logged_meds")
    assert seq.should_fire(_ctx(t0, completed_sequences={"morning_routine"})) is True
    assert ua.should_fire(_ctx(t0, user_actions={"logged_meds"})) is True
    assert seq.should_fire(_ctx(t0)) is False
    assert ua.should_fire(_ctx(t0)) is False


def test_any_of_fires_when_any_child_matches() -> None:
    t0 = datetime(2026, 4, 14, 12, 0, 0, tzinfo=UTC)
    compound = any_of(
        "p",
        event("p", event_type="a"),
        event("p", event_type="b"),
    )
    assert compound.should_fire(_ctx(t0)) is False
    ctx = _ctx(t0, last_event_payloads={"b": {}})
    assert compound.should_fire(ctx) is True


def test_all_of_requires_every_child() -> None:
    t0 = datetime(2026, 4, 14, 12, 0, 0, tzinfo=UTC)
    compound = all_of(
        "p",
        event("p", event_type="a"),
        event("p", event_type="b"),
    )
    ctx = _ctx(t0, last_event_payloads={"a": {}})
    assert compound.should_fire(ctx) is False
    ctx2 = _ctx(t0, last_event_payloads={"a": {}, "b": {}})
    assert compound.should_fire(ctx2) is True


def test_kinds_are_set_correctly() -> None:
    assert interval("p", every=timedelta(seconds=1)).kind is TriggerKind.INTERVAL
    assert event("p", event_type="x").kind is TriggerKind.EVENT
    assert condition("p", predicate=lambda n, c: True).kind is TriggerKind.CONDITION
    assert time_of_day("p", at=time(0, 0)).kind is TriggerKind.TIME_OF_DAY
    assert sequence_complete("p", sequence_name="s").kind is TriggerKind.SEQUENCE_COMPLETE
    assert user_action("p", action_name="a").kind is TriggerKind.USER_ACTION
    assert any_of("p").kind is TriggerKind.ANY_OF
    assert all_of("p").kind is TriggerKind.ALL_OF
