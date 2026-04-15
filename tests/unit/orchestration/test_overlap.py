"""Unit tests for topic-overlap scoring (spec §17.7a relocation)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from kora_v2.runtime.orchestration.overlap import (
    OverlapResult,
    _classify,
    _cosine,
    _lexical_jaccard,
    check_topic_overlap,
)


def _container_with_embeddings(vectors: dict[str, list[float]]) -> Any:
    class _Svc:
        async def encode_async(self, text: str) -> list[float] | None:
            return vectors.get(text)

    return SimpleNamespace(embedding_service=_Svc())


def test_cosine_basic() -> None:
    assert _cosine([1, 0], [1, 0]) == pytest.approx(1.0)
    assert _cosine([1, 0], [0, 1]) == pytest.approx(0.0)
    assert _cosine(None, [1, 0]) == 0.0
    assert _cosine([0, 0], [1, 1]) == 0.0


def test_lexical_jaccard_content_words_only() -> None:
    # "the" and "with" are stop words; < 4 chars skipped.
    score = _lexical_jaccard(
        "reading novels with focus", "reading comics with laughter"
    )
    # Content words: {reading, novels, focus} vs {reading, comics, laughter}
    # Intersection: {reading} — size 1. Union: 5. → 0.2
    assert score == pytest.approx(1 / 5)


def test_lexical_jaccard_empty_strings() -> None:
    assert _lexical_jaccard("", "") == 0.0


def test_classify_boundaries() -> None:
    assert _classify(0.80).action == "pause"
    assert _classify(0.70).action == "pause"
    assert _classify(0.60).action == "ambiguous"
    assert _classify(0.45).action == "ambiguous"
    assert _classify(0.30).action == "continue"


async def test_check_topic_overlap_pause_path() -> None:
    vectors = {
        "user is asking about the budget": [1.0, 0.0],
        "reviewing the monthly budget spreadsheet": [1.0, 0.05],
        "budget review for April": [1.0, 0.1],
    }
    container = _container_with_embeddings(vectors)
    result = await check_topic_overlap(
        user_message="user is asking about the budget",
        autonomous_goal="budget review for April",
        active_step_description="reviewing the monthly budget spreadsheet",
        container=container,
    )
    assert isinstance(result, OverlapResult)
    # Pure cosine terms ~= 1.0 each, lex jaccard > 0 → score ≥ 0.7.
    assert result.action == "pause"


async def test_check_topic_overlap_continue_path() -> None:
    vectors = {
        "remind me to call mom": [0.0, 1.0],
        "processing overnight logs for anomalies": [1.0, 0.0],
        "pipeline maintenance": [1.0, 0.0],
    }
    container = _container_with_embeddings(vectors)
    result = await check_topic_overlap(
        user_message="remind me to call mom",
        autonomous_goal="pipeline maintenance",
        active_step_description="processing overnight logs for anomalies",
        container=container,
    )
    assert result.action == "continue"


async def test_check_topic_overlap_handles_missing_embeddings() -> None:
    class _NoEmb:
        pass

    container = SimpleNamespace(embedding_service=_NoEmb())
    result = await check_topic_overlap(
        user_message="hi",
        autonomous_goal="do a thing",
        active_step_description="do a thing",
        container=container,
    )
    # No embeddings → cosine contributions zero; lexical may still fire,
    # but the low score should at worst land in "continue".
    assert result.action in {"continue", "ambiguous"}


async def test_check_topic_overlap_exception_falls_back_to_continue() -> None:
    class _Exploding:
        async def encode_async(self, text: str):
            raise RuntimeError("boom")

    container = SimpleNamespace(embedding_service=_Exploding())
    result = await check_topic_overlap(
        user_message="a",
        autonomous_goal="b",
        active_step_description="c",
        container=container,
    )
    assert result.action == "continue"
    assert result.score == 0.0
