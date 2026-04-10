"""recall() — Fast deterministic memory search tool.

No LLM call. Embeds query via local model, runs hybrid search
(0.7 vector + 0.3 FTS5) across projection.db. Target: <500ms.
"""

from __future__ import annotations

import json
import time
from typing import Any

import structlog

from kora_v2.memory.retrieval import hybrid_search

log = structlog.get_logger(__name__)

# Per-session dedup tracking: list of (timestamp, query) for recent empty recalls
_recent_empty_recalls: list[tuple[float, str]] = []
_DEDUP_WINDOW_SECONDS = 120.0
_DEDUP_THRESHOLD = 3


async def recall(
    query: str,
    layer: str = "all",
    max_results: int = 10,
    container: Any = None,
) -> str:
    """Search memory via hybrid vector + FTS5 search.

    Args:
        query: Natural-language search query.
        layer: Memory layer — ``all``, ``long_term``, or ``user_model``.
        max_results: Maximum number of results to return.
        container: Service container providing embedding model and
            projection_db.

    Returns:
        JSON-serialized list of matching memory results.
    """
    if not query.strip():
        return json.dumps({"results": [], "message": "Empty query"})

    if container is None:
        return json.dumps({
            "results": [],
            "message": "No service container available",
        })

    # Get embedding model and projection DB from container
    embedding_model = container.embedding_model
    projection_db = container.projection_db

    if embedding_model is None or projection_db is None:
        return json.dumps({
            "results": [],
            "message": "Memory subsystem not initialized",
        })

    # Embed the query (no LLM call — local model)
    query_embedding = embedding_model.embed(query, task_type="search_query")

    # Run hybrid search
    results = await hybrid_search(
        db=projection_db,
        query=query,
        query_embedding=query_embedding,
        layer=layer,
        max_results=max_results,
    )

    # Format as JSON
    formatted = [
        {
            "id": r.id,
            "content": r.content,
            "summary": r.summary,
            "type": r.memory_type,
            "importance": r.importance,
            "score": round(r.score, 4),
            "source": r.source,
        }
        for r in results
    ]

    log.info(
        "recall_complete",
        query=query[:80],
        layer=layer,
        results=len(formatted),
    )

    if not formatted:
        now = time.monotonic()
        # Prune old entries outside the dedup window
        _recent_empty_recalls[:] = [
            (t, q) for t, q in _recent_empty_recalls
            if now - t < _DEDUP_WINDOW_SECONDS
        ]
        _recent_empty_recalls.append((now, query))

        message = (
            "No memories found matching this query. "
            "The memory store may not have relevant data yet."
        )

        if len(_recent_empty_recalls) >= _DEDUP_THRESHOLD:
            message += (
                " This query has been attempted multiple times with no results. "
                "Consider proceeding without memory recall."
            )

        return json.dumps({"results": [], "message": message})

    return json.dumps({"results": formatted})
