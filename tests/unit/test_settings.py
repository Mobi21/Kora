"""Tests for kora_v2.core.settings — Pydantic Settings with nested sections."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from kora_v2.core.settings import (
    LLMSettings,
    MCPSettings,
    Settings,
    get_settings,
)


class TestDefaultSettings:
    """Verify all default values are correctly set."""

    def test_default_settings(self):
        """Create Settings() and verify all top-level section defaults."""
        s = Settings(mcp=MCPSettings(servers={}), vault={"enabled": True, "path": ""})

        # LLM defaults
        assert s.llm.provider == "minimax"
        assert s.llm.model == "MiniMax-M2.7-highspeed"
        assert s.llm.background_model == ""
        assert s.llm.api_base == "https://api.minimax.io/anthropic"
        assert s.llm.max_tokens == 16384
        assert s.llm.context_window == 205_000
        assert s.llm.temperature == 0.7
        assert s.llm.thinking_budget == 10000
        assert s.llm.timeout == 120
        assert s.llm.retry_attempts == 3
        assert s.llm.enable_caching is True

        # Memory defaults
        assert s.memory.embedding_model == "nomic-ai/nomic-embed-text-v1.5"
        assert s.memory.embedding_dims == 768
        assert s.memory.hybrid_vector_weight == 0.7
        assert s.memory.hybrid_fts_weight == 0.3
        assert s.memory.dedup_threshold == 0.50
        assert s.memory.max_signal_scanner_patterns == 50

        # Agent defaults
        assert s.agents.iteration_budget == 150
        assert s.agents.default_timeout == 300
        assert s.agents.loop_detection_threshold == 3

        # Quality defaults
        assert s.quality.confidence_threshold == 0.6
        assert s.quality.regression_window_days == 7

        # Daemon defaults
        assert s.daemon.host == "127.0.0.1"
        assert s.daemon.port == 8765

        # Notifications defaults
        assert s.notifications.enabled is True
        assert s.notifications.cooldown_minutes == 15

        # Autonomous defaults
        assert s.autonomous.enabled is True
        assert s.autonomous.checkpoint_interval_minutes == 30

        # MCP defaults
        assert s.mcp.servers == {}
        assert s.mcp.startup_timeout == 30

        # Security defaults
        assert s.security.cors_origins == ["http://localhost:*", "http://127.0.0.1:*"]
        assert s.security.injection_scan_enabled is True

        # Vault defaults
        assert s.vault.enabled is True


class TestTemperatureValidation:
    """Temperature must be in [0.01, 1.0]."""

    def test_temperature_too_low(self):
        """Temperature below 0.01 should fail validation."""
        with pytest.raises(ValidationError):
            LLMSettings(temperature=0.0)

    def test_temperature_at_minimum(self):
        """Temperature at 0.01 should pass."""
        llm = LLMSettings(temperature=0.01)
        assert llm.temperature == 0.01

    def test_temperature_at_maximum(self):
        """Temperature at 1.0 should pass."""
        llm = LLMSettings(temperature=1.0)
        assert llm.temperature == 1.0

    def test_temperature_above_maximum(self):
        """Temperature above 1.0 should fail validation."""
        with pytest.raises(ValidationError):
            LLMSettings(temperature=1.01)

    def test_temperature_negative(self):
        """Negative temperature should fail validation."""
        with pytest.raises(ValidationError):
            LLMSettings(temperature=-0.5)


class TestPathExpansion:
    """Path fields with ~ should be expanded to actual home dir."""

    def test_kora_memory_path_expanded(self):
        """kora_memory_path should expand ~ to the user's home directory."""
        s = Settings(vault={"enabled": True, "path": ""})
        home = str(Path.home())
        assert s.memory.kora_memory_path.startswith(home)
        assert "~" not in s.memory.kora_memory_path

    def test_api_token_path_expanded(self):
        """api_token_path should not contain ~ after expansion."""
        s = Settings(vault={"enabled": True, "path": ""})
        # api_token_path defaults to "data/.api_token" (no ~), so no expansion
        # but the validator runs regardless
        assert "~" not in s.security.api_token_path

    def test_vault_path_expansion_when_set(self):
        """vault.path with ~ should be expanded."""
        s = Settings(vault={"enabled": True, "path": "~/my_vault"})
        home = str(Path.home())
        assert s.vault.path.startswith(home)
        assert "~" not in s.vault.path

    def test_vault_path_empty_not_expanded(self):
        """vault.path empty string should stay empty (falsy check in validator)."""
        s = Settings(vault={"enabled": True, "path": ""})
        assert s.vault.path == ""

    def test_workspace_config_is_typed_when_overridden(self):
        """workspace TOML/env overrides should still produce WorkspaceConfig."""
        s = Settings(
            workspace={
                "mcp_server_name": "workspace",
                "user_google_email": "user@example.com",
            }
        )
        assert s.workspace.mcp_server_name == "workspace"
        assert s.workspace.user_google_email == "user@example.com"


class TestEnvOverride:
    """Environment variables should override defaults via KORA_ prefix."""

    def test_env_override_llm_model(self, monkeypatch):
        """KORA_LLM__MODEL should override the default LLM model."""
        # Clear the lru_cache to ensure fresh Settings
        get_settings.cache_clear()

        monkeypatch.setenv("KORA_LLM__MODEL", "test-model-override")
        s = Settings()
        assert s.llm.model == "test-model-override"

    def test_env_override_daemon_port(self, monkeypatch):
        """KORA_DAEMON__PORT should override the default daemon port."""
        monkeypatch.setenv("KORA_DAEMON__PORT", "9999")
        s = Settings()
        assert s.daemon.port == 9999

    def test_env_override_temperature(self, monkeypatch):
        """KORA_LLM__TEMPERATURE should override default temperature."""
        monkeypatch.setenv("KORA_LLM__TEMPERATURE", "0.5")
        s = Settings()
        assert s.llm.temperature == 0.5
