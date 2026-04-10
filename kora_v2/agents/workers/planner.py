"""Kora V2 -- Planner worker agent.

Creates ADHD-friendly execution plans from high-level goals.  Plans are
time-bounded, dependency-aware, and respect energy levels.

Execution flow
--------------
1. Build a system prompt with ADHD-aware planning rules.
2. If an existing plan is provided, append a revision addendum.
3. Build user message from goal, context, and constraints.
4. Call the LLM with a ``submit_plan`` structured-output tool.
5. Parse the tool-call arguments into PlanOutput.
6. Confidence check: if confidence < 0.3, retry once.
7. After 2 LLM failures, raise PlanningFailedError.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog

from kora_v2.agents.harness import AgentHarness
from kora_v2.core.exceptions import PlanningFailedError
from kora_v2.core.models import (
    Plan,
    PlanInput,
    PlanOutput,
    PlanStep,
)
from kora_v2.tools.registry import get_schema_tool

log = structlog.get_logger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

MAX_RETRIES = 2

PLANNER_SYSTEM_PROMPT = """\
You are Kora's planner agent. Your job is to decompose goals into concrete, \
ADHD-friendly execution plans.

Planning rules:
- Each step MUST be time-bounded (5-30 minutes).
- The very first step MUST be <= 10 minutes (ideally <= 5). This is the \
"initiation micro-step" — its purpose is overcoming startup friction, not \
making progress. Good examples: "Open the editor and create an empty file", \
"Write the task name on a sticky note", "Load the dataset in a notebook cell". \
A 15-minute or longer first step DEFEATS THE PURPOSE and WILL BE REJECTED \
by the reviewer.
- Maximum 7 steps per plan.
- Assign each step a worker: executor, memory, reviewer, research, code, \
life_mgmt, or screen.
- Set estimated_minutes, depends_on (list of step IDs), energy_level \
(low/medium/high), and needs_review (true for important steps).
- If needs_review is true, populate review_criteria with 1-3 concrete checks.
- Set adhd_notes with encouragement, break reminders, or tips for maintaining \
focus.
- Set estimated_effort to "quick" (<15 min total), "moderate" (15-60 min), \
or "complex" (>60 min).
- Set confidence between 0.0 and 1.0 reflecting plan quality.

Call the submit_plan tool with the complete plan.
"""

REVISION_ADDENDUM = """
REVISE THIS PLAN: An existing plan is provided below. Improve it based on \
the feedback in the goal/context. Keep working step IDs where possible; only \
add or remove steps when necessary.

