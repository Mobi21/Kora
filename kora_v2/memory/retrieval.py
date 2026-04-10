"""Memory retrieval — hybrid vector + FTS5 search across projection.db.

Provides vector search, FTS5 full-text search, and a merged hybrid
search with configurable weighting and time decay.  All functions
operate on an open ProjectionDB and return ``MemoryResult`` objects
sorted by relevance.

Target latency: <500ms for hybrid search on typical projection sizes.
"""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime

import structlog
from pydantic import BaseModel, Field

from kora_v2.memory.projection import ProjectionDB, serialize_float32

log = structlog.get_logger(__name__)

# FTS5 reserved words that must be quoted to avoid parse errors
_FTS5_RESERVED = frozenset({"OR", "AND", "NOT", "NEAR"})

# Regex to split on whitespace while keeping quoted phrases intact
_TOKEN_RE = re.compile(r'"[^"]*"|\S+')

# A "bare" FTS5 token is a sequence of ASCII word characters. Anything
# else (hyphens, colons, dots, slashes, plus, Unicode) must be quoted.
_BARE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_]+$")


# =====================================================================
# Result model
# =====================================================================


class MemoryResult(BaseModel):
    """A single search result from memory retrieval."""

    id: str
    content: str
    summary: str | None = None
    memory_type: str = "episodic"
    importance: float = 0.5
    score: float = Field(default=0.0, description="Relevance score 0-1")
    source: str = Field(
        default="long_term",
        description="Memory layer: long_term or user_model",
    )
    source_path: str = ""


# =====================================================================
# FTS5 sanitization
# =====================================================================


def _sanitize_fts5_query(query: str) -> str:
    """Escape FTS5 reserved operators and strip syntax-breaking chars.

    Defensive quoting rules (in order):

    1. Strip characters FTS5 always fails on: ``?``, ``(``, ``)``, ``'``.
    2. Preserve tokens that are already double-quoted verbatim.
    3. Quote any reserved operator (``OR``, ``AND``, ``NOT``, ``NEAR``).
    4. Quote any token that contains a character outside ``[A-Za-z0-9_]``
       so hyphens, colons, dots, slashes, plus signs, and Unicode can't
       be mis-parsed as column specifiers (``week:x``), NOT operators
       (``-foo``), or phrase delimiters.

    Args:
        query: Raw user query string.

    Returns:
        Sanitized query safe for FTS5 MATCH.
    """
    # Strip characters that break FTS5 syntax outright
    cleaned = query.replace("?", "").replace("(", "").replace(")", "")
    cleaned = cleaned.replace("'", "")

    tokens = _TOKEN_RE.findall(cleaned)
    sanitized: list[str] = []
    for token in tokens:
        # Already quoted — leave as-is
        if token.startswith('"') and token.endswith('"'):
            sanitized.append(token)
            continue
        if token.upper() in _FTS5_RESERVED:
            sanitized.append(f'"{token}"')
            continue
        if not _BARE_TOKEN_RE.match(token):
            # Contains hyphens, colons, unicode, etc. — quote it.
            # Strip any embedded double quotes first to avoid escaping
            # issues (FTS5 has no ``\"`` escape; doubled ``""`` is the
            # literal-quote convention).
            safe = token.replace('"', "")
            if safe:
                sanitized.append(f'"{safe}"')
            continue
        sanitized.append(token)
    return " ".join(sanitized)


# =====================================================================
# Vector search
# =====================================================================


async def vector_search(
    db: ProjectionDB,
    query_embedding: list[float],
    table: str = "memories",
    k: int = 10,
) -> list[MemoryResult]:
    """Search by vector similarity using sqlite-vec.

    Args:
        db: Open ProjectionDB instance.
        query_embedding: Query embedding vector (768-dim float32).
        table: Base table name (``memories`` or ``user_model_facts``).
        k: Maximum number of results.

    Returns:
        List of MemoryResult sorted by similarity (highest first).
    """
    # Migration creates memories_vec and user_model_vec (not user_model_facts_vec)
    vec_table_map = {"memories": "memories_vec", "user_model_facts": "user_model_vec"}
    vec_table = vec_table_map.get(table, f"{table}_vec")
    source = "long_term" if table == "memories" else "user_model"

    query_bytes = serialize_float32(query_embedding)

    sql = (
        f"SELECT m.*, v.distance "
        f"FROM {vec_table} v "
        f"INNER JOIN {table} m ON m.rowid = v.rowid "
        f"WHERE v.embedding MATCH ? AND k = ? "
        f"ORDER BY v.distance"
    )

    cursor = await db._db.execute(sql, (query_bytes, k))
    rows = await cursor.fetchall()

    results: list[MemoryResult] = []
    for row in rows:
        row_dict = dict(row)
        distance = row_dict.pop("distance", 0.0)
        # Convert cosine distance to similarity score (1 - distance)
        similarity = 1.0 - distance

        results.append(MemoryResult(
            id=row_dict.get("id", ""),
            content=row_dict.get("content", ""),
            summary=row_dict.get("summary"),
            memory_type=row_dict.get("memory_type", "episodic"),
            importance=row_dict.get(
                "importance", row_dict.get("confidence", 0.5)
            ),
            score=similarity,
            source=source,
            source_path=row_dict.get("source_path", ""),
        ))

    return results


