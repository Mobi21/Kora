"""Unit tests for the RSD output filter (Phase 5)."""

from __future__ import annotations

import pytest

from kora_v2.adhd import ADHDModule, ADHDProfile
from kora_v2.core.rsd_filter import check_output


@pytest.fixture
def rules():
    return ADHDModule(ADHDProfile()).output_rules()


async def test_clean_text_passes(rules):
    result = await check_output("Great work on this", rules)
    assert result.passed is True
    assert result.violations == []


async def test_banned_phrase_flags(rules):
    result = await check_output("You forgot to log that", rules)
    assert result.passed is False
    assert any(v["rule"] == "banned_phrases" for v in result.violations)


async def test_again_in_neutral_context_passes(rules):
    result = await check_output("Tell me that again, please", rules)
    assert result.passed is True


async def test_again_in_failure_context_flags(rules):
    result = await check_output("I failed that task again", rules)
    assert result.passed is False
    assert any(v["rule"] == "failure_context_again" for v in result.violations)


async def test_empty_text_passes(rules):
    result = await check_output("", rules)
    assert result.passed is True


async def test_empty_rules_pass_everything(rules):
    result = await check_output("You forgot everything", [])
    assert result.passed is True


async def test_violation_payload_shape(rules):
    result = await check_output("You forgot something", rules)
    v = result.violations[0]
    assert {"rule", "match", "position", "suggestion"} <= set(v.keys())
    assert v["position"] == 0
    assert v["rule"] == "banned_phrases"
