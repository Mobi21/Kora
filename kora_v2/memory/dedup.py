"""Memory deduplication with merge logic.

When a new fact is similar to an existing one:
- If exact duplicate (same info) → increment evidence count only
- If similar but with new details → merge new details into existing memory

Uses FTS5 candidate search, then LLM judgment to determine
duplicate/merge/new status.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any

import aiosqlite
import structlog
from pydantic import BaseModel

logger = structlog.get_logger()


# ============================================================
# FTS5 sanitisation (escape operators to prevent query errors)
# ============================================================

_FTS5_OPERATORS = re.compile(
    r"\b(OR|AND|NOT|NEAR)\b", re.IGNORECASE,
)


_BARE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_]+$")


def _sanitize_fts5_query(query: str) -> str:
    """Build an OR-joined FTS5 query for dedup candidate search.

    Dedup needs OR semantics — find documents that share ANY terms
    with the new content, not documents that contain ALL terms
    (which is FTS5's default implicit AND).

    Defensively quotes any token containing non-alphanumeric characters
    (hyphens, colons, dots) to prevent FTS5 from interpreting them as
    operators or column specifiers.
    """
    # Remove punctuation / special chars that break FTS5
    cleaned = query.replace("'", "").replace('"', "").replace("?", "")
    cleaned = cleaned.replace("(", "").replace(")", "")
    # Split into tokens, filter short/operator words
    tokens = cleaned.split()
    safe_tokens: list[str] = []
    for token in tokens:
        word = token.strip(".,!;:")
        if not word or len(word) < 2:
            continue
        if word.upper() in {"OR", "AND", "NOT", "NEAR"}:
            safe_tokens.append(f'"{word}"')
            continue
        if not _BARE_TOKEN_RE.match(word):
            # Contains hyphens, unicode, etc. — must quote to avoid
            # "no such column" and NOT-operator misparses.
            safe_tokens.append(f'"{word}"')
            continue
        safe_tokens.append(word)
    if not safe_tokens:
        return ""
    return " OR ".join(safe_tokens)


# ============================================================
# Models
# ============================================================


class DedupAction(StrEnum):
    """Possible dedup outcomes."""

    NEW = "new"            # No duplicate found — store as new
    DUPLICATE = "duplicate"  # Exact duplicate — increment evidence only
    MERGE = "merge"        # Similar with new details — merge into existing


class DedupResult(BaseModel):
    """Result of a deduplication check."""

    action: DedupAction
    existing_id: str | None = None    # ID of existing note if dup/merge
    merged_content: str | None = None  # Combined text if action is MERGE


# ============================================================
# LLM response parser
# ============================================================


def _parse_dedup_response(response: str) -> tuple[str, str | None]:
    """Parse ACTION and optional MERGED text from LLM response.

    Expected format::

        ACTION: DUPLICATE|MERGE|NEW
        MERGED: <combined text>   (only when ACTION is MERGE)

    Returns:
        (action_str, merged_text_or_None)
    """
    action = "new"
    merged: str | None = None
    merged_lines: list[str] = []
    in_merged = False

    for line in response.strip().split("\n"):
        stripped = line.strip()

        if stripped.upper().startswith("ACTION:"):
            raw = stripped.split(":", 1)[1].strip().lower()
            if raw in ("duplicate", "merge", "new"):
                action = raw
            in_merged = False
        elif stripped.upper().startswith("MERGED:"):
            text = stripped.split(":", 1)[1].strip()
            if text:
                merged_lines.append(text)
            in_merged = True
        elif in_merged and stripped:
            # Continuation lines of the merged text
            merged_lines.append(stripped)

    if merged_lines:
        merged = "\n".join(merged_lines)

    return action, merged


# ============================================================
# BM25 candidate search
# ============================================================


async def _fts5_candidate_search(
    content: str,
    db: aiosqlite.Connection,
    table: str = "memories_fts",
    *,
    score_threshold: float = 0.50,
    limit: int = 5,
) -> list[dict]:
    """Search FTS5 for candidate duplicates using BM25 scoring.

    BM25 returns *negative* values (more negative = more relevant).
    We negate and normalise so that higher scores = more relevant,
    then filter by *score_threshold*.

    Args:
        content: Text to search for similar content.
        db: Open aiosqlite connection with FTS5 table.
        table: Name of the FTS5 virtual table.
        score_threshold: Minimum normalised score (0.0–1.0) to include.
        limit: Maximum candidates to return.

    Returns:
        List of dicts with keys: ``id``, ``content``, ``score``.
    """
    query = _sanitize_fts5_query(content[:500])  # Truncate long content
    if not query:
        return []

    # Map FTS5 table to base table for joining to get the real ID
    base_table_map = {
        "memories_fts": "memories",
        "user_model_fts": "user_model_facts",
    }
    base_table = base_table_map.get(table)

    if base_table:
        # Join FTS5 with base table to get the real record ID
        sql = f"""
            SELECT m.id, m.content, f.rank
            FROM {table} f
            INNER JOIN {base_table} m ON m.rowid = f.rowid
            WHERE {table} MATCH ?
            ORDER BY f.rank
            LIMIT ?
        """
    else:
        # Fallback for standalone FTS5 tables (e.g. in tests)
        sql = f"""
            SELECT rowid, content, rank
            FROM {table}
            WHERE {table} MATCH ?
            ORDER BY rank
            LIMIT ?
        """

    try:
        cursor = await db.execute(sql, (query, limit * 2))
        rows = await cursor.fetchall()
    except Exception:
        logger.warning(
            "fts5_candidate_search_error",
            query_preview=query[:80],
            exc_info=True,
        )
        return []

    if not rows:
        return []

    # BM25 scores: negate so higher = more relevant
    raw_scores = [(-row[2]) for row in rows]
    max_score = max(raw_scores)
    if max_score <= 0:
        return []

    candidates: list[dict] = []
    for row, raw in zip(rows, raw_scores):
        normalised = raw / max_score
        if normalised >= score_threshold:
            candidates.append({
                "id": str(row[0]),
                "content": row[1],
                "score": normalised,
            })

    # Sort by score descending, limit
    candidates.sort(key=lambda c: c["score"], reverse=True)
    return candidates[:limit]


# ============================================================
# Dedup prompt
# ============================================================

_DEDUP_PROMPT = """Compare these two pieces of information:

EXISTING: {existing}
NEW: {new}

Respond with exactly one of:
- DUPLICATE: if they contain the same information
- MERGE: if they are about the same topic but the NEW one has additional \
details not in EXISTING. Include the merged text that combines both.
- NEW: if they are about different topics

Format your response as:
ACTION: [DUPLICATE|MERGE|NEW]
MERGED: [only if ACTION is MERGE, the combined text]"""


# ============================================================
# Public API
# ============================================================


async def dedup_check(
    content: str,
    db: aiosqlite.Connection,
    llm: Any,
    *,
    layer: str = "long_term",
    score_threshold: float = 0.50,
    table: str = "memories_fts",
) -> DedupResult:
    """Check whether *content* duplicates or overlaps an existing memory.

    1. FTS5 candidate search for textually similar existing notes.
    2. For each close candidate, ask the LLM to judge duplicate/merge/new.
    3. Return on the first duplicate or merge match.

    Args:
        content: New content to check for duplicates.
        db: Open aiosqlite connection with the FTS5 table populated.
        llm: LLM callable — must support
            ``await llm(prompt, temperature=0.1)`` returning a string.
        layer: Memory layer to search (``"long_term"`` or ``"user_model"``).
        score_threshold: Minimum BM25 normalised score for candidates.
        table: FTS5 virtual table name.

    Returns:
        DedupResult indicating whether to store as new, skip as
        duplicate, or merge with an existing note.
    """
    candidates = await _fts5_candidate_search(
        content, db, table, score_threshold=score_threshold,
    )

    if not candidates:
        logger.debug("dedup_no_candidates", content_preview=content[:80])
        return DedupResult(action=DedupAction.NEW)

    logger.debug(
        "dedup_candidates_found",
        count=len(candidates),
        top_score=candidates[0]["score"] if candidates else 0,
    )

    for candidate in candidates:
        prompt = _DEDUP_PROMPT.format(
            existing=candidate["content"],
            new=content,
        )

        try:
            response = await llm(prompt, temperature=0.1)
        except Exception:
            logger.warning(
                "dedup_llm_call_failed",
                candidate_id=candidate["id"],
                exc_info=True,
            )
            continue

        action, merged = _parse_dedup_response(response)

        if action == "duplicate":
            logger.info(
                "dedup_duplicate_found",
                existing_id=candidate["id"],
            )
            return DedupResult(
                action=DedupAction.DUPLICATE,
                existing_id=str(candidate["id"]),
            )

        if action == "merge":
            logger.info(
                "dedup_merge_found",
                existing_id=candidate["id"],
            )
            return DedupResult(
                action=DedupAction.MERGE,
                existing_id=str(candidate["id"]),
                merged_content=merged,
            )

    # No duplicate or merge match — store as new
    return DedupResult(action=DedupAction.NEW)
