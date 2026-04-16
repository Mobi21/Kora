"""Memory Steward — shared helpers, LLM prompts, and constants.

This module contains:
- LLM prompt templates for extraction, consolidation, dedup confirmation,
  entity resolution, and ADHD profile refinement
- Structured output parsing helpers
- Constants (batch sizes, thresholds)
- Jaro-Winkler string similarity for entity fuzzy matching

Used by ``memory_steward_handlers.py`` which implements the per-stage
handler functions for the ``post_session_memory`` and
``weekly_adhd_profile`` pipelines.
"""

from __future__ import annotations

import json
import math
from typing import Any

import structlog

log = structlog.get_logger(__name__)

# ── Constants ────────────────────────────────────────────────────────────

# Extract stage
MAX_SIGNALS_PER_INVOCATION = 10
MAX_TRANSCRIPTS_PER_INVOCATION = 5

# Consolidate stage
MAX_CONSOLIDATION_GROUPS = 3
CONSOLIDATION_THRESHOLD = 0.82
SHRINKAGE_REJECTION_THRESHOLD = 0.40  # reject if >40% shorter

# Dedup stage
MAX_DEDUP_PAIRS = 5
DEDUP_THRESHOLD = 0.92

# Entity stage
MAX_ENTITY_PAIRS = 5
ENTITY_FUZZY_THRESHOLD = 0.85

# ADHD profile
ADHD_PROFILE_LOOKBACK_DAYS = 7


# ── LLM Prompt Templates ────────────────────────────────────────────────

EXTRACTION_SYSTEM_PROMPT = """\
You are Kora's Memory Steward, responsible for extracting memorable facts
from conversation transcripts. Extract distinct, atomic facts that are
worth remembering about the user or their world.

For each extracted fact, provide:
- content: the fact itself (clear, self-contained statement)
- memory_type: one of "episodic" (events/experiences), "reflective"
  (insights/opinions), "procedural" (how-to/processes), "user_model"
  (personal facts about the user)
- domain: relevant domain (identity, preferences, health, relationships,
  work, hobbies, goals, values, routines, etc.)
- importance: 0.0 to 1.0 (how significant is this for understanding the user)
- entities: list of entity names mentioned (people, places, projects, etc.)
- tags: relevant tags for categorization

Respond with a JSON array of extracted facts. Only extract facts that are
genuinely worth remembering. Skip small talk, filler, and trivial exchanges.
"""

EXTRACTION_USER_TEMPLATE = """\
Extract memorable facts from this conversation transcript:

{transcript}

{signal_context}

Respond with ONLY a JSON array:
[{{"content": "...", "memory_type": "...", "domain": "...", "importance": 0.0, "entities": [...], "tags": [...]}}]
"""

CONSOLIDATION_SYSTEM_PROMPT = """\
You are Kora's Memory Steward performing memory consolidation.
Given a set of related notes, produce ONE coherent note that preserves
EVERY distinct fact from the originals. Do NOT summarize or lose details.
Merge overlapping information but keep all unique details intact.

The consolidated note should read naturally as a single coherent piece
of knowledge, not a bullet list of fragments.
"""

CONSOLIDATION_USER_TEMPLATE = """\
Consolidate these related notes into one coherent note that preserves
every distinct fact:

{notes}

Respond with ONLY the consolidated note text (no JSON wrapper, no metadata).
"""

DEDUP_SYSTEM_PROMPT = """\
You are Kora's Memory Steward performing deduplication verification.
Given two notes, determine if they represent the same underlying fact
(true duplicates) or if they are distinct memories about related things.

Respond with ONLY a JSON object:
{{"is_duplicate": true/false, "reasoning": "brief explanation"}}
"""

DEDUP_USER_TEMPLATE = """\
Are these two notes true duplicates (same underlying fact) or distinct memories?

Note A:
{note_a}

Note B:
{note_b}

Respond with ONLY: {{"is_duplicate": true/false, "reasoning": "..."}}
"""

