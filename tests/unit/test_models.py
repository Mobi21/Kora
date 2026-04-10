"""Tests for kora_v2.core.models — Shared Pydantic data models."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from kora_v2.core.models import (
    Artifact,
    EmotionalState,
    EnergyEstimate,
    MemoryResult,
    Notification,
    Plan,
    PlanStep,
    QualityGateResult,
    WorkerResult,
)


class TestEmotionalState:
    """Test EmotionalState model validation and defaults."""

    def test_emotional_state_defaults(self):
        """EmotionalState with just required fields should use correct defaults."""
        state = EmotionalState(valence=0.5, arousal=0.3, dominance=0.7)
        assert state.valence == 0.5
        assert state.arousal == 0.3
        assert state.dominance == 0.7
        assert state.mood_label == "neutral"
        assert state.confidence == 0.5
        assert state.source == "fast"
        assert isinstance(state.assessed_at, datetime)

    def test_emotional_state_valence_too_high(self):
        """Valence > 1.0 should fail validation."""
        with pytest.raises(ValidationError):
            EmotionalState(valence=1.5, arousal=0.5, dominance=0.5)

    def test_emotional_state_valence_too_low(self):
        """Valence < -1.0 should fail validation."""
        with pytest.raises(ValidationError):
            EmotionalState(valence=-1.5, arousal=0.5, dominance=0.5)

    def test_emotional_state_arousal_out_of_range(self):
        """Arousal outside [0, 1] should fail validation."""
        with pytest.raises(ValidationError):
            EmotionalState(valence=0.0, arousal=1.5, dominance=0.5)
        with pytest.raises(ValidationError):
            EmotionalState(valence=0.0, arousal=-0.1, dominance=0.5)

    def test_emotional_state_dominance_out_of_range(self):
        """Dominance outside [0, 1] should fail validation."""
        with pytest.raises(ValidationError):
            EmotionalState(valence=0.0, arousal=0.5, dominance=1.5)

    def test_emotional_state_boundary_values(self):
        """Boundary values should be accepted."""
        state = EmotionalState(valence=-1.0, arousal=0.0, dominance=0.0)
        assert state.valence == -1.0
        assert state.arousal == 0.0
        assert state.dominance == 0.0

        state2 = EmotionalState(valence=1.0, arousal=1.0, dominance=1.0)
        assert state2.valence == 1.0

    def test_emotional_state_source_literal(self):
        """Source must be one of fast/llm/loaded."""
        state = EmotionalState(valence=0.0, arousal=0.5, dominance=0.5, source="llm")
        assert state.source == "llm"

        with pytest.raises(ValidationError):
            EmotionalState(valence=0.0, arousal=0.5, dominance=0.5, source="invalid")


class TestPlanStep:
    """Test PlanStep model creation."""

    def test_plan_step_creation(self):
        """Create PlanStep with all fields."""
        step = PlanStep(
            id="step_1",
            title="Search memories",
            description="Search all memory layers for relevant context",
            depends_on=[],
            estimated_minutes=2,
            worker="memory",
            tools_needed=["recall", "browse_memory"],
            energy_level="low",
            needs_review=True,
            review_criteria=["Found relevant context", "No hallucinated memories"],
        )
        assert step.id == "step_1"
        assert step.title == "Search memories"
        assert step.worker == "memory"
        assert step.needs_review is True
        assert len(step.review_criteria) == 2
        assert step.energy_level == "low"

    def test_plan_step_defaults(self):
        """PlanStep should have correct defaults for optional fields."""
        step = PlanStep(
            id="s1",
            title="Do thing",
            description="A step",
            estimated_minutes=5,
            worker="executor",
            tools_needed=["search_memories"],
            energy_level="medium",
        )
        assert step.depends_on == []
        assert step.needs_review is False
        assert step.review_criteria == []


class TestMemoryResult:
    """Test MemoryResult model creation and fields."""

    def test_memory_result_creation(self):
        """Create MemoryResult with all fields populated."""
        mr = MemoryResult(
            id="mem_001",
            content="User likes coffee",
            layer="user_model",
            memory_type="preference",
            domain="interests",
            score=0.95,
            source_path="_KoraMemory/User Model/interests/coffee.md",
        )
        assert mr.id == "mem_001"
        assert mr.content == "User likes coffee"
        assert mr.layer == "user_model"
        assert mr.memory_type == "preference"
        assert mr.domain == "interests"
        assert mr.score == 0.95

    def test_memory_result_optional_fields(self):
        """MemoryResult with optional fields as None."""
        mr = MemoryResult(
            id="mem_002",
            content="Had a great day",
            layer="long_term",
            score=0.7,
            source_path="/some/path",
        )
        assert mr.memory_type is None
        assert mr.domain is None


class TestOtherModels:
    """Test additional model types."""

    def test_energy_estimate(self):
        """EnergyEstimate creation with all fields."""
        ee = EnergyEstimate(
            level="high",
            focus="locked_in",
            confidence=0.8,
            source="behavioral_signals",
            signals={"typing_speed": "fast"},
        )
        assert ee.level == "high"
        assert ee.focus == "locked_in"
        assert ee.confidence == 0.8

    def test_quality_gate_result(self):
        """QualityGateResult creation."""
        qgr = QualityGateResult(
            gate_name="relevance_check",
            passed=False,
            reason="Response was off-topic",
            suggested_fix="Re-read the user's question",
        )
        assert qgr.passed is False
        assert qgr.reason == "Response was off-topic"

    def test_artifact(self):
        """Artifact model creation."""
        a = Artifact(
            type="file",
            uri="/path/to/output.txt",
            label="Results",
            size_bytes=1024,
        )
        assert a.type == "file"
        assert a.size_bytes == 1024

    def test_notification(self):
        """Notification model creation."""
        n = Notification(
            id="notif_001",
            priority="high",
            content="Task completed!",
            category="task_complete",
            delivery_channel="tray",
        )
        assert n.priority == "high"
        assert n.delivery_channel == "tray"

    def test_worker_result(self):
        """WorkerResult model creation."""
        wr = WorkerResult(
            worker_name="executor",
            success=True,
            result_json='{"output": "done"}',
            confidence=0.9,
            duration_ms=1500,
            tool_calls=3,
        )
        assert wr.success is True
        assert wr.tool_calls == 3
        assert wr.error is None