# =====================================================================
# FTS5 search
# =====================================================================


async def fts5_search(
    db: ProjectionDB,
    query: str,
    table: str = "memories",
    limit: int = 20,
) -> list[MemoryResult]:
    """Full-text search using FTS5 with BM25 scoring.

    Args:
        db: Open ProjectionDB instance.
        query: Natural-language search query (auto-sanitized).
        table: Base table name (``memories`` or ``user_model_facts``).
        limit: Maximum number of results.

    Returns:
        List of MemoryResult sorted by BM25 relevance (highest first).
    """
    # Migration creates memories_fts and user_model_fts (not user_model_facts_fts)
    fts_table_map = {"memories": "memories_fts", "user_model_facts": "user_model_fts"}
    fts_table = fts_table_map.get(table, f"{table}_fts")
    source = "long_term" if table == "memories" else "user_model"

    sanitized = _sanitize_fts5_query(query)
    if not sanitized.strip():
        return []

    sql = (
        f"SELECT m.*, bm25({fts_table}) as score "
        f"FROM {fts_table} f "
        f"INNER JOIN {table} m ON m.rowid = f.rowid "
        f"WHERE {fts_table} MATCH ? "
        f"ORDER BY score "
        f"LIMIT ?"
    )

    try:
        cursor = await db._db.execute(sql, (sanitized, limit))
        rows = await cursor.fetchall()
    except Exception as exc:
        log.warning("fts5_search_failed", query=sanitized, error=str(exc))
        return []

    # BM25 returns negative scores (more negative = more relevant).
    # Convert to positive: negate, then normalize to 0-1.
    raw_results: list[tuple[dict, float]] = []
    for row in rows:
        row_dict = dict(row)
        raw_score = row_dict.pop("score", 0.0)
        raw_results.append((row_dict, -raw_score))  # negate to positive

    if not raw_results:
        return []

    # Normalize to 0-1 via min-max
    scores = [s for _, s in raw_results]
    max_score = max(scores)
    min_score = min(scores)

    if max_score <= 0:
        return []

    score_range = max_score - min_score
    results: list[MemoryResult] = []
    for row_dict, pos_score in raw_results:
        if score_range > 0:
            normalized = (pos_score - min_score) / score_range
        else:
            normalized = 1.0  # single result gets perfect score

        results.append(MemoryResult(
            id=row_dict.get("id", ""),
            content=row_dict.get("content", ""),
            summary=row_dict.get("summary"),
            memory_type=row_dict.get("memory_type", "episodic"),
            importance=row_dict.get(
                "importance", row_dict.get("confidence", 0.5)
            ),
            score=normalized,
            source=source,
            source_path=row_dict.get("source_path", ""),
        ))

    return results


# =====================================================================
# Merge and rank
# =====================================================================


def merge_and_rank(
    vec_results: list[MemoryResult],
    fts_results: list[MemoryResult],
    vec_weight: float = 0.7,
    fts_weight: float = 0.3,
) -> list[MemoryResult]:
    """Merge vector and FTS5 results with weighted scoring.

    If one source is empty, the other gets effective weight 1.0 so
    results are not penalized by the missing source.

    Args:
        vec_results: Results from vector search.
        fts_results: Results from FTS5 search.
        vec_weight: Weight for vector similarity scores.
        fts_weight: Weight for FTS5 BM25 scores.

    Returns:
        Merged list sorted by combined score (descending).
    """
    # Graceful fallback: if one source is empty, the other gets weight 1.0
    if not vec_results and not fts_results:
        return []
    if not vec_results:
        vec_weight = 0.0
        fts_weight = 1.0
    if not fts_results:
        vec_weight = 1.0
        fts_weight = 0.0

    # Normalize scores within each set (min-max)
    vec_results = _normalize_scores(vec_results)
    fts_results = _normalize_scores(fts_results)

    # Index by ID for merging
    combined: dict[str, MemoryResult] = {}
    combined_scores: dict[str, float] = {}

    for r in vec_results:
        combined[r.id] = r
        combined_scores[r.id] = vec_weight * r.score

    for r in fts_results:
        fts_contribution = fts_weight * r.score
        if r.id in combined_scores:
            combined_scores[r.id] += fts_contribution
        else:
            combined[r.id] = r
            combined_scores[r.id] = fts_contribution

    # Apply combined scores
    results: list[MemoryResult] = []
    for mem_id, result in combined.items():
        result.score = combined_scores[mem_id]
        results.append(result)

    results.sort(key=lambda r: r.score, reverse=True)
    return results


