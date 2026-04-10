"""Kora V2 -- Reviewer worker agent.

Evaluates work products against specified criteria and produces structured
review findings with severity, category, and suggested fixes.

Execution flow
--------------
1. Build system prompt for review with criteria list.
2. Call LLM with ``submit_review`` structured-output tool.
3. Post-validation:
   - If passed=True and confidence < 0.5: retry once.
   - If passed=True and zero findings: add info finding.
   - If recommendation="revise" and no revision_guidance: set default.
4. After 2 retries: return best result (highest confidence).
"""

from __future__ import annotations

from typing import Any

import structlog

from kora_v2.agents.harness import AgentHarness
from kora_v2.core.models import (
    ReviewFinding,
    ReviewInput,
    ReviewOutput,
)
from kora_v2.tools.registry import get_schema_tool

log = structlog.get_logger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

MAX_RETRIES = 2

REVIEWER_SYSTEM_PROMPT = """\
You are Kora's reviewer agent. Your job is to evaluate a work product against \
specified criteria and produce a structured review.

Review rules:
- Evaluate the work product against EACH criterion listed.
- For each issue found, create a finding with:
  - severity: "critical" (blocks acceptance), "warning" (should fix), \
or "info" (nice to have)
  - category: "correctness", "completeness", "security", "quality", \
or "adhd_friendliness"
  - description: clear explanation of the issue
  - suggested_fix: concrete suggestion to resolve it (optional for info)
- Set passed=true ONLY if there are no critical findings.
- Set confidence between 0.0 and 1.0 reflecting review thoroughness.
- recommendation: "accept" (no issues or only info), "revise" (warnings to \
address), "reject" (critical issues found).
- If recommendation is "revise", provide revision_guidance with specific \
instructions.

Call the submit_review tool with the complete review.
"""


# ── Structured output tool schema ─────────────────────────────────────────────


def _build_submit_review_tool() -> dict[str, Any]:
    """Build the Anthropic tool definition for structured review output."""
    return get_schema_tool(
        name="submit_review",
        description=(
            "Submit the review result. Fill in: passed (bool), findings "
            "(list of {severity, category, description, suggested_fix}), "
            "confidence (0-1), recommendation (accept/revise/reject), and "
            "revision_guidance (string, required if recommendation is revise)."
        ),
        schema=ReviewOutput.model_json_schema(),
    )


# ── Reviewer Harness ─────────────────────────────────────────────────────────


