"""Tests for Phase 4 foundation models and BudgetTier.PRUNE rename."""

import pytest

from kora_v2.context.budget import BudgetTier
from kora_v2.core.models import (
    CompactionResult,
    QualityGateResult,
    QualityTurnMetrics,
    SessionBridge,
    WorkingMemoryItem,
)


class TestSessionBridgeModel:
    """Tests for the SessionBridge model."""

    def test_session_bridge_model(self):
        """SessionBridge should accept required fields and have correct defaults."""
        bridge = SessionBridge(
            session_id="sess-abc",
            summary="We discussed the task backlog and ADHD coping strategies.",
        )
        assert bridge.session_id == "sess-abc"
        assert bridge.summary == "We discussed the task backlog and ADHD coping strategies."
        assert bridge.open_threads == []
        assert bridge.emotional_trajectory == ""
        assert bridge.active_plan_id is None
        assert bridge.continuation_checkpoint_id is None
        assert bridge.created_at is not None

    def test_session_bridge_with_all_fields(self):
        """SessionBridge should accept all optional fields."""
        bridge = SessionBridge(
            session_id="sess-xyz",
            summary="Brief overview",
            open_threads=["thread-1", "thread-2"],
            emotional_trajectory="neutral → anxious",
            active_plan_id="plan-123",
            continuation_checkpoint_id="ckpt-456",
        )
        assert bridge.open_threads == ["thread-1", "thread-2"]
        assert bridge.emotional_trajectory == "neutral → anxious"
        assert bridge.active_plan_id == "plan-123"
        assert bridge.continuation_checkpoint_id == "ckpt-456"

    def test_continuation_checkpoint_id_defaults_to_none(self):
        """continuation_checkpoint_id must default to None."""
        bridge = SessionBridge(session_id="s1", summary="hello")
        assert bridge.continuation_checkpoint_id is None


class TestCompactionResultModel:
    """Tests for the CompactionResult model."""

    def test_compaction_result_model(self):
        """CompactionResult should have correct fields and tokens_saved property."""
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"},
        ]
        result = CompactionResult(
            stage="observation_masking",
            messages=messages,
            tokens_before=5000,
            tokens_after=3000,
        )
        assert result.stage == "observation_masking"
        assert result.messages == messages
        assert result.tokens_before == 5000
        assert result.tokens_after == 3000
        assert result.tokens_saved == 2000

    def test_tokens_saved_property(self):
        """tokens_saved should compute the correct difference."""
        result = CompactionResult(
            stage="hard_stop",
            messages=[],
            tokens_before=10_000,
            tokens_after=6_500,
        )
        assert result.tokens_saved == 3_500

    def test_compaction_result_with_metadata(self):
        """CompactionResult should accept optional metadata fields."""
        result = CompactionResult(
            stage="structured_summary",
            messages=[{"role": "assistant", "content": "Summary: ..."}],
            tokens_before=8000,
            tokens_after=2000,
            messages_removed=12,
            messages_masked=3,
            summary_text="This session covered task planning.",
        )
        assert result.messages_removed == 12
        assert result.messages_masked == 3
        assert result.summary_text == "This session covered task planning."
        assert result.tokens_saved == 6000

    def test_compaction_result_valid_stages(self):
        """All four valid stage literals should be accepted."""
        stages = [
            "observation_masking",
            "structured_summary",
            "aggressive_recompress",
            "hard_stop",
        ]
        for stage in stages:
            result = CompactionResult(
                stage=stage,
                messages=[],
                tokens_before=100,
                tokens_after=50,
            )
            assert result.stage == stage


class TestQualityTurnMetricsModel:
    """Tests for the QualityTurnMetrics model."""

    def test_quality_turn_metrics_model(self):
        """QualityTurnMetrics should accept required fields and have correct defaults."""
        metrics = QualityTurnMetrics(
            session_id="sess-abc",
            turn=3,
            latency_ms=420,
        )
        assert metrics.session_id == "sess-abc"
        assert metrics.turn == 3
        assert metrics.latency_ms == 420
        assert metrics.tool_calls == 0
        assert metrics.worker_dispatches == 0
        assert metrics.gate_results == []
        assert metrics.compaction_triggered is False
        assert metrics.tokens_used == 0
        assert metrics.timestamp is not None

    def test_quality_turn_metrics_with_gate_results(self):
        """QualityTurnMetrics should accept QualityGateResult objects."""
        gate1 = QualityGateResult(gate_name="brevity", passed=True)
        gate2 = QualityGateResult(gate_name="clarity", passed=False, reason="Too verbose")

        metrics = QualityTurnMetrics(
            session_id="sess-xyz",
            turn=1,
            latency_ms=800,
            tool_calls=2,
            worker_dispatches=1,
            gate_results=[gate1, gate2],
            compaction_triggered=True,
            tokens_used=1234,
        )
        assert metrics.tool_calls == 2
        assert metrics.worker_dispatches == 1
        assert len(metrics.gate_results) == 2
        assert metrics.gate_results[0].gate_name == "brevity"
        assert metrics.compaction_triggered is True
        assert metrics.tokens_used == 1234


class TestWorkingMemoryItemModel:
    """Tests for the WorkingMemoryItem model."""

    def test_working_memory_item_model(self):
        """WorkingMemoryItem should accept required fields with correct defaults."""
        item = WorkingMemoryItem(
            source="items_db",
            content="Review the sprint backlog",
        )
        assert item.source == "items_db"
        assert item.content == "Review the sprint backlog"
        assert item.priority == 3  # default
        assert item.due_date is None
        assert item.item_id is None

    def test_working_memory_item_priority_constraint(self):
        """Priority must be between 1 and 5 inclusive."""
        # Valid priorities
        for p in [1, 2, 3, 4, 5]:
            item = WorkingMemoryItem(source="commitments", content="test", priority=p)
            assert item.priority == p

        # Invalid priority below 1
        with pytest.raises(Exception):
            WorkingMemoryItem(source="commitments", content="test", priority=0)

        # Invalid priority above 5
        with pytest.raises(Exception):
            WorkingMemoryItem(source="commitments", content="test", priority=6)

    def test_working_memory_item_valid_sources(self):
        """All four valid source literals should be accepted."""
        sources = ["items_db", "commitments", "events", "bridge"]
        for source in sources:
            item = WorkingMemoryItem(source=source, content="test")
            assert item.source == source

    def test_working_memory_item_with_all_fields(self):
        """WorkingMemoryItem should accept all optional fields."""
        item = WorkingMemoryItem(
            source="events",
            content="Doctor appointment",
            priority=1,
            due_date="2026-04-01",
            item_id="evt-789",
        )
        assert item.priority == 1
        assert item.due_date == "2026-04-01"
        assert item.item_id == "evt-789"


class TestBudgetTierPrune:
    """Verify BudgetTier.PRUNE exists and replaces STRIP_THINKING."""

    def test_budget_tier_prune_exists(self):
        """BudgetTier.PRUNE must exist."""
        assert hasattr(BudgetTier, "PRUNE")

    def test_budget_tier_prune_value(self):
        """BudgetTier.PRUNE should have value 'prune'."""
        assert BudgetTier.PRUNE.value == "prune"

    def test_strip_thinking_removed(self):
        """BudgetTier.STRIP_THINKING must no longer exist."""
        assert not hasattr(BudgetTier, "STRIP_THINKING")

    def test_budget_tier_still_has_5_values(self):
        """BudgetTier should still have exactly 5 values after rename."""
        assert len(BudgetTier) == 5
