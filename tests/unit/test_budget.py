"""Tests for kora_v2.context.budget — Context budget monitoring and tier management."""

import pytest

from kora_v2.context.budget import (
    BUDGET_ALLOCATION,
    TIER_THRESHOLDS,
    BudgetTier,
    ContextBudgetMonitor,
    count_message_tokens,
    count_messages_tokens,
    count_tokens,
)


class TestBudgetTier:
    """Verify BudgetTier enum has all 5 values."""

    def test_budget_tiers_count(self):
        """BudgetTier should have exactly 5 values."""
        assert len(BudgetTier) == 5

    def test_budget_tier_values(self):
        """All expected tier values should exist."""
        assert BudgetTier.NORMAL.value == "normal"
        assert BudgetTier.PRUNE.value == "prune"
        assert BudgetTier.SUMMARIZE.value == "summarize"
        assert BudgetTier.AGGRESSIVE.value == "aggressive"
        assert BudgetTier.HARD_STOP.value == "hard_stop"

    def test_tier_thresholds_order(self):
        """Tier thresholds should be in increasing order."""
        assert TIER_THRESHOLDS[BudgetTier.NORMAL] == 0
        assert TIER_THRESHOLDS[BudgetTier.PRUNE] == 100_000
        assert TIER_THRESHOLDS[BudgetTier.SUMMARIZE] == 150_000
        assert TIER_THRESHOLDS[BudgetTier.AGGRESSIVE] == 175_000
        assert TIER_THRESHOLDS[BudgetTier.HARD_STOP] == 195_000


class TestCountTokens:
    """Test token counting functions."""

    def test_empty_string(self):
        """Empty string should return 0 tokens."""
        assert count_tokens("") == 0

    def test_nonempty_string(self):
        """Non-empty string should return positive token count."""
        tokens = count_tokens("Hello, world!")
        assert tokens > 0

    def test_message_overhead(self):
        """Message token count should include 4-token overhead."""
        msg = {"role": "user", "content": "Hi"}
        tokens = count_message_tokens(msg)
        # Should be at least 4 (overhead) + tokens for "Hi"
        assert tokens >= 5

    def test_message_with_blocks(self):
        """Message with content blocks should count all blocks."""
        msg = {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Here is the answer"},
                {"type": "thinking", "thinking": "Let me think about this"},
            ],
        }
        tokens = count_message_tokens(msg)
        assert tokens > 4  # More than just overhead

    def test_count_messages_tokens(self):
        """Total count across multiple messages should sum correctly."""
        msgs = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        total = count_messages_tokens(msgs)
        individual_sum = sum(count_message_tokens(m) for m in msgs)
        assert total == individual_sum


class TestContextBudgetMonitor:
    """Test the ContextBudgetMonitor tier detection and budget tracking."""

    def test_normal_tier_for_short_conversation(self):
        """Short conversation should be in NORMAL tier."""
        monitor = ContextBudgetMonitor(context_window=200_000)
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi!"},
        ]
        tier = monitor.get_tier(messages)
        assert tier == BudgetTier.NORMAL

    def test_hard_stop_tier_for_massive_content(self):
        """Very large content should trigger HARD_STOP tier."""
        monitor = ContextBudgetMonitor(context_window=200_000)
        # Create a message with enough text to exceed 195K tokens
        # ~4 chars per token, so ~800K chars should be ~200K tokens
        big_content = "word " * 200_000
        messages = [{"role": "user", "content": big_content}]
        tier = monitor.get_tier(messages)
        assert tier == BudgetTier.HARD_STOP

    def test_remaining_budget(self):
        """remaining_budget should decrease as messages grow."""
        monitor = ContextBudgetMonitor(context_window=200_000)
        empty_remaining = monitor.remaining_budget([])
        with_msg = monitor.remaining_budget([{"role": "user", "content": "Hello world"}])
        assert with_msg < empty_remaining

    def test_remaining_budget_never_negative(self):
        """remaining_budget should never be negative."""
        monitor = ContextBudgetMonitor(context_window=100)
        big_msg = [{"role": "user", "content": "x" * 10000}]
        assert monitor.remaining_budget(big_msg) >= 0

    def test_should_refuse_generation(self):
        """should_refuse_generation should return True at HARD_STOP."""
        monitor = ContextBudgetMonitor(context_window=200_000)
        big_content = "word " * 200_000
        messages = [{"role": "user", "content": big_content}]
        assert monitor.should_refuse_generation(messages) is True

    def test_should_not_refuse_short_conversation(self):
        """should_refuse_generation should return False for normal conversation."""
        monitor = ContextBudgetMonitor(context_window=200_000)
        messages = [{"role": "user", "content": "Hello"}]
        assert monitor.should_refuse_generation(messages) is False

    def test_get_status(self):
        """get_status should return context_window and tiktoken info."""
        monitor = ContextBudgetMonitor(context_window=205_000)
        status = monitor.get_status()
        assert status["context_window"] == 205_000
        assert "has_tiktoken" in status

    def test_reset_is_noop(self):
        """reset() should be a no-op (stateless monitor)."""
        monitor = ContextBudgetMonitor()
        monitor.reset()  # Should not raise

    def test_scaled_thresholds_small_window(self):
        """Small context window should scale tier thresholds proportionally."""
        monitor = ContextBudgetMonitor(context_window=1000)
        # With a 1000 token window, thresholds should be much smaller than defaults
        thresholds = monitor._tier_thresholds()
        assert thresholds[BudgetTier.NORMAL] == 0
        # PRUNE should be scaled down from 100K
        assert thresholds[BudgetTier.PRUNE] < TIER_THRESHOLDS[BudgetTier.PRUNE]
        # HARD_STOP must be below context_window
        assert thresholds[BudgetTier.HARD_STOP] < 1000

    def test_budget_allocation_keys(self):
        """BUDGET_ALLOCATION should have expected component keys."""
        expected_keys = {
            "tools", "system_prompt", "conversation",
            "tool_context", "output_and_thinking", "safety_margin",
        }
        assert set(BUDGET_ALLOCATION.keys()) == expected_keys