class ReviewerWorkerHarness(AgentHarness[ReviewInput, ReviewOutput]):
    """Reviewer worker: evaluates work products against criteria.

    Uses the LLM with structured output forcing (submit_review tool) to
    produce a ReviewOutput with findings, recommendation, and guidance.
    """

    input_schema = ReviewInput
    output_schema = ReviewOutput

    def __init__(self, container: Any, middleware: list | None = None) -> None:
        super().__init__(
            middleware=middleware or [],
            quality_gates=[],
            agent_name="reviewer",
        )
        self._container = container

    # ── Main execute method ────────────────────────────────────────────────

    async def _execute(self, input_data: ReviewInput) -> ReviewOutput:
        """Review a work product against criteria.

        1. Build system + user messages.
        2. Call LLM with submit_review tool.
        3. Post-validate the result.
        4. Retry on suspicious passes or LLM errors (up to MAX_RETRIES).
        5. Return best result after exhausting retries.
        """
        user_content = f"Work product to review:\n{input_data.work_product}"
        if input_data.criteria:
            user_content += "\n\nReview criteria:"
            for i, criterion in enumerate(input_data.criteria, 1):
                user_content += f"\n{i}. {criterion}"
        if input_data.original_goal:
            user_content += f"\n\nOriginal goal: {input_data.original_goal}"
        if input_data.context:
            user_content += f"\n\nContext: {input_data.context}"

        messages = [{"role": "user", "content": user_content}]
        tool_defs = [_build_submit_review_tool()]

        best_result: ReviewOutput | None = None
        last_error: Exception | None = None

        for attempt in range(MAX_RETRIES):
            try:
                log.info(
                    "reviewer_calling_llm",
                    criteria_count=len(input_data.criteria),
                    attempt=attempt + 1,
                )

                result = await self._container.llm.generate_with_tools(
                    messages=messages,
                    tools=tool_defs,
                    system_prompt=REVIEWER_SYSTEM_PROMPT,
                    thinking_enabled=False,
                )

                if not result.tool_calls:
                    raise ValueError(
                        "Reviewer LLM did not produce a tool call. "
                        "Structured output via submit_review is required."
                    )

                tc = result.tool_calls[0]
                if tc.name != "submit_review":
                    raise ValueError(
                        f"Reviewer LLM called unexpected tool '{tc.name}'. "
                        "Expected 'submit_review'."
                    )

                review_output = _parse_review_output(tc.arguments)

                # Post-validation: rubber-stamp rejection
                if (
                    review_output.passed
                    and review_output.confidence < 0.5
                    and attempt < MAX_RETRIES - 1
                ):
                    log.warning(
                        "reviewer_suspicious_pass_retry",
                        confidence=review_output.confidence,
                        attempt=attempt + 1,
                    )
                    # Track best and retry
                    if best_result is None or review_output.confidence > best_result.confidence:
                        best_result = review_output
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "Your review passed with low confidence "
                                f"({review_output.confidence:.2f}). "
                                "Please re-examine the work product more carefully "
                                "and submit a more thorough review."
                            ),
                        }
                    )
                    continue

                # Post-validation: pass with zero findings gets info finding
                if review_output.passed and not review_output.findings:
                    review_output.findings.append(
                        ReviewFinding(
                            severity="info",
                            category="completeness",
                            description="Review passed with no specific findings noted.",
                            suggested_fix=None,
                        )
                    )

                # Post-validation: revise without guidance gets default
                if (
                    review_output.recommendation == "revise"
                    and not review_output.revision_guidance
                ):
                    review_output.revision_guidance = (
                        "Address the warnings listed in the findings above "
                        "and resubmit for review."
                    )

                # Track best result
                if best_result is None or review_output.confidence > best_result.confidence:
                    best_result = review_output

                log.info(
                    "reviewer_complete",
                    passed=review_output.passed,
                    findings=len(review_output.findings),
                    recommendation=review_output.recommendation,
                    confidence=review_output.confidence,
                )
                return review_output

            except (ValueError, KeyError, TypeError) as exc:
                last_error = exc
                log.warning(
                    "reviewer_attempt_failed",
                    attempt=attempt + 1,
                    error=str(exc),
                )
                continue

        # Exhausted retries -- return best result if we have one
        if best_result is not None:
            log.warning(
                "reviewer_returning_best_effort",
                confidence=best_result.confidence,
                passed=best_result.passed,
            )
            return best_result

        # No result at all -- return a conservative rejection
        log.error(
            "reviewer_all_attempts_failed",
            last_error=str(last_error) if last_error else "unknown",
        )
        return ReviewOutput(
            passed=False,
            findings=[
                ReviewFinding(
                    severity="critical",
                    category="quality",
                    description=(
                        "Review could not be completed after "
                        f"{MAX_RETRIES} attempts. "
                        f"Last error: {last_error}"
                    ),
                    suggested_fix="Retry the review or inspect the work product manually.",
                )
            ],
            confidence=0.0,
            recommendation="reject",
            revision_guidance="Review process failed. Manual inspection required.",
        )


# ── Output Parsing ────────────────────────────────────────────────────────────


def _parse_review_output(args: dict[str, Any]) -> ReviewOutput:
    """Parse LLM tool-call arguments into a validated ReviewOutput.

    Handles LLM quirks like missing fields and ensures type safety.
    """
    data = dict(args)

    # Defaults for missing fields
    data.setdefault("passed", False)
    data.setdefault("findings", [])
    data.setdefault("confidence", 0.5)
    data.setdefault("recommendation", "reject" if not data["passed"] else "accept")
    data.setdefault("revision_guidance", None)

    # Parse findings into models
    raw_findings = data.get("findings", [])
    parsed_findings: list[ReviewFinding] = []
    for f in raw_findings:
        if isinstance(f, dict):
            f.setdefault("severity", "info")
            f.setdefault("category", "quality")
            f.setdefault("description", "")
            parsed_findings.append(ReviewFinding.model_validate(f))
        elif isinstance(f, ReviewFinding):
            parsed_findings.append(f)
    data["findings"] = parsed_findings

    return ReviewOutput.model_validate(data)
