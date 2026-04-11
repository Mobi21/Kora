"""Tests for Phase 4 emotion system — two-tier PAD assessors.

Covers:
- FastEmotionAssessor: rule-based PAD from text signals (8 tests)
- LLMEmotionAssessor: LLM-based PAD assessment (3 tests)
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from kora_v2.core.models import EmotionalState
from kora_v2.emotion.fast_assessor import FastEmotionAssessor, _pad_to_mood
from kora_v2.emotion.llm_assessor import LLMEmotionAssessor, should_trigger_llm_assessment

# ── Helpers ─────────────────────────────────────────────────────────────


def _neutral_state() -> EmotionalState:
    return EmotionalState(valence=0.0, arousal=0.5, dominance=0.5, confidence=0.5)


def _positive_state() -> EmotionalState:
    return EmotionalState(valence=0.7, arousal=0.6, dominance=0.6, confidence=0.8)


def _negative_state() -> EmotionalState:
    return EmotionalState(valence=-0.7, arousal=0.6, dominance=0.3, confidence=0.8)


# ── FastEmotionAssessor ──────────────────────────────────────────────────


class TestFastEmotionAssessor:
    """Rule-based PAD assessor tests."""

    def setup_method(self):
        self.assessor = FastEmotionAssessor()

    def test_neutral_message(self):
        """'What time is it?' yields valence near 0."""
        result = self.assessor.assess("What time is it?", [], None)
        assert isinstance(result, EmotionalState)
        assert -0.3 < result.valence < 0.3

    def test_positive_message(self):
        """'I'm so happy today!' yields valence > 0.2."""
        result = self.assessor.assess("I'm so happy today! Everything is wonderful!", [], None)
        assert result.valence > 0.2

    def test_negative_message(self):
        """'I'm frustrated and sad' yields valence < -0.2."""
        result = self.assessor.assess("I'm really frustrated and sad about this.", [], None)
        assert result.valence < -0.2

    def test_high_arousal_indicators(self):
        """ALL CAPS text with !!! triggers high arousal."""
        result = self.assessor.assess("I AM SO EXCITED ABOUT THIS!!!", [], None)
        assert result.arousal > 0.5

    def test_low_dominance_helplessness(self):
        """Helplessness language drives dominance below 0.5."""
        result = self.assessor.assess(
            "I can't handle this anymore, I feel stuck and I'm lost.", [], None
        )
        assert result.dominance < 0.5

    def test_confidence_output(self):
        """Confidence is always in [0, 1]."""
        for msg in [
            "What time is it?",
            "I'm SO HAPPY!!!",
            "I feel terrible and hopeless.",
            "",
            "ok",
        ]:
            result = self.assessor.assess(msg, [], None)
            assert 0.0 <= result.confidence <= 1.0, f"confidence out of range for: {msg!r}"

    def test_continuity_from_current_state(self):
        """A negative prior state biases an ambiguous message toward negative."""
        negative_prior = EmotionalState(
            valence=-0.8, arousal=0.4, dominance=0.3, confidence=0.9
        )
        result = self.assessor.assess("I don't know.", [], negative_prior)
        # Momentum (0.2 weight) should pull valence negative from an ambiguous message
        assert result.valence < 0.0

    def test_pad_to_mood_mapping(self):
        """_pad_to_mood returns the expected label for key PAD combinations."""
        # (+V, +A, +D) → excited / elated
        label = _pad_to_mood(0.6, 0.7, 0.7)
        assert label in ("excited", "elated")

        # (+V, -A, +D) → calm / content
        label = _pad_to_mood(0.5, 0.2, 0.7)
        assert label in ("calm", "content")

        # (-V, +A, -D) → anxious / distressed
        label = _pad_to_mood(-0.5, 0.7, 0.2)
        assert label in ("anxious", "distressed")

        # (-V, -A, -D) → sad / helpless
        label = _pad_to_mood(-0.5, 0.2, 0.2)
        assert label in ("sad", "helpless")

        # (~0V, ~0A) → neutral / relaxed
        label = _pad_to_mood(0.05, 0.1, 0.5)
        assert label in ("neutral", "relaxed")

    # -- Expanded lexicon coverage (Phase 4 Gap 3) ---

    def test_expanded_positive_words_yield_positive_valence(self):
        """Expanded positive words (excited, helpful, ready, etc.) produce valence > 0."""
        for word in ["excited", "helpful", "ready", "perfect", "amazing",
                      "wonderful", "fantastic", "brilliant", "excellent",
                      "awesome", "glad", "pleased", "thrilled", "delighted",
                      "grateful", "thankful", "relieved", "optimistic",
                      "hopeful", "confident", "proud", "inspired", "motivated",
                      "energized", "refreshed"]:
            result = self.assessor.assess(f"I'm feeling {word}!", [], None)
            assert result.valence > 0, f"{word!r} should yield positive valence, got {result.valence}"

    def test_expanded_negative_words_yield_negative_valence(self):
        """Expanded negative words (overwhelmed, stressed, etc.) produce valence < 0."""
        for word in ["overwhelmed", "stressed", "anxious", "worried",
                      "confused", "stuck", "struggling", "exhausted",
                      "drained", "burned", "irritated", "annoyed",
                      "disappointed", "discouraged", "panicked",
                      "miserable"]:
            result = self.assessor.assess(f"I feel {word} today.", [], None)
            assert result.valence < 0, f"{word!r} should yield negative valence, got {result.valence}"

    def test_single_sentiment_word_not_diluted(self):
        """A single sentiment word in a longer message still produces a meaningful signal."""
        result = self.assessor.assess(
            "I went to the store and bought some things and everything was wonderful overall.",
            [], None,
        )
        # After the scoring fix, 'wonderful' should push valence positive
        assert result.valence > 0.1


