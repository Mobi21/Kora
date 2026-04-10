"""Confidence scoring for autonomous work outputs."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

import structlog
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    pass

log = structlog.get_logger(__name__)


class ConfidenceComponents(BaseModel):
    llm_confidence: float = Field(ge=0.0, le=1.0)
    tool_success_rate: float = Field(ge=0.0, le=1.0)
    completeness: float = Field(ge=0.0, le=1.0)


class ConfidenceResult(BaseModel):
    score: float = Field(ge=0.0, le=1.0)
    components: ConfidenceComponents
    label: Literal["low", "medium", "high"]
    threshold_passed: bool


def _label(score: float) -> Literal["low", "medium", "high"]:
    if score >= 0.7:
        return "high"
    if score >= 0.4:
        return "medium"
    return "low"


def compute_confidence(
    llm_confidence: float,
    tool_call_records: list[dict[str, Any]],
    criteria_met: int,
    criteria_total: int,
    threshold: float = 0.6,
) -> ConfidenceResult:
    """Composite confidence: 40% LLM + 30% tool success + 30% completeness.

    Parameters
    ----------
    llm_confidence:
        Confidence value drawn directly from a ReviewOutput (0.0–1.0).
    tool_call_records:
        List of dicts, each expected to have a ``'success'`` bool key.
        Pass an empty list when no tool calls were made — the rate defaults
        to 1.0 (absence of failures is not a penalty).
    criteria_met:
        Number of review criteria that were satisfied.
    criteria_total:
        Total number of review criteria.  When 0, completeness defaults to
        1.0 (no criteria = nothing is incomplete).
    threshold:
        Minimum score required for ``threshold_passed`` to be True.

    Returns
    -------
    ConfidenceResult
        Composite score, labelled component breakdown, and gate decision.
    """
    # Tool success rate
    if not tool_call_records:
        tool_success_rate = 1.0
    else:
        successes = sum(
            1 for r in tool_call_records if r.get("success", False)
        )
        tool_success_rate = successes / len(tool_call_records)

    # Completeness
    if criteria_total <= 0:
        completeness = 1.0
    else:
        completeness = max(0.0, min(1.0, criteria_met / criteria_total))

    # Clamp inputs
    llm_confidence = max(0.0, min(1.0, llm_confidence))

    score = (
        0.4 * llm_confidence
        + 0.3 * tool_success_rate
        + 0.3 * completeness
    )
    # Floating-point safety clamp
    score = max(0.0, min(1.0, score))

    components = ConfidenceComponents(
        llm_confidence=llm_confidence,
        tool_success_rate=tool_success_rate,
        completeness=completeness,
    )
    result = ConfidenceResult(
        score=score,
        components=components,
        label=_label(score),
        threshold_passed=score >= threshold,
    )
    log.debug(
        "confidence_computed",
        score=round(score, 4),
        label=result.label,
        threshold_passed=result.threshold_passed,
    )
    return result


def confidence_from_review(
    review_output: Any,  # ReviewOutput instance — typed as Any to avoid circular import
    tool_call_records: list[dict[str, Any]],
    criteria_met: int = 0,
    criteria_total: int = 0,
    threshold: float = 0.6,
) -> ConfidenceResult:
    """Convenience wrapper that pulls ``llm_confidence`` from a ReviewOutput.

    Parameters
    ----------
    review_output:
        A ``kora_v2.core.models.ReviewOutput`` instance.  The ``.confidence``
        attribute is used as the LLM confidence component.
    tool_call_records:
        See :func:`compute_confidence`.
    criteria_met:
        Number of criteria satisfied.  Defaults to 0.
    criteria_total:
        Total criteria.  When 0, completeness is 1.0.
    threshold:
        Minimum passing score.
    """
    llm_confidence = getattr(review_output, "confidence", 0.0)
    return compute_confidence(
        llm_confidence=llm_confidence,
        tool_call_records=tool_call_records,
        criteria_met=criteria_met,
        criteria_total=criteria_total,
        threshold=threshold,
    )
