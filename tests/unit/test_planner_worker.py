"""Tests for kora_v2.agents.workers.planner -- PlannerWorkerHarness."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from kora_v2.agents.harness import InputValidationError
from kora_v2.agents.workers.planner import (
    MAX_RETRIES,
    PLANNER_SYSTEM_PROMPT,
    REVISION_ADDENDUM,
    PlannerWorkerHarness,
    _parse_plan_output,
)
from kora_v2.core.exceptions import PlanningFailedError
from kora_v2.core.models import (
    Plan,
    PlanConstraints,
    PlanInput,
    PlanOutput,
    PlanStep,
)
from kora_v2.llm.types import GenerationResult, ToolCall


# ── Helpers ───────────────────────────────────────────────────────────────


def _make_container(llm_mock: AsyncMock | None = None) -> MagicMock:
    """Create a mock container with an LLM provider."""
    container = MagicMock()
    container.llm = llm_mock or AsyncMock()
    return container


def _make_plan_tool_call_args(
    *,
    goal: str = "Test goal",
    confidence: float = 0.8,
    steps: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build valid submit_plan tool-call arguments."""
    if steps is None:
        steps = [
            {
                "id": "step-1",
                "title": "Quick setup",
                "description": "Do the initial setup",
                "depends_on": [],
                "estimated_minutes": 5,
                "worker": "executor",
                "tools_needed": ["write_file"],
                "energy_level": "low",
                "needs_review": False,
                "review_criteria": [],
            },
            {
                "id": "step-2",
                "title": "Main work",
                "description": "Do the main task",
                "depends_on": ["step-1"],
                "estimated_minutes": 20,
                "worker": "executor",
                "tools_needed": ["write_file"],
                "energy_level": "medium",
                "needs_review": True,
                "review_criteria": ["File exists", "Content correct"],
            },
        ]
    return {
        "plan": {
            "id": "plan-1",
            "goal": goal,
            "steps": steps,
            "estimated_total_minutes": sum(s["estimated_minutes"] for s in steps),
            "confidence": confidence,
            "adhd_notes": "Start small, you got this!",
        },
        "steps": steps,
        "estimated_effort": "moderate",
        "confidence": confidence,
        "adhd_notes": "Start small, you got this!",
    }


def _make_generation_result(
    tool_name: str = "submit_plan",
    tool_args: dict[str, Any] | None = None,
) -> GenerationResult:
    """Build a GenerationResult with a single tool call."""
    if tool_args is None:
        tool_args = _make_plan_tool_call_args()
    return GenerationResult(
        content="",
        tool_calls=[ToolCall(id="tc-1", name=tool_name, arguments=tool_args)],
    )


# ── Tests: Construction ──────────────────────────────────────────────────


class TestPlannerConstruction:
    def test_agent_name(self):
        container = _make_container()
        planner = PlannerWorkerHarness(container)
        assert planner.agent_name == "planner"

    def test_stores_container(self):
        container = _make_container()
        planner = PlannerWorkerHarness(container)
        assert planner._container is container

    def test_schema_types(self):
        container = _make_container()
        planner = PlannerWorkerHarness(container)
        assert planner.input_schema is PlanInput
        assert planner.output_schema is PlanOutput


# ── Tests: Input Validation ──────────────────────────────────────────────


class TestPlannerInputValidation:
    @pytest.mark.asyncio
    async def test_rejects_wrong_input_type(self):
        container = _make_container()
        planner = PlannerWorkerHarness(container)
        with pytest.raises(InputValidationError, match="Expected PlanInput"):
            await planner.execute("not a PlanInput")  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_accepts_valid_input(self):
        llm_mock = AsyncMock()
        llm_mock.generate_with_tools.return_value = _make_generation_result()
        container = _make_container(llm_mock)
        planner = PlannerWorkerHarness(container)

        result = await planner.execute(PlanInput(goal="Test goal"))
        assert isinstance(result, PlanOutput)


# ── Tests: System Prompt Construction ────────────────────────────────────


