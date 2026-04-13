"""ADHD neurodivergent support module (Phase 5).

Implements ``NeurodivergentModule`` — contributes energy signals from
medication/calendar/time-of-day data, output rules for RSD filtering,
focus detection (hyperfocus), planning adjustments, and check-in
triggers.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, tzinfo
from typing import Any

from kora_v2.adhd.profile import ADHDProfile
from kora_v2.adhd.protocol import (
    CheckInTrigger,
    EnergySignal,
    FocusState,
    OutputRule,
    PlanningConfig,
)

# ── Module-level constants (tweak without touching the protocol) ─────────────

MEDS_TAKEN_ADJUSTMENT = 0.2
MEDS_TAKEN_CONFIDENCE = 0.7
MEDS_MISSED_ADJUSTMENT = -0.3
MEDS_MISSED_CONFIDENCE = 0.8
BUSY_MORNING_ADJUSTMENT = -0.25
BUSY_MORNING_CONFIDENCE = 0.6
BUSY_MORNING_THRESHOLD = 3  # events before noon
OPEN_MORNING_ADJUSTMENT = 0.15
OPEN_MORNING_CONFIDENCE = 0.5
PEAK_WINDOW_ADJUSTMENT = 0.15
CRASH_WINDOW_ADJUSTMENT = -0.2
TIME_OF_DAY_CONFIDENCE = 0.4

HYPERFOCUS_MIN_TURNS = 3
HYPERFOCUS_MIN_MINUTES = 45
FOCUSED_MIN_TURNS = 2
FOCUSED_MIN_MINUTES = 20
SCATTERED_MAX_MINUTES = 5


class ADHDModule:
    """ADHD-specific neurodivergent support."""

    name = "adhd"

    def __init__(self, profile: ADHDProfile):
        self._profile = profile

    @property
    def profile(self) -> ADHDProfile:
        return self._profile

    # ── Energy signals ────────────────────────────────────────────────

    def energy_signals(
        self,
        day_context: Any,
        now: datetime | None = None,
        user_tz: tzinfo | None = None,
    ) -> list[EnergySignal]:
        """Produce a list of signals from today's state.

        ``day_context`` is a ``DayContext`` (or dict) with
        ``medication_status`` and ``schedule`` fields populated.
        ``now`` should be the user's *local* datetime when available
        (the context engine converts UTC to ``Settings.user_tz`` before
        calling this); defaults to ``datetime.now(UTC)`` for tests.
        ``user_tz`` is required for the morning-events comparison
        to use local wall-clock time on calendar entries that are stored
        in UTC; if omitted the entry's ``starts_at.tzinfo`` is used as a
        fallback (which collapses to UTC for tz-aware entries).
        """
        signals: list[EnergySignal] = []
        if now is None:
            now = datetime.now(UTC)
        if user_tz is None and now.tzinfo is not None:
            user_tz = now.tzinfo

        # Medication timing
        med_status = _get_attr(day_context, "medication_status")
        if med_status is not None:
            taken = _get_attr(med_status, "taken") or []
            if taken:
                last_med = taken[-1]
                signals.append(
                    EnergySignal(
                        source="medication",
                        level_adjustment=MEDS_TAKEN_ADJUSTMENT,
                        confidence=MEDS_TAKEN_CONFIDENCE,
                        description=(
                            f"meds taken at {last_med.get('taken_at', 'earlier')}"
                        ),
                        is_guess=True,
                    )
                )
            missed = _get_attr(med_status, "missed") or []
            for m in missed:
                signals.append(
                    EnergySignal(
                        source="medication",
                        level_adjustment=MEDS_MISSED_ADJUSTMENT,
                        confidence=MEDS_MISSED_CONFIDENCE,
                        description=(
                            f"{m.get('name', 'meds')} overdue by "
                            f"{m.get('hours_overdue', '?')}hr"
                        ),
                        is_guess=True,
                    )
                )

        # Calendar load (morning meetings) — compare in the user's
        # local frame so a 9am PST event isn't read as a 5pm UTC event.
        schedule = _get_attr(day_context, "schedule") or []
        morning_events = [
            e
            for e in schedule
            if _get_attr(e, "kind") == "event"
            and _starts_before(e, time(12, 0), user_tz)
        ]
        if len(morning_events) >= BUSY_MORNING_THRESHOLD:
            signals.append(
                EnergySignal(
                    source="calendar_load",
                    level_adjustment=BUSY_MORNING_ADJUSTMENT,
                    confidence=BUSY_MORNING_CONFIDENCE,
                    description=f"{len(morning_events)} meetings already today",
                    is_guess=True,
                )
            )
        elif len(morning_events) == 0 and now.time() < time(12, 0):
            signals.append(
                EnergySignal(
                    source="calendar_load",
                    level_adjustment=OPEN_MORNING_ADJUSTMENT,
                    confidence=OPEN_MORNING_CONFIDENCE,
                    description="light morning so far",
                    is_guess=True,
                )
            )

        # Time-of-day peak/crash windows from profile
        hour = now.hour
        for start, end in self._profile.peak_windows:
            if start <= hour < end:
                signals.append(
                    EnergySignal(
                        source="time_of_day",
                        level_adjustment=PEAK_WINDOW_ADJUSTMENT,
                        confidence=TIME_OF_DAY_CONFIDENCE,
                        description="your usual focus window",
                        is_guess=True,
                    )
                )
                break
        for start, end in self._profile.crash_periods:
            if start <= hour < end:
                signals.append(
                    EnergySignal(
                        source="time_of_day",
                        level_adjustment=CRASH_WINDOW_ADJUSTMENT,
                        confidence=TIME_OF_DAY_CONFIDENCE,
                        description="your usual crash window",
                        is_guess=True,
                    )
                )
                break

        return signals

    # ── Output rules ──────────────────────────────────────────────────

    def output_rules(self) -> list[OutputRule]:
        """Regex-only output filters. Behavioural guidance lives in
        ``output_guidance()``."""
        return [
            OutputRule(
                name="banned_phrases",
                pattern=(
                    r"\b(you forgot|you should have|you didn't|"
                    r"why didn't you|you missed)\b"
                ),
                description="Phrases that trigger rejection sensitivity",
                replacement_guidance=(
                    "Reframe as observation: 'Looks like X hasn't been logged "
                    "yet' not 'You forgot X'"
                ),
            ),
            OutputRule(
                name="failure_context_again",
                # 'again' is only RSD-triggering in a failure/miss context.
                # This pattern flags 'again' within 30 chars of a miss/fail
                # word; pure-neutral 'again' (e.g. 'tell me again') is safe.
                pattern=(
                    r"\b(fail(?:ed)?|miss(?:ed)?|forgot|wrong|broke(?:n)?)"
                    r"[\w\s,\.]{0,30}\bagain\b"
                    r"|\bagain[\w\s,\.]{0,30}"
                    r"(fail(?:ed)?|miss(?:ed)?|forgot|wrong|broke(?:n)?)\b"
                ),
                description=(
                    "'again' in failure context triggers RSD. OK in neutral "
                    "context."
                ),
                replacement_guidance=(
                    "Remove 'again' when discussing missed items or mistakes"
                ),
            ),
        ]

    def output_guidance(self) -> list[str]:
        """Prose guidance injected into the frozen prefix.

        Not programmatically checked — the LLM reads and applies this.
        """
        return [
            (
                "Lead with effort acknowledgment before corrective feedback. "
                "'Nice work on X. For Y, maybe try...' not 'Y needs fixing'."
            ),
            (
                "Frame misses as normal, not failures. 'Happens!' not 'You "
                "failed to...'."
            ),
        ]

    # ── Supervisor context ────────────────────────────────────────────

    def supervisor_context(self) -> dict[str, Any]:
        """Extra prose context the supervisor injects into the frozen
        prefix (currently: overwhelm triggers)."""
        return {
            "overwhelm_triggers": list(self._profile.overwhelm_triggers),
        }

    # ── Focus detection ───────────────────────────────────────────────

    def focus_detection(
        self, turns_in_topic: int, session_minutes: int
    ) -> FocusState:
        """Return the current focus state (includes hyperfocus)."""
        if (
            turns_in_topic >= HYPERFOCUS_MIN_TURNS
            and session_minutes >= HYPERFOCUS_MIN_MINUTES
        ):
            return FocusState(
                level="locked_in",
                turns_at_level=turns_in_topic,
                session_minutes=session_minutes,
                hyperfocus_mode=True,
            )
        if (
            turns_in_topic >= FOCUSED_MIN_TURNS
            and session_minutes >= FOCUSED_MIN_MINUTES
        ):
            return FocusState(
                level="focused",
                turns_at_level=turns_in_topic,
                session_minutes=session_minutes,
                hyperfocus_mode=False,
            )
        if session_minutes < SCATTERED_MAX_MINUTES:
            return FocusState(
                level="scattered",
                turns_at_level=turns_in_topic,
                session_minutes=session_minutes,
                hyperfocus_mode=False,
            )
        return FocusState(
            level="normal",
            turns_at_level=turns_in_topic,
            session_minutes=session_minutes,
            hyperfocus_mode=False,
        )

    # ── Planning / check-in ───────────────────────────────────────────

    def planning_adjustments(self) -> PlanningConfig:
        return PlanningConfig(
            time_correction_factor=self._profile.time_correction_factor,
            max_steps_per_plan=7,
            first_step_max_minutes=10,
            require_micro_step_first=True,
            energy_matching=True,
        )

    def check_in_triggers(self) -> list[CheckInTrigger]:
        return [
            CheckInTrigger(
                name="energy_check",
                interval_minutes=self._profile.check_in_interval_minutes,
                condition="only_if_no_recent_self_report",
                message_template=(
                    "You've been going for {session_minutes}min — how's your "
                    "energy?"
                ),
            ),
        ]

    def profile_schema(self) -> dict[str, Any]:
        return ADHDProfile.model_json_schema()


# ── Helpers ──────────────────────────────────────────────────────────────────


def _get_attr(obj: Any, name: str) -> Any:
    """Get a field from either a pydantic model or a dict."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _starts_before(
    entry: Any, cutoff: time, user_tz: tzinfo | None = None
) -> bool:
    """True if the calendar entry starts before ``cutoff`` in the user's
    local frame.

    ``user_tz`` should be the user's IANA zoneinfo. If it's None and the
    entry is tz-aware, the comparison falls back to the entry's own
    timezone (which is UTC for entries stored in our schema). Naive
    datetimes are compared as-is.
    """
    starts_at = _get_attr(entry, "starts_at")
    if starts_at is None:
        return False
    if isinstance(starts_at, str):
        try:
            starts_at = datetime.fromisoformat(starts_at)
        except ValueError:
            return False
    try:
        if user_tz is not None and starts_at.tzinfo is not None:
            starts_at = starts_at.astimezone(user_tz)
        return starts_at.time() < cutoff
    except AttributeError:
        return False


__all__ = [
    "ADHDModule",
    "BUSY_MORNING_ADJUSTMENT",
    "BUSY_MORNING_CONFIDENCE",
    "BUSY_MORNING_THRESHOLD",
    "CRASH_WINDOW_ADJUSTMENT",
    "MEDS_MISSED_ADJUSTMENT",
    "MEDS_MISSED_CONFIDENCE",
    "MEDS_TAKEN_ADJUSTMENT",
    "MEDS_TAKEN_CONFIDENCE",
    "OPEN_MORNING_ADJUSTMENT",
    "OPEN_MORNING_CONFIDENCE",
    "PEAK_WINDOW_ADJUSTMENT",
    "TIME_OF_DAY_CONFIDENCE",
]