ENTITY_RESOLUTION_SYSTEM_PROMPT = """\
You are Kora's Memory Steward performing entity resolution.
Given two entity names and context from notes they appear in, determine
if they refer to the same real-world entity (person, place, thing, etc.).

Respond with ONLY a JSON object:
{{"is_same": true/false, "reasoning": "brief explanation"}}
"""

ENTITY_RESOLUTION_USER_TEMPLATE = """\
Are these two entities the same? Consider the context from notes they appear in.

Entity A: {entity_a} (type: {type_a})
Entity B: {entity_b} (type: {type_b})

Context from notes mentioning Entity A:
{context_a}

Context from notes mentioning Entity B:
{context_b}

Respond with ONLY: {{"is_same": true/false, "reasoning": "..."}}
"""

ADHD_PROFILE_SYSTEM_PROMPT = """\
You are Kora's Memory Steward refining the user's ADHD profile based on
observed patterns from the past 7 days. Update peak focus windows, energy
patterns, crash timing, and time estimation accuracy based on the data.

IMPORTANT: The following fields are locked by the user and MUST NOT be
modified. Return them exactly as provided:
{locked_fields_json}

Respond with a YAML-formatted ADHD profile containing these fields:
- peak_focus_windows: list of time ranges (e.g. "09:00-11:30")
- afternoon_crash_start: typical crash start time
- afternoon_crash_end: typical crash end time
- time_estimation_factor: multiplier for user's time estimates
- energy_pattern: description of daily energy pattern
- medication_schedule: (only if data supports it)
- focus_session_optimal_length: in minutes
- break_interval: preferred break frequency in minutes

Only update fields where the observed data clearly supports a change.
Keep existing values when data is insufficient.
"""

ADHD_PROFILE_USER_TEMPLATE = """\
Current ADHD profile:
{current_profile}

Observed patterns from last 7 days:
{observed_patterns}

{merge_mode_instruction}

Respond with ONLY the updated YAML profile content (no fences, no explanation).
"""


# ── Structured Output Parsing ────────────────────────────────────────────


def parse_json_response(text: str) -> Any:
    """Parse a JSON response from an LLM, handling common formatting issues.

    Strips markdown code fences and extracts the first valid JSON
    structure found in the text.
    """
    cleaned = text.strip()

    # Strip markdown code fences
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        # Remove first line (```json or ```) and last line (```)
        lines = [
            line
            for i, line in enumerate(lines)
            if not (
                (i == 0 and line.startswith("```"))
                or (i == len(lines) - 1 and line.strip() == "```")
            )
        ]
        cleaned = "\n".join(lines).strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Try to find JSON array or object in the text
        for start_char, end_char in [("[", "]"), ("{", "}")]:
            start = cleaned.find(start_char)
            if start == -1:
                continue
            # Find matching end
            depth = 0
            for i in range(start, len(cleaned)):
                if cleaned[i] == start_char:
                    depth += 1
                elif cleaned[i] == end_char:
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(cleaned[start : i + 1])
                        except json.JSONDecodeError:
                            break
        log.warning("json_parse_failed", text_preview=cleaned[:200])
        return None


def validate_extracted_facts(facts: Any) -> list[dict[str, Any]]:
    """Validate and normalize extracted facts from LLM response.

    Returns a list of valid fact dicts, filtering out malformed entries.
    """
    if not isinstance(facts, list):
        log.warning("extracted_facts_not_list", type=type(facts).__name__)
        return []

    valid: list[dict[str, Any]] = []
    for fact in facts:
        if not isinstance(fact, dict):
            continue
        content = fact.get("content", "").strip()
        if not content:
            continue

        memory_type = fact.get("memory_type", "episodic")
        if memory_type not in ("episodic", "reflective", "procedural", "user_model"):
            memory_type = "episodic"

        importance = fact.get("importance", 0.5)
        if not isinstance(importance, (int, float)):
            importance = 0.5
        importance = max(0.0, min(1.0, float(importance)))

        entities = fact.get("entities", [])
        if not isinstance(entities, list):
            entities = []
        entities = [str(e) for e in entities if e]

        tags = fact.get("tags", [])
        if not isinstance(tags, list):
            tags = []
        tags = [str(t) for t in tags if t]

        valid.append(
            {
                "content": content,
                "memory_type": memory_type,
                "domain": str(fact.get("domain", "")),
                "importance": importance,
                "entities": entities,
                "tags": tags,
            }
        )

    return valid


