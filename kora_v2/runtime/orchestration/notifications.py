"""NotificationGate primitive — stub for slice 7.5a.

The real notification gate arrives in 7.5b and is responsible for
coalescing background-task completions into calm user-facing messages
that respect DND and hourly caps. This stub exists so callers can
import ``NotificationGate`` today and so the engine can hand one to
the dispatcher as ``None`` without breaking future signatures.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass
class PendingNotification:
    task_id: str
    message: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class NotificationGate:
    """Placeholder queue of pending notifications."""

    def __init__(self) -> None:
        self._pending: list[PendingNotification] = []

    def enqueue(self, note: PendingNotification) -> None:
        self._pending.append(note)

    def drain(self) -> list[PendingNotification]:
        out, self._pending = self._pending, []
        return out

    def __len__(self) -> int:
        return len(self._pending)
