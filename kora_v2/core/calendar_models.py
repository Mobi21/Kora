"""Calendar Pydantic models (Phase 5).

These models describe entries on Kora's unified timeline store
(``calendar_entries`` table in ``operational.db``). They cover events,
medication windows, focus blocks, routine windows, buffers, reminders,
and deadlines.

Storage is UTC; display conversion to ``Settings.user_tz`` is handled
by the consumers of these models.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

CalendarKind = Literal[
    "event",
    "medication_window",
    "focus_block",
    "routine_window",
    "buffer",
    "reminder",
    "deadline",
]

CalendarSource = Literal["google", "kora", "user"]

CalendarStatus = Literal["active", "completed", "cancelled", "missed"]


class CalendarEntry(BaseModel):
    """A single row (or synthetic recurring expansion) on the timeline."""

    id: str
    kind: CalendarKind = "event"
    title: str
    description: str | None = None
    starts_at: datetime
    ends_at: datetime | None = None
    all_day: bool = False
    source: CalendarSource = "kora"
    google_event_id: str | None = None
    recurring_rule: str | None = None
    energy_match: Literal["low", "medium", "high"] | None = None
    location: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    synced_at: datetime | None = None
    status: CalendarStatus = "active"
    override_parent_id: str | None = None
    override_occurrence_date: str | None = None
    created_at: datetime
    updated_at: datetime


class CalendarTimelineSlot(BaseModel):
    """A rendered slot in the day's timeline (with conflict/buffer info)."""

    entry: CalendarEntry
    conflict_with: list[str] = Field(default_factory=list)
    buffer_before: int = 0  # minutes


__all__ = [
    "CalendarEntry",
    "CalendarKind",
    "CalendarSource",
    "CalendarStatus",
    "CalendarTimelineSlot",
]
