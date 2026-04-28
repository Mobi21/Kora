"""Focused executor tests for structured artifact validation."""

from __future__ import annotations

import pytest

from kora_v2.agents.workers.executor import ExecutorWorkerHarness
from kora_v2.core.models import ExecutionInput


class _ToolCall:
    def __init__(self, name: str, arguments: dict):
        self.name = name
        self.arguments = arguments


class _ToolResult:
    def __init__(self, tool_calls: list[_ToolCall]):
        self.tool_calls = tool_calls


class _LLM:
    def __init__(self, result: str):
        self.result = result
        self.calls = 0

    async def generate_with_tools(self, **_kwargs):
        self.calls += 1
        return _ToolResult(
            [
                _ToolCall(
                    "structured_execution_output",
                    {
                        "result": self.result,
                        "tool_calls_made": [],
                        "artifacts": [],
                        "success": True,
                        "confidence": 0.8,
                    },
                )
            ]
        )


class _Container:
    def __init__(self, llm: _LLM):
        self.llm = llm


@pytest.mark.asyncio
async def test_structured_artifact_request_rejects_one_sentence_success():
    llm = _LLM("I created the requested report.")
    worker = ExecutorWorkerHarness(_Container(llm))

    with pytest.raises(ValueError, match="too thin"):
        await worker.execute(
            ExecutionInput(
                task=(
                    "Create a markdown report artifact with sections and "
                    "bullet points about the research findings."
                ),
                context="The deliverable must be structured, not a summary.",
            )
        )

    assert llm.calls == 1 + worker.max_repair_attempts


@pytest.mark.asyncio
async def test_structured_artifact_request_accepts_substantive_sections():
    result = """# Key Findings

- First finding has enough detail to be useful and cites the observed theme.
- Second finding explains the implication and why it matters for the task.

# Connections

- This connects the research thread to the user's current planning context.
- It also names the likely next decision and the evidence still missing.

# Open Questions

- Which source should be verified next before turning this into final advice?
"""
    llm = _LLM(result)
    worker = ExecutorWorkerHarness(_Container(llm))

    output = await worker.execute(
        ExecutionInput(
            task=(
                "Create a markdown report artifact with sections and "
                "bullet points about the research findings."
            ),
            context="The deliverable must be structured, not a summary.",
        )
    )

    assert output.success is True
    assert output.result == result
    assert llm.calls == 1
