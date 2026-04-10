"""Tests for session event emission."""

import pytest
from unittest.mock import MagicMock, AsyncMock

from kora_v2.core.events import EventEmitter, EventType
from kora_v2.daemon.session import SessionManager


@pytest.fixture
def container_with_emitter(tmp_path):
    container = MagicMock()
    container.event_emitter = EventEmitter()
    container.settings.data_dir = tmp_path
    container.settings.memory.kora_memory_path = str(tmp_path / "kora_memory")
    container.projection_db = None
    return container


@pytest.fixture(autouse=True)
def _mock_working_memory(monkeypatch):
    """Mock WorkingMemoryLoader and estimate_energy to avoid side effects."""
    mock_loader = MagicMock()
    mock_loader.return_value.load = AsyncMock(return_value=[])
    monkeypatch.setattr("kora_v2.daemon.session.WorkingMemoryLoader", mock_loader)
    from kora_v2.core.models import EnergyEstimate
    monkeypatch.setattr(
        "kora_v2.daemon.session.estimate_energy",
        lambda: EnergyEstimate(level="medium", focus="moderate", confidence=0.4, source="time_of_day", signals={}),
    )


class TestSessionEventEmission:
    @pytest.mark.asyncio
    async def test_init_session_emits_session_start(self, container_with_emitter):
        handler = AsyncMock()
        container_with_emitter.event_emitter.on(EventType.SESSION_START, handler)

        mgr = SessionManager(container_with_emitter)
        session = await mgr.init_session()

        handler.assert_called_once()
        payload = handler.call_args[0][0]
        assert payload["event_type"] == EventType.SESSION_START
        assert payload["session_id"] == session.session_id

    @pytest.mark.asyncio
    async def test_end_session_emits_session_end(self, container_with_emitter):
        handler = AsyncMock()
        container_with_emitter.event_emitter.on(EventType.SESSION_END, handler)

        mgr = SessionManager(container_with_emitter)
        await mgr.init_session()

        from kora_v2.core.models import EmotionalState
        await mgr.end_session(
            messages=[],
            emotional_state=EmotionalState(valence=0, arousal=0.3, dominance=0.5),
        )

        handler.assert_called_once()
        payload = handler.call_args[0][0]
        assert payload["event_type"] == EventType.SESSION_END