def _normalize_scores(results: list[MemoryResult]) -> list[MemoryResult]:
    """Min-max normalize scores within a result set to 0-1."""
    if not results:
        return results

    scores = [r.score for r in results]
    max_score = max(scores)
    min_score = min(scores)

    if max_score <= 0:
        return []

    score_range = max_score - min_score
    for r in results:
        if score_range > 0:
            r.score = (r.score - min_score) / score_range
        else:
            r.score = 1.0

    return results


# =====================================================================
# Time weighting
# =====================================================================


def apply_time_weighting(
    results: list[MemoryResult],
    decay_factor: float = 0.1,
) -> list[MemoryResult]:
    """Boost recent memories using exponential time decay.

    Applies ``score *= exp(-decay_factor * days_old)`` to each result
    and re-sorts by the adjusted score.

    Args:
        results: List of MemoryResult (must have source_path with date
            or rely on created_at from the memory content).
        decay_factor: How quickly older memories lose weight.  Lower
            values produce a gentler decay curve.

    Returns:
        Re-sorted list with time-weighted scores.
    """
    now = datetime.now(UTC)

    for r in results:
        # Try to extract a timestamp from the memory — fall back to no decay
        days_old = _estimate_age_days(r, now)
        if days_old > 0:
            r.score *= math.exp(-decay_factor * days_old)

    results.sort(key=lambda r: r.score, reverse=True)
    return results


def _estimate_age_days(result: MemoryResult, now: datetime) -> float:
    """Estimate how many days old a memory is.

    Tries to parse an ISO-8601 date from the source_path or content
    metadata.  Returns 0 if unparseable (no decay applied).
    """
    # source_path often contains date-like segments; look for ISO dates
    date_match = re.search(r"\d{4}-\d{2}-\d{2}", result.source_path)
    if date_match:
        try:
            dt = datetime.fromisoformat(date_match.group())
            dt = dt.replace(tzinfo=UTC)
            delta = now - dt
            return max(0.0, delta.total_seconds() / 86400)
        except ValueError:
            pass
    return 0.0


# =====================================================================
# Hybrid search (main entry point)
# =====================================================================


async def hybrid_search(
    db: ProjectionDB,
    query: str,
    query_embedding: list[float],
    layer: str = "all",
    memory_type: str | None = None,
    max_results: int = 10,
) -> list[MemoryResult]:
    """Run hybrid vector + FTS5 search across memory layers.

    Combines both search modalities, merges with configurable weights,
    applies time decay, and returns the top results.

    Args:
        db: Open ProjectionDB instance.
        query: Natural-language query for FTS5.
        query_embedding: Query vector for similarity search.
        layer: Memory layer — ``all``, ``long_term``, or ``user_model``.
        memory_type: Optional filter (e.g. ``episodic``, ``semantic``).
        max_results: Maximum results to return.

    Returns:
        List of MemoryResult sorted by combined relevance score.
    """
    all_results: list[MemoryResult] = []

    tables: list[str] = []
    if layer in ("all", "long_term"):
        tables.append("memories")
    if layer in ("all", "user_model"):
        tables.append("user_model_facts")

    for table in tables:
        vec_results = await vector_search(db, query_embedding, table=table)
        fts_results = await fts5_search(db, query, table=table)
        merged = merge_and_rank(vec_results, fts_results)
        all_results.extend(merged)

    # Apply time weighting
    all_results = apply_time_weighting(all_results)

    # Filter by memory_type if specified
    if memory_type:
        all_results = [
            r for r in all_results if r.memory_type == memory_type
        ]

    # Final sort and truncate
    all_results.sort(key=lambda r: r.score, reverse=True)
    return all_results[:max_results]
