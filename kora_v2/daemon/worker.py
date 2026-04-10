"""Two-tier background work scheduler.

Runs conversation-safe tasks during chat and idle-only tasks between
sessions.  Work items register dynamically with priority, tier, and
interval.

Design decisions:
- Server owns lifecycle (start/stop)
- EventEmitter for conversation state awareness (SESSION_START/END)
- Dynamic WorkItem registry sorted by priority
- EventEmitter for broadcasting to WebSocket clients
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Awaitable
from dataclasses import dataclass, field
from typing import Any, Literal

import structlog

from kora_v2.core.events import EventEmitter, EventType

log = structlog.get_logger(__name__)


@dataclass
class WorkItem:
    """A registered background work item.

    Attributes:
        name: Unique identifier for this work item.
        priority: Lower number = higher priority (1 = highest).
        tier: "safe" runs during conversations, "idle" only between sessions.
        interval_seconds: Minimum seconds between runs.
        handler: Async callable that performs the work.
        last_run: Monotonic timestamp of last execution (0.0 = never run).
    """

    name: str
    priority: int
    tier: Literal["safe", "idle"]
    interval_seconds: int
    handler: Callable[[], Awaitable[None]]
    last_run: float = field(default=0.0)


class BackgroundWorker:
    """Two-tier background work scheduler.

    Two async loops run as ``asyncio.Task`` instances:
    - **safe loop**: executes ``tier="safe"`` items on a short interval,
      even during active conversations.
    - **idle loop**: executes ``tier="idle"`` items on a longer interval,
      only when no conversation is active.

    Conversation state is tracked via EventEmitter subscriptions to
    SESSION_START and SESSION_END events.
    """

    def __init__(self, container: Any) -> None:
        self.container = container
        self._items: list[WorkItem] = []
        self._conversation_active: bool = False
        self._safe_task: asyncio.Task[None] | None = None
        self._idle_task: asyncio.Task[None] | None = None
        self._stopped = asyncio.Event()

        # Settings
        settings = container.settings.daemon
        self._idle_interval: int = settings.idle_check_interval
        self._safe_interval: int = settings.background_safe_interval

        # Subscribe to session events
        emitter: EventEmitter = container.event_emitter
        emitter.on(EventType.SESSION_START, self._on_session_start)
        emitter.on(EventType.SESSION_END, self._on_session_end)

    # -- Registration ----------------------------------------------------------

    @property
    def items(self) -> list[WorkItem]:
        """Return registered items (sorted by priority, ascending)."""
        return list(self._items)

    def register(self, item: WorkItem) -> None:
        """Register a work item, replacing any with the same name."""
        self._items = [i for i in self._items if i.name != item.name]
        self._items.append(item)
        self._items.sort(key=lambda i: i.priority)
        log.info("work_item_registered", name=item.name, priority=item.priority, tier=item.tier)

    # -- Event handlers --------------------------------------------------------

    async def _on_session_start(self, payload: dict) -> None:
        self._conversation_active = True
        log.debug("background_worker_conversation_active")

    async def _on_session_end(self, payload: dict) -> None:
        self._conversation_active = False
        log.debug("background_worker_conversation_idle")

    # -- Lifecycle -------------------------------------------------------------

    async def start(self) -> None:
        """Start the safe and idle background loops."""
        self._stopped.clear()
        self._safe_task = asyncio.create_task(
            self._safe_loop(), name="bg_safe_loop",
        )
        self._idle_task = asyncio.create_task(
            self._idle_loop(), name="bg_idle_loop",
        )
        log.info("background_worker_started")

    async def stop(self) -> None:
        """Cancel background loops and wait for clean shutdown."""
        self._stopped.set()
        for task in (self._safe_task, self._idle_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        log.info("background_worker_stopped")

    @property
    def is_running(self) -> bool:
        """True if at least one loop task is alive."""
        return any(
            t is not None and not t.done()
            for t in (self._safe_task, self._idle_task)
        )

    # -- Loops -----------------------------------------------------------------

    def _is_ready(self, item: WorkItem) -> bool:
        """Check if item's cooldown interval has elapsed."""
        if item.interval_seconds <= 0:
            return True
        return (time.monotonic() - item.last_run) >= item.interval_seconds

    async def _safe_loop(self) -> None:
        """Execute conversation-safe work items on a short interval."""
        try:
            while not self._stopped.is_set():
                for item in self._items:
                    if item.tier == "safe" and self._is_ready(item):
                        await self._run_item(item)
                        break  # one item per cycle
                await asyncio.sleep(self._safe_interval)
        except asyncio.CancelledError:
            pass

    async def _idle_loop(self) -> None:
        """Execute idle-only work items when no conversation is active."""
        try:
            while not self._stopped.is_set():
                if not self._conversation_active:
                    for item in self._items:
                        if item.tier == "idle" and self._is_ready(item):
                            await self._run_item(item)
                            break  # one item per cycle
                await asyncio.sleep(self._idle_interval)
        except asyncio.CancelledError:
            pass

    async def _run_item(self, item: WorkItem) -> None:
        """Execute a single work item with error handling."""
        log.info("background_work_start", name=item.name, tier=item.tier)
        start = time.monotonic()
        try:
            await item.handler()
            elapsed_ms = int((time.monotonic() - start) * 1000)
            item.last_run = time.monotonic()
            log.info("background_work_complete", name=item.name, elapsed_ms=elapsed_ms)
        except Exception:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            log.exception("background_work_error", name=item.name, elapsed_ms=elapsed_ms)
