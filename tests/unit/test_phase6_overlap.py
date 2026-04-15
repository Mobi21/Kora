"""Tests for kora_v2.autonomous.overlap — Phase 6 topic overlap detection."""

from __future__ import annotations

import math
from unittest.mock import MagicMock

import pytest

from kora_v2.autonomous.overlap import (
    OverlapResult,
    check_topic_overlap,
)
from kora_v2.runtime.orchestration.overlap import (
    _cosine,
    _lexical_jaccard,
    _tokenize,
)

# ── Unit: _tokenize ───────────────────────────────────────────────────────


class TestTokenize:
    def test_filters_short_words(self):
        tokens = _tokenize("go to the gym")
        assert "go" not in tokens
        assert "to" not in tokens
        assert "the" not in tokens

    def test_keeps_content_words(self):
        tokens = _tokenize("write a Python function for parsing")
        assert "write" in tokens
        assert "python" in tokens
        assert "function" in tokens
        assert "parsing" in tokens

    def test_strips_punctuation(self):
        tokens = _tokenize("read file.txt, process data!")
        # no punctuation in output
        for t in tokens:
            assert t.isalpha(), t

    def test_lowercases(self):
        tokens = _tokenize("Python FUNCTION")
        assert "python" in tokens
        assert "function" in tokens


# ── Unit: _cosine ─────────────────────────────────────────────────────────


class TestCosine:
    def test_identical_vectors(self):
        v = [1.0, 0.0, 0.0]
        assert _cosine(v, v) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert _cosine(a, b) == pytest.approx(0.0)

    def test_opposite_vectors(self):
        a = [1.0, 0.0]
        b = [-1.0, 0.0]
        assert _cosine(a, b) == pytest.approx(-1.0)

    def test_none_returns_zero(self):
        # None means no embedding signal — contributes 0.0 so the composite
        # score is not biased toward ambiguous/pause when embeddings are unavailable.
        assert _cosine(None, [1.0, 0.0]) == 0.0
        assert _cosine([1.0, 0.0], None) == 0.0
        assert _cosine(None, None) == 0.0

    def test_zero_vector_returns_zero(self):
        a = [0.0, 0.0]
        b = [1.0, 0.0]
        assert _cosine(a, b) == 0.0


# ── Unit: _lexical_jaccard ────────────────────────────────────────────────


class TestLexicalJaccard:
    def test_identical_text(self):
        text = "write unit tests for the authentication module"
        score = _lexical_jaccard(text, text)
        assert score == pytest.approx(1.0)

    def test_disjoint_text(self):
        a = "programming language compiler"
        b = "cooking recipe ingredients"
        score = _lexical_jaccard(a, b)
        assert score == 0.0

    def test_partial_overlap(self):
        a = "refactor authentication module tests"
        b = "write tests for authentication service"
        score = _lexical_jaccard(a, b)
        assert 0.0 < score < 1.0

    def test_empty_strings(self):
        assert _lexical_jaccard("", "") == 0.0
        assert _lexical_jaccard("hello world", "") == 0.0


# ── check_topic_overlap (async, with mocked embeddings) ──────────────────


def _make_container(embeddings: dict[str, list[float]] | None = None) -> MagicMock:
    """Build a fake DI container whose embedding_service.encode returns
    preset vectors from the *embeddings* dict, keyed by input text.
    If embeddings is None, no embedding_service is set.
    """
    container = MagicMock()
    if embeddings is None:
        container.embedding_service = None
        return container

    async def fake_encode_async(text: str) -> list[float]:
        return embeddings.get(text, [0.0, 0.0, 0.0])

    svc = MagicMock()
    svc.encode_async = fake_encode_async
    container.embedding_service = svc
    return container


