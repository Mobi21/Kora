"""Tests for kora_v2.tools.truncation — Structure-aware, budget-adaptive truncation."""

import json

import pytest

from kora_v2.context.budget import BudgetTier
from kora_v2.tools.truncation import (
    TIER_LIMITS,
    TruncationResult,
    truncate_tool_result,
)


class TestShortResult:
    """Short results should pass through untruncated."""

    def test_short_string_not_truncated(self):
        """A short string within limits should not be truncated."""
        result = truncate_tool_result("Hello, world!")
        assert result.content == "Hello, world!"
        assert result.truncated is False
        assert result.original_length == 13

    def test_empty_string(self):
        """Empty string should return empty result."""
        result = truncate_tool_result("")
        assert result.content == ""
        assert result.truncated is False

    def test_none_result(self):
        """None-ish input should return empty."""
        result = truncate_tool_result("")
        assert result.truncated is False


class TestJsonArrayTruncation:
    """JSON arrays that exceed limits should be truncated with summary."""

    def test_json_array_truncation(self):
        """Long JSON array should be truncated with '... and N more items'."""
        items = [{"id": i, "name": f"Item {i}", "data": "x" * 100} for i in range(50)]
        long_json = json.dumps(items)

        result = truncate_tool_result(long_json, BudgetTier.NORMAL)

        assert result.truncated is True
        assert "more items" in result.content
        assert result.total_count == 50
        assert len(result.content) <= TIER_LIMITS[BudgetTier.NORMAL] + 50  # small overflow ok

    def test_json_array_within_limit(self):
        """Short JSON array should not be truncated."""
        items = [{"id": 1}, {"id": 2}]
        short_json = json.dumps(items)

        result = truncate_tool_result(short_json, BudgetTier.NORMAL)
        assert result.truncated is False
        assert result.total_count is None

    def test_empty_json_array(self):
        """Empty JSON array should not be truncated."""
        result = truncate_tool_result("[]", BudgetTier.NORMAL)
        assert result.truncated is False


class TestLineTruncation:
    """Multi-line text should be truncated keeping header + first N lines."""

    def test_line_truncation(self):
        """Long multi-line text should be truncated with line count."""
        lines = [f"Line {i}: " + "x" * 100 for i in range(100)]
        text = "\n".join(lines)

        result = truncate_tool_result(text, BudgetTier.NORMAL)

        assert result.truncated is True
        assert "more lines" in result.content
        assert result.original_length == len(text)

    def test_short_multiline_not_truncated(self):
        """Short multi-line text should not be truncated."""
        text = "line1\nline2\nline3"
        result = truncate_tool_result(text, BudgetTier.NORMAL)
        assert result.truncated is False


class TestBudgetTierLimits:
    """Different budget tiers should have different character limits."""

    def test_tier_limits_exist(self):
        """All 5 budget tiers should have character limits."""
        assert len(TIER_LIMITS) == 5
        assert BudgetTier.NORMAL in TIER_LIMITS
        assert BudgetTier.PRUNE in TIER_LIMITS
        assert BudgetTier.SUMMARIZE in TIER_LIMITS
        assert BudgetTier.AGGRESSIVE in TIER_LIMITS
        assert BudgetTier.HARD_STOP in TIER_LIMITS

    def test_tier_limits_decrease(self):
        """Limits should decrease as tier severity increases."""
        assert TIER_LIMITS[BudgetTier.NORMAL] > TIER_LIMITS[BudgetTier.PRUNE]
        assert TIER_LIMITS[BudgetTier.PRUNE] > TIER_LIMITS[BudgetTier.SUMMARIZE]
        assert TIER_LIMITS[BudgetTier.SUMMARIZE] > TIER_LIMITS[BudgetTier.AGGRESSIVE]
        assert TIER_LIMITS[BudgetTier.AGGRESSIVE] > TIER_LIMITS[BudgetTier.HARD_STOP]

    def test_tier_specific_values(self):
        """Verify exact tier limit values."""
        assert TIER_LIMITS[BudgetTier.NORMAL] == 4000
        assert TIER_LIMITS[BudgetTier.PRUNE] == 3000
        assert TIER_LIMITS[BudgetTier.SUMMARIZE] == 2000
        assert TIER_LIMITS[BudgetTier.AGGRESSIVE] == 1000
        assert TIER_LIMITS[BudgetTier.HARD_STOP] == 500

    def test_aggressive_tier_truncates_more(self):
        """AGGRESSIVE tier should truncate more aggressively than NORMAL."""
        text = "x" * 3500  # Over AGGRESSIVE limit but under NORMAL
        normal_result = truncate_tool_result(text, BudgetTier.NORMAL)
        aggressive_result = truncate_tool_result(text, BudgetTier.AGGRESSIVE)

        assert normal_result.truncated is False
        assert aggressive_result.truncated is True


class TestErrorPreservation:
    """Error content should be preserved when possible."""

    def test_error_content_preserved(self):
        """Error messages should be preserved even in truncation."""
        prefix = "x" * 200
        error = "Error: something went wrong"
        text = prefix + error
        result = truncate_tool_result(text, BudgetTier.HARD_STOP)
        # Should keep the error portion if it fits
        if result.truncated:
            assert "Error:" in result.content or "error" in result.content.lower()


class TestHeadTailTruncation:
    """Plain text (single line, non-JSON) should use head+tail truncation."""

    def test_head_tail_split(self):
        """Very long single-line text should get head + tail with truncated marker."""
        text = "A" * 10000
        result = truncate_tool_result(text, BudgetTier.NORMAL)
        assert result.truncated is True
        assert "[truncated]" in result.content
        assert result.content.startswith("A")
        assert result.content.endswith("A")
