"""Kora V2 — Typed event bus with async dispatch.

Lightweight pub/sub for cross-cutting concerns (telemetry, quality gates,
notifications) without tight coupling between services.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from enum import Enum, auto
from typing import Any

import structlog

from kora_v2.core.logging import get_correlation_id

log = structlog.get_logger(__name__)


# ── Event types ──────────────────────────────────────────────────────────

class EventType(Enum):
    """Canonical event types emitted across Kora V2."""

    # Session lifecycle
    SESSION_START = auto()
    SESSION_END = auto()
    TURN_START = auto()
    TURN_END = auto()

    # Background worker
    WORKER_DISPATCHED = auto()
    WORKER_COMPLETED = auto()
    WORKER_FAILED = auto()

    # Tool execution
    TOOL_CALLED = auto()
    TOOL_RESULT = auto()

    # Memory
    MEMORY_STORED = auto()

    # Quality
    QUALITY_GATE_RESULT = auto()

    # Notifications
    NOTIFICATION_SENT = auto()

    # Autonomous execution
    AUTONOMOUS_CHECKPOINT = auto()
    AUTONOMOUS_COMPLETE = auto()
    AUTONOMOUS_FAILED = auto()

    # Errors
    ERROR_OCCURRED = auto()


# ── Handler type alias ───────────────────────────────────────────────────

EventHandler = Callable[..., Coroutine[Any, Any, None]]


# ── Emitter ──────────────────────────────────────────────────────────────

class EventEmitter:
    """Async event bus.

    Usage::

        emitter = EventEmitter()
        emitter.on(EventType.TURN_START, my_handler)
        await emitter.emit(EventType.TURN_START, turn_number=1)

    Handler errors are logged but never propagated to the caller.
    """

    __slots__ = ("_handlers",)

    def __init__(self) -> None:
        self._handlers: dict[EventType, list[EventHandler]] = defaultdict(list)

    # ── Registration ─────────────────────────────────────────────

    def on(self, event_type: EventType, handler: EventHandler) -> None:
        """Register *handler* for *event_type*."""
        self._handlers[event_type].append(handler)

    def off(self, event_type: EventType, handler: EventHandler) -> None:
        """Remove a previously registered handler (no-op if absent)."""
        try:
            self._handlers[event_type].remove(handler)
        except ValueError:
            pass

    # ── Dispatch ─────────────────────────────────────────────────

    async def emit(self, event_type: EventType, **data: Any) -> None:
        """Dispatch *event_type* to all registered handlers.

        Every handler receives a single ``dict`` with at least:
        * ``event_type`` — the :class:`EventType` value
        * ``timestamp`` — ISO-8601 UTC string
        * ``correlation_id`` — current request correlation ID (may be empty)
        * any extra ``**data`` keyword arguments
        """
        payload: dict[str, Any] = {
            "event_type": event_type,
            "timestamp": datetime.now(UTC).isoformat(),
            "correlation_id": get_correlation_id(),
            **data,
        }

        handlers = self._handlers.get(event_type, [])
        if not handlers:
            return

        for handler in handlers:
            try:
                await handler(payload)
            except asyncio.CancelledError:
                raise  # never swallow cancellations
            except Exception:
                log.exception(
                    "event_handler_error",
                    event_type=event_type.name,
                    handler=getattr(handler, "__qualname__", repr(handler)),
                )

    # ── Introspection ────────────────────────────────────────────

    def handler_count(self, event_type: EventType) -> int:
        """Return the number of handlers registered for *event_type*."""
        return len(self._handlers.get(event_type, []))

    def clear(self) -> None:
        """Remove all handlers (useful in tests)."""
        self._handlers.clear()
