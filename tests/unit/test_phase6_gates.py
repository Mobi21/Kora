"""Phase 6 quality gate retry logic tests."""
from __future__ import annotations

import pytest

from kora_v2.quality.gates import GateAttempt, GateResult, execute_with_quality_gates


# ---------------------------------------------------------------------------
# Fake ReviewOutput helpers
# ---------------------------------------------------------------------------

class _PassReview:
    """Simulates a ReviewOutput that passes with high confidence."""
    passed = True
    confidence = 0.9
    findings = []
    revision_guidance = None


class _FailReview:
    """Simulates a ReviewOutput that fails."""
    passed = False
    confidence = 0.3
    revision_guidance = "Try harder."

    def __init__(self, findings=None):
        self.findings = findings or [
            {"description": "Not good enough", "suggested_fix": "Improve this"}
        ]


def _make_producer(outputs):
    """Return a producer that yields items from *outputs* in order."""
    call_count = [0]

    async def producer(**kwargs):
        idx = min(call_count[0], len(outputs) - 1)
        call_count[0] += 1
        out = outputs[idx]
        if isinstance(out, Exception):
            raise out
        return out

    return producer


def _make_reviewer(reviews):
    """Return a reviewer that returns items from *reviews* in order."""
    call_count = [0]

    async def reviewer(output):
        idx = min(call_count[0], len(reviews) - 1)
        call_count[0] += 1
        rev = reviews[idx]
        if isinstance(rev, Exception):
            raise rev
        return rev

    return reviewer


# ---------------------------------------------------------------------------
# Basic pass/fail scenarios
# ---------------------------------------------------------------------------

class TestExecuteWithQualityGates:
    @pytest.mark.asyncio
    async def test_first_attempt_passes(self):
        producer = _make_producer(["output_v1"])
        reviewer = _make_reviewer([_PassReview()])
        result = await execute_with_quality_gates(producer, reviewer, threshold=0.6)
        assert result.passed is True
        assert len(result.attempts) == 1
        assert result.output == "output_v1"
        assert result.partial_result is False

    @pytest.mark.asyncio
    async def test_first_fails_second_passes(self):
        producer = _make_producer(["output_v1", "output_v2"])
        reviewer = _make_reviewer([_FailReview(), _PassReview()])
        result = await execute_with_quality_gates(
            producer, reviewer, threshold=0.6, max_attempts=2
        )
        assert result.passed is True
        assert len(result.attempts) == 2
        assert result.output == "output_v2"

    @pytest.mark.asyncio
    async def test_both_fail_returns_partial(self):
        producer = _make_producer(["v1", "v2"])
        reviewer = _make_reviewer([_FailReview(), _FailReview()])
        result = await execute_with_quality_gates(
            producer, reviewer, threshold=0.6, max_attempts=2
        )
        assert result.passed is False
        assert result.partial_result is True
        assert len(result.attempts) == 2
        assert result.output == "v2"

    @pytest.mark.asyncio
    async def test_max_attempts_one_no_pass(self):
        producer = _make_producer(["only"])
        reviewer = _make_reviewer([_FailReview()])
        result = await execute_with_quality_gates(
            producer, reviewer, threshold=0.6, max_attempts=1
        )
        assert result.passed is False
        assert len(result.attempts) == 1
        assert result.partial_result is True

    @pytest.mark.asyncio
    async def test_max_attempts_one_pass(self):
        producer = _make_producer(["only"])
        reviewer = _make_reviewer([_PassReview()])
        result = await execute_with_quality_gates(
            producer, reviewer, threshold=0.6, max_attempts=1
        )
        assert result.passed is True
        assert len(result.attempts) == 1


# ---------------------------------------------------------------------------
# Exception handling
# ---------------------------------------------------------------------------

