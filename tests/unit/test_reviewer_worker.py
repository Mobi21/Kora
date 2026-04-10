"""Tests for kora_v2.agents.workers.reviewer -- ReviewerWorkerHarness."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from kora_v2.agents.harness import InputValidationError
from kora_v2.agents.workers.reviewer import (
    MAX_RETRIES,
    REVIEWER_SYSTEM_PROMPT,
    ReviewerWorkerHarness,
    _parse_review_output,
)
from kora_v2.core.models import (
    ReviewFinding,
    ReviewInput,
    ReviewOutput,
)
from kora_v2.llm.types import GenerationResult, ToolCall


# ── Helpers ───────────────────────────────────────────────────────────────


def _make_container(llm_mock: AsyncMock | None = None) -> MagicMock:
    """Create a mock container with an LLM provider."""
    container = MagicMock()
    container.llm = llm_mock or AsyncMock()
    return container


def _make_review_tool_call_args(
    *,
    passed: bool = True,
    confidence: float = 0.85,
    recommendation: str = "accept",
    findings: list[dict[str, Any]] | None = None,
    revision_guidance: str | None = None,
) -> dict[str, Any]:
    """Build valid submit_review tool-call arguments."""
    if findings is None:
        findings = [
            {
                "severity": "info",
                "category": "quality",
                "description": "Code style is clean.",
                "suggested_fix": None,
            }
        ]
    return {
        "passed": passed,
        "findings": findings,
        "confidence": confidence,
        "recommendation": recommendation,
        "revision_guidance": revision_guidance,
    }


def _make_generation_result(
    tool_name: str = "submit_review",
    tool_args: dict[str, Any] | None = None,
) -> GenerationResult:
    """Build a GenerationResult with a single tool call."""
    if tool_args is None:
        tool_args = _make_review_tool_call_args()
    return GenerationResult(
        content="",
        tool_calls=[ToolCall(id="tc-1", name=tool_name, arguments=tool_args)],
    )


# ── Tests: Construction ──────────────────────────────────────────────────


class TestReviewerConstruction:
    def test_agent_name(self):
        container = _make_container()
        reviewer = ReviewerWorkerHarness(container)
        assert reviewer.agent_name == "reviewer"

    def test_stores_container(self):
        container = _make_container()
        reviewer = ReviewerWorkerHarness(container)
        assert reviewer._container is container

    def test_schema_types(self):
        container = _make_container()
        reviewer = ReviewerWorkerHarness(container)
        assert reviewer.input_schema is ReviewInput
        assert reviewer.output_schema is ReviewOutput


# ── Tests: Input Validation ──────────────────────────────────────────────


class TestReviewerInputValidation:
    @pytest.mark.asyncio
    async def test_rejects_wrong_input_type(self):
        container = _make_container()
        reviewer = ReviewerWorkerHarness(container)
        with pytest.raises(InputValidationError, match="Expected ReviewInput"):
            await reviewer.execute("not valid")  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_accepts_valid_input(self):
        llm_mock = AsyncMock()
        llm_mock.generate_with_tools.return_value = _make_generation_result()
        container = _make_container(llm_mock)
        reviewer = ReviewerWorkerHarness(container)

        result = await reviewer.execute(
            ReviewInput(work_product="Some output", criteria=["Correct"])
        )
        assert isinstance(result, ReviewOutput)


# ── Tests: System Prompt and User Message ────────────────────────────────


class TestReviewerPromptConstruction:
    @pytest.mark.asyncio
    async def test_system_prompt_sent(self):
        llm_mock = AsyncMock()
        llm_mock.generate_with_tools.return_value = _make_generation_result()
        container = _make_container(llm_mock)
        reviewer = ReviewerWorkerHarness(container)

        await reviewer.execute(
            ReviewInput(work_product="Output", criteria=["Check A"])
        )

        call_kwargs = llm_mock.generate_with_tools.call_args.kwargs
        assert call_kwargs["system_prompt"] == REVIEWER_SYSTEM_PROMPT

    @pytest.mark.asyncio
    async def test_user_message_includes_work_product_and_criteria(self):
        llm_mock = AsyncMock()
        llm_mock.generate_with_tools.return_value = _make_generation_result()
        container = _make_container(llm_mock)
        reviewer = ReviewerWorkerHarness(container)

        await reviewer.execute(
            ReviewInput(
                work_product="The file content",
                criteria=["Is correct", "Is complete"],
                original_goal="Build feature X",
                context="This is a V2 module",
            )
        )

        call_kwargs = llm_mock.generate_with_tools.call_args.kwargs
        user_msg = call_kwargs["messages"][0]["content"]
        assert "The file content" in user_msg
        assert "Is correct" in user_msg
        assert "Is complete" in user_msg
        assert "Build feature X" in user_msg
        assert "V2 module" in user_msg


# ── Tests: Output Parsing ────────────────────────────────────────────────


class TestReviewerOutputParsing:
    def test_parse_valid_output(self):
        args = _make_review_tool_call_args()
        result = _parse_review_output(args)

        assert isinstance(result, ReviewOutput)
        assert result.passed is True
        assert result.confidence == 0.85
        assert result.recommendation == "accept"
        assert len(result.findings) == 1

    def test_parse_defaults_missing_fields(self):
        """Missing fields get sensible defaults."""
        result = _parse_review_output({})
        assert result.passed is False
        assert result.recommendation == "reject"
        assert result.confidence == 0.5

    def test_parse_finding_defaults(self):
        """Finding with missing fields gets defaults."""
        args = {
            "passed": True,
            "findings": [{"description": "Something"}],
            "confidence": 0.7,
            "recommendation": "accept",
        }
        result = _parse_review_output(args)
        finding = result.findings[0]
        assert finding.severity == "info"
        assert finding.category == "quality"
        assert finding.description == "Something"


# ── Tests: Post-Validation (Rubber-Stamp Rejection) ─────────────────────


class TestReviewerPostValidation:
    @pytest.mark.asyncio
    async def test_retries_suspicious_pass(self):
        """Retries when passed=True but confidence < 0.5."""
        low_conf = _make_review_tool_call_args(passed=True, confidence=0.3)
        high_conf = _make_review_tool_call_args(passed=True, confidence=0.9)

        llm_mock = AsyncMock()
        llm_mock.generate_with_tools.side_effect = [
            _make_generation_result(tool_args=low_conf),
            _make_generation_result(tool_args=high_conf),
        ]
        container = _make_container(llm_mock)
        reviewer = ReviewerWorkerHarness(container)

        result = await reviewer.execute(
            ReviewInput(work_product="Output", criteria=["Check"])
        )
        assert result.confidence == 0.9
        assert llm_mock.generate_with_tools.call_count == 2

    @pytest.mark.asyncio
    async def test_adds_info_finding_when_pass_with_no_findings(self):
        """Adds an info finding when passed=True with empty findings list."""
        args = _make_review_tool_call_args(
            passed=True, confidence=0.8, findings=[]
        )
        llm_mock = AsyncMock()
        llm_mock.generate_with_tools.return_value = _make_generation_result(
            tool_args=args
        )
        container = _make_container(llm_mock)
        reviewer = ReviewerWorkerHarness(container)

        result = await reviewer.execute(
            ReviewInput(work_product="Output", criteria=["Check"])
        )
        assert result.passed is True
        assert len(result.findings) == 1
        assert result.findings[0].severity == "info"
        assert "no specific findings" in result.findings[0].description.lower()

    @pytest.mark.asyncio
    async def test_sets_default_revision_guidance(self):
        """Sets default revision_guidance when recommendation=revise but guidance empty."""
        args = _make_review_tool_call_args(
            passed=False,
            confidence=0.7,
            recommendation="revise",
            revision_guidance=None,
            findings=[
                {
                    "severity": "warning",
                    "category": "completeness",
                    "description": "Missing error handling",
                }
            ],
        )
        llm_mock = AsyncMock()
        llm_mock.generate_with_tools.return_value = _make_generation_result(
            tool_args=args
        )
        container = _make_container(llm_mock)
        reviewer = ReviewerWorkerHarness(container)

        result = await reviewer.execute(
            ReviewInput(work_product="Code", criteria=["Has error handling"])
        )
        assert result.recommendation == "revise"
        assert result.revision_guidance is not None
        assert "Address" in result.revision_guidance


# ── Tests: Error Handling ────────────────────────────────────────────────


class TestReviewerErrorHandling:
    @pytest.mark.asyncio
    async def test_returns_rejection_on_all_failures(self):
        """Returns conservative rejection when all attempts fail."""
        llm_mock = AsyncMock()
        llm_mock.generate_with_tools.return_value = GenerationResult(
            content="No tool call", tool_calls=[]
        )
        container = _make_container(llm_mock)
        reviewer = ReviewerWorkerHarness(container)

        result = await reviewer.execute(
            ReviewInput(work_product="Output", criteria=["Check"])
        )
        # Reviewer returns rejection instead of raising
        assert result.passed is False
        assert result.recommendation == "reject"
        assert result.confidence == 0.0
        assert len(result.findings) == 1
        assert result.findings[0].severity == "critical"

    @pytest.mark.asyncio
    async def test_returns_rejection_on_wrong_tool(self):
        """Returns rejection when LLM calls wrong tool every time."""
        llm_mock = AsyncMock()
        llm_mock.generate_with_tools.return_value = _make_generation_result(
            tool_name="wrong_tool", tool_args={"foo": "bar"}
        )
        container = _make_container(llm_mock)
        reviewer = ReviewerWorkerHarness(container)

        result = await reviewer.execute(
            ReviewInput(work_product="Output", criteria=["Check"])
        )
        assert result.passed is False
        assert result.recommendation == "reject"

    @pytest.mark.asyncio
    async def test_recovers_from_first_failure(self):
        """Second attempt succeeds after first fails."""
        good_args = _make_review_tool_call_args(confidence=0.9)

        llm_mock = AsyncMock()
        llm_mock.generate_with_tools.side_effect = [
            GenerationResult(content="", tool_calls=[]),
            _make_generation_result(tool_args=good_args),
        ]
        container = _make_container(llm_mock)
        reviewer = ReviewerWorkerHarness(container)

        result = await reviewer.execute(
            ReviewInput(work_product="Output", criteria=["Check"])
        )
        assert result.passed is True
        assert result.confidence == 0.9

    @pytest.mark.asyncio
    async def test_returns_best_result_from_suspicious_pass_retries(self):
        """Returns best (highest confidence) result when all are suspicious passes."""
        low_conf = _make_review_tool_call_args(passed=True, confidence=0.3)

        llm_mock = AsyncMock()
        llm_mock.generate_with_tools.return_value = _make_generation_result(
            tool_args=low_conf
        )
        container = _make_container(llm_mock)
        reviewer = ReviewerWorkerHarness(container)

        result = await reviewer.execute(
            ReviewInput(work_product="Output", criteria=["Check"])
        )
        # Should return the low-confidence result rather than rejecting
        assert result.passed is True
        assert result.confidence == 0.3


# ── Tests: LLM Call Configuration ────────────────────────────────────────


class TestReviewerLLMConfig:
    @pytest.mark.asyncio
    async def test_thinking_disabled(self):
        llm_mock = AsyncMock()
        llm_mock.generate_with_tools.return_value = _make_generation_result()
        container = _make_container(llm_mock)
        reviewer = ReviewerWorkerHarness(container)

        await reviewer.execute(
            ReviewInput(work_product="Output", criteria=["Check"])
        )

        call_kwargs = llm_mock.generate_with_tools.call_args.kwargs
        assert call_kwargs["thinking_enabled"] is False

    @pytest.mark.asyncio
    async def test_submit_review_tool_provided(self):
        llm_mock = AsyncMock()
        llm_mock.generate_with_tools.return_value = _make_generation_result()
        container = _make_container(llm_mock)
        reviewer = ReviewerWorkerHarness(container)

        await reviewer.execute(
            ReviewInput(work_product="Output", criteria=["Check"])
        )

        call_kwargs = llm_mock.generate_with_tools.call_args.kwargs
        tools = call_kwargs["tools"]
        assert len(tools) == 1
        assert tools[0]["name"] == "submit_review"
