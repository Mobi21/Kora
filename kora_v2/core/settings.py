"""Kora V2 — Pydantic Settings with 10 nested sections.

All configuration is loaded from ~/.kora/settings.toml and/or KORA_*
environment variables.  Path fields that start with ``~`` are expanded
at validation time.
"""

from __future__ import annotations

import os
from datetime import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Self

from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict, TomlConfigSettingsSource


def _detect_user_tz() -> str:
    """Auto-detect the system's IANA timezone name.

    Prefers ``tzlocal`` when available (canonical IANA names).
    Falls back to reading ``/etc/localtime`` (macOS/Linux), then to the
    ``datetime.now().astimezone()`` tzinfo, then to ``UTC``.
    """
    try:
        import tzlocal  # type: ignore

        return str(tzlocal.get_localzone())
    except Exception:
        pass
    # /etc/localtime is typically a symlink to something like
    # /usr/share/zoneinfo/America/Los_Angeles on macOS and most Linux distros.
    try:
        link = Path("/etc/localtime").resolve()
        zi = link.as_posix()
        marker = "/zoneinfo/"
        if marker in zi:
            name = zi.split(marker, 1)[1]
            if name:
                return name
    except Exception:
        pass
    try:
        from datetime import datetime as _dt
        tz = _dt.now().astimezone().tzinfo
        if tz is not None:
            name = getattr(tz, "key", None) or str(tz)
            if name and name != "None":
                return str(name)
    except Exception:
        pass
    return "UTC"

# ── LLM ──────────────────────────────────────────────────────────────────

class LLMSettings(BaseModel):
    """MiniMax M2.7 provider defaults."""

    provider: str = "minimax"
    model: str = "MiniMax-M2.7-highspeed"
    background_model: str = ""  # empty = use primary model for background too
    api_base: str = "https://api.minimax.io/anthropic"
    api_key: str = ""  # KORA_LLM__API_KEY or fallback to MINIMAX_API_KEY

    @model_validator(mode="after")
    def _resolve_api_key(self) -> Self:
        """Fall back to legacy env names and local dotenv files if api_key is empty."""
        if not self.api_key:
            self.api_key = os.environ.get("MINIMAX_API_KEY", "")
        if not self.api_key:
            try:
                from dotenv import dotenv_values

                project_root = Path(__file__).resolve().parents[2]
                for dotenv_path in (project_root / ".env.local", project_root / ".env"):
                    if not dotenv_path.exists():
                        continue
                    data = dotenv_values(dotenv_path)
                    key = data.get("MINIMAX_API_KEY") or data.get("KORA_LLM__API_KEY")
                    if key:
                        self.api_key = str(key)
                        break
            except Exception:
                pass
        return self
    max_tokens: int = 16384
    context_window: int = 205_000
    temperature: float = Field(default=0.7, ge=0.01, le=1.0)
    thinking_budget: int = 10000
    timeout: int = 120
    retry_attempts: int = 3
    enable_caching: bool = True


# ── Memory ───────────────────────────────────────────────────────────────

class MemorySettings(BaseModel):
    """Filesystem-canonical memory stack + local embeddings."""

    kora_memory_path: str = "~/.kora/memory"
    embedding_model: str = "nomic-ai/nomic-embed-text-v1.5"
    embedding_dims: int = 768
    hybrid_vector_weight: float = 0.7
    hybrid_fts_weight: float = 0.3
    dedup_threshold: float = 0.50
    max_signal_scanner_patterns: int = 50


# ── Agents ───────────────────────────────────────────────────────────────

class AgentSettings(BaseModel):
    """Multi-agent execution budget and safety limits."""

    iteration_budget: int = 150
    default_timeout: int = 300
    loop_detection_threshold: int = 3
    reviewer_sampling_rate: float = 0.1
    thinking_for_planner: bool = True


# ── Quality ──────────────────────────────────────────────────────────────

