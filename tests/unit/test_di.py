"""Tests for kora_v2.core.di — DI Container."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from kora_v2.core.di import Container
from kora_v2.core.events import EventEmitter
from kora_v2.core.settings import Settings
from kora_v2.llm.minimax import MiniMaxProvider


@pytest.fixture()
def settings() -> Settings:
    """Return a default Settings instance for DI tests."""
    return Settings()


class TestContainerConstruction:
    """Container initializes all Phase 1 services from settings."""

    def test_container_constructs_from_settings(self, settings: Settings):
        """Container accepts a Settings object without error."""
        container = Container(settings)
        assert container.settings is settings

    def test_llm_is_minimax_provider(self, settings: Settings):
        """container.llm is a MiniMaxProvider instance."""
        container = Container(settings)
        assert isinstance(container.llm, MiniMaxProvider)

    def test_event_emitter_is_created(self, settings: Settings):
        """container.event_emitter is an EventEmitter instance."""
        container = Container(settings)
        assert isinstance(container.event_emitter, EventEmitter)

    def test_life_os_services_resolve_lazily(self, settings: Settings, tmp_path, monkeypatch):
        """Life OS services are reachable from the runtime container."""
        monkeypatch.chdir(tmp_path)
        settings.memory.kora_memory_path = str(tmp_path / "memory")
        container = Container(settings)

        assert container.life_event_ledger is not None
        assert container.day_plan_service is not None
        assert container.support_registry is not None
        assert container.crisis_safety_router is not None
        assert container.life_load_engine is not None
        assert container.day_repair_engine is not None
        assert container.proactivity_policy_engine is not None
        assert container.stabilization_mode_service is not None
        assert container.context_pack_service is not None
        assert container.future_self_bridge_service is not None


class TestSupervisorGraphLazy:
    """Supervisor graph is built lazily on first property access."""

    def test_graph_not_built_at_init(self, settings: Settings):
        """The private _supervisor_graph is None after __init__."""
        container = Container(settings)
        assert container._supervisor_graph is None

    @patch("kora_v2.core.di.MiniMaxProvider")
    def test_graph_builds_on_first_access(self, mock_provider_cls: MagicMock):
        """Accessing container.supervisor_graph triggers build_supervisor_graph."""
        s = Settings()
        container = Container(s)

        mock_graph = MagicMock()
        with patch(
            "kora_v2.graph.supervisor.build_supervisor_graph",
            return_value=mock_graph,
        ) as mock_build:
            graph = container.supervisor_graph
            mock_build.assert_called_once_with(container)
            assert graph is mock_graph

    @patch("kora_v2.core.di.MiniMaxProvider")
    def test_graph_cached_after_first_build(self, mock_provider_cls: MagicMock):
        """Second access returns the same instance without rebuilding."""
        s = Settings()
        container = Container(s)

        mock_graph = MagicMock()
        with patch(
            "kora_v2.graph.supervisor.build_supervisor_graph",
            return_value=mock_graph,
        ) as mock_build:
            first = container.supervisor_graph
            second = container.supervisor_graph
            assert first is second
            assert mock_build.call_count == 1


class TestPhase4Initialization:
    """Phase 4 services: emotion, quality, session manager."""

    def test_phase4_attrs_none_before_init(self, settings: Settings):
        """Phase 4 attributes are None before initialize_phase4()."""
        container = Container(settings)
        assert container.fast_emotion is None
        assert container.llm_emotion is None
        assert container.quality_collector is None
        assert container.session_manager is None

    def test_initialize_phase4(self, settings: Settings):
        """initialize_phase4 creates all Phase 4 services."""
        container = Container(settings)
        container.initialize_phase4()
        assert container.fast_emotion is not None
        assert container.llm_emotion is not None
        assert container.quality_collector is not None
        assert container.session_manager is not None

    def test_initialize_phase4_types(self, settings: Settings):
        """Phase 4 services have correct types."""
        from kora_v2.daemon.session import SessionManager
        from kora_v2.emotion.fast_assessor import FastEmotionAssessor
        from kora_v2.emotion.llm_assessor import LLMEmotionAssessor
        from kora_v2.quality.tier1 import QualityCollector

        container = Container(settings)
        container.initialize_phase4()
        assert isinstance(container.fast_emotion, FastEmotionAssessor)
        assert isinstance(container.llm_emotion, LLMEmotionAssessor)
        assert isinstance(container.quality_collector, QualityCollector)
        assert isinstance(container.session_manager, SessionManager)

    def test_session_manager_has_container_ref(self, settings: Settings):
        """SessionManager receives the container reference."""
        container = Container(settings)
        container.initialize_phase4()
        assert container.session_manager.container is container


class TestWorkerResolution:
    """resolve_worker routes by worker type (Phase 3)."""

    def test_resolve_worker_unknown_raises(self, settings: Settings):
        """Unknown worker names raise ValueError.

        The Phase 5 "on-demand agents" design was never implemented —
        resolve_worker only knows planner/executor/reviewer and raises
        ValueError for anything else. This test pins that contract.
        """
        container = Container(settings)
        with pytest.raises(ValueError, match="Unknown worker"):
            container.resolve_worker("memory")

    def test_resolve_worker_core_before_init_raises(self, settings: Settings):
        """Core workers raise RuntimeError before initialize_workers()."""
        container = Container(settings)
        with pytest.raises(RuntimeError, match="not initialized"):
            container.resolve_worker("planner")
