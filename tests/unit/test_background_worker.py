"""Tests for BackgroundWorker and WorkItem."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from kora_v2.core.events import EventType
from kora_v2.daemon.worker import BackgroundWorker, WorkItem


@pytest.fixture
def mock_container():
    """Minimal mock container with event_emitter and settings."""
    from unittest.mock import MagicMock
    from kora_v2.core.events import EventEmitter

    container = MagicMock()
    container.event_emitter = EventEmitter()
    container.settings.daemon.idle_check_interval = 1  # fast for tests
    container.settings.daemon.background_safe_interval = 1
    return container


class TestWorkItem:
    def test_create_work_item(self):
        handler = AsyncMock()
        item = WorkItem(
            name="test_task",
            priority=2,
            tier="idle",
            interval_seconds=300,
            handler=handler,
        )
        assert item.name == "test_task"
        assert item.priority == 2
        assert item.tier == "idle"
        assert item.interval_seconds == 300
        assert item.last_run == 0.0

    def test_work_item_default_last_run(self):
        item = WorkItem(
            name="task",
            priority=1,
            tier="safe",
            interval_seconds=60,
            handler=AsyncMock(),
        )
        assert item.last_run == 0.0


class TestBackgroundWorkerRegistration:
    def test_register_single_item(self, mock_container):
        worker = BackgroundWorker(mock_container)
        item = WorkItem(
            name="task_a", priority=2, tier="idle",
            interval_seconds=60, handler=AsyncMock(),
        )
        worker.register(item)
        assert len(worker.items) == 1
        assert worker.items[0].name == "task_a"

    def test_register_sorts_by_priority(self, mock_container):
        worker = BackgroundWorker(mock_container)
        worker.register(WorkItem(
            name="low", priority=3, tier="idle",
            interval_seconds=60, handler=AsyncMock(),
        ))
        worker.register(WorkItem(
            name="high", priority=1, tier="idle",
            interval_seconds=60, handler=AsyncMock(),
        ))
        worker.register(WorkItem(
            name="mid", priority=2, tier="idle",
            interval_seconds=60, handler=AsyncMock(),
        ))
        assert [i.name for i in worker.items] == ["high", "mid", "low"]

    def test_register_duplicate_name_replaces(self, mock_container):
        worker = BackgroundWorker(mock_container)
        handler_a = AsyncMock()
        handler_b = AsyncMock()
        worker.register(WorkItem(
            name="task", priority=2, tier="idle",
            interval_seconds=60, handler=handler_a,
        ))
        worker.register(WorkItem(
            name="task", priority=1, tier="safe",
            interval_seconds=30, handler=handler_b,
        ))
        assert len(worker.items) == 1
        assert worker.items[0].handler is handler_b
        assert worker.items[0].priority == 1


class TestBackgroundWorkerConversationState:
    @pytest.mark.asyncio
    async def test_starts_idle(self, mock_container):
        worker = BackgroundWorker(mock_container)
        assert worker._conversation_active is False

    @pytest.mark.asyncio
    async def test_session_start_sets_active(self, mock_container):
        worker = BackgroundWorker(mock_container)
        await mock_container.event_emitter.emit(EventType.SESSION_START, session_id="test123")
        assert worker._conversation_active is True

    @pytest.mark.asyncio
    async def test_session_end_clears_active(self, mock_container):
        worker = BackgroundWorker(mock_container)
        await mock_container.event_emitter.emit(EventType.SESSION_START, session_id="test123")
        assert worker._conversation_active is True
        await mock_container.event_emitter.emit(EventType.SESSION_END, session_id="test123")
        assert worker._conversation_active is False


class TestBackgroundWorkerLifecycle:
    @pytest.mark.asyncio
    async def test_start_creates_tasks(self, mock_container):
        worker = BackgroundWorker(mock_container)
        await worker.start()
        assert worker._safe_task is not None
        assert worker._idle_task is not None
        assert not worker._safe_task.done()
        assert not worker._idle_task.done()
        await worker.stop()

    @pytest.mark.asyncio
    async def test_stop_cancels_tasks(self, mock_container):
        worker = BackgroundWorker(mock_container)
        await worker.start()
        await worker.stop()
        assert worker._safe_task.cancelled() or worker._safe_task.done()
        assert worker._idle_task.cancelled() or worker._idle_task.done()

    @pytest.mark.asyncio
    async def test_idle_item_runs_when_idle(self, mock_container):
        handler = AsyncMock()
        worker = BackgroundWorker(mock_container)
        worker.register(WorkItem(
            name="test", priority=1, tier="idle",
            interval_seconds=0,  # no cooldown for test
            handler=handler,
        ))
        await worker.start()
        await asyncio.sleep(0.15)  # let loop run one cycle
        await worker.stop()
        handler.assert_called()

    @pytest.mark.asyncio
    async def test_idle_item_skipped_during_conversation(self, mock_container):
        handler = AsyncMock()
        worker = BackgroundWorker(mock_container)
        worker.register(WorkItem(
            name="test", priority=1, tier="idle",
            interval_seconds=0,
            handler=handler,
        ))
        # Simulate active conversation
        await mock_container.event_emitter.emit(EventType.SESSION_START, session_id="x")
        await worker.start()
        await asyncio.sleep(0.15)
        await worker.stop()
        handler.assert_not_called()

    @pytest.mark.asyncio
    async def test_safe_item_runs_during_conversation(self, mock_container):
        handler = AsyncMock()
        worker = BackgroundWorker(mock_container)
        worker.register(WorkItem(
            name="test", priority=1, tier="safe",
            interval_seconds=0,
            handler=handler,
        ))
        await mock_container.event_emitter.emit(EventType.SESSION_START, session_id="x")
        await worker.start()
        await asyncio.sleep(0.15)
        await worker.stop()
        handler.assert_called()


class TestBackgroundWorkerCooldown:
    @pytest.mark.asyncio
    async def test_item_respects_interval(self, mock_container):
        """Item should not run again until interval has elapsed."""
        handler = AsyncMock()
        worker = BackgroundWorker(mock_container)
        worker.register(WorkItem(
            name="slow", priority=1, tier="idle",
            interval_seconds=999,  # very long cooldown
            handler=handler,
        ))
        await worker.start()
        await asyncio.sleep(0.15)
        await worker.stop()
        # Should have run exactly once (first time, never run before)
        assert handler.call_count == 1


class TestBackgroundWorkerErrorHandling:
    @pytest.mark.asyncio
    async def test_handler_error_does_not_crash_loop(self, mock_container):
        """A failing handler should not kill the background loop."""
        failing = AsyncMock(side_effect=RuntimeError("boom"))
        worker = BackgroundWorker(mock_container)
        worker.register(WorkItem(
            name="fail", priority=1, tier="idle",
            interval_seconds=0, handler=failing,
        ))
        await worker.start()
        await asyncio.sleep(0.15)
        assert worker.is_running
        await worker.stop()

    @pytest.mark.asyncio
    async def test_double_stop_is_safe(self, mock_container):
        worker = BackgroundWorker(mock_container)
        await worker.start()
        await worker.stop()
        await worker.stop()  # should not raise
