"""Quality gate execution with retry logic for autonomous work."""
from __future__ import annotations

import inspect
from typing import Any
from collections.abc import Awaitable, Callable

import structlog
from pydantic import BaseModel

from kora_v2.quality.confidence import ConfidenceResult, confidence_from_review

log = structlog.get_logger(__name__)


class GateAttempt(BaseModel):
    attempt: int
    confidence: float
    passed: bool
    findings: list[dict[str, Any]]
    suggested_fix: str | None = None


class GateResult(BaseModel):
    passed: bool
    attempts: list[GateAttempt]
    final_confidence: float
    output: Any  # final output (last attempt's result)
    partial_result: bool = False  # True if returning partial after max retries


def _extract_findings(review: Any) -> list[dict[str, Any]]:
    """Convert ReviewOutput.findings to plain dicts for GateAttempt storage."""
    findings_attr = getattr(review, "findings", [])
    result: list[dict[str, Any]] = []
    for finding in findings_attr:
        if hasattr(finding, "model_dump"):
            result.append(finding.model_dump())
        elif isinstance(finding, dict):
            result.append(finding)
        else:
            result.append({"description": str(finding)})
    return result


def _extract_fix_suggestion(review: Any) -> str | None:
    """Pull a suggested fix string from a ReviewOutput if present."""
    guidance = getattr(review, "revision_guidance", None)
    if guidance:
        return guidance
    findings = getattr(review, "findings", [])
    for f in findings:
        fix = (
            f.get("suggested_fix")
            if isinstance(f, dict)
            else getattr(f, "suggested_fix", None)
        )
        if fix:
            return fix
    return None


async def execute_with_quality_gates(
    producer: Callable[[], Awaitable[Any]],
    reviewer: Callable[[Any], Awaitable[Any]],
    threshold: float = 0.6,
    max_attempts: int = 2,
    feedback_injector: Callable[[Any, list[str]], Any] | None = None,
) -> GateResult:
    """Execute *producer*, review with *reviewer*, retry on failure.

    Parameters
    ----------
    producer:
        Async callable that produces work output.  On retries, if the callable
        accepts a ``feedback`` keyword argument **and** a ``feedback_injector``
        is provided, the improved input returned by the injector is passed as
        ``feedback``.
    reviewer:
        Async callable that accepts the producer's output and returns a
        ``ReviewOutput`` (or any object with ``.passed``, ``.confidence``,
        ``.findings``, and optional ``.revision_guidance``).
    threshold:
        Minimum composite confidence score required for a gate pass.
    max_attempts:
        Maximum number of producer/reviewer cycles before giving up.
        Must be >= 1.
    feedback_injector:
        Optional callable ``(output, findings: list[str]) -> improved_input``.
        Called on failed attempts to build a new argument that is passed as
        the ``feedback`` kwarg to *producer* on the next cycle.  When ``None``
        the producer is simply retried with no arguments.

    Returns
    -------
    GateResult
        Contains the final output, all attempt records, and whether the gate
        ultimately passed.  When all attempts are exhausted without passing,
        ``passed=False`` and ``partial_result=True``.
    """
    max_attempts = max(1, max_attempts)
    attempts: list[GateAttempt] = []
    current_output: Any = None
    current_feedback: Any = None  # injected input for the next producer call
    producer_accepts_feedback = "feedback" in inspect.signature(producer).parameters

    for attempt_num in range(1, max_attempts + 1):
        log.info(
            "quality_gate_attempt",
            attempt=attempt_num,
            max_attempts=max_attempts,
        )

        # ── Step 1: call producer ───────────────────────────────────────────
        try:
            if (
                producer_accepts_feedback
                and feedback_injector is not None
                and current_feedback is not None
            ):
                current_output = await producer(feedback=current_feedback)
            else:
                current_output = await producer()
        except Exception as exc:
            log.warning(
                "quality_gate_producer_error",
                attempt=attempt_num,
                error=str(exc),
            )
            gate_attempt = GateAttempt(
                attempt=attempt_num,
                confidence=0.0,
                passed=False,
                findings=[{"description": f"Producer error: {exc}"}],
                suggested_fix=None,
            )
            attempts.append(gate_attempt)
            # No output to feed the reviewer — skip reviewer, go to next attempt
            continue

        # ── Step 2: call reviewer ───────────────────────────────────────────
        try:
            review = await reviewer(current_output)
        except Exception as exc:
            log.warning(
                "quality_gate_reviewer_error",
                attempt=attempt_num,
                error=str(exc),
            )
            gate_attempt = GateAttempt(
                attempt=attempt_num,
                confidence=0.0,
                passed=False,
                findings=[{"description": f"Reviewer error: {exc}"}],
                suggested_fix=None,
            )
            attempts.append(gate_attempt)
            continue

        # ── Step 3: compute confidence ──────────────────────────────────────
        confidence: ConfidenceResult = confidence_from_review(
            review_output=review,
            tool_call_records=[],
            threshold=threshold,
        )

        review_passed: bool = bool(getattr(review, "passed", False))
        gate_passed = review_passed and confidence.threshold_passed
        findings = _extract_findings(review)
        suggested_fix = _extract_fix_suggestion(review)

        gate_attempt = GateAttempt(
            attempt=attempt_num,
            confidence=confidence.score,
            passed=gate_passed,
            findings=findings,
            suggested_fix=suggested_fix,
        )
        attempts.append(gate_attempt)

        log.info(
            "quality_gate_attempt_result",
            attempt=attempt_num,
            gate_passed=gate_passed,
            confidence=round(confidence.score, 4),
            review_passed=review_passed,
        )

        if gate_passed:
            return GateResult(
                passed=True,
                attempts=attempts,
                final_confidence=confidence.score,
                output=current_output,
                partial_result=False,
            )

        # ── Step 4: prepare feedback for next attempt ───────────────────────
        if feedback_injector is not None and attempt_num < max_attempts:
            finding_texts = [
                f.get("description", str(f)) if isinstance(f, dict) else str(f)
                for f in findings
            ]
            try:
                current_feedback = feedback_injector(current_output, finding_texts)
            except Exception as exc:
                log.warning(
                    "quality_gate_injector_error",
                    attempt=attempt_num,
                    error=str(exc),
                )
                current_feedback = None

    # ── All attempts exhausted ──────────────────────────────────────────────
    final_confidence = attempts[-1].confidence if attempts else 0.0
    log.warning(
        "quality_gate_exhausted",
        attempts=len(attempts),
        final_confidence=round(final_confidence, 4),
    )
    return GateResult(
        passed=False,
        attempts=attempts,
        final_confidence=final_confidence,
        output=current_output,
        partial_result=True,
    )
