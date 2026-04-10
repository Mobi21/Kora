"""Tests for kora_v2.agents.harness — AgentHarness execution pipeline."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel

from kora_v2.agents.harness import (
    AgentHarness,
    InputValidationError,
    QualityGateFailedError,
)
from kora_v2.agents.middleware import AgentMiddleware
from kora_v2.agents.quality_gates import (
    ConfidenceCheckGate,
    QualityGate,
    SchemaValidationGate,
)
from kora_v2.core.models import QualityGateResult


# ── Test Models ─────────────────────────────────────────────────────────


class EchoInput(BaseModel):
    message: str


class EchoOutput(BaseModel):
    reply: str
    confidence: float = 1.0


# ── Concrete Harness for Testing ────────────────────────────────────────


class EchoAgent(AgentHarness[EchoInput, EchoOutput]):
    input_schema = EchoInput
    output_schema = EchoOutput

    async def _execute(self, input_data: EchoInput) -> EchoOutput:
        return EchoOutput(reply=f"Echo: {input_data.message}")


class LowConfidenceAgent(AgentHarness[EchoInput, EchoOutput]):
    input_schema = EchoInput
    output_schema = EchoOutput

    async def _execute(self, input_data: EchoInput) -> EchoOutput:
        return EchoOutput(reply="unsure", confidence=0.2)


# ── Tracking Middleware ─────────────────────────────────────────────────


class TrackingMiddleware(AgentMiddleware):
    """Records pre/post calls for assertion."""

    def __init__(self) -> None:
        self.pre_called = False
        self.post_called = False
        self.pre_order = 0
        self.post_order = 0

    async def pre_execute(self, input_data: Any, context: dict) -> None:
        self.pre_called = True
        counter = context.get("_mw_counter", 0)
        self.pre_order = counter
        context["_mw_counter"] = counter + 1

    async def post_execute(self, output_data: Any, context: dict) -> None:
        self.post_called = True
        counter = context.get("_mw_counter", 0)
        self.post_order = counter
        context["_mw_counter"] = counter + 1


# ── Tests ───────────────────────────────────────────────────────────────


class TestHarnessExecution:
    @pytest.mark.asyncio
    async def test_basic_execute(self):
        """Basic execute returns typed output."""
        agent = EchoAgent()
        result = await agent.execute(EchoInput(message="hello"))

        assert isinstance(result, EchoOutput)
        assert result.reply == "Echo: hello"

    @pytest.mark.asyncio
    async def test_input_validation_rejects_wrong_type(self):
        """Raises InputValidationError for wrong input type."""
        agent = EchoAgent()

        with pytest.raises(InputValidationError, match="Expected EchoInput"):
            await agent.execute("not a model")  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_middleware_runs_in_order(self):
        """Middleware pre/post hooks run in registration order."""
        mw1 = TrackingMiddleware()
        mw2 = TrackingMiddleware()
        agent = EchoAgent(middleware=[mw1, mw2])

        await agent.execute(EchoInput(message="test"))

        assert mw1.pre_called
        assert mw2.pre_called
        assert mw1.post_called
        assert mw2.post_called
        assert mw1.pre_order < mw2.pre_order
        assert mw1.post_order < mw2.post_order

    @pytest.mark.asyncio
    async def test_quality_gate_passes(self):
        """Execution succeeds when all quality gates pass."""
        gate = SchemaValidationGate(EchoOutput)
        agent = EchoAgent(quality_gates=[gate])

        result = await agent.execute(EchoInput(message="hi"))
        assert result.reply == "Echo: hi"

    @pytest.mark.asyncio
    async def test_quality_gate_fails(self):
        """Raises QualityGateFailedError when a gate fails."""
        confidence_gate = ConfidenceCheckGate(threshold=0.5)
        agent = LowConfidenceAgent(quality_gates=[confidence_gate])

        with pytest.raises(QualityGateFailedError, match="confidence_check"):
            await agent.execute(EchoInput(message="test"))

    @pytest.mark.asyncio
    async def test_middleware_and_gates_combined(self):
        """Full pipeline: middleware + gates run together."""
        mw = TrackingMiddleware()
        gate = SchemaValidationGate(EchoOutput)
        agent = EchoAgent(middleware=[mw], quality_gates=[gate])

        result = await agent.execute(EchoInput(message="full"))

        assert result.reply == "Echo: full"
        assert mw.pre_called
        assert mw.post_called

    @pytest.mark.asyncio
    async def test_stream_yields_result(self):
        """Default stream yields the execute result."""
        agent = EchoAgent()
        results = []

        async for item in agent.stream(EchoInput(message="stream")):
            results.append(item)

        assert len(results) == 1
        assert results[0].reply == "Echo: stream"

    @pytest.mark.asyncio
    async def test_agent_name_defaults_to_class_name(self):
        """Agent name defaults to the class name."""
        agent = EchoAgent()
        assert agent.agent_name == "EchoAgent"

    @pytest.mark.asyncio
    async def test_agent_name_custom(self):
        """Custom agent name is used."""
        agent = EchoAgent(agent_name="my_echo")
        assert agent.agent_name == "my_echo"