class TestPlannerPromptConstruction:
    @pytest.mark.asyncio
    async def test_basic_system_prompt(self):
        """System prompt is passed to LLM."""
        llm_mock = AsyncMock()
        llm_mock.generate_with_tools.return_value = _make_generation_result()
        container = _make_container(llm_mock)
        planner = PlannerWorkerHarness(container)

        await planner.execute(PlanInput(goal="Build a thing"))

        call_kwargs = llm_mock.generate_with_tools.call_args
        assert PLANNER_SYSTEM_PROMPT in call_kwargs.kwargs["system_prompt"]

    @pytest.mark.asyncio
    async def test_revision_addendum_when_existing_plan(self):
        """System prompt includes revision addendum when existing_plan set."""
        llm_mock = AsyncMock()
        llm_mock.generate_with_tools.return_value = _make_generation_result()
        container = _make_container(llm_mock)
        planner = PlannerWorkerHarness(container)

        existing = Plan(
            id="old-1",
            goal="Old goal",
            steps=[],
            estimated_total_minutes=10,
            confidence=0.5,
        )
        await planner.execute(PlanInput(goal="Revise plan", existing_plan=existing))

        call_kwargs = llm_mock.generate_with_tools.call_args
        system_prompt = call_kwargs.kwargs["system_prompt"]
        assert "REVISE THIS PLAN" in system_prompt
        assert "old-1" in system_prompt

    @pytest.mark.asyncio
    async def test_user_message_includes_goal_and_constraints(self):
        """User message includes goal, context, and constraints."""
        llm_mock = AsyncMock()
        llm_mock.generate_with_tools.return_value = _make_generation_result()
        container = _make_container(llm_mock)
        planner = PlannerWorkerHarness(container)

        await planner.execute(
            PlanInput(
                goal="Organize files",
                context="User has ADHD",
                constraints=PlanConstraints(
                    max_steps=5,
                    max_minutes=30,
                    available_tools=["write_file", "read_file"],
                ),
            )
        )

        call_kwargs = llm_mock.generate_with_tools.call_args
        messages = call_kwargs.kwargs["messages"]
        user_msg = messages[0]["content"]
        assert "Organize files" in user_msg
        assert "User has ADHD" in user_msg
        assert "Max steps: 5" in user_msg
        assert "Max minutes: 30" in user_msg
        assert "write_file" in user_msg


# ── Tests: Structured Output Parsing ─────────────────────────────────────


class TestPlannerOutputParsing:
    def test_parse_valid_output(self):
        """Parses complete LLM tool-call args into PlanOutput."""
        args = _make_plan_tool_call_args()
        result = _parse_plan_output(args, "Test goal")

        assert isinstance(result, PlanOutput)
        assert isinstance(result.plan, Plan)
        assert len(result.steps) == 2
        assert result.estimated_effort == "moderate"
        assert result.confidence == 0.8

    def test_parse_generates_step_ids(self):
        """Missing step IDs get auto-generated."""
        args = _make_plan_tool_call_args(
            steps=[
                {
                    "title": "Do thing",
                    "description": "A task",
                    "estimated_minutes": 10,
                    "worker": "executor",
                    "tools_needed": [],
                    "energy_level": "low",
                }
            ]
        )
        # Remove step id
        for s in args["plan"]["steps"]:
            s.pop("id", None)
        for s in args["steps"]:
            s.pop("id", None)

        result = _parse_plan_output(args, "Goal")
        assert result.steps[0].id == "step-1"

    def test_parse_generates_plan_id(self):
        """Missing plan ID gets auto-generated."""
        args = _make_plan_tool_call_args()
        args["plan"]["id"] = ""
        result = _parse_plan_output(args, "Goal")
        assert result.plan.id  # non-empty

    def test_parse_calculates_total_minutes(self):
        """Total minutes calculated from steps when zero."""
        args = _make_plan_tool_call_args()
        args["plan"]["estimated_total_minutes"] = 0
        result = _parse_plan_output(args, "Goal")
        assert result.plan.estimated_total_minutes == 25  # 5 + 20

    def test_parse_flat_args_without_plan_key(self):
        """Handles LLM output that puts fields flat (no 'plan' wrapper)."""
        args = {
            "steps": [
                {
                    "id": "s1",
                    "title": "Step one",
                    "description": "First step",
                    "estimated_minutes": 5,
                    "worker": "executor",
                    "tools_needed": [],
                    "energy_level": "low",
                }
            ],
            "estimated_effort": "quick",
            "confidence": 0.9,
            "adhd_notes": "Easy!",
        }
        result = _parse_plan_output(args, "Flat goal")
        assert result.plan.goal == "Flat goal"
        assert len(result.steps) == 1


# ── Tests: Confidence Retry Logic ────────────────────────────────────────


