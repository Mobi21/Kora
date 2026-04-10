"""Kora V2 — Agent harness: typed execution pipeline.

The :class:`AgentHarness` is the base class for all agent implementations.
It provides a standardised execution pipeline:

1. Validate input against ``input_schema``
2. Run middleware pre-hooks
3. Execute agent logic (subclass ``_execute``)
4. Run quality gates on output
5. Run middleware post-hooks
6. Return typed output
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any, Generic, TypeVar

import structlog
from pydantic import BaseModel, ValidationError

from kora_v2.agents.middleware import AgentMiddleware
from kora_v2.agents.quality_gates import QualityGate
from kora_v2.core.exceptions import KoraError
from kora_v2.core.models import QualityGateResult

log = structlog.get_logger(__name__)

TInput = TypeVar("TInput", bound=BaseModel)
TOutput = TypeVar("TOutput", bound=BaseModel)


class QualityGateFailedError(KoraError):
    """Raised when a mandatory quality gate fails."""


class InputValidationError(KoraError):
    """Raised when input fails schema validation."""


class AgentHarness(ABC, Generic[TInput, TOutput]):
    """Base class for all agent implementations.

    Subclasses must:
    - Set ``input_schema`` and ``output_schema`` class attributes
    - Implement ``_execute(input_data) -> TOutput``

    The harness handles middleware, quality gates, and schema validation.
    """

    input_schema: type[TInput]
    output_schema: type[TOutput]

    # Maximum number of structured-output repair attempts before giving
    # up and re-raising. The first attempt is NOT counted as a repair,
    # so a value of 2 means up to 3 total attempts. Subclasses can
    # override by setting a class-level attribute.
    max_repair_attempts: int = 2

    def __init__(
        self,
        middleware: list[AgentMiddleware] | None = None,
        quality_gates: list[QualityGate] | None = None,
        *,
        agent_name: str = "",
    ) -> None:
        self.middleware: list[AgentMiddleware] = middleware or []
        self.quality_gates: list[QualityGate] = quality_gates or []
        self.agent_name = agent_name or self.__class__.__name__
        # Cross-attempt repair hint. Subclasses read this in _execute()
        # to re-prompt the LLM with the previous failure reason.
        # Harness sets it between repair attempts and clears on success.
        self._schema_repair_hint: str | None = None

    # ── Public API ──────────────────────────────────────────────────

    async def execute(self, input_data: TInput) -> TOutput:
        """Run the full execution pipeline.

        Parameters
        ----------
        input_data:
            Validated input conforming to ``input_schema``.

        Returns
        -------
        TOutput
            Validated output conforming to ``output_schema``.

        Raises
        ------
        InputValidationError
            If *input_data* does not match ``input_schema``.
        QualityGateFailedError
            If a mandatory quality gate fails.
        """
        context: dict[str, Any] = {"agent_name": self.agent_name}

        # 1. Validate input
        self._validate_input(input_data)

        # 2. Pre-execute middleware
        for mw in self.middleware:
            await mw.pre_execute(input_data, context)

        # 3. Execute agent logic with bounded schema-repair retry loop.
        #
        # LLMs occasionally return prose instead of a tool call, or a
        # tool call whose arguments fail Pydantic validation. Previously
        # the first failure terminated the worker. Now we catch
        # ``ValidationError`` and ``ValueError``, set a repair hint on
        # self so the subclass can re-prompt with the error, and retry
        # up to ``max_repair_attempts`` times. Other exceptions
        # (auth, network, quality gate) still propagate unchanged.
        log.info("agent_execute_start", agent=self.agent_name)
        self._schema_repair_hint = None
        output: TOutput | None = None
        attempt = 0
        max_attempts = 1 + max(0, self.max_repair_attempts)
        while True:
            attempt += 1
            try:
                output = await self._execute(input_data)
                self._schema_repair_hint = None
                break
            except (ValidationError, ValueError) as exc:
                if attempt >= max_attempts:
                    log.error(
                        "agent_execute_repair_exhausted",
                        agent=self.agent_name,
                        attempts=attempt,
                        error=str(exc)[:200],
                    )
                    raise
                self._schema_repair_hint = str(exc)[:400]
                log.warning(
                    "agent_execute_repair_retry",
                    agent=self.agent_name,
                    attempt=attempt,
                    error=str(exc)[:120],
                )

        assert output is not None  # noqa: S101 — unreachable if loop exited via break

        # 4. Quality gates
        gate_results = await self._run_quality_gates(output, context)
        context["gate_results"] = gate_results

        # Check for failures
        for result in gate_results:
            if not result.passed:
                log.warning(
                    "quality_gate_failed",
                    agent=self.agent_name,
                    gate=result.gate_name,
                    reason=result.reason,
                )
                raise QualityGateFailedError(
                    f"Quality gate '{result.gate_name}' failed: {result.reason}",
                    details={
                        "gate_name": result.gate_name,
                        "reason": result.reason,
                        "suggested_fix": result.suggested_fix,
                    },
                )

        # 5. Post-execute middleware
        for mw in self.middleware:
            await mw.post_execute(output, context)

        log.info("agent_execute_complete", agent=self.agent_name)
        return output

    async def stream(self, input_data: TInput) -> AsyncIterator[Any]:
        """Stream execution results.

        Default implementation yields the single result from :meth:`execute`.
        Subclasses can override for true streaming behaviour.
        """
        result = await self.execute(input_data)
        yield result

    # ── Abstract ────────────────────────────────────────────────────

    @abstractmethod
    async def _execute(self, input_data: TInput) -> TOutput:
        """Subclasses implement actual execution logic."""

    # ── Internal ────────────────────────────────────────────────────

    def _validate_input(self, input_data: TInput) -> None:
        """Validate input against ``input_schema``."""
        if not isinstance(input_data, self.input_schema):
            # Try to construct from dict
            if isinstance(input_data, dict):
                try:
                    self.input_schema.model_validate(input_data)
                    return
                except ValidationError as exc:
                    raise InputValidationError(
                        f"Input validation failed: {exc.error_count()} error(s)",
                        details={"errors": str(exc)},
                    ) from exc
            raise InputValidationError(
                f"Expected {self.input_schema.__name__}, got {type(input_data).__name__}",
            )

    async def _run_quality_gates(
        self, output: TOutput, context: dict
    ) -> list[QualityGateResult]:
        """Run all quality gates and collect results."""
        results: list[QualityGateResult] = []
        for gate in self.quality_gates:
            result = await gate.check(output, context)
            results.append(result)
        return results
