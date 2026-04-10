"""Shared Pydantic models used across Kora V2.

These models are the data contracts between the supervisor, workers,
tools, and infrastructure. All typed, all validated.
"""

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

# --- Emotion & Energy ---

class EmotionalState(BaseModel):
    valence: float = Field(ge=-1.0, le=1.0)
    arousal: float = Field(ge=0.0, le=1.0)
    dominance: float = Field(ge=0.0, le=1.0)
    mood_label: str = "neutral"
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    source: Literal["fast", "llm", "loaded"] = "fast"
    assessed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

class EnergyEstimate(BaseModel):
    level: Literal["low", "medium", "high"]
    focus: Literal["scattered", "moderate", "locked_in"]
    confidence: float = Field(ge=0.0, le=1.0)
    source: str  # "time_of_day", "behavioral_signals", "adhd_profile"
    signals: dict[str, Any] = {}

# --- Planning ---

class PlanConstraints(BaseModel):
    max_steps: int = 10
    max_minutes: int = 120
    autonomy_level: Literal["ask_always", "ask_important", "full_auto"] = "ask_important"
    available_tools: list[str] = []

class ExecutionConstraints(BaseModel):
    timeout_seconds: int = 300
    token_budget: int = 50000
    auth_level: Literal["always_allowed", "ask_first", "never"] = "ask_first"

class PlanStep(BaseModel):
    id: str
    title: str
    description: str
    depends_on: list[str] = []
    estimated_minutes: int
    worker: Literal["executor", "memory", "reviewer", "research", "code", "life_mgmt", "screen"]
    tools_needed: list[str]
    energy_level: Literal["low", "medium", "high"]
    needs_review: bool = False
    review_criteria: list[str] = []

class Plan(BaseModel):
    id: str
    goal: str
    steps: list[PlanStep]
    estimated_total_minutes: int
    confidence: float
    adhd_notes: str = ""

# --- Worker Communication ---

class Artifact(BaseModel):
    type: Literal["file", "url", "data", "report", "code"]
    uri: str
    label: str
    size_bytes: int | None = None

class WorkerStatus(BaseModel):
    worker_name: str
    state: Literal["idle", "running", "completed", "failed"]
    current_task: str | None = None
    percent: float = 0.0
    started_at: datetime | None = None

class WorkerResult(BaseModel):
    worker_name: str
    success: bool
    result_json: str
    confidence: float
    duration_ms: int
    tool_calls: int
    error: str | None = None

class ToolCallRecord(BaseModel):
    tool_name: str
    args: dict[str, Any]
    result_summary: str
    success: bool
    duration_ms: int
    timestamp: datetime

class MemoryResult(BaseModel):
    id: str
    content: str
    layer: Literal["long_term", "user_model"]
    memory_type: str | None = None
    domain: str | None = None
    score: float
    source_path: str

# --- Quality ---

class QualityGateResult(BaseModel):
    gate_name: str
    passed: bool
    reason: str | None = None
    suggested_fix: str | None = None

# --- Session ---

class SessionState(BaseModel):
    session_id: str
    turn_count: int
    started_at: datetime
    emotional_state: EmotionalState
    energy_estimate: EnergyEstimate
    active_plan: Plan | None = None
    pending_items: list[dict] = []

# --- Notifications ---

class Notification(BaseModel):
    id: str
    priority: Literal["high", "medium", "low"]
    content: str
    category: str
    delivery_channel: Literal["tray", "native", "inline", "greeting"]

# --- MCP ---

class MCPServer(BaseModel):
    name: str
    command: str
    args: list[str] = []
    env: dict[str, str] = {}
    enabled: bool = True
    tools: list[str] = []


# --- Worker I/O (Phase 3) ---


class PlanInput(BaseModel):
    goal: str
    context: str = ""
    constraints: PlanConstraints = Field(default_factory=PlanConstraints)
    existing_plan: Plan | None = None


class PlanOutput(BaseModel):
    plan: Plan
    steps: list[PlanStep]
    estimated_effort: Literal["quick", "moderate", "complex"]
    confidence: float = Field(ge=0.0, le=1.0)
    adhd_notes: str = ""


class ExecutionInput(BaseModel):
    task: str
    tools_available: list[str] = []
    context: str = ""
    constraints: ExecutionConstraints = Field(default_factory=ExecutionConstraints)
    energy_level: Literal["low", "medium", "high"] | None = None
    estimated_minutes: int | None = None
    # Side-effecting parameters (e.g. ``path``/``content`` for write_file).
    # Populated by ``_coerce_executor_input`` in graph/dispatch.py and
    # consumed by the executor's deterministic fast path in
    # agents/workers/executor.py. Without this field, Pydantic silently
    # drops the dispatch-side params dict and the fast path is dead code.
    params: dict[str, Any] = Field(default_factory=dict)


class ExecutionOutput(BaseModel):
    result: str
    tool_calls_made: list[ToolCallRecord] = []
    artifacts: list[Artifact] = []
    success: bool
    confidence: float = Field(ge=0.0, le=1.0)
    error: str | None = None


class ReviewInput(BaseModel):
    work_product: str
    criteria: list[str] = []
    original_goal: str = ""
    context: str = ""


class ReviewFinding(BaseModel):
    severity: Literal["critical", "warning", "info"]
    category: Literal[
        "correctness", "completeness", "security", "quality", "adhd_friendliness"
    ]
    description: str
    suggested_fix: str | None = None


class ReviewOutput(BaseModel):
    passed: bool
    findings: list[ReviewFinding] = []
    confidence: float = Field(ge=0.0, le=1.0)
    recommendation: Literal["accept", "revise", "reject"]
    revision_guidance: str | None = None


class ADHDViolation(BaseModel):
    pattern_name: str
    category: str
    matched_text: str
    severity: Literal["high", "medium", "low"]
    suggested_rewrite: str | None = None


class ADHDScanResult(BaseModel):
    is_clean: bool
    violations: list[ADHDViolation] = []
    scan_time_ms: float = 0.0


# --- Session (Phase 4) ---

class SessionBridge(BaseModel):
    """Bridge note connecting sessions for continuity."""
    session_id: str
    summary: str
    open_threads: list[str] = []
    emotional_trajectory: str = ""
    active_plan_id: str | None = None
    continuation_checkpoint_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class CompactionResult(BaseModel):
    """Result of a compaction stage — carries both new messages and metadata."""
    stage: Literal[
        "observation_masking", "structured_summary",
        "aggressive_recompress", "hard_stop"
    ]
    messages: list[dict[str, Any]]  # The new compacted message list
    tokens_before: int
    tokens_after: int
    messages_removed: int = 0
    messages_masked: int = 0
    summary_text: str | None = None

    @property
    def tokens_saved(self) -> int:
        return self.tokens_before - self.tokens_after


class QualityTurnMetrics(BaseModel):
    """Per-turn quality counters (Tier 1 — automatic, free)."""
    session_id: str
    turn: int
    latency_ms: int
    tool_calls: int = 0
    worker_dispatches: int = 0
    gate_results: list[QualityGateResult] = []
    compaction_triggered: bool = False
    tokens_used: int = 0
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class WorkingMemoryItem(BaseModel):
    """Item surfaced by WorkingMemoryLoader for dynamic suffix."""
    source: Literal["items_db", "commitments", "events", "bridge"]
    content: str
    priority: int = Field(ge=1, le=5, default=3)
    due_date: str | None = None
    item_id: str | None = None
