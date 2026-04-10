"""Tests for Phase 4 Session Manager."""
import uuid
import pytest
from unittest.mock import MagicMock, AsyncMock
from kora_v2.core.models import EmotionalState, EnergyEstimate


class TestSessionManager:
    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path):
        from kora_v2.daemon.session import SessionManager
        # Use a unique temp path per test to prevent cross-test bridge contamination
        unique_id = uuid.uuid4().hex[:8]
        self.container = MagicMock()
        self.container.settings = MagicMock()
        self.container.settings.data_dir = tmp_path
        self.container.settings.memory = MagicMock()
        self.container.settings.memory.kora_memory_path = str(tmp_path / f"test_kora_{unique_id}")
        self.container.projection_db = None
        self.container.event_emitter = MagicMock()
        self.container.event_emitter.emit = AsyncMock()
        self.manager = SessionManager(self.container)

    @pytest.mark.asyncio
    async def test_init_session_creates_id(self):
        session = await self.manager.init_session()
        assert session.session_id is not None
        assert len(session.session_id) > 0

    @pytest.mark.asyncio
    async def test_init_session_sets_emotional_state(self):
        session = await self.manager.init_session()
        assert isinstance(session.emotional_state, EmotionalState)

    @pytest.mark.asyncio
    async def test_init_session_sets_energy(self):
        session = await self.manager.init_session()
        assert isinstance(session.energy_estimate, EnergyEstimate)

    @pytest.mark.asyncio
    async def test_end_session_creates_bridge(self):
        session = await self.manager.init_session()
        bridge = await self.manager.end_session(
            messages=[
                {"role": "user", "content": "Let's plan my morning"},
                {"role": "assistant", "content": "Sure! Let's start."},
            ],
            emotional_state=EmotionalState(valence=0.3, arousal=0.4, dominance=0.6),
        )
        assert bridge is not None
        assert bridge.session_id == session.session_id
        assert len(bridge.summary) > 0

    @pytest.mark.asyncio
    async def test_no_active_session_initially(self):
        assert self.manager.active_session is None

    @pytest.mark.asyncio
    async def test_end_session_clears_active(self):
        await self.manager.init_session()
        assert self.manager.active_session is not None
        await self.manager.end_session(
            messages=[],
            emotional_state=EmotionalState(valence=0, arousal=0.3, dominance=0.5),
        )
        assert self.manager.active_session is None

    @pytest.mark.asyncio
    async def test_get_thread_id_persistent(self):
        await self.manager.init_session()
        tid1 = self.manager.get_thread_id()
        tid2 = self.manager.get_thread_id()
        assert tid1 == tid2  # Same within session

    @pytest.mark.asyncio
    async def test_load_last_bridge_returns_none_when_empty(self):
        bridge = await self.manager.load_last_bridge()
        assert bridge is None

    @pytest.mark.asyncio
    async def test_extract_open_threads(self):
        threads = self.manager._extract_open_threads([
            {"role": "user", "content": "What time should I wake up?"},
            {"role": "assistant", "content": "I'd suggest 7am."},
            {"role": "user", "content": "Should I take meds before breakfast?"},
        ])
        assert len(threads) == 2
        assert "wake up?" in threads[0]


class TestEmotionDecay:
    def test_decay_toward_neutral(self):
        from kora_v2.daemon.session import apply_emotion_decay
        state = EmotionalState(
            valence=-0.8, arousal=0.9, dominance=0.2,
            mood_label="distressed", confidence=0.8, source="fast",
        )
        decayed = apply_emotion_decay(state, hours_elapsed=1.0)
        assert decayed.valence > state.valence  # closer to 0
        assert abs(decayed.valence) < abs(state.valence)
        assert decayed.source == "loaded"

    def test_no_decay_within_session(self):
        from kora_v2.daemon.session import apply_emotion_decay
        state = EmotionalState(
            valence=0.8, arousal=0.3, dominance=0.7,
            mood_label="happy", confidence=0.9, source="fast",
        )
        decayed = apply_emotion_decay(state, hours_elapsed=0.0)
        assert decayed.valence == state.valence

    def test_heavy_decay_after_long_gap(self):
        from kora_v2.daemon.session import apply_emotion_decay
        state = EmotionalState(
            valence=-1.0, arousal=1.0, dominance=0.0,
            mood_label="distressed", confidence=0.9, source="llm",
        )
        decayed = apply_emotion_decay(state, hours_elapsed=5.0)
        assert abs(decayed.valence) < 0.4  # significantly decayed

    def test_decay_formula(self):
        """Verify the 20%/hr exponential decay formula."""
        from kora_v2.daemon.session import apply_emotion_decay
        state = EmotionalState(
            valence=1.0, arousal=1.0, dominance=1.0,
            mood_label="excited", confidence=1.0, source="fast",
        )
        # After 1 hour: remaining = 0.8^1 = 0.8
        # valence: 0 + (1.0 - 0) * 0.8 = 0.8
        decayed = apply_emotion_decay(state, hours_elapsed=1.0)
        assert abs(decayed.valence - 0.8) < 0.01
        # arousal: 0.5 + (1.0 - 0.5) * 0.8 = 0.5 + 0.4 = 0.9
        assert abs(decayed.arousal - 0.9) < 0.01
