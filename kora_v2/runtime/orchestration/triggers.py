"""Trigger primitives — spec §6.

Six base kinds (``interval``, ``event``, ``condition``, ``time_of_day``,
``sequence_complete``, ``user_action``) plus two combinators
(:func:`any_of`, :func:`all_of`). Triggers are evaluated by the
:class:`~kora_v2.runtime.orchestration.dispatcher.Dispatcher` at every
tick; the machinery is deliberately pull-based so tests can drive
evaluation from a fake clock without wiring a timer into the module.

Each trigger exposes three hooks:

    * ``should_fire(now, context)`` — compute whether it is eligible
    * ``mark_fired(now, reason)`` — update internal last-fire state
    * ``next_eligible(now)`` — advisory timestamp, used for logging
      and for the dispatcher's sleep hint

Last-fire state lives on the trigger instance. The registry is
responsible for persisting/loading that state to the
``trigger_state`` SQL table so cooldowns survive a process restart.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from enum import StrEnum
from typing import Any


class TriggerKind(StrEnum):
    """The six base trigger kinds plus the two composition kinds."""

    INTERVAL = "interval"
    EVENT = "event"
    CONDITION = "condition"
    TIME_OF_DAY = "time_of_day"
    SEQUENCE_COMPLETE = "sequence_complete"
    USER_ACTION = "user_action"
    ANY_OF = "any_of"
    ALL_OF = "all_of"


ConditionFn = Callable[[datetime, dict[str, Any]], bool]


@dataclass
class TriggerContext:
    """Evaluation context passed to ``should_fire``.

    The dispatcher builds a fresh context every tick and hands it to
    each trigger. Triggers must not cache the context across ticks.
    """

    now: datetime
    last_event_payloads: dict[str, dict[str, Any]] = field(default_factory=dict)
    completed_sequences: set[str] = field(default_factory=set)
    user_actions: set[str] = field(default_factory=set)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Trigger:
    """Unified trigger dataclass.

    Construction is typically done via the factory functions below
    (:func:`interval`, :func:`event`, etc.) rather than by hand.
    """

    id: str
    kind: TriggerKind
    pipeline_name: str
    description: str = ""

    # Shared cooldown + dedup state
    last_fired_at: datetime | None = None
    cooldown: timedelta | None = None
    min_interval: timedelta | None = None

    # Kind-specific config
    interval: timedelta | None = None
    event_type: str | None = None
    condition_fn: ConditionFn | None = None
    time_of_day_local: time | None = None
    sequence_name: str | None = None
    user_action_name: str | None = None
    children: list[Trigger] = field(default_factory=list)

    # Metadata
    allowed_phases: frozenset[str] | None = None

    # ── Evaluation ───────────────────────────────────────────────
    def should_fire(self, ctx: TriggerContext) -> bool:
        if self._in_cooldown(ctx.now):
            return False

        if self.kind is TriggerKind.INTERVAL:
            return self._eval_interval(ctx.now)
        if self.kind is TriggerKind.EVENT:
            return self._eval_event(ctx)
        if self.kind is TriggerKind.CONDITION:
            return bool(self.condition_fn and self.condition_fn(ctx.now, ctx.extra))
        if self.kind is TriggerKind.TIME_OF_DAY:
            return self._eval_time_of_day(ctx.now)
        if self.kind is TriggerKind.SEQUENCE_COMPLETE:
            return bool(self.sequence_name and self.sequence_name in ctx.completed_sequences)
        if self.kind is TriggerKind.USER_ACTION:
            return bool(self.user_action_name and self.user_action_name in ctx.user_actions)
        if self.kind is TriggerKind.ANY_OF:
            return any(child.should_fire(ctx) for child in self.children)
        if self.kind is TriggerKind.ALL_OF:
            return all(child.should_fire(ctx) for child in self.children) if self.children else False
        return False

    def mark_fired(self, now: datetime) -> None:
        self.last_fired_at = now
        for child in self.children:
            child.mark_fired(now)

    def next_eligible(self, now: datetime) -> datetime | None:
        """Best-effort next eligibility timestamp.

        Interval and time-of-day triggers can give a concrete next time;
        event/condition/sequence triggers return ``None`` because they
        depend on external signals.
        """
        if self.kind is TriggerKind.INTERVAL and self.interval is not None:
            base = self.last_fired_at or now
            return base + self.interval
        if self.kind is TriggerKind.TIME_OF_DAY and self.time_of_day_local is not None:
            return _next_time_of_day(now, self.time_of_day_local)
        return None

    def _in_cooldown(self, now: datetime) -> bool:
        if self.last_fired_at is None:
            return False
        if self.cooldown is not None and now - self.last_fired_at < self.cooldown:
            return True
        if self.min_interval is not None and now - self.last_fired_at < self.min_interval:
            return True
        return False

    def _eval_interval(self, now: datetime) -> bool:
        if self.interval is None:
            return False
        if self.last_fired_at is None:
            return True
        return now - self.last_fired_at >= self.interval

    def _eval_event(self, ctx: TriggerContext) -> bool:
        if self.event_type is None:
            return False
        if self.event_type not in ctx.last_event_payloads:
            return False
        # Basic at-least-once: if a new event payload is present, fire.
        return True

    def _eval_time_of_day(self, now: datetime) -> bool:
        if self.time_of_day_local is None:
            return False
        target = self.time_of_day_local
        # Fire when we cross the target second (coarse granularity is
        # fine — the dispatcher ticks more often than once per second)
        now_t = now.time()
        if self.last_fired_at is None:
            return now_t >= target
        # Already fired today? Wait for next day.
        same_day = self.last_fired_at.date() == now.date()
        if same_day:
            return False
        return now_t >= target


# ── Factories ─────────────────────────────────────────────────────────────


def interval(
    pipeline_name: str,
    *,
    every: timedelta,
    id: str | None = None,
    description: str = "",
    cooldown: timedelta | None = None,
    allowed_phases: Iterable[str] | None = None,
) -> Trigger:
    return Trigger(
        id=id or f"{pipeline_name}.interval",
        kind=TriggerKind.INTERVAL,
        pipeline_name=pipeline_name,
        description=description,
        interval=every,
        cooldown=cooldown,
        allowed_phases=frozenset(allowed_phases) if allowed_phases else None,
    )


def event(
    pipeline_name: str,
    *,
    event_type: str,
    id: str | None = None,
    description: str = "",
    cooldown: timedelta | None = None,
) -> Trigger:
    return Trigger(
        id=id or f"{pipeline_name}.event.{event_type}",
        kind=TriggerKind.EVENT,
        pipeline_name=pipeline_name,
        description=description,
        event_type=event_type,
        cooldown=cooldown,
    )


def condition(
    pipeline_name: str,
    *,
    predicate: ConditionFn,
    id: str | None = None,
    description: str = "",
    cooldown: timedelta | None = None,
    min_interval: timedelta | None = None,
) -> Trigger:
    return Trigger(
        id=id or f"{pipeline_name}.condition",
        kind=TriggerKind.CONDITION,
        pipeline_name=pipeline_name,
        description=description,
        condition_fn=predicate,
        cooldown=cooldown,
        min_interval=min_interval,
    )


def time_of_day(
    pipeline_name: str,
    *,
    at: time,
    id: str | None = None,
    description: str = "",
) -> Trigger:
    return Trigger(
        id=id or f"{pipeline_name}.time.{at.isoformat(timespec='minutes')}",
        kind=TriggerKind.TIME_OF_DAY,
        pipeline_name=pipeline_name,
        description=description,
        time_of_day_local=at,
    )


def sequence_complete(
    pipeline_name: str,
    *,
    sequence_name: str,
    id: str | None = None,
    description: str = "",
) -> Trigger:
    return Trigger(
        id=id or f"{pipeline_name}.seq.{sequence_name}",
        kind=TriggerKind.SEQUENCE_COMPLETE,
        pipeline_name=pipeline_name,
        description=description,
        sequence_name=sequence_name,
    )


def user_action(
    pipeline_name: str,
    *,
    action_name: str,
    id: str | None = None,
    description: str = "",
) -> Trigger:
    return Trigger(
        id=id or f"{pipeline_name}.user.{action_name}",
        kind=TriggerKind.USER_ACTION,
        pipeline_name=pipeline_name,
        description=description,
        user_action_name=action_name,
    )


def any_of(
    pipeline_name: str,
    *children: Trigger,
    id: str | None = None,
    description: str = "",
) -> Trigger:
    return Trigger(
        id=id or f"{pipeline_name}.any_of",
        kind=TriggerKind.ANY_OF,
        pipeline_name=pipeline_name,
        description=description,
        children=list(children),
    )


def all_of(
    pipeline_name: str,
    *children: Trigger,
    id: str | None = None,
    description: str = "",
) -> Trigger:
    return Trigger(
        id=id or f"{pipeline_name}.all_of",
        kind=TriggerKind.ALL_OF,
        pipeline_name=pipeline_name,
        description=description,
        children=list(children),
    )


# ── Helpers ───────────────────────────────────────────────────────────────


def _next_time_of_day(now: datetime, target: time) -> datetime:
    """Return the next wall-clock datetime matching ``target``."""
    candidate = now.replace(
        hour=target.hour,
        minute=target.minute,
        second=target.second,
        microsecond=0,
    )
    if candidate <= now:
        candidate = candidate + timedelta(days=1)
    return candidate


def utc_now() -> datetime:
    return datetime.now(UTC)