class TestPlannerConfidenceRetry:
    @pytest.mark.asyncio
    async def test_retries_on_low_confidence(self):
        """Planner retries when confidence < 0.3."""
        low_conf_args = _make_plan_tool_call_args(confidence=0.2)
        high_conf_args = _make_plan_tool_call_args(confidence=0.8)

        llm_mock = AsyncMock()
        llm_mock.generate_with_tools.side_effect = [
            _make_generation_result(tool_args=low_conf_args),
            _make_generation_result(tool_args=high_conf_args),
        ]
        container = _make_container(llm_mock)
        planner = PlannerWorkerHarness(container)

        result = await planner.execute(PlanInput(goal="Test"))
        assert result.confidence == 0.8
        assert llm_mock.generate_with_tools.call_count == 2

    @pytest.mark.asyncio
    async def test_returns_low_confidence_after_max_retries(self):
        """Returns low-confidence result if no better result after retries."""
        low_conf_args = _make_plan_tool_call_args(confidence=0.2)

        llm_mock = AsyncMock()
        llm_mock.generate_with_tools.return_value = _make_generation_result(
            tool_args=low_conf_args
        )
        container = _make_container(llm_mock)
        planner = PlannerWorkerHarness(container)

        result = await planner.execute(PlanInput(goal="Test"))
        # Should return the low-confidence result rather than failing
        assert result.confidence == 0.2
        assert llm_mock.generate_with_tools.call_count == MAX_RETRIES


# ── Tests: Error Handling ────────────────────────────────────────────────


class TestPlannerErrorHandling:
    @pytest.mark.asyncio
    async def test_raises_planning_failed_on_no_tool_call(self):
        """Raises PlanningFailedError when LLM never returns a tool call."""
        llm_mock = AsyncMock()
        llm_mock.generate_with_tools.return_value = GenerationResult(
            content="I'm just going to chat instead",
            tool_calls=[],
        )
        container = _make_container(llm_mock)
        planner = PlannerWorkerHarness(container)

        with pytest.raises(PlanningFailedError, match="failed after"):
            await planner.execute(PlanInput(goal="Test"))

    @pytest.mark.asyncio
    async def test_raises_planning_failed_on_wrong_tool(self):
        """Raises PlanningFailedError when LLM calls wrong tool."""
        llm_mock = AsyncMock()
        llm_mock.generate_with_tools.return_value = _make_generation_result(
            tool_name="wrong_tool",
            tool_args={"foo": "bar"},
        )
        container = _make_container(llm_mock)
        planner = PlannerWorkerHarness(container)

        with pytest.raises(PlanningFailedError, match="failed after"):
            await planner.execute(PlanInput(goal="Test"))

    @pytest.mark.asyncio
    async def test_planning_failed_error_has_details(self):
        """PlanningFailedError includes last_error in details."""
        llm_mock = AsyncMock()
        llm_mock.generate_with_tools.return_value = GenerationResult(
            content="", tool_calls=[]
        )
        container = _make_container(llm_mock)
        planner = PlannerWorkerHarness(container)

        with pytest.raises(PlanningFailedError) as exc_info:
            await planner.execute(PlanInput(goal="Test"))

        assert "last_error" in exc_info.value.details

    @pytest.mark.asyncio
    async def test_recovers_from_first_failure(self):
        """Second attempt succeeds after first fails."""
        good_args = _make_plan_tool_call_args(confidence=0.9)

        llm_mock = AsyncMock()
        llm_mock.generate_with_tools.side_effect = [
            GenerationResult(content="", tool_calls=[]),  # First: no tool call
            _make_generation_result(tool_args=good_args),  # Second: success
        ]
        container = _make_container(llm_mock)
        planner = PlannerWorkerHarness(container)

        result = await planner.execute(PlanInput(goal="Test"))
        assert result.confidence == 0.9


# ── Tests: LLM Call Configuration ────────────────────────────────────────


class TestPlannerLLMConfig:
    @pytest.mark.asyncio
    async def test_thinking_disabled(self):
        """Planner calls LLM with thinking disabled."""
        llm_mock = AsyncMock()
        llm_mock.generate_with_tools.return_value = _make_generation_result()
        container = _make_container(llm_mock)
        planner = PlannerWorkerHarness(container)

        await planner.execute(PlanInput(goal="Test"))

        call_kwargs = llm_mock.generate_with_tools.call_args.kwargs
        assert call_kwargs["thinking_enabled"] is False

    @pytest.mark.asyncio
    async def test_submit_plan_tool_provided(self):
        """Planner provides submit_plan tool to LLM."""
        llm_mock = AsyncMock()
        llm_mock.generate_with_tools.return_value = _make_generation_result()
        container = _make_container(llm_mock)
        planner = PlannerWorkerHarness(container)

        await planner.execute(PlanInput(goal="Test"))

        call_kwargs = llm_mock.generate_with_tools.call_args.kwargs
        tools = call_kwargs["tools"]
        assert len(tools) == 1
        assert tools[0]["name"] == "submit_plan"
