"""Kora V2 — Quality gates for agent output validation.

Quality gates are post-execution checks that validate agent output
before it is returned to the supervisor. Each gate produces a
:class:`QualityGateResult` indicating pass/fail with an optional
reason and suggested fix.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

import structlog
from pydantic import BaseModel, ValidationError

from kora_v2.core.models import QualityGateResult
from kora_v2.core.settings import get_settings

log = structlog.get_logger(__name__)


# ── Base ────────────────────────────────────────────────────────────────


class QualityGate(ABC):
    """Base quality gate.

    Subclasses implement :meth:`check` to validate agent output.
    """

    @abstractmethod
    async def check(self, output: Any, context: dict) -> QualityGateResult:
        """Validate *output* and return a gate result."""


# ── Schema Validation ───────────────────────────────────────────────────


class SchemaValidationGate(QualityGate):
    """Validate output against a Pydantic schema.

    Mandatory gate. Ensures the agent's output conforms to the declared
    ``output_schema`` of the harness.
    """

    def __init__(self, schema: type[BaseModel]) -> None:
        self._schema = schema

    async def check(self, output: Any, context: dict) -> QualityGateResult:
        """Validate *output* against the Pydantic schema."""
        # Already an instance of the correct type
        if isinstance(output, self._schema):
            return QualityGateResult(
                gate_name="schema_validation",
                passed=True,
            )

        # Try to construct from dict
        if isinstance(output, dict):
            try:
                self._schema.model_validate(output)
                return QualityGateResult(
                    gate_name="schema_validation",
                    passed=True,
                )
            except ValidationError as exc:
                return QualityGateResult(
                    gate_name="schema_validation",
                    passed=False,
                    reason=f"Schema validation failed: {exc.error_count()} error(s)",
                    suggested_fix=str(exc),
                )

        return QualityGateResult(
            gate_name="schema_validation",
            passed=False,
            reason=f"Expected {self._schema.__name__}, got {type(output).__name__}",
            suggested_fix=f"Return an instance of {self._schema.__name__}",
        )


# ── Rules Check ─────────────────────────────────────────────────────────


class RulesCheckGate(QualityGate):
    """Domain-specific rules per agent type.

    Mandatory gate. Takes a list of rule callables in the constructor.
    Each rule receives ``(output, context)`` and returns a
    ``(passed: bool, reason: str | None)`` tuple.
    """

    RuleCallable = Callable[[Any, dict], tuple[bool, str | None]]

    def __init__(self, rules: list[RuleCallable]) -> None:
        self._rules = rules

    async def check(self, output: Any, context: dict) -> QualityGateResult:
        """Run all rules and return the first failure, or pass."""
        failures: list[str] = []

        for rule in self._rules:
            passed, reason = rule(output, context)
            if not passed:
                failures.append(reason or "Rule check failed")

        if failures:
            return QualityGateResult(
                gate_name="rules_check",
                passed=False,
                reason="; ".join(failures),
            )

        return QualityGateResult(
            gate_name="rules_check",
            passed=True,
        )


# ── Confidence Check ────────────────────────────────────────────────────


class ConfidenceCheckGate(QualityGate):
    """Agent self-reported confidence score.

    Mandatory gate. When the agent's output includes a ``confidence``
    field below the configured threshold, the result is marked as
    needing escalation.
    """

    def __init__(self, threshold: float | None = None) -> None:
        settings = get_settings()
        self._threshold = threshold if threshold is not None else settings.quality.confidence_threshold

    async def check(self, output: Any, context: dict) -> QualityGateResult:
        """Check the confidence value on the output."""
        confidence: float | None = None

        if isinstance(output, BaseModel) and hasattr(output, "confidence"):
            confidence = getattr(output, "confidence")
        elif isinstance(output, dict):
            confidence = output.get("confidence")

        if confidence is None:
            # No confidence field — gate passes (agent doesn't report confidence)
            return QualityGateResult(
                gate_name="confidence_check",
                passed=True,
                reason="No confidence field present",
            )

        if confidence < self._threshold:
            return QualityGateResult(
                gate_name="confidence_check",
                passed=False,
                reason=f"Confidence {confidence:.2f} < threshold {self._threshold:.2f}",
                suggested_fix="Escalate to supervisor or request additional context",
            )

        return QualityGateResult(
            gate_name="confidence_check",
            passed=True,
        )


# ── LLM Review ──────────────────────────────────────────────────────────


class LLMReviewGate(QualityGate):
    """Optional LLM-as-judge for high-stakes outputs.

    Only runs when explicitly enabled. Takes an LLM provider callable
    in the constructor that accepts a prompt string and returns a
    judgment string.
    """

    def __init__(
        self,
        llm_provider: Callable[..., Any] | None = None,
        *,
        enabled: bool = False,
    ) -> None:
        self._llm_provider = llm_provider
        self._enabled = enabled

    async def check(self, output: Any, context: dict) -> QualityGateResult:
        """Run LLM review if enabled, otherwise pass."""
        if not self._enabled or self._llm_provider is None:
            return QualityGateResult(
                gate_name="llm_review",
                passed=True,
                reason="LLM review not enabled",
            )

        try:
            prompt = (
                f"Review the following agent output for quality and correctness.\n"
                f"Output: {output}\n"
                f"Context: {context}\n"
                f"Respond with PASS or FAIL followed by a brief reason."
            )
            judgment = await self._llm_provider(prompt)
            judgment_str = str(judgment).strip()

            passed = judgment_str.upper().startswith("PASS")
            return QualityGateResult(
                gate_name="llm_review",
                passed=passed,
                reason=judgment_str,
            )
        except Exception as exc:
            log.warning("llm_review_gate_error", error=str(exc))
            # On LLM error, fail open (pass) to avoid blocking
            return QualityGateResult(
                gate_name="llm_review",
                passed=True,
                reason=f"LLM review failed with error: {exc}",
            )
