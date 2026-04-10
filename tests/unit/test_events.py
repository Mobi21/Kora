"""Tests for kora_v2.core.events — Typed event bus with async dispatch."""

import asyncio

import pytest

from kora_v2.core.events import EventEmitter, EventType


class TestEventTypes:
    """Verify all expected EventType values exist."""

    def test_event_types_exist(self):
        """All 16 EventType values should be present."""
        expected = {
            "SESSION_START",
            "SESSION_END",
            "TURN_START",
            "TURN_END",
            "WORKER_DISPATCHED",
            "WORKER_COMPLETED",
            "WORKER_FAILED",
            "TOOL_CALLED",
            "TOOL_RESULT",
            "MEMORY_STORED",
            "QUALITY_GATE_RESULT",
            "NOTIFICATION_SENT",
            "AUTONOMOUS_CHECKPOINT",
            "AUTONOMOUS_COMPLETE",
            "ERROR_OCCURRED",
        }
        actual = {e.name for e in EventType}
        # Check that all expected are present (actual may have more)
        assert expected.issubset(actual), f"Missing: {expected - actual}"

    def test_event_types_are_unique(self):
        """All EventType values should be unique."""
        values = [e.value for e in EventType]
        assert len(values) == len(set(values))


class TestEventEmitter:
    """Test the EventEmitter pub/sub system."""

    @pytest.fixture
    def emitter(self):
        return EventEmitter()

    async def test_emit_and_receive(self, emitter):
        """Register handler, emit event, verify handler called with correct data."""
        received = []

        async def handler(payload):
            received.append(payload)

        emitter.on(EventType.TURN_START, handler)
        await emitter.emit(EventType.TURN_START, turn_number=1)

        assert len(received) == 1
        payload = received[0]
        assert payload["event_type"] == EventType.TURN_START
        assert payload["turn_number"] == 1
        assert "timestamp" in payload
        assert "correlation_id" in payload

    async def test_handler_error_isolation(self, emitter):
        """Handler that raises should not crash emit or prevent other handlers."""
        call_order = []

        async def bad_handler(payload):
            call_order.append("bad")
            raise ValueError("intentional error")

        async def good_handler(payload):
            call_order.append("good")

        emitter.on(EventType.SESSION_START, bad_handler)
        emitter.on(EventType.SESSION_START, good_handler)

        # Should not raise even though bad_handler raises
        await emitter.emit(EventType.SESSION_START)

        assert "bad" in call_order
        assert "good" in call_order

    async def test_multiple_handlers(self, emitter):
        """Two handlers on same event type should both get called."""
        results = []

        async def handler_a(payload):
            results.append("a")

        async def handler_b(payload):
            results.append("b")

        emitter.on(EventType.TOOL_CALLED, handler_a)
        emitter.on(EventType.TOOL_CALLED, handler_b)
        await emitter.emit(EventType.TOOL_CALLED, tool="test")

        assert len(results) == 2
        assert "a" in results
        assert "b" in results

    async def test_no_handlers_no_error(self, emitter):
        """Emitting with no handlers should not raise."""
        await emitter.emit(EventType.ERROR_OCCURRED, detail="test")

    async def test_off_removes_handler(self, emitter):
        """off() should unregister a handler."""
        received = []

        async def handler(payload):
            received.append(payload)

        emitter.on(EventType.SESSION_END, handler)
        emitter.off(EventType.SESSION_END, handler)
        await emitter.emit(EventType.SESSION_END)

        assert len(received) == 0

    def test_handler_count(self, emitter):
        """handler_count() should return correct count."""

        async def h1(p):
            pass

        async def h2(p):
            pass

        assert emitter.handler_count(EventType.TURN_START) == 0
        emitter.on(EventType.TURN_START, h1)
        assert emitter.handler_count(EventType.TURN_START) == 1
        emitter.on(EventType.TURN_START, h2)
        assert emitter.handler_count(EventType.TURN_START) == 2

    def test_clear(self, emitter):
        """clear() should remove all handlers."""

        async def h(p):
            pass

        emitter.on(EventType.TURN_START, h)
        emitter.on(EventType.SESSION_START, h)
        emitter.clear()
        assert emitter.handler_count(EventType.TURN_START) == 0
        assert emitter.handler_count(EventType.SESSION_START) == 0