Existing plan:
{existing_plan_json}
"""


# ── Structured output tool schema ─────────────────────────────────────────────


def _build_submit_plan_tool() -> dict[str, Any]:
    """Build the Anthropic tool definition for structured plan output."""
    return get_schema_tool(
        name="submit_plan",
        description=(
            "Submit the execution plan. Fill in all fields: plan (with id, "
            "goal, steps, estimated_total_minutes, confidence, adhd_notes), "
            "the top-level steps list, estimated_effort, confidence, and "
            "adhd_notes."
        ),
        schema=PlanOutput.model_json_schema(),
    )


# ── Planner Harness ──────────────────────────────────────────────────────────


class PlannerWorkerHarness(AgentHarness[PlanInput, PlanOutput]):
    """Planner worker: decomposes goals into ADHD-friendly step plans.

    Uses the LLM with structured output forcing (submit_plan tool) to
    produce a PlanOutput that contains time-bounded, dependency-aware
    execution steps.
    """

    input_schema = PlanInput
    output_schema = PlanOutput

    def __init__(self, container: Any, middleware: list | None = None) -> None:
        super().__init__(
            middleware=middleware or [],
            quality_gates=[],
            agent_name="planner",
        )
        self._container = container

    # ── Main execute method ────────────────────────────────────────────────

    async def _execute(self, input_data: PlanInput) -> PlanOutput:
        """Create an execution plan for the given goal.

        1. Build system + user messages.
        2. Call LLM with submit_plan tool.
        3. Parse and validate output.
        4. Retry on low confidence or LLM error (up to MAX_RETRIES).
        5. Raise PlanningFailedError after exhausting retries.
        """
        system_prompt = PLANNER_SYSTEM_PROMPT

        # Revision addendum
        if input_data.existing_plan is not None:
            system_prompt += REVISION_ADDENDUM.format(
                existing_plan_json=input_data.existing_plan.model_dump_json(indent=2),
            )

        user_content = f"Goal: {input_data.goal}"
        if input_data.context:
            user_content += f"\n\nContext: {input_data.context}"
        user_content += "\n\nConstraints:"
        user_content += f"\n- Max steps: {input_data.constraints.max_steps}"
        user_content += f"\n- Max minutes: {input_data.constraints.max_minutes}"
        user_content += f"\n- Autonomy: {input_data.constraints.autonomy_level}"
        if input_data.constraints.available_tools:
            user_content += (
                f"\n- Available tools: {', '.join(input_data.constraints.available_tools)}"
            )

        messages = [{"role": "user", "content": user_content}]
        tool_defs = [_build_submit_plan_tool()]

        best_result: PlanOutput | None = None
        last_error: Exception | None = None

        for attempt in range(MAX_RETRIES):
            try:
                log.info(
                    "planner_calling_llm",
                    goal=input_data.goal[:80],
                    attempt=attempt + 1,
                )

                result = await self._container.llm.generate_with_tools(
                    messages=messages,
                    tools=tool_defs,
                    system_prompt=system_prompt,
                    thinking_enabled=False,
                )

                if not result.tool_calls:
                    raise ValueError(
                        "Planner LLM did not produce a tool call. "
                        "Structured output via submit_plan is required."
                    )

                tc = result.tool_calls[0]
                if tc.name != "submit_plan":
                    raise ValueError(
                        f"Planner LLM called unexpected tool '{tc.name}'. "
                        "Expected 'submit_plan'."
                    )

                plan_output = _parse_plan_output(tc.arguments, input_data.goal)

                # Confidence check -- retry if too low
                if plan_output.confidence < 0.3 and attempt < MAX_RETRIES - 1:
                    log.warning(
                        "planner_low_confidence_retry",
                        confidence=plan_output.confidence,
                        attempt=attempt + 1,
                    )
                    best_result = plan_output
                    # Add repair hint for next attempt
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "Your plan confidence was too low "
                                f"({plan_output.confidence:.2f}). "
                                "Please revise and submit a higher-quality plan."
                            ),
                        }
                    )
                    continue

                # Track best result
                if best_result is None or plan_output.confidence > best_result.confidence:
                    best_result = plan_output

                log.info(
                    "planner_complete",
                    steps=len(plan_output.steps),
                    effort=plan_output.estimated_effort,
                    confidence=plan_output.confidence,
                )
                return plan_output

            except (ValueError, KeyError, TypeError) as exc:
                last_error = exc
                log.warning(
                    "planner_attempt_failed",
                    attempt=attempt + 1,
                    error=str(exc),
                )
                continue

        # Exhausted retries
        if best_result is not None:
            log.warning(
                "planner_returning_best_effort",
                confidence=best_result.confidence,
            )
            return best_result

        raise PlanningFailedError(
            f"Planner failed after {MAX_RETRIES} attempts",
            details={"last_error": str(last_error) if last_error else "unknown"},
        )


# ── Output Parsing ────────────────────────────────────────────────────────────


def _parse_plan_output(args: dict[str, Any], goal: str) -> PlanOutput:
    """Parse LLM tool-call arguments into a validated PlanOutput.

    Handles both cases:
    - LLM returns nested ``plan`` dict with embedded ``steps``
    - LLM returns flat fields that we assemble

    Always ensures plan.id and step.id are present.
    """
    data = dict(args)

    # Ensure plan dict exists
    if "plan" not in data or not isinstance(data.get("plan"), dict):
        # Build plan from top-level fields
        steps_raw = data.get("steps", [])
        data["plan"] = {
            "id": data.get("id", str(uuid.uuid4())[:8]),
            "goal": goal,
            "steps": steps_raw,
            "estimated_total_minutes": data.get("estimated_total_minutes", 0),
            "confidence": data.get("confidence", 0.5),
            "adhd_notes": data.get("adhd_notes", ""),
        }

    plan_data = data["plan"]

    # Ensure plan has an id
    if not plan_data.get("id"):
        plan_data["id"] = str(uuid.uuid4())[:8]

    # Ensure plan has goal
    if not plan_data.get("goal"):
        plan_data["goal"] = goal

    # Ensure each step has an id
    steps_data = plan_data.get("steps", [])
    for i, step in enumerate(steps_data):
        if isinstance(step, dict) and not step.get("id"):
            step["id"] = f"step-{i + 1}"

    # Build validated models
    steps = [PlanStep.model_validate(s) for s in steps_data]
    plan_data["steps"] = steps

    plan = Plan.model_validate(plan_data)

    # Calculate total minutes if missing
    if plan.estimated_total_minutes == 0 and steps:
        plan.estimated_total_minutes = sum(s.estimated_minutes for s in steps)

    # Top-level steps mirror plan.steps
    data["steps"] = steps
    data["plan"] = plan

    # Defaults for top-level fields
    data.setdefault("estimated_effort", "moderate")
    data.setdefault("confidence", plan.confidence)
    data.setdefault("adhd_notes", plan.adhd_notes)

    return PlanOutput.model_validate(data)