# ── Jaro-Winkler Similarity ─────────────────────────────────────────────


def jaro_similarity(s1: str, s2: str) -> float:
    """Compute Jaro similarity between two strings."""
    if s1 == s2:
        return 1.0
    if not s1 or not s2:
        return 0.0

    len_s1 = len(s1)
    len_s2 = len(s2)

    match_distance = max(len_s1, len_s2) // 2 - 1
    if match_distance < 0:
        match_distance = 0

    s1_matches = [False] * len_s1
    s2_matches = [False] * len_s2

    matches = 0
    transpositions = 0

    for i in range(len_s1):
        start = max(0, i - match_distance)
        end = min(i + match_distance + 1, len_s2)
        for j in range(start, end):
            if s2_matches[j] or s1[i] != s2[j]:
                continue
            s1_matches[i] = True
            s2_matches[j] = True
            matches += 1
            break

    if matches == 0:
        return 0.0

    k = 0
    for i in range(len_s1):
        if not s1_matches[i]:
            continue
        while not s2_matches[k]:
            k += 1
        if s1[i] != s2[k]:
            transpositions += 1
        k += 1

    jaro = (
        matches / len_s1 + matches / len_s2 + (matches - transpositions / 2) / matches
    ) / 3

    return jaro


def jaro_winkler_similarity(s1: str, s2: str, p: float = 0.1) -> float:
    """Compute Jaro-Winkler similarity between two strings.

    Args:
        s1: First string.
        s2: Second string.
        p: Scaling factor for common prefix bonus (max 0.25).

    Returns:
        Similarity score between 0.0 and 1.0.
    """
    jaro = jaro_similarity(s1, s2)

    # Common prefix length (up to 4 characters)
    prefix_len = 0
    for i in range(min(len(s1), len(s2), 4)):
        if s1[i] == s2[i]:
            prefix_len += 1
        else:
            break

    return jaro + prefix_len * p * (1 - jaro)


def compute_shrinkage(original_texts: list[str], consolidated_text: str) -> float:
    """Compute the shrinkage ratio of consolidation.

    Returns:
        Fraction of content lost (0.0 = no shrinkage, 1.0 = all lost).
        Values > SHRINKAGE_REJECTION_THRESHOLD should trigger rejection.
    """
    original_total = sum(len(t) for t in original_texts)
    if original_total == 0:
        return 0.0
    consolidated_len = len(consolidated_text)
    if consolidated_len >= original_total:
        return 0.0
    return 1.0 - (consolidated_len / original_total)


def pick_richer_note(
    note_a: dict[str, Any], note_b: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Given two notes, return (richer, poorer) based on heuristics.

    Heuristics: importance score, entity count, content length,
    recency (updated_at).
    """

    def _score(note: dict[str, Any]) -> float:
        importance = note.get("importance", 0.5)
        if isinstance(importance, str):
            try:
                importance = float(importance)
            except ValueError:
                importance = 0.5

        entities = note.get("entities", [])
        if isinstance(entities, str):
            try:
                entities = json.loads(entities)
            except (json.JSONDecodeError, TypeError):
                entities = []
        entity_count = len(entities) if isinstance(entities, list) else 0

        content_len = len(note.get("content", ""))

        return importance * 2 + entity_count * 0.5 + math.log1p(content_len) * 0.1

    score_a = _score(note_a)
    score_b = _score(note_b)

    if score_a >= score_b:
        return note_a, note_b
    return note_b, note_a
