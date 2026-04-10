"""Phase 6 confidence scoring tests."""
from __future__ import annotations

import pytest

from kora_v2.quality.confidence import (
    ConfidenceComponents,
    ConfidenceResult,
    compute_confidence,
    confidence_from_review,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _records(n_success: int, n_fail: int) -> list[dict]:
    return [{"success": True}] * n_success + [{"success": False}] * n_fail


# ---------------------------------------------------------------------------
# compute_confidence — formula correctness
# ---------------------------------------------------------------------------

class TestComputeConfidenceFormula:
    def test_all_ones(self):
        result = compute_confidence(
            llm_confidence=1.0,
            tool_call_records=_records(5, 0),
            criteria_met=4,
            criteria_total=4,
            threshold=0.6,
        )
        assert result.score == pytest.approx(1.0)

    def test_all_zeros(self):
        result = compute_confidence(
            llm_confidence=0.0,
            tool_call_records=_records(0, 3),
            criteria_met=0,
            criteria_total=4,
            threshold=0.6,
        )
        assert result.score == pytest.approx(0.0)

    def test_weights_40_30_30(self):
        # llm=0.8, tool=0.5, completeness=0.6
        # expected: 0.4*0.8 + 0.3*0.5 + 0.3*0.6 = 0.32 + 0.15 + 0.18 = 0.65
        result = compute_confidence(
            llm_confidence=0.8,
            tool_call_records=_records(1, 1),   # 0.5 success rate
            criteria_met=3,
            criteria_total=5,                   # 0.6 completeness
            threshold=0.6,
        )
        assert result.score == pytest.approx(0.65)

    def test_only_llm_component(self):
        # tool_call_records=[] → rate=1.0, criteria_total=0 → completeness=1.0
        # score = 0.4*0.5 + 0.3*1.0 + 0.3*1.0 = 0.2 + 0.3 + 0.3 = 0.8
        result = compute_confidence(
            llm_confidence=0.5,
            tool_call_records=[],
            criteria_met=0,
            criteria_total=0,
        )
        assert result.score == pytest.approx(0.8)

    def test_score_clamped_to_one(self):
        # Passing values that would exceed 1.0 if not clamped
        result = compute_confidence(
            llm_confidence=1.0,
            tool_call_records=[],
            criteria_met=10,
            criteria_total=5,   # completeness would be 2.0 without clamp
        )
        assert result.score <= 1.0

    def test_returns_confidence_result_type(self):
        result = compute_confidence(0.7, [], 0, 0)
        assert isinstance(result, ConfidenceResult)
        assert isinstance(result.components, ConfidenceComponents)


# ---------------------------------------------------------------------------
# Default rates (no tool calls / no criteria)
# ---------------------------------------------------------------------------

class TestDefaultRates:
    def test_tool_success_rate_is_one_when_no_tool_calls(self):
        result = compute_confidence(0.5, [], 0, 0)
        assert result.components.tool_success_rate == pytest.approx(1.0)

    def test_completeness_is_one_when_no_criteria(self):
        result = compute_confidence(0.5, [], 0, 0)
        assert result.components.completeness == pytest.approx(1.0)

    def test_completeness_is_one_when_criteria_total_zero(self):
        result = compute_confidence(0.5, _records(2, 0), 99, 0)
        assert result.components.completeness == pytest.approx(1.0)

    def test_tool_success_rate_partial(self):
        result = compute_confidence(0.5, _records(3, 1), 0, 0)
        assert result.components.tool_success_rate == pytest.approx(0.75)

    def test_tool_success_rate_all_fail(self):
        result = compute_confidence(0.5, _records(0, 4), 0, 0)
        assert result.components.tool_success_rate == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Labels
# ---------------------------------------------------------------------------

class TestLabels:
    def test_label_low_below_0_4(self):
        result = compute_confidence(0.0, _records(0, 5), 0, 10)
        assert result.label == "low"

    def test_label_medium_at_0_4(self):
        # score = 0.4*0.0 + 0.3*0.0 + 0.3*0.4 = 0.12, too low
        # Let's craft: llm=0.5, tool=0.5, complete=0.5
        # 0.4*0.5 + 0.3*0.5 + 0.3*0.5 = 0.2+0.15+0.15 = 0.5 → medium
        result = compute_confidence(0.5, _records(1, 1), 1, 2)
        assert result.label == "medium"

    def test_label_high_at_0_7(self):
        result = compute_confidence(0.8, _records(4, 0), 3, 4)
        # 0.4*0.8 + 0.3*1.0 + 0.3*0.75 = 0.32 + 0.30 + 0.225 = 0.845
        assert result.label == "high"

    def test_label_medium_below_0_7(self):
        # score just under 0.7
        result = compute_confidence(0.5, _records(2, 3), 2, 4)
        # 0.4*0.5 + 0.3*0.4 + 0.3*0.5 = 0.2 + 0.12 + 0.15 = 0.47 → medium
        assert result.label == "medium"


# ---------------------------------------------------------------------------
# threshold_passed
# ---------------------------------------------------------------------------

class TestThresholdPassed:
    def test_passed_when_above_threshold(self):
        result = compute_confidence(1.0, [], 0, 0, threshold=0.6)
        assert result.threshold_passed is True

    def test_failed_when_below_threshold(self):
        result = compute_confidence(0.0, _records(0, 5), 0, 5, threshold=0.6)
        assert result.threshold_passed is False

    def test_passed_at_exact_threshold(self):
        # Craft a score of exactly 0.6:
        # 0.4*llm + 0.3*1.0 + 0.3*1.0 = 0.6 → llm = 0.0
        # 0.4*0.0 + 0.6 = 0.6
        result = compute_confidence(0.0, [], 0, 0, threshold=0.6)
        # score = 0.4*0.0 + 0.3*1.0 + 0.3*1.0 = 0.6
        assert result.score == pytest.approx(0.6)
        assert result.threshold_passed is True

    def test_custom_threshold(self):
        result = compute_confidence(0.4, [], 0, 0, threshold=0.9)
        # score = 0.4*0.4 + 0.3 + 0.3 = 0.76 → fails 0.9 threshold
        assert result.threshold_passed is False


# ---------------------------------------------------------------------------
# confidence_from_review
# ---------------------------------------------------------------------------

class TestConfidenceFromReview:
    class _FakeReview:
        def __init__(self, confidence: float):
            self.confidence = confidence

    def test_pulls_confidence_from_review(self):
        review = self._FakeReview(confidence=0.9)
        result = confidence_from_review(review, [])
        assert result.components.llm_confidence == pytest.approx(0.9)

    def test_defaults_to_zero_on_missing_attribute(self):
        class _Bare:
            pass
        result = confidence_from_review(_Bare(), [])
        assert result.components.llm_confidence == pytest.approx(0.0)

    def test_passes_tool_records_through(self):
        review = self._FakeReview(confidence=0.5)
        result = confidence_from_review(review, _records(1, 1))
        assert result.components.tool_success_rate == pytest.approx(0.5)

    def test_passes_criteria_through(self):
        review = self._FakeReview(confidence=0.5)
        result = confidence_from_review(
            review, [], criteria_met=3, criteria_total=4
        )
        assert result.components.completeness == pytest.approx(0.75)

    def test_returns_confidence_result(self):
        review = self._FakeReview(confidence=0.7)
        result = confidence_from_review(review, [])
        assert isinstance(result, ConfidenceResult)
