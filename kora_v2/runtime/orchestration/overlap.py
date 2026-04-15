"""Kora V2 — Topic overlap detection for the orchestration layer.

Moved verbatim from ``kora_v2.autonomous.overlap`` per spec §17.7a so
any pipeline (not just the autonomous runtime) can reuse the scoring.

When the user sends a message while a LONG_BACKGROUND task is running,
Kora needs to decide whether to pause and handle the message or
continue without interruption. ``check_topic_overlap`` computes a
weighted similarity score and maps it to an action recommendation.

Score formula::

    0.6 * cosine(user_message, active_step_description)
  + 0.2 * cosine(user_message, autonomous_goal)
  + 0.2 * lexical_jaccard(user_message, active_step_description)

Cosine similarity is computed via the LocalEmbeddingService when
available. Lexical similarity uses Jaccard over content words (length
> 3, after stop-word removal). Thresholds: ``>= 0.70`` pause,
``>= 0.45`` ambiguous, else continue.
"""

from __future__ import annotations

import math
import re
from typing import Any, Literal

import structlog
from pydantic import BaseModel

log = structlog.get_logger(__name__)

# ── Stop words ────────────────────────────────────────────────────────────

_STOP_WORDS: frozenset[str] = frozenset(
    {
        "the", "and", "that", "this", "with", "from", "have", "been",
        "will", "would", "could", "should", "about", "which", "their",
        "there", "then", "than", "what", "when", "where", "some", "into",
        "also", "more", "over", "such", "just", "like", "very", "your",
        "each", "they", "were", "does", "been", "make", "made", "take",
    }
)

# ── Result model ──────────────────────────────────────────────────────────


class OverlapResult(BaseModel):
    """Output of a single topic-overlap check."""

    score: float
    action: Literal["pause", "ambiguous", "continue"]
    message: str | None = None


# ── Public entry point ────────────────────────────────────────────────────


async def check_topic_overlap(
    user_message: str,
    autonomous_goal: str,
    active_step_description: str,
    container: Any,
) -> OverlapResult:
    """Compute topic overlap between an incoming user message and the
    currently running background work.
    """
    try:
        user_emb = await _embed(user_message, container)
        step_emb = await _embed(active_step_description, container)
        goal_emb = await _embed(autonomous_goal, container)

        cos_step = _cosine(user_emb, step_emb)
        cos_goal = _cosine(user_emb, goal_emb)
        lex = _lexical_jaccard(user_message, active_step_description)

        score = 0.6 * cos_step + 0.2 * cos_goal + 0.2 * lex

        log.debug(
            "overlap_check",
            cos_step=round(cos_step, 4),
            cos_goal=round(cos_goal, 4),
            lex=round(lex, 4),
            score=round(score, 4),
        )

        return _classify(score)

    except Exception as exc:
        log.warning("overlap_check_failed", error=str(exc))
        return OverlapResult(score=0.0, action="continue", message=None)


# ── Threshold classification ──────────────────────────────────────────────


def _classify(score: float) -> OverlapResult:
    if score >= 0.70:
        return OverlapResult(
            score=score,
            action="pause",
            message=(
                "That sounds related to the work I am doing. "
                "I am pausing at the next safe point so I do not drift."
            ),
        )
    if score >= 0.45:
        return OverlapResult(score=score, action="ambiguous", message=None)
    return OverlapResult(score=score, action="continue", message=None)


# ── Embedding helpers ─────────────────────────────────────────────────────


async def _embed(text: str, container: Any) -> list[float] | None:
    """Encode *text* using the container's embedding service."""
    service = getattr(container, "embedding_service", None)
    if service is None:
        return None
    encode_fn = getattr(service, "encode_async", None) or getattr(
        service, "encode", None
    )
    if encode_fn is None:
        return None
    import asyncio

    if asyncio.iscoroutinefunction(encode_fn):
        return await encode_fn(text)
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, encode_fn, text)


# ── Similarity functions ──────────────────────────────────────────────────


def _cosine(a: list[float] | None, b: list[float] | None) -> float:
    """Cosine similarity between two vectors. Returns 0.0 when either
    vector is ``None`` — no signal, so the axis contributes nothing.
    """
    if a is None or b is None:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _tokenize(text: str) -> set[str]:
    """Extract content words: lowercase, alpha-only, length > 3, not in stop-word list."""
    words = re.sub(r"[^a-zA-Z\s]", " ", text).lower().split()
    return {w for w in words if len(w) > 3 and w not in _STOP_WORDS}


def _lexical_jaccard(a: str, b: str) -> float:
    """Jaccard similarity of content-word sets."""
    set_a = _tokenize(a)
    set_b = _tokenize(b)
    if not set_a and not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union else 0.0