class TestExceptionHandling:
    @pytest.mark.asyncio
    async def test_producer_exception_caught_retry_succeeds(self):
        producer = _make_producer([RuntimeError("boom"), "output_ok"])
        reviewer = _make_reviewer([_PassReview()])
        result = await execute_with_quality_gates(
            producer, reviewer, threshold=0.6, max_attempts=2
        )
        # First attempt: producer error → recorded as failed attempt (no reviewer call)
        # Second attempt: succeeds
        assert result.passed is True
        assert len(result.attempts) == 2
        assert result.attempts[0].passed is False
        assert result.attempts[0].confidence == 0.0

    @pytest.mark.asyncio
    async def test_producer_exception_all_attempts(self):
        producer = _make_producer([RuntimeError("boom"), RuntimeError("boom2")])
        reviewer = _make_reviewer([_PassReview()])
        result = await execute_with_quality_gates(
            producer, reviewer, threshold=0.6, max_attempts=2
        )
        assert result.passed is False
        assert result.partial_result is True
        for attempt in result.attempts:
            assert attempt.passed is False
            assert attempt.confidence == 0.0

    @pytest.mark.asyncio
    async def test_reviewer_exception_caught_retry_succeeds(self):
        producer = _make_producer(["v1", "v2"])
        reviewer = _make_reviewer([RuntimeError("review error"), _PassReview()])
        result = await execute_with_quality_gates(
            producer, reviewer, threshold=0.6, max_attempts=2
        )
        assert result.passed is True
        assert len(result.attempts) == 2
        assert result.attempts[0].confidence == 0.0

    @pytest.mark.asyncio
    async def test_reviewer_exception_all_attempts(self):
        producer = _make_producer(["v1", "v2"])
        reviewer = _make_reviewer(
            [RuntimeError("err1"), RuntimeError("err2")]
        )
        result = await execute_with_quality_gates(
            producer, reviewer, threshold=0.6, max_attempts=2
        )
        assert result.passed is False
        assert result.partial_result is True


# ---------------------------------------------------------------------------
# Feedback injector
# ---------------------------------------------------------------------------

class TestFeedbackInjector:
    @pytest.mark.asyncio
    async def test_feedback_injector_called_on_retry(self):
        """feedback_injector must be called exactly once (on the failed attempt)."""
        injector_calls: list = []

        def injector(output, findings):
            injector_calls.append((output, findings))
            return "improved"

        producer = _make_producer(["v1", "v2"])
        reviewer = _make_reviewer([_FailReview(), _PassReview()])
        result = await execute_with_quality_gates(
            producer, reviewer, threshold=0.6, max_attempts=2,
            feedback_injector=injector,
        )
        assert result.passed is True
        assert len(injector_calls) == 1
        # First argument is the failed output
        assert injector_calls[0][0] == "v1"
        # Findings must be a list of strings
        assert isinstance(injector_calls[0][1], list)

    @pytest.mark.asyncio
    async def test_feedback_not_called_when_none(self):
        """Without feedback_injector, retries call producer with no args."""
        call_count = [0]

        async def producer():
            call_count[0] += 1
            return f"v{call_count[0]}"

        reviewer = _make_reviewer([_FailReview(), _PassReview()])
        result = await execute_with_quality_gates(
            producer, reviewer, threshold=0.6, max_attempts=2,
            feedback_injector=None,
        )
        assert result.passed is True
        assert call_count[0] == 2

    @pytest.mark.asyncio
    async def test_feedback_passed_to_producer_via_kwarg(self):
        """Producer receives the injected feedback as the ``feedback`` kwarg."""
        received_feedback: list = []

        async def producer(feedback=None):
            received_feedback.append(feedback)
            return "output"

        def injector(output, findings):
            return "injected_data"

        reviewer = _make_reviewer([_FailReview(), _PassReview()])
        await execute_with_quality_gates(
            producer, reviewer, threshold=0.6, max_attempts=2,
            feedback_injector=injector,
        )
        # First call: no feedback (None)
        assert received_feedback[0] is None
        # Second call: receives injected data
        assert received_feedback[1] == "injected_data"


# ---------------------------------------------------------------------------
# GateResult model
# ---------------------------------------------------------------------------

class TestGateResultModel:
    @pytest.mark.asyncio
    async def test_result_contains_all_attempts(self):
        producer = _make_producer(["v1", "v2"])
        reviewer = _make_reviewer([_FailReview(), _FailReview()])
        result = await execute_with_quality_gates(
            producer, reviewer, threshold=0.6, max_attempts=2
        )
        assert isinstance(result, GateResult)
        assert all(isinstance(a, GateAttempt) for a in result.attempts)
        assert result.attempts[0].attempt == 1
        assert result.attempts[1].attempt == 2

    @pytest.mark.asyncio
    async def test_final_confidence_matches_last_attempt(self):
        producer = _make_producer(["v1", "v2"])
        reviewer = _make_reviewer([_FailReview(), _FailReview()])
        result = await execute_with_quality_gates(
            producer, reviewer, threshold=0.6, max_attempts=2
        )
        assert result.final_confidence == result.attempts[-1].confidence

    @pytest.mark.asyncio
    async def test_findings_stored_in_attempt(self):
        producer = _make_producer(["v1"])
        reviewer = _make_reviewer([_PassReview()])
        result = await execute_with_quality_gates(
            producer, reviewer, threshold=0.6, max_attempts=1
        )
        # PassReview has empty findings
        assert result.attempts[0].findings == []
