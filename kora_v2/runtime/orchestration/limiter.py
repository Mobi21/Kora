"""Sliding-window :class:`RequestLimiter` — spec §9.1.

The limiter is the single choke-point for the 5-hour API budget Kora
consumes across conversation, notification, and background traffic.

* Window: 5 hours rolling
* Cap: 4500 requests total
* Reserves: 300 for :data:`RequestClass.CONVERSATION`,
  100 for :data:`RequestClass.NOTIFICATION`

The limiter's job is to *refuse* background dispatches when the
remaining budget would eat into either reserve, never to police
conversation traffic. Conversation requests always succeed (they are
the reason the reserves exist); the limiter still records them so the
sliding window stays accurate.

Persistence: every acquisition is written to ``request_limiter_log``
so the count survives process restarts. On boot, ``replay_from_log``
rehydrates the window from the last five hours of rows.

Concurrency: the limiter is protected by an :class:`asyncio.Lock`
because several background tasks can hit it at the same time and each
``acquire`` is a read-then-write sequence.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import aiosqlite
import structlog

from kora_v2.runtime.orchestration.worker_task import RequestClass

log = structlog.get_logger(__name__)

# ── Tunables ──────────────────────────────────────────────────────────────

WINDOW_SECONDS = 5 * 3600           # 5 hours
WINDOW_CAPACITY = 4500              # absolute cap
CONVERSATION_RESERVE = 300
NOTIFICATION_RESERVE = 100


@dataclass
class LimiterSnapshot:
    """Immutable view of the limiter at a point in time."""

    now: datetime
    total_in_window: int
    by_class: dict[RequestClass, int]
    capacity: int
    conversation_reserve: int
    notification_reserve: int

    @property
    def remaining(self) -> int:
        return max(0, self.capacity - self.total_in_window)

    def remaining_for(self, cls: RequestClass) -> int:
        """Remaining budget for *cls* after reserves are applied."""
        base = self.remaining
        if cls is RequestClass.BACKGROUND:
            return max(
                0,
                base - self.conversation_reserve - self.notification_reserve,
            )
        if cls is RequestClass.NOTIFICATION:
            return max(0, base - self.conversation_reserve)
        # CONVERSATION: the reserve exists *for* it — always return base
        return base


class RequestLimiter:
    """Sliding-window request limiter backed by ``request_limiter_log``."""

    def __init__(
        self,
        db_path: Path,
        *,
        capacity: int = WINDOW_CAPACITY,
        conversation_reserve: int = CONVERSATION_RESERVE,
        notification_reserve: int = NOTIFICATION_RESERVE,
        window_seconds: int = WINDOW_SECONDS,
    ) -> None:
        self._db_path = db_path
        self._capacity = capacity
        self._conversation_reserve = conversation_reserve
        self._notification_reserve = notification_reserve
        self._window_seconds = window_seconds
        # (timestamp, class, count)
        self._window: deque[tuple[datetime, RequestClass, int]] = deque()
        self._totals: dict[RequestClass, int] = {
            RequestClass.CONVERSATION: 0,
            RequestClass.NOTIFICATION: 0,
            RequestClass.BACKGROUND: 0,
        }
        self._lock = asyncio.Lock()
        self._loaded = False

    # ── Public API ───────────────────────────────────────────────

    async def replay_from_log(self) -> None:
        """Rehydrate the in-memory window from the SQL log.

        Any rows older than ``window_seconds`` are ignored. This is the
        crash-recovery path: it runs once during engine start.
        """
        async with self._lock:
            self._window.clear()
            for value in self._totals:
                self._totals[value] = 0

            now = _utcnow()
            cutoff = now - timedelta(seconds=self._window_seconds)
            async with aiosqlite.connect(str(self._db_path)) as db:
                cursor = await db.execute(
                    "SELECT timestamp, class, request_count "
                    "FROM request_limiter_log "
                    "WHERE timestamp >= ? "
                    "ORDER BY timestamp ASC",
                    (cutoff.isoformat(),),
                )
                rows = await cursor.fetchall()

            for ts_str, class_str, count in rows:
                try:
                    ts = datetime.fromisoformat(ts_str)
                    cls = RequestClass(class_str)
                except ValueError:
                    continue
                self._window.append((ts, cls, count))
                self._totals[cls] = self._totals.get(cls, 0) + count

            self._loaded = True
            log.debug(
                "request_limiter_replayed",
                total=sum(self._totals.values()),
                conversation=self._totals[RequestClass.CONVERSATION],
                notification=self._totals[RequestClass.NOTIFICATION],
                background=self._totals[RequestClass.BACKGROUND],
            )

    async def acquire(
        self,
        cls: RequestClass,
        *,
        count: int = 1,
        worker_task_id: str | None = None,
    ) -> bool:
        """Try to consume *count* budget units for *cls*.

        Returns ``True`` if the acquisition succeeds and the consumption
        has been written to the log; ``False`` if the acquisition would
        violate either the absolute cap or a reserve. Conversation
        requests never fail — the reserve is *for* them.
        """
        async with self._lock:
            now = _utcnow()
            self._evict_expired(now)
            total = sum(self._totals.values())

            if cls is RequestClass.CONVERSATION:
                # Always accept, even if this pushes us over the cap.
                pass
            elif cls is RequestClass.NOTIFICATION:
                if total + count > self._capacity - self._conversation_reserve:
                    return False
            else:  # BACKGROUND
                if (
                    total + count
                    > self._capacity - self._conversation_reserve - self._notification_reserve
                ):
                    return False

            self._window.append((now, cls, count))
            self._totals[cls] = self._totals.get(cls, 0) + count

        await self._record(now, cls, count, worker_task_id)
        return True

    async def snapshot(self) -> LimiterSnapshot:
        async with self._lock:
            now = _utcnow()
            self._evict_expired(now)
            return LimiterSnapshot(
                now=now,
                total_in_window=sum(self._totals.values()),
                by_class=dict(self._totals),
                capacity=self._capacity,
                conversation_reserve=self._conversation_reserve,
                notification_reserve=self._notification_reserve,
            )

    async def reset(self) -> None:
        """Wipe in-memory state. Tests only."""
        async with self._lock:
            self._window.clear()
            for value in self._totals:
                self._totals[value] = 0

    # ── Internal helpers ─────────────────────────────────────────

    def _evict_expired(self, now: datetime) -> None:
        cutoff = now - timedelta(seconds=self._window_seconds)
        while self._window and self._window[0][0] < cutoff:
            ts, cls, count = self._window.popleft()
            self._totals[cls] = max(0, self._totals.get(cls, 0) - count)

    async def _record(
        self,
        now: datetime,
        cls: RequestClass,
        count: int,
        worker_task_id: str | None,
    ) -> None:
        async with aiosqlite.connect(str(self._db_path)) as db:
            await db.execute(
                "INSERT INTO request_limiter_log "
                "(timestamp, class, worker_task_id, request_count) "
                "VALUES (?, ?, ?, ?)",
                (now.isoformat(), cls.value, worker_task_id, count),
            )
            await db.commit()


def _utcnow() -> datetime:
    return datetime.now(UTC)


# Convenience for wiring via kwargs
def limiter_kwargs(**overrides: Any) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "capacity": WINDOW_CAPACITY,
        "conversation_reserve": CONVERSATION_RESERVE,
        "notification_reserve": NOTIFICATION_RESERVE,
        "window_seconds": WINDOW_SECONDS,
    }
    defaults.update(overrides)
    return defaults