@pytest.mark.asyncio
class TestCheckTopicOverlap:
    async def test_high_overlap_causes_pause(self):
        """When cosine similarity is high the action should be 'pause'."""
        # identical embeddings → cosine=1.0 for all pairs
        v = [1.0, 0.0]
        user_msg = "fix the authentication bug"
        goal = "debug the auth system"
        step_desc = "fix authentication token issue"

        container = _make_container({
            user_msg: v,
            goal: v,
            step_desc: v,
        })

        result = await check_topic_overlap(user_msg, goal, step_desc, container)
        assert result.action == "pause"
        assert result.score >= 0.70
        assert result.message is not None

    async def test_low_overlap_causes_continue(self):
        """Orthogonal embeddings should produce a low score → continue."""
        user_msg = "what is the weather like today"
        goal = "refactor database connection pool"
        step_desc = "optimise SQL query execution plans"

        # orthogonal: cosine=0.0 → weighted 0.6*0 + 0.2*0 = 0.0 plus lex ≈ 0
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        container = _make_container({
            user_msg: a,
            goal: b,
            step_desc: b,
        })

        result = await check_topic_overlap(user_msg, goal, step_desc, container)
        assert result.action == "continue"
        assert result.score < 0.45

    async def test_ambiguous_middle_score(self):
        """Vectors at ~0.57 cosine should yield 'ambiguous'."""
        # Use partial overlap vectors to land in the ambiguous 0.45-0.69 band
        norm = math.sqrt(2)
        v_half = [1.0 / norm, 1.0 / norm]  # 45-degree vector
        v_ref = [1.0, 0.0]
        # cosine(half, ref) = 1/sqrt(2) ≈ 0.707
        # weighted: 0.6*0.707 + 0.2*0.707 = 0.565 + 0.141 ≈ 0.566 (ambiguous if lex≈0)
        user_msg = "unrelated query about weather"
        goal = "something totally different xylophone"
        step_desc = "another very different rainbow topic"

        container = _make_container({
            user_msg: v_half,
            goal: v_ref,
            step_desc: v_ref,
        })

        result = await check_topic_overlap(user_msg, goal, step_desc, container)
        # score ~= 0.6 * 0.707 + 0.2 * 0.707 + 0.2 * lex ≈ 0.57
        assert result.action == "ambiguous"
        assert 0.45 <= result.score < 0.70

    async def test_no_embedding_service_falls_back_to_neutral(self):
        """With no embedding service, neutral cosine (0.5) is used.
        Score = 0.6*0.5 + 0.2*0.5 + 0.2*lex = 0.4 + lex*0.2.
        For unrelated text lex≈0 → score≈0.4 → continue.
        """
        container = _make_container(None)
        user_msg = "unrelated"
        result = await check_topic_overlap(
            user_msg, "goal text", "step description", container
        )
        # neutral score ≈ 0.4 → should be continue or ambiguous depending on lex
        assert result.action in ("continue", "ambiguous")

    async def test_embedding_error_returns_continue_score_zero(self):
        """If the embedding service raises, the function must return continue/0.0."""
        container = MagicMock()

        async def raise_error(text):
            raise RuntimeError("embedding failure")

        svc = MagicMock()
        svc.encode_async = raise_error
        container.embedding_service = svc

        result = await check_topic_overlap(
            "user message", "goal", "step description", container
        )
        assert result.action == "continue"
        assert result.score == 0.0

    async def test_result_is_overlap_result_model(self):
        container = _make_container(None)
        result = await check_topic_overlap("hi", "goal", "step", container)
        assert isinstance(result, OverlapResult)

    async def test_score_formula_math(self):
        """Verify the exact formula: 0.6*cos_step + 0.2*cos_goal + 0.2*lex."""
        # Manually computed: all cosine = 1.0, lex = 1.0 → score must be 1.0
        v = [1.0, 0.0]
        text = "identical text identical text"
        container = _make_container({text: v})
        result = await check_topic_overlap(text, text, text, container)
        # cos_step=1, cos_goal=1, lex=1 → 0.6+0.2+0.2 = 1.0
        assert result.score == pytest.approx(1.0, abs=0.01)

    async def test_pause_message_is_non_empty(self):
        v = [1.0, 0.0]
        text = "authentication refactor session"
        container = _make_container({text: v})
        result = await check_topic_overlap(text, text, text, container)
        if result.action == "pause":
            assert isinstance(result.message, str)
            assert len(result.message) > 0
