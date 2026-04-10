"""Tests for kora_v2.agents.quality_gates — all gate classes."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from kora_v2.agents.quality_gates import (
    ConfidenceCheckGate,
    LLMReviewGate,
    RulesCheckGate,
    SchemaValidationGate,
)


# ── Test Models ─────────────────────────────────────────────────────────


class SampleOutput(BaseModel):
    answer: str
    confidence: float


class NoConfidenceOutput(BaseModel):
    answer: str


# ── Schema Validation ───────────────────────────────────────────────────


class TestSchemaValidation:
    @pytest.mark.asyncio
    async def test_passes_valid_instance(self):
        """Passes when output is already the correct model instance."""
        gate = SchemaValidationGate(SampleOutput)
        output = SampleOutput(answer="hello", confidence=0.9)

        result = await gate.check(output, {})
        assert result.passed is True
        assert result.gate_name == "schema_validation"

    @pytest.mark.asyncio
    async def test_passes_valid_dict(self):
        """Passes when output is a dict that validates against schema."""
        gate = SchemaValidationGate(SampleOutput)

        result = await gate.check({"answer": "hello", "confidence": 0.9}, {})
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_fails_invalid_dict(self):
        """Fails when dict is missing required fields."""
        gate = SchemaValidationGate(SampleOutput)

        result = await gate.check({"answer": "hello"}, {})
        assert result.passed is False
        assert "validation failed" in result.reason.lower()

    @pytest.mark.asyncio
    async def test_fails_wrong_type(self):
        """Fails when output is neither instance nor dict."""
        gate = SchemaValidationGate(SampleOutput)

        result = await gate.check("not a model", {})
        assert result.passed is False
        assert "Expected SampleOutput" in result.reason


# ── Rules Check ─────────────────────────────────────────────────────────


class TestRulesCheck:
    @pytest.mark.asyncio
    async def test_all_rules_pass(self):
        """Passes when all rules return True."""

        def rule_ok(output: Any, ctx: dict) -> tuple[bool, str | None]:
            return True, None

        gate = RulesCheckGate([rule_ok, rule_ok])
        result = await gate.check("anything", {})
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_rule_fails(self):
        """Fails when any rule returns False."""

        def rule_ok(output: Any, ctx: dict) -> tuple[bool, str | None]:
            return True, None

        def rule_bad(output: Any, ctx: dict) -> tuple[bool, str | None]:
            return False, "output too short"

        gate = RulesCheckGate([rule_ok, rule_bad])
        result = await gate.check("x", {})
        assert result.passed is False
        assert "output too short" in result.reason

    @pytest.mark.asyncio
    async def test_multiple_failures_collected(self):
        """All failures are collected in the reason string."""

        def rule_a(output: Any, ctx: dict) -> tuple[bool, str | None]:
            return False, "error A"

        def rule_b(output: Any, ctx: dict) -> tuple[bool, str | None]:
            return False, "error B"

        gate = RulesCheckGate([rule_a, rule_b])
        result = await gate.check("x", {})
        assert result.passed is False
        assert "error A" in result.reason
        assert "error B" in result.reason


# ── Confidence Check ────────────────────────────────────────────────────


class TestConfidenceCheck:
    @pytest.mark.asyncio
    async def test_passes_above_threshold(self):
        """Passes when confidence is above threshold."""
        gate = ConfidenceCheckGate(threshold=0.6)
        output = SampleOutput(answer="yes", confidence=0.85)

        result = await gate.check(output, {})
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_fails_below_threshold(self):
        """Fails when confidence is below threshold."""
        gate = ConfidenceCheckGate(threshold=0.6)
        output = SampleOutput(answer="maybe", confidence=0.3)

        result = await gate.check(output, {})
        assert result.passed is False
        assert "0.30" in result.reason
        assert "Escalate" in result.suggested_fix

    @pytest.mark.asyncio
    async def test_passes_no_confidence_field(self):
        """Passes when output has no confidence field."""
        gate = ConfidenceCheckGate(threshold=0.6)
        output = NoConfidenceOutput(answer="hello")

        result = await gate.check(output, {})
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_works_with_dict(self):
        """Also works when output is a plain dict."""
        gate = ConfidenceCheckGate(threshold=0.5)

        result = await gate.check({"confidence": 0.2}, {})
        assert result.passed is False


# ── LLM Review ──────────────────────────────────────────────────────────


class TestLLMReview:
    @pytest.mark.asyncio
    async def test_disabled_passes(self):
        """When not enabled, always passes."""
        gate = LLMReviewGate(enabled=False)

        result = await gate.check("anything", {})
        assert result.passed is True
        assert "not enabled" in result.reason

    @pytest.mark.asyncio
    async def test_enabled_pass_judgment(self):
        """Passes when LLM responds with 'PASS'."""

        async def mock_llm(prompt: str) -> str:
            return "PASS — output looks correct"

        gate = LLMReviewGate(llm_provider=mock_llm, enabled=True)
        result = await gate.check("good output", {})
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_enabled_fail_judgment(self):
        """Fails when LLM responds with 'FAIL'."""

        async def mock_llm(prompt: str) -> str:
            return "FAIL — output contains hallucinated facts"

        gate = LLMReviewGate(llm_provider=mock_llm, enabled=True)
        result = await gate.check("bad output", {})
        assert result.passed is False

    @pytest.mark.asyncio
    async def test_llm_error_fails_open(self):
        """On LLM error, gate fails open (passes)."""

        async def broken_llm(prompt: str) -> str:
            raise RuntimeError("LLM unavailable")

        gate = LLMReviewGate(llm_provider=broken_llm, enabled=True)
        result = await gate.check("output", {})
        assert result.passed is True
        assert "error" in result.reason.lower()