class QualitySettings(BaseModel):
    """Quality-gate thresholds and regression detection."""

    confidence_threshold: float = 0.6
    regression_window_days: int = 7
    regression_threshold: float = 0.15
    llm_judge_sampling: float = 0.1


# ── Daemon ───────────────────────────────────────────────────────────────

class DaemonSettings(BaseModel):
    """Daemon bind address and background-work pacing."""

    host: str = "127.0.0.1"
    port: int = 8765  # fixed port for sandbox compatibility
    idle_check_interval: int = 300
    background_safe_interval: int = 60


# ── Notifications ────────────────────────────────────────────────────────

class NotificationSettings(BaseModel):
    """Proactive engagement and ADHD-aware pacing.

    Phase 5 adds cadence + DND fields that the Phase 8 ProactiveAgent will
    consume. Phase 5 builds the hooks but does not act on them.
    """

    enabled: bool = True
    cooldown_minutes: int = 15
    respect_dnd: bool = True
    re_engagement_hours: int = 4
    hyperfocus_threshold_turns: int = 3
    # Phase 5: hourly cap + DND window (consumed by Phase 8 ProactiveAgent).
    max_per_hour: int = 4
    dnd_start: time | None = None  # local user_tz; None disables window
    dnd_end: time | None = None


# ── Planning ─────────────────────────────────────────────────────────────

class PlanningCadence(BaseModel):
    """Kora-led planning cadence — daily/weekly/monthly triggers.

    Phase 5 stores the configuration; Phase 8 ProactiveAgent acts on it.
    """

    daily_planning_enabled: bool = True
    daily_planning_time: time | None = None  # None = first session of the day
    weekly_planning_enabled: bool = True
    weekly_planning_day: str = "Sunday"
    weekly_planning_time: time = time(18, 0)
    monthly_reflection_enabled: bool = True
    monthly_reflection_day: int = 1


class PlanningSettings(BaseModel):
    """Top-level planning configuration (cadence + helpers)."""

    cadence: PlanningCadence = PlanningCadence()


# ── Autonomous ───────────────────────────────────────────────────────────

class AutonomousSettings(BaseModel):
    """Checkpoint-based autonomous execution."""

    enabled: bool = True
    daily_cost_limit: float = 5.0
    per_session_cost_limit: float = 1.0
    max_session_hours: float = 4.0
    checkpoint_interval_minutes: int = 30
    auto_continue_seconds: int = 30
    decision_timeout_minutes: int = 10
    request_limit_per_hour: int | None = None
    request_limit_per_5h_window: int | None = None
    max_request_count: int | None = None
    request_warning_threshold: float = Field(default=0.85, gt=0.0, le=1.0)
    request_hard_stop_threshold: float = Field(default=1.0, gt=0.0, le=1.0)
    cost_warning_threshold: float = 0.8
    token_warning_threshold: float = 0.85
    max_concurrent_tasks: int = 1
    overlap_similarity_threshold: float = 0.6
    delegate_to_claude_code: bool = False
    claude_code_binary: str = "claude"


# ── Orchestration ─────────────────────────────────────────────────────────

class OrchestrationSettings(BaseModel):
    """Background orchestration runtime controls."""

    trigger_evaluator_enabled: bool = True
    trigger_tick_interval_seconds: float = 5.0


# ── MCP ──────────────────────────────────────────────────────────────────

class MCPServerConfig(BaseModel):
    """Single MCP server definition."""

    command: str
    args: list[str] = []
    env: dict[str, str] = {}
    enabled: bool = True


class MCPSettings(BaseModel):
    """Model Context Protocol server registry."""

    servers: dict[str, MCPServerConfig] = {}
    startup_timeout: int = 30


# ── Security ─────────────────────────────────────────────────────────────

class SecuritySettings(BaseModel):
    """Localhost-only binding, CORS, injection scanning, auth mode."""

    api_token_path: str = "data/.api_token"
    cors_origins: list[str] = ["http://localhost:*", "http://127.0.0.1:*"]
    injection_scan_enabled: bool = True
    auth_mode: str = "prompt"  # "prompt" = normal flow, "trust_all" = skip ASK_FIRST prompts


