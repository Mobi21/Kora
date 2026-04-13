"""Working memory loader and energy inference for Kora V2.

Provides:
- estimate_energy(): DEPRECATED in Phase 5 — kept as a thin time-of-day
  fallback. New code should use ``ContextEngine._estimate_energy`` in
  ``kora_v2/context/engine.py`` which combines medication, calendar,
  time-of-day, and self-report signals.
- WorkingMemoryLoader: loads max 5 prioritized items for the dynamic suffix
"""

from __future__ import annotations

from kora_v2.core.models import EnergyEstimate, SessionBridge, WorkingMemoryItem

# ---------------------------------------------------------------------------
# Energy inference
# ---------------------------------------------------------------------------

# (level, focus) keyed by hour range start — ranges are half-open [start, end)
# Ordered list of (start_hour, end_hour, level, focus)
_TIME_OF_DAY_CURVE: list[tuple[int, int, str, str]] = [
    (6,  9,  "medium", "moderate"),   # early morning
    (9,  12, "high",   "locked_in"),  # peak morning
    (12, 14, "medium", "moderate"),   # post-lunch dip
    (14, 16, "low",    "scattered"),  # ADHD crash window
    (16, 21, "medium", "moderate"),   # recovery + evening
    # everything else (21-24, 0-6) falls through to the default below
]
_DEFAULT_LEVEL = "low"
_DEFAULT_FOCUS = "scattered"

_BASE_CONFIDENCE = 0.4  # no behavioural signals yet


def _now_hour() -> int:
    """Return the current hour (0-23). Isolated for easy mocking in tests."""
    from datetime import datetime
    return datetime.now().hour


def _level_focus_for_hour(hour: int) -> tuple[str, str]:
    """Map an hour (0-23) to (level, focus) using the time-of-day curve."""
    for start, end, level, focus in _TIME_OF_DAY_CURVE:
        if start <= hour < end:
            return level, focus
    return _DEFAULT_LEVEL, _DEFAULT_FOCUS


def estimate_energy(adhd_profile: dict | None = None) -> EnergyEstimate:
    """Infer energy and focus level from the current time of day.

    Args:
        adhd_profile: Optional dict with keys ``peak_windows`` and/or
            ``crash_periods`` (each a list of ``[start_hour, end_hour]``
            pairs).  When provided these override the default curve for
            the relevant windows.

    Returns:
        EnergyEstimate with confidence 0.4 (time-of-day only — no
        behavioural signals yet).
    """
    hour = _now_hour()
    level, focus = _level_focus_for_hour(hour)

    if adhd_profile is not None:
        # Override with profile peak windows → high / locked_in
        for window in adhd_profile.get("peak_windows", []):
            start, end = window[0], window[1]
            if start <= hour < end:
                level, focus = "high", "locked_in"
                break
        # Override with profile crash periods → low / scattered
        for window in adhd_profile.get("crash_periods", []):
            start, end = window[0], window[1]
            if start <= hour < end:
                level, focus = "low", "scattered"
                break

    return EnergyEstimate(
        level=level,  # type: ignore[arg-type]
        focus=focus,  # type: ignore[arg-type]
        confidence=_BASE_CONFIDENCE,
        source="time_of_day",
        signals={"hour": hour},
    )


# ---------------------------------------------------------------------------
# Working memory loader
# ---------------------------------------------------------------------------


class WorkingMemoryLoader:
    """Load max 5 working-memory items ranked by priority for the dynamic suffix.

    Sources (priority order):
    1. Bridge open threads (source="bridge", priority=1)
    2. Items due within 48h  (source="items_db", priority=2)  — PLACEHOLDER
    3. Recent commitments   (source="commitments", priority=3) — PLACEHOLDER
    """

    def __init__(
        self,
        projection_db: object | None = None,
        items_db: object | None = None,
        last_bridge: SessionBridge | None = None,
    ) -> None:
        self.projection_db = projection_db
        self.items_db = items_db
        self.last_bridge = last_bridge

    async def load(self) -> list[WorkingMemoryItem]:
        """Load and return up to 5 items sorted by ascending priority."""
        items: list[WorkingMemoryItem] = []

        # Source 1: open threads from the previous session bridge
        if self.last_bridge and self.last_bridge.open_threads:
            for thread in self.last_bridge.open_threads:
                items.append(
                    WorkingMemoryItem(
                        source="bridge",
                        content=thread,
                        priority=1,
                    )
                )

        # Source 2: items due soon from items_db. Phase 5 fix: the
        # canonical column is ``type`` (a SQL keyword in some dialects
        # but a valid identifier in SQLite). The previous implementation
        # selected ``item_type`` which silently never existed, and
        # swallowed the error so broken queries looked like "no items".
        if self.items_db is not None:
            from datetime import UTC, datetime, timedelta

            cutoff = (datetime.now(UTC) + timedelta(hours=48)).isoformat()
            async with self.items_db.execute(
                """SELECT title, type, priority, due_date FROM items
                   WHERE status NOT IN ('done', 'cancelled')
                   AND (due_date IS NULL OR due_date <= ?)
                   ORDER BY priority ASC, due_date ASC LIMIT 5""",
                (cutoff,),
            ) as cursor:
                rows = await cursor.fetchall()
                for row in rows:
                    title, item_kind, priority, due_date = row
                    label = f"[{item_kind}] {title}"
                    if due_date:
                        label += f" (due {due_date[:10]})"
                    items.append(
                        WorkingMemoryItem(
                            source="items_db",
                            content=label,
                            priority=priority or 2,
                        )
                    )

        # Source 3: commitments from projection_db (placeholder — not yet implemented)

        # Sort by priority (lowest number = highest priority), cap at 5
        items.sort(key=lambda x: x.priority)
        return items[:5]
