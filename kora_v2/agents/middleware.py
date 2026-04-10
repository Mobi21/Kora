"""Kora V2 — Agent middleware pipeline.

Middleware classes wrap agent execution with cross-cutting concerns:
loop detection, budget enforcement, timeout, and observability.
Each middleware has pre/post hooks that run around _execute().
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from abc import ABC, abstractmethod
from typing import Any

import structlog

from kora_v2.core.events import EventEmitter, EventType
from kora_v2.core.exceptions import KoraError
from kora_v2.core.settings import get_settings

log = structlog.get_logger(__name__)


# ── Errors ──────────────────────────────────────────────────────────────


class LoopDetectedError(KoraError):
    """Raised when an agent repeats the same tool call too many times."""


class BudgetExhaustedError(KoraError):
    """Raised when the shared iteration budget is exhausted."""


class AgentTimeoutError(KoraError):
    """Raised when an agent exceeds its allowed execution time."""


# ── Base ────────────────────────────────────────────────────────────────


class AgentMiddleware(ABC):
    """Base middleware class.

    Subclasses override :meth:`pre_execute` and/or :meth:`post_execute`
    to inject behaviour around agent execution.
    """

    @abstractmethod
    async def pre_execute(self, input_data: Any, context: dict) -> None:
        """Run before agent execution. May mutate *context*."""

    @abstractmethod
    async def post_execute(self, output_data: Any, context: dict) -> None:
        """Run after agent execution. May mutate *context*."""


# ── Loop Detection ──────────────────────────────────────────────────────


class LoopDetectionMiddleware(AgentMiddleware):
    """Detect repeated identical tool calls and abort.

    Tracks ``(tool_name, args_hash)`` tuples across an agent's lifetime.
    When the same call is seen ``loop_detection_threshold`` times, raises
    :class:`LoopDetectedError`.
    """

    def __init__(self, threshold: int | None = None) -> None:
        settings = get_settings()
        self._threshold = threshold or settings.agents.loop_detection_threshold
        self._call_counts: dict[str, int] = {}

    # ── Helpers ───────────────────────────────────────────────────

    @staticmethod
    def _hash_args(args: dict[str, Any]) -> str:
        """Deterministic hash of tool arguments."""
        serialized = json.dumps(args, sort_keys=True, default=str)
        return hashlib.sha256(serialized.encode()).hexdigest()[:16]

    def record_tool_call(self, tool_name: str, args: dict[str, Any]) -> None:
        """Record a tool call and raise if the loop threshold is hit."""
        key = f"{tool_name}:{self._hash_args(args)}"
        self._call_counts[key] = self._call_counts.get(key, 0) + 1
        count = self._call_counts[key]

        if count >= self._threshold:
            raise LoopDetectedError(
                f"Loop detected: {tool_name} called {count} times with same args",
                details={"tool_name": tool_name, "count": count},
            )

        log.debug(
            "tool_call_recorded",
            tool_name=tool_name,
            count=count,
            threshold=self._threshold,
        )

    def reset(self) -> None:
        """Clear all recorded calls."""
        self._call_counts.clear()

    # ── Middleware hooks ──────────────────────────────────────────

    async def pre_execute(self, input_data: Any, context: dict) -> None:
        """Check for tool call loops in context."""
        tool_calls = context.get("tool_calls", [])
        for call in tool_calls:
            name = call.get("name", "") if isinstance(call, dict) else getattr(call, "name", "")
            args = call.get("arguments", {}) if isinstance(call, dict) else getattr(call, "arguments", {})
            self.record_tool_call(name, args)

    async def post_execute(self, output_data: Any, context: dict) -> None:
        """No-op for loop detection."""


# ── Budget Enforcement ──────────────────────────────────────────────────


class BudgetEnforcementMiddleware(AgentMiddleware):
    """Shared iteration budget across all agents (thread-safe).

    Uses :data:`settings.agents.iteration_budget` as the ceiling.
    Each call to :meth:`pre_execute` increments the counter; once the
    budget is exhausted, :class:`BudgetExhaustedError` is raised.
    """

    def __init__(self, budget: int | None = None) -> None:
        settings = get_settings()
        self._budget = budget or settings.agents.iteration_budget
        self._used = 0
        self._lock = threading.Lock()

    @property
    def remaining(self) -> int:
        with self._lock:
            return max(0, self._budget - self._used)

    @property
    def used(self) -> int:
        with self._lock:
            return self._used

    def consume(self, n: int = 1) -> None:
        """Consume *n* iterations from the budget.

        Raises :class:`BudgetExhaustedError` if the budget would go negative.
        """
        with self._lock:
            if self._used + n > self._budget:
                raise BudgetExhaustedError(
                    f"Iteration budget exhausted: {self._used}/{self._budget}",
                    details={"used": self._used, "budget": self._budget},
                )
            self._used += n

    def reset(self) -> None:
        """Reset the counter (e.g. at start of a new session)."""
        with self._lock:
            self._used = 0

    # ── Middleware hooks ──────────────────────────────────────────

    async def pre_execute(self, input_data: Any, context: dict) -> None:
        """Consume one iteration from the budget."""
        self.consume(1)

    async def post_execute(self, output_data: Any, context: dict) -> None:
        """No-op for budget enforcement."""


# ── Timeout ─────────────────────────────────────────────────────────────


class TimeoutMiddleware(AgentMiddleware):
    """Per-agent execution timeout.

    Stores the deadline in *context* during :meth:`pre_execute` and
    checks elapsed time in :meth:`post_execute`. The actual timeout
    enforcement is done at the harness level using ``asyncio.wait_for``.
    """

    def __init__(self, timeout_seconds: float | None = None) -> None:
        settings = get_settings()
        self._timeout = timeout_seconds or float(settings.agents.default_timeout)

    @property
    def timeout(self) -> float:
        return self._timeout

    async def pre_execute(self, input_data: Any, context: dict) -> None:
        """Record the execution start time in context."""
        context["_timeout_start"] = time.monotonic()
        context["_timeout_seconds"] = self._timeout

    async def post_execute(self, output_data: Any, context: dict) -> None:
        """Check if execution exceeded the timeout."""
        start = context.get("_timeout_start")
        if start is not None:
            elapsed = time.monotonic() - start
            if elapsed > self._timeout:
                raise AgentTimeoutError(
                    f"Agent exceeded timeout: {elapsed:.1f}s > {self._timeout:.1f}s",
                    details={"elapsed": elapsed, "timeout": self._timeout},
                )


# ── Observability ───────────────────────────────────────────────────────


class ObservabilityMiddleware(AgentMiddleware):
    """Track tokens, latency, and tool calls per agent execution.

    Emits :data:`EventType.WORKER_DISPATCHED` on pre-execute and
    :data:`EventType.WORKER_COMPLETED` on post-execute via the
    :class:`EventEmitter` passed at construction time.
    """

    def __init__(self, emitter: EventEmitter) -> None:
        self._emitter = emitter

    async def pre_execute(self, input_data: Any, context: dict) -> None:
        """Emit dispatch event and record start time."""
        context["_obs_start"] = time.monotonic()
        await self._emitter.emit(
            EventType.WORKER_DISPATCHED,
            agent=context.get("agent_name", "unknown"),
        )

    async def post_execute(self, output_data: Any, context: dict) -> None:
        """Emit completion event with latency metrics."""
        start = context.get("_obs_start", time.monotonic())
        latency_ms = int((time.monotonic() - start) * 1000)

        await self._emitter.emit(
            EventType.WORKER_COMPLETED,
            agent=context.get("agent_name", "unknown"),
            latency_ms=latency_ms,
            tool_calls=context.get("tool_call_count", 0),
            prompt_tokens=context.get("prompt_tokens", 0),
            completion_tokens=context.get("completion_tokens", 0),
        )
