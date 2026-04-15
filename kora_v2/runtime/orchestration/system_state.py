"""SystemStatePhase enum and derivation machine.

The phase encodes Kora's coarse operational mode so the dispatcher can
gate worker tasks on top of it. Seven phases cover the full day: active
conversation, three tiers of idleness, a wake-up window, the user's DND
block, and their declared sleep hours.

The state machine is small and single-source: it holds the most recent
``session_active`` flag plus the timestamps needed to compute the time-
based phases, and it emits ``SYSTEM_STATE_CHANGED`` on the event bus
whenever the computed phase actually moves.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import aiosqlite
import structlog

if TYPE_CHECKING:
    from kora_v2.core.events import EventEmitter

log = structlog.get_logger(__name__)


class SystemStatePhase(StrEnum):
    """Orchestration layer's view of Kora's current operational phase."""

    CONVERSATION = "conversation"
    ACTIVE_IDLE = "active_idle"
    LIGHT_IDLE = "light_idle"
    DEEP_IDLE = "deep_idle"
    WAKE_UP_WINDOW = "wake_up_window"
    DND = "dnd"
    SLEEPING = "sleeping"


ACTIVE_IDLE_SECONDS = 300          # < 5 min since session end
LIGHT_IDLE_SECONDS = 3600          # < 1 hour since session end
WAKE_UP_WINDOW_MINUTES = 30        # 30 min before wake time


@dataclass
class UserScheduleProfile:
    """Per-user time anchors used to compute time-based phases.

    All ``time`` objects are interpreted in ``timezone`` (IANA name).
    ``None`` fields mean "the corresponding phase is never entered"
    (e.g. a user with no DND window defined never lands in DND).

    Spec §16.3 extends the profile with the fields needed by the
    orchestration gate and the weekly-review pipeline:

    * ``weekly_review_time`` — the (weekday, time) anchor used by the
      ``weekly_review`` pipeline's cron trigger. Stored as a plain
      ``time`` plus a 0-indexed weekday so the trigger engine can do
      its own cron expansion.
    * ``hyperfocus_suppression`` — toggled by the user via the life
      surface to ask Kora to hold non-urgent notifications while a
      hyperfocus window is active. Defaults to True for safety.
    """

    timezone: str = "UTC"
    wake_time: time | None = None
    sleep_start: time | None = None
    sleep_end: time | None = None
    dnd_start: time | None = None
    dnd_end: time | None = None
    weekly_review_time: time | None = None
    weekly_review_weekday: int | None = None  # 0 = Monday .. 6 = Sunday
    hyperfocus_suppression: bool = True

    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


def _in_window(now_local: time, start: time | None, end: time | None) -> bool:
    """Return True if *now_local* falls inside the [start, end] window.

    Handles wrap-around windows (e.g. 22:00 → 08:00) by OR-ing the two
    halves. Either bound being ``None`` disables the window entirely.
    """
    if start is None or end is None:
        return False
    if start <= end:
        return start <= now_local < end
    return now_local >= start or now_local < end


class SystemStateMachine:
    """Compute and publish the current :class:`SystemStatePhase`.

    The machine is *pull-plus-push*: consumers can call
    :meth:`current_phase` at any time and get an immediately-valid
    answer, and the machine also re-evaluates on session events + an
    external periodic tick (``SYSTEM_STATE_CHECK``) so the dispatcher
    sees transitions promptly.

    It does not own its own timer — the engine's dispatcher calls
    :meth:`tick` on a cadence. This keeps the module importable without
    pulling in asyncio and keeps tests deterministic.
    """

    def __init__(
        self,
        profile: UserScheduleProfile | None = None,
        *,
        event_emitter: EventEmitter | None = None,
        db_path: Path | None = None,
    ) -> None:
        self._profile = profile or UserScheduleProfile()
        self._emitter = event_emitter
        self._db_path = db_path
        self._session_active: bool = False
        self._last_session_ended_at: datetime | None = None
        # `_last_phase` tracks the last *computed* phase for the sync
        # tick() path, while `_last_published_phase` tracks what the
        # async publish_if_changed() last announced. They are kept
        # separate so the sync log-only path does not silently swallow
        # a transition that the dispatcher would otherwise emit.
        self._last_phase: SystemStatePhase | None = None
        self._last_published_phase: SystemStatePhase | None = None

    def update_profile(self, profile: UserScheduleProfile) -> None:
        self._profile = profile

    def note_session_start(self, now: datetime) -> SystemStatePhase:
        self._session_active = True
        return self._recompute(now, reason="session_start")

    def note_session_end(self, now: datetime) -> SystemStatePhase:
        self._session_active = False
        self._last_session_ended_at = now
        return self._recompute(now, reason="session_end")

    def tick(self, now: datetime) -> SystemStatePhase:
        return self._recompute(now, reason="tick")

    def current_phase(self, now: datetime) -> SystemStatePhase:
        return self._compute_phase(now)

    async def publish_if_changed(self, now: datetime, reason: str) -> SystemStatePhase:
        """Compute, publish on change, and return the current phase.

        Per spec §6.3: every phase transition publishes
        ``SYSTEM_STATE_CHANGED(previous, new, reason)`` on the event
        bus and writes a row to ``system_state_log`` for audit. This is
        the only path that should be used at runtime — :meth:`tick` is
        a sync alternative for callers that cannot await.
        """
        new_phase = self._compute_phase(now)
        # Compare against the last *published* phase, not the last
        # computed one — the sync tick()/note_* path may have advanced
        # the computed cursor without firing an event.
        if new_phase != self._last_published_phase:
            previous = self._last_published_phase
            self._last_published_phase = new_phase
            self._last_phase = new_phase
            log.debug(
                "system_state_changed",
                previous=previous.value if previous else None,
                new=new_phase.value,
                reason=reason,
            )
            await self._record_transition(now, previous, new_phase, reason)
            if self._emitter is not None:
                from kora_v2.core.events import EventType

                await self._emitter.emit(
                    EventType.SYSTEM_STATE_CHANGED,
                    previous_phase=previous.value if previous else None,
                    new_phase=new_phase.value,
                    reason=reason,
                )
        return new_phase

    async def _record_transition(
        self,
        now: datetime,
        previous: SystemStatePhase | None,
        new: SystemStatePhase,
        reason: str,
    ) -> None:
        """Write a row to ``system_state_log`` (spec §16.1)."""
        if self._db_path is None:
            return
        try:
            async with aiosqlite.connect(str(self._db_path)) as db:
                await db.execute(
                    "INSERT INTO system_state_log "
                    "(transitioned_at, previous_phase, new_phase, reason, context_json) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        now.astimezone(UTC).isoformat(),
                        previous.value if previous else "",
                        new.value,
                        reason,
                        json.dumps({}),
                    ),
                )
                await db.commit()
        except Exception:
            log.exception("system_state_log_write_failed")

    # ── internal ──────────────────────────────────────────────

    def _recompute(self, now: datetime, reason: str) -> SystemStatePhase:
        new_phase = self._compute_phase(now)
        if new_phase != self._last_phase:
            log.debug(
                "system_state_changed",
                previous=self._last_phase.value if self._last_phase else None,
                new=new_phase.value,
                reason=reason,
            )
        self._last_phase = new_phase
        return new_phase

    def _compute_phase(self, now: datetime) -> SystemStatePhase:
        if self._session_active:
            return SystemStatePhase.CONVERSATION

        local = now.astimezone(self._profile.tz())
        local_t = local.time()

        # Strictest first: sleeping is a stricter subset of DND.
        if _in_window(local_t, self._profile.sleep_start, self._profile.sleep_end):
            return SystemStatePhase.SLEEPING
        if _in_window(local_t, self._profile.dnd_start, self._profile.dnd_end):
            return SystemStatePhase.DND
        if self._profile.wake_time is not None and self._in_wake_window(local):
            return SystemStatePhase.WAKE_UP_WINDOW

        if self._last_session_ended_at is None:
            return SystemStatePhase.DEEP_IDLE

        since_end = (now - self._last_session_ended_at).total_seconds()
        if since_end < ACTIVE_IDLE_SECONDS:
            return SystemStatePhase.ACTIVE_IDLE
        if since_end < LIGHT_IDLE_SECONDS:
            return SystemStatePhase.LIGHT_IDLE
        return SystemStatePhase.DEEP_IDLE

    def _in_wake_window(self, local: datetime) -> bool:
        wake = self._profile.wake_time
        if wake is None:
            return False
        wake_dt = local.replace(
            hour=wake.hour,
            minute=wake.minute,
            second=0,
            microsecond=0,
        )
        start_dt = wake_dt - timedelta(minutes=WAKE_UP_WINDOW_MINUTES)
        return start_dt <= local < wake_dt
