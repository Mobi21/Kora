"""Tests for kora_v2.agents.middleware — all middleware classes."""

from __future__ import annotations

import asyncio
import threading

import pytest

from kora_v2.agents.middleware import (
    AgentTimeoutError,
    BudgetEnforcementMiddleware,
    BudgetExhaustedError,
    LoopDetectedError,
    LoopDetectionMiddleware,
    ObservabilityMiddleware,
    TimeoutMiddleware,
)
from kora_v2.core.events import EventEmitter, EventType


# ── Loop Detection ──────────────────────────────────────────────────────


class TestLoopDetection:
    def test_detects_repeated_tool_calls(self):
        """Raises LoopDetectedError when same tool+args hit threshold."""
        mw = LoopDetectionMiddleware(threshold=3)

        mw.record_tool_call("search", {"query": "hello"})
        mw.record_tool_call("search", {"query": "hello"})

        with pytest.raises(LoopDetectedError, match="Loop detected"):
            mw.record_tool_call("search", {"query": "hello"})

    def test_different_args_no_loop(self):
        """Different arguments are tracked separately."""
        mw = LoopDetectionMiddleware(threshold=3)

        mw.record_tool_call("search", {"query": "hello"})
        mw.record_tool_call("search", {"query": "world"})
        mw.record_tool_call("search", {"query": "foo"})
        # No exception — all different args

    def test_different_tools_no_loop(self):
        """Different tool names are tracked separately."""
        mw = LoopDetectionMiddleware(threshold=2)

        mw.record_tool_call("search", {"query": "hello"})
        mw.record_tool_call("recall", {"query": "hello"})
        # No exception — different tools

    def test_reset_clears_counts(self):
        """Reset allows the same calls to be made again."""
        mw = LoopDetectionMiddleware(threshold=2)

        mw.record_tool_call("search", {"query": "hello"})
        mw.reset()
        mw.record_tool_call("search", {"query": "hello"})
        # No exception — counter was reset

    @pytest.mark.asyncio
    async def test_pre_execute_checks_tool_calls(self):
        """pre_execute reads tool_calls from context."""
        mw = LoopDetectionMiddleware(threshold=2)

        context = {
            "tool_calls": [{"name": "search", "arguments": {"q": "x"}}]
        }
        await mw.pre_execute(None, context)

        with pytest.raises(LoopDetectedError):
            await mw.pre_execute(None, context)


# ── Budget Enforcement ──────────────────────────────────────────────────


class TestBudgetEnforcement:
    def test_budget_exhausted(self):
        """Raises BudgetExhaustedError when budget is consumed."""
        mw = BudgetEnforcementMiddleware(budget=3)

        mw.consume(1)
        mw.consume(1)
        mw.consume(1)

        with pytest.raises(BudgetExhaustedError, match="exhausted"):
            mw.consume(1)

    def test_remaining_tracks_correctly(self):
        """remaining property reflects consumed budget."""
        mw = BudgetEnforcementMiddleware(budget=10)
        assert mw.remaining == 10

        mw.consume(3)
        assert mw.remaining == 7
        assert mw.used == 3

    def test_reset_restores_budget(self):
        """Reset sets used back to 0."""
        mw = BudgetEnforcementMiddleware(budget=5)
        mw.consume(5)
        assert mw.remaining == 0

        mw.reset()
        assert mw.remaining == 5

    def test_thread_safety(self):
        """Concurrent consume calls don't exceed budget."""
        mw = BudgetEnforcementMiddleware(budget=100)
        errors: list[Exception] = []

        def consume_many():
            for _ in range(10):
                try:
                    mw.consume(1)
                except BudgetExhaustedError as e:
                    errors.append(e)

        threads = [threading.Thread(target=consume_many) for _ in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 12 threads * 10 attempts = 120, but budget is 100
        # So exactly 20 should have failed
        assert mw.used == 100
        assert len(errors) == 20

    @pytest.mark.asyncio
    async def test_pre_execute_consumes(self):
        """pre_execute consumes one iteration."""
        mw = BudgetEnforcementMiddleware(budget=2)

        await mw.pre_execute(None, {})
        assert mw.used == 1

        await mw.pre_execute(None, {})
        assert mw.used == 2

        with pytest.raises(BudgetExhaustedError):
            await mw.pre_execute(None, {})


# ── Timeout ─────────────────────────────────────────────────────────────


class TestTimeout:
    @pytest.mark.asyncio
    async def test_pre_execute_sets_start(self):
        """pre_execute stores the start time in context."""
        mw = TimeoutMiddleware(timeout_seconds=60.0)
        context: dict = {}

        await mw.pre_execute(None, context)
        assert "_timeout_start" in context
        assert context["_timeout_seconds"] == 60.0

    @pytest.mark.asyncio
    async def test_post_execute_timeout_exceeded(self):
        """post_execute raises AgentTimeoutError when time exceeds limit."""
        import time

        mw = TimeoutMiddleware(timeout_seconds=0.01)
        context: dict = {}

        await mw.pre_execute(None, context)
        # Simulate work that exceeds the timeout
        await asyncio.sleep(0.05)

        with pytest.raises(AgentTimeoutError, match="exceeded timeout"):
            await mw.post_execute(None, context)


# ── Observability ───────────────────────────────────────────────────────


class TestObservability:
    @pytest.mark.asyncio
    async def test_emits_dispatch_and_completion(self):
        """Emits WORKER_DISPATCHED and WORKER_COMPLETED events."""
        emitter = EventEmitter()
        events_received: list[dict] = []

        async def capture(payload: dict):
            events_received.append(payload)

        emitter.on(EventType.WORKER_DISPATCHED, capture)
        emitter.on(EventType.WORKER_COMPLETED, capture)

        mw = ObservabilityMiddleware(emitter)
        context = {"agent_name": "test_agent"}

        await mw.pre_execute(None, context)
        await mw.post_execute(None, context)

        assert len(events_received) == 2
        assert events_received[0]["event_type"] == EventType.WORKER_DISPATCHED
        assert events_received[0]["agent"] == "test_agent"
        assert events_received[1]["event_type"] == EventType.WORKER_COMPLETED
        assert events_received[1]["agent"] == "test_agent"
        assert "latency_ms" in events_received[1]