# ── LLMEmotionAssessor ───────────────────────────────────────────────────


class TestLLMEmotionAssessor:
    """LLM-tier emotion assessor tests."""

    def _make_mock_llm(self, response_text: str) -> MagicMock:
        llm = MagicMock()
        llm.generate = AsyncMock(return_value=response_text)
        return llm

    @pytest.mark.asyncio
    async def test_assess_returns_emotional_state(self):
        """LLM returns valid JSON → an EmotionalState with source='llm' is returned."""
        llm_response = (
            '{"valence": 0.5, "arousal": 0.6, "dominance": 0.7, '
            '"mood_label": "excited", "reasoning": "The user expressed joy."}'
        )
        llm = self._make_mock_llm(llm_response)
        assessor = LLMEmotionAssessor(llm)
        current = _neutral_state()

        result = await assessor.assess(["I'm feeling really great today!"], current)

        assert isinstance(result, EmotionalState)
        assert result.source == "llm"
        assert result.valence == pytest.approx(0.5, abs=0.01)
        assert result.arousal == pytest.approx(0.6, abs=0.01)
        assert result.dominance == pytest.approx(0.7, abs=0.01)
        assert result.mood_label == "excited"

    @pytest.mark.asyncio
    async def test_fallback_on_parse_error(self):
        """Garbage LLM response → fall back to current_state with halved confidence."""
        llm = self._make_mock_llm("this is not JSON at all, sorry!!!")
        assessor = LLMEmotionAssessor(llm)
        current = EmotionalState(
            valence=0.3, arousal=0.5, dominance=0.6, confidence=0.8
        )

        result = await assessor.assess(["something happened"], current)

        assert isinstance(result, EmotionalState)
        # Falls back to current_state values
        assert result.valence == pytest.approx(current.valence, abs=0.01)
        assert result.arousal == pytest.approx(current.arousal, abs=0.01)
        # Confidence is halved
        assert result.confidence == pytest.approx(current.confidence / 2, abs=0.01)
        # Still marks source as llm (attempted)
        assert result.source == "llm"

    @pytest.mark.asyncio
    async def test_timeout_triggers_fallback(self):
        """LLM call exceeding timeout falls back instead of blocking."""
        async def _slow_generate(**kwargs):
            await asyncio.sleep(10)  # Simulate cold-start delay
            return '{"valence": 0.5, "arousal": 0.6, "dominance": 0.7, "mood_label": "excited", "reasoning": "late"}'

        llm = MagicMock()
        llm.generate = _slow_generate
        assessor = LLMEmotionAssessor(llm, timeout=0.1)  # 100ms timeout
        current = EmotionalState(
            valence=0.3, arousal=0.5, dominance=0.6, confidence=0.8
        )

        result = await assessor.assess(["hello"], current)

        # Should fall back: same PAD values, halved confidence
        assert result.valence == pytest.approx(current.valence, abs=0.01)
        assert result.confidence == pytest.approx(current.confidence / 2, abs=0.01)
        assert result.source == "llm"

    @pytest.mark.asyncio
    async def test_cache_hit_skips_llm_call(self):
        """Second call with identical message window reuses cached result."""
        llm_response = (
            '{"valence": 0.2, "arousal": 0.4, "dominance": 0.5, "mood_label": "calm"}'
        )
        llm = self._make_mock_llm(llm_response)
        assessor = LLMEmotionAssessor(llm)
        current = _neutral_state()

        result_1 = await assessor.assess(["hello world"], current)
        result_2 = await assessor.assess(["hello world"], current)

        # Same result, but only one actual LLM call
        assert result_1.valence == result_2.valence
        assert result_1.mood_label == result_2.mood_label
        assert llm.generate.await_count == 1

    @pytest.mark.asyncio
    async def test_cache_miss_on_different_window(self):
        """Different message windows get distinct LLM calls."""
        llm_response = (
            '{"valence": 0.1, "arousal": 0.3, "dominance": 0.5, "mood_label": "neutral"}'
        )
        llm = self._make_mock_llm(llm_response)
        assessor = LLMEmotionAssessor(llm)
        current = _neutral_state()

        await assessor.assess(["message one"], current)
        await assessor.assess(["message two"], current)

        assert llm.generate.await_count == 2

    @pytest.mark.asyncio
    async def test_schema_drift_batched_shape_handled(self):
        """LLM returning {'messages': [{...}]} batch shape is coerced to first entry."""
        batched_response = json.dumps({
            "messages": [
                {
                    "valence": 0.3,
                    "arousal": 0.4,
                    "dominance": 0.6,
                    "mood_label": "content",
                }
            ]
        })
        llm = self._make_mock_llm(batched_response)
        assessor = LLMEmotionAssessor(llm)
        current = _neutral_state()

        result = await assessor.assess(["some message"], current)

        # Drift is coerced in _parse_and_validate without repair retry,
        # so only one LLM call should have happened
        assert result.source == "llm"
        assert result.valence == pytest.approx(0.3, abs=0.01)
        assert result.mood_label == "content"
        assert llm.generate.await_count == 1

    @pytest.mark.asyncio
    async def test_repair_retry_on_invalid_schema(self):
        """Invalid schema on first attempt triggers one repair retry."""
        responses = [
            # First: valid JSON but missing required fields
            '{"mood": "happy", "energy": "high"}',
            # Repair retry: correct schema
            '{"valence": 0.5, "arousal": 0.6, "dominance": 0.5, "mood_label": "happy"}',
        ]
        call_count = {"n": 0}

        async def _sequenced_generate(**kwargs):
            idx = call_count["n"]
            call_count["n"] += 1
            return responses[idx]

        llm = MagicMock()
        llm.generate = _sequenced_generate
        assessor = LLMEmotionAssessor(llm)
        current = _neutral_state()

        result = await assessor.assess(["something"], current)

        # Repair retry succeeded → proper emotional state
        assert result.source == "llm"
        assert result.valence == pytest.approx(0.5, abs=0.01)
        assert call_count["n"] == 2

    @pytest.mark.asyncio
    async def test_repair_retry_failure_falls_back(self):
        """Both attempts failing → fallback to current_state with halved confidence."""
        responses = [
            '{"not": "valid"}',
            '{"still": "wrong"}',
        ]
        call_count = {"n": 0}

        async def _sequenced_generate(**kwargs):
            idx = call_count["n"]
            call_count["n"] += 1
            return responses[idx]

        llm = MagicMock()
        llm.generate = _sequenced_generate
        assessor = LLMEmotionAssessor(llm)
        current = _negative_state()

        result = await assessor.assess(["something"], current)

        assert result.confidence == pytest.approx(current.confidence / 2, abs=0.01)
        assert call_count["n"] == 2

    @pytest.mark.asyncio
    async def test_should_trigger(self):
        """Trigger logic: low confidence=True, big shift=True, stable high-conf=False."""
        low_conf = EmotionalState(valence=0.0, arousal=0.5, dominance=0.5, confidence=0.3)
        high_conf = EmotionalState(valence=0.2, arousal=0.5, dominance=0.5, confidence=0.8)
        previous = EmotionalState(valence=0.2, arousal=0.5, dominance=0.5, confidence=0.8)
        big_shift = EmotionalState(valence=-0.6, arousal=0.9, dominance=0.2, confidence=0.7)

        # Low confidence → should trigger (first assessment, turns_since=0)
        assert should_trigger_llm_assessment(low_conf, None, turns_since_last_llm=0) is True

        # Big shift from previous → should trigger (after cooldown expires)
        assert should_trigger_llm_assessment(big_shift, previous, turns_since_last_llm=5) is True

        # Stable, high confidence → should NOT trigger
        assert should_trigger_llm_assessment(high_conf, previous) is False

    @pytest.mark.asyncio
    async def test_cooldown_suppresses_trigger(self):
        """During cooldown (turns 1-2 after LLM), moderate triggers are suppressed."""
        # Confidence 0.4 would normally trigger (< 0.5) but cooldown suppresses
        moderate_low = EmotionalState(valence=0.0, arousal=0.5, dominance=0.5, confidence=0.4)
        previous = EmotionalState(valence=0.2, arousal=0.5, dominance=0.5, confidence=0.8)

        # 1 turn since LLM — cooldown active
        assert should_trigger_llm_assessment(moderate_low, previous, turns_since_last_llm=1) is False
        # 2 turns since LLM — cooldown still active
        assert should_trigger_llm_assessment(moderate_low, previous, turns_since_last_llm=2) is False
        # 3 turns since LLM — cooldown expired, low confidence triggers
        assert should_trigger_llm_assessment(moderate_low, previous, turns_since_last_llm=3) is True

    @pytest.mark.asyncio
    async def test_very_low_confidence_overrides_cooldown(self):
        """confidence < 0.3 always triggers regardless of cooldown."""
        very_low = EmotionalState(valence=0.0, arousal=0.5, dominance=0.5, confidence=0.2)
        previous = EmotionalState(valence=0.2, arousal=0.5, dominance=0.5, confidence=0.8)

        # Even at turn 1 after LLM (cooldown active), very low confidence fires
        assert should_trigger_llm_assessment(very_low, previous, turns_since_last_llm=1) is True
        assert should_trigger_llm_assessment(very_low, previous, turns_since_last_llm=2) is True

    @pytest.mark.asyncio
    async def test_raised_delta_threshold(self):
        """PAD delta threshold raised from 0.3 to 0.4 — delta of 0.35 no longer triggers."""
        # Delta of 0.35 on valence (was >0.3 so would trigger before, now 0.4 threshold)
        current = EmotionalState(valence=0.55, arousal=0.5, dominance=0.5, confidence=0.8)
        previous = EmotionalState(valence=0.2, arousal=0.5, dominance=0.5, confidence=0.8)
        assert should_trigger_llm_assessment(current, previous, turns_since_last_llm=5) is False

        # Delta of 0.45 on valence — exceeds 0.4 threshold
        big_current = EmotionalState(valence=0.65, arousal=0.5, dominance=0.5, confidence=0.8)
        assert should_trigger_llm_assessment(big_current, previous, turns_since_last_llm=5) is True