# ── Browser ──────────────────────────────────────────────────────────────

class BrowserSettings(BaseModel):
    """Agent-browser integration."""

    enabled: bool = False
    binary_path: str = ""  # empty = auto-detect on PATH
    default_profile: str = ""  # empty = use browser default
    clip_target: str = "vault"  # "vault" | "memory" | "both" | "none"
    max_session_duration_seconds: int = 3600
    command_timeout_seconds: int = 30


# ── Vault ────────────────────────────────────────────────────────────────

class VaultSettings(BaseModel):
    """Obsidian vault integration."""

    enabled: bool = True
    path: str = ""


# ── Workspace config default factory ─────────────────────────────────────────
# Imported lazily via a factory to keep settings.py free of capability imports
# while still allowing WorkspaceConfig to be declared on Settings.

def _workspace_config_default() -> Any:
    from kora_v2.capabilities.workspace.config import WorkspaceConfig  # noqa: PLC0415
    return WorkspaceConfig()


# ── Root ─────────────────────────────────────────────────────────────────

class Settings(BaseSettings):
    """Top-level configuration.

    Loaded (in priority order) from:
      1. Environment variables  (``KORA_LLM__MODEL``, etc.)
      2. ``~/.kora/settings.toml``
      3. In-code defaults above
    """

    llm: LLMSettings = LLMSettings()
    memory: MemorySettings = MemorySettings()
    agents: AgentSettings = AgentSettings()
    quality: QualitySettings = QualitySettings()
    daemon: DaemonSettings = DaemonSettings()
    notifications: NotificationSettings = NotificationSettings()
    autonomous: AutonomousSettings = AutonomousSettings()
    orchestration: OrchestrationSettings = OrchestrationSettings()
    mcp: MCPSettings = MCPSettings()
    security: SecuritySettings = SecuritySettings()
    vault: VaultSettings = VaultSettings()
    browser: BrowserSettings = BrowserSettings()
    planning: PlanningSettings = PlanningSettings()
    workspace: Any = Field(default_factory=_workspace_config_default)
    # Phase 5: display timezone (storage is always UTC). Auto-detected on
    # first run; user can override in the first-run wizard.
    user_tz: str = Field(default_factory=_detect_user_tz)

    model_config = SettingsConfigDict(
        toml_file=Path("~/.kora/settings.toml").expanduser(),
        env_prefix="KORA_",
        env_nested_delimiter="__",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ):
        def filtered_env_settings() -> dict[str, object]:
            data = env_settings()
            if isinstance(data, dict):
                daemon_value = data.get("daemon")
                if daemon_value is not None and not isinstance(daemon_value, dict):
                    data = {k: v for k, v in data.items() if k != "daemon"}
            return data

        return (
            init_settings,
            filtered_env_settings,
            TomlConfigSettingsSource(settings_cls),
            file_secret_settings,
        )

    # ── Path expansion ───────────────────────────────────────────────

    @model_validator(mode="after")
    def expand_home_paths(self) -> Self:
        """Expand ``~`` in every path-like field after loading."""
        self.memory.kora_memory_path = str(
            Path(self.memory.kora_memory_path).expanduser()
        )
        self.security.api_token_path = str(
            Path(self.security.api_token_path).expanduser()
        )
        if self.vault.path:
            self.vault.path = str(Path(self.vault.path).expanduser())
        if self.browser.binary_path:
            self.browser.binary_path = str(Path(self.browser.binary_path).expanduser())
        return self

    # ── Derived helpers ──────────────────────────────────────────────

    @property
    def data_dir(self) -> Path:
        """Return (and lazily create) the runtime data directory."""
        p = Path("data")
        p.mkdir(parents=True, exist_ok=True)
        return p


# ── Factory ──────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the singleton *Settings* instance.

    First call constructs from env + TOML; subsequent calls return the
    cached object.  Call ``get_settings.cache_clear()`` in tests to reset.
    """
    return Settings()
