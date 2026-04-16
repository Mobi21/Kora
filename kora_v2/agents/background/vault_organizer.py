"""Vault Organizer — shared helpers for the ``post_memory_vault`` pipeline.

This module contains:
- Markdown-aware wikilink injector (hand-rolled tokenizer, NOT regex)
- MOC page builder
- Entity page template
- Session note builder
- Folder hierarchy constants

Used by ``vault_organizer_handlers.py`` which implements the per-stage
handler functions for the ``post_memory_vault`` pipeline.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog

log = structlog.get_logger(__name__)


# ── Constants ────────────────────────────────────────────────────────────

# Batch limits per invocation
MAX_REINDEX_ENTRIES = 50
MAX_INBOX_TRIAGE = 10
MAX_LINKS_PER_INVOCATION = 30
MOC_REGEN_THRESHOLD = 5  # minimum changed notes before regenerating MOC

# Folder hierarchy under _KoraMemory/
FOLDER_HIERARCHY: list[str] = [
    "Long-Term/Episodic",
    "Long-Term/Reflective",
    "Long-Term/Procedural",
    "User Model/Identity",
    "User Model/Preferences",
    "User Model/Relationships",
    "User Model/Health",
    "User Model/Routines",
    "User Model/Work",
    "User Model/Education",
    "User Model/Finances",
    "User Model/Hobbies",
    "User Model/Goals",
    "User Model/Values",
    "User Model/Communication Style",
    "User Model/Emotional Patterns",
    "User Model/Triggers",
    "User Model/Strengths",
    "User Model/Challenges",
    "User Model/Medications",
    "User Model/Pets",
    "User Model/Living Situation",
    "User Model/Diet",
    "User Model/adhd_profile",
    "Entities/People",
    "Entities/Places",
    "Entities/Projects",
    "Inbox",
    "References",
    "Ideas",
    "Sessions",
    "Maps of Content",
    ".kora",
]

# Mapping from memory_type to expected folder prefix
MEMORY_TYPE_FOLDER_MAP: dict[str, str] = {
    "episodic": "Long-Term",
    "reflective": "Long-Term",
    "procedural": "Long-Term",
    "user_model": "User Model",
}

# Entity type to folder mapping
ENTITY_TYPE_FOLDER: dict[str, str] = {
    "person": "People",
    "place": "Places",
    "project": "Projects",
    "organization": "Projects",
    "medication": "Projects",
}


# ══════════════════════════════════════════════════════════════════════════
# Markdown-aware wikilink injector
# ══════════════════════════════════════════════════════════════════════════


@dataclass
class _TextRegion:
    """A region of text with its type (linkable or excluded)."""

    text: str
    linkable: bool
    start: int  # offset in original text
    end: int  # offset in original text


def _tokenize_markdown(text: str) -> list[_TextRegion]:
    """Tokenize markdown text into linkable and excluded regions.

    This is a hand-rolled tokenizer that tracks state through the
    document to identify regions where wikilinks must NOT be injected:
    - YAML frontmatter (``---`` delimiters at the start)
    - Fenced code blocks (triple-backtick or triple-tilde)
    - Inline code (single-backtick spans)
    - Existing wikilinks ``[[...]]`` and embed links ``![[...]]``
    - Markdown link text and URLs ``[text](url)``
    - HTML comment blocks ``<!-- ... -->``

    Returns a list of _TextRegion objects covering the entire input.
    """
    regions: list[_TextRegion] = []
    pos = 0
    length = len(text)

    # Check for YAML frontmatter at the very start
    if text.startswith("---"):
        # Use regex to find a standalone closing --- line (not --- inside YAML values)
        close_match = re.search(r'\n---[ \t]*\n|\n---[ \t]*$', text[3:])
        if close_match is not None:
            # close_match offsets are relative to text[3:]
            fm_end = 3 + close_match.end()
            regions.append(_TextRegion(text[:fm_end], linkable=False, start=0, end=fm_end))
            pos = fm_end

    while pos < length:
        # Check for fenced code block (``` or ~~~)
        if text[pos] in ("`", "~"):
            fence_char = text[pos]
            # Count consecutive fence chars
            fence_count = 0
            scan = pos
            while scan < length and text[scan] == fence_char:
                fence_count += 1
                scan += 1
            if fence_count >= 3:
                # Check if this is at the start of a line
                if pos == 0 or text[pos - 1] == "\n":
                    # Find the closing fence
                    fence_str = fence_char * fence_count
                    # Skip to end of the opening fence line
                    line_end = text.find("\n", scan)
                    if line_end == -1:
                        # No newline after opening fence - treat rest as code
                        regions.append(
                            _TextRegion(text[pos:], linkable=False, start=pos, end=length)
                        )
                        pos = length
                        continue
                    # Search for closing fence on its own line
                    search_pos = line_end + 1
                    close_pos = -1
                    while search_pos < length:
                        nl = text.find("\n", search_pos)
                        if nl == -1:
                            # Last line without newline
                            line = text[search_pos:]
                            if line.strip().startswith(fence_str) and all(
                                c == fence_char for c in line.strip()
                            ):
                                close_pos = length
                            break
                        line = text[search_pos:nl]
                        if line.strip().startswith(fence_str) and all(
                            c == fence_char for c in line.strip()
                        ):
                            close_pos = nl + 1
                            break
                        search_pos = nl + 1
                    if close_pos == -1:
                        close_pos = length
                    regions.append(
                        _TextRegion(
                            text[pos:close_pos],
                            linkable=False,
                            start=pos,
                            end=close_pos,
                        )
                    )
                    pos = close_pos
                    continue

        # Check for inline code (backtick spans, but not fenced blocks)
        if pos < length and text[pos] == "`":
            # Count opening backticks
            tick_count = 0
            scan = pos
            while scan < length and text[scan] == "`":
                tick_count += 1
                scan += 1
            if tick_count < 3 or (pos > 0 and text[pos - 1] != "\n"):
                # This is inline code, not a fence
                # Find matching closing backticks
                close_ticks = "`" * tick_count
                close_pos = text.find(close_ticks, scan)
                if close_pos != -1:
                    end_pos = close_pos + tick_count
                    regions.append(
                        _TextRegion(
                            text[pos:end_pos],
                            linkable=False,
                            start=pos,
                            end=end_pos,
                        )
                    )
                    pos = end_pos
                    continue
                else:
                    # No closing backticks found - treat as literal
                    pass

        # Check for HTML comments
        if text[pos:pos + 4] == "<!--":
            close_pos = text.find("-->", pos + 4)
            if close_pos != -1:
                end_pos = close_pos + 3
                regions.append(
                    _TextRegion(
                        text[pos:end_pos],
                        linkable=False,
                        start=pos,
                        end=end_pos,
                    )
                )
                pos = end_pos
                continue

        # Check for embed wikilinks ![[...]]
        if text[pos:pos + 3] == "![[":
            close_pos = text.find("]]", pos + 3)
            if close_pos != -1:
                end_pos = close_pos + 2
                regions.append(
                    _TextRegion(
                        text[pos:end_pos],
                        linkable=False,
                        start=pos,
                        end=end_pos,
                    )
                )
                pos = end_pos
                continue

        # Check for wikilinks [[...]]
        if text[pos:pos + 2] == "[[":
            close_pos = text.find("]]", pos + 2)
            if close_pos != -1:
                end_pos = close_pos + 2
                regions.append(
                    _TextRegion(
                        text[pos:end_pos],
                        linkable=False,
                        start=pos,
                        end=end_pos,
                    )
                )
                pos = end_pos
                continue

        # Check for markdown links [text](url)
        if text[pos] == "[":
            # Find the closing ] then (url)
            bracket_close = _find_matching_bracket(text, pos)
            if bracket_close != -1 and bracket_close + 1 < length and text[bracket_close + 1] == "(":
                paren_close = text.find(")", bracket_close + 2)
                if paren_close != -1:
                    end_pos = paren_close + 1
                    regions.append(
                        _TextRegion(
                            text[pos:end_pos],
                            linkable=False,
                            start=pos,
                            end=end_pos,
                        )
                    )
                    pos = end_pos
                    continue

        # This character is part of linkable text
        # Accumulate linkable text until we hit a special sequence
        link_start = pos
        while pos < length:
            # Peek ahead for special sequences
            ch = text[pos]
            if ch == "`":
                break
            if ch == "[":
                break
            if ch == "!" and pos + 2 < length and text[pos + 1:pos + 3] == "[[":
                break
            if ch == "<" and text[pos:pos + 4] == "<!--":
                break
            # Check for fenced code block at start of line
            if ch in ("`", "~") and (pos == 0 or text[pos - 1] == "\n"):
                fence_check = 0
                s = pos
                while s < length and text[s] == ch:
                    fence_check += 1
                    s += 1
                if fence_check >= 3:
                    break
            pos += 1

        if pos > link_start:
            regions.append(
                _TextRegion(
                    text[link_start:pos],
                    linkable=True,
                    start=link_start,
                    end=pos,
                )
            )

    return regions


def _find_matching_bracket(text: str, pos: int) -> int:
    """Find the closing ``]`` matching the ``[`` at *pos*.

    Handles nested brackets. Returns -1 if no match.
    """
    depth = 0
    i = pos
    while i < len(text):
        if text[i] == "[":
            depth += 1
        elif text[i] == "]":
            depth -= 1
            if depth == 0:
                return i
        elif text[i] == "\n":
            # Markdown links don't span lines in standard MD
            return -1
        i += 1
    return -1


def inject_wikilinks(body: str, entities: list[str]) -> str:
    """Inject wikilinks into markdown body for entity references.

    Uses a parser-based approach (NOT regex over raw strings) to
    respect excluded regions (frontmatter, code blocks, inline code,
    existing links, HTML comments).

    Rules:
    - Whole-word matching only (word boundary at both ends)
    - Longest-match-wins: "Sarah Connor" preferred over "Sarah"
    - First-occurrence-only per entity per note
    - Case-sensitive matching

    Args:
        body: The markdown body text (no frontmatter).
        entities: List of entity names to link.

    Returns:
        Updated body with wikilinks injected.
    """
    if not entities or not body:
        return body

    # Sort entities by length (longest first) for longest-match-wins
    sorted_entities = sorted(entities, key=len, reverse=True)

    # Build a compiled pattern for each entity (word boundary matching)
    entity_patterns: list[tuple[str, re.Pattern[str]]] = []
    for entity in sorted_entities:
        # Use word boundaries for whole-word matching
        pattern = re.compile(
            r"(?<!\w)" + re.escape(entity) + r"(?!\w)"
        )
        entity_patterns.append((entity, pattern))

    # Tokenize the body into linkable and excluded regions
    regions = _tokenize_markdown(body)

    # Track which entities have been linked (first-occurrence-only)
    linked_entities: set[str] = set()

    # Process each linkable region
    result_parts: list[str] = []
    for region in regions:
        if not region.linkable:
            result_parts.append(region.text)
            continue

        # Process this linkable region
        region_text = region.text
        # We need to track positions of replacements to avoid overlapping
        replacements: list[tuple[int, int, str, str]] = []

        for entity, pattern in entity_patterns:
            if entity in linked_entities:
                continue

            # Search for all matches; skip those that overlap with
            # already-scheduled replacements (e.g. "Sarah" inside
            # a "Sarah Connor" replacement).
            for match in pattern.finditer(region_text):
                m_start, m_end = match.start(), match.end()
                overlaps = False
                for r_start, r_end, _ent, _rep in replacements:
                    if m_start < r_end and m_end > r_start:
                        overlaps = True
                        break

                if not overlaps:
                    replacement = f"[[{entity}]]"
                    replacements.append((m_start, m_end, entity, replacement))
                    linked_entities.add(entity)
                    break  # first-occurrence-only per entity

        if not replacements:
            result_parts.append(region_text)
        else:
            # Apply replacements in reverse order to preserve positions
            replacements.sort(key=lambda r: r[0])
            parts: list[str] = []
            last_end = 0
            for r_start, r_end, _ent, replacement in replacements:
                parts.append(region_text[last_end:r_start])
                parts.append(replacement)
                last_end = r_end
            parts.append(region_text[last_end:])
            result_parts.append("".join(parts))

    return "".join(result_parts)


# ══════════════════════════════════════════════════════════════════════════
# Entity page builder
# ══════════════════════════════════════════════════════════════════════════


@dataclass
class EntityPageData:
    """Data needed to build an entity page."""

    name: str
    entity_type: str
    backlinks: list[dict[str, Any]] = field(default_factory=list)
    relationships: list[dict[str, Any]] = field(default_factory=list)
    first_mention: str | None = None
    last_mention: str | None = None


def build_entity_page(data: EntityPageData) -> str:
    """Build the markdown content for an entity page.

    Format:
    - YAML frontmatter with metadata
    - Backlinks section with linked notes
    - Relationships section with related entities
    - Timeline with first/last mention dates

    Args:
        data: EntityPageData with all needed information.

    Returns:
        Complete markdown content including frontmatter.
    """
    now = datetime.now(UTC).isoformat(timespec="seconds")

    lines = [
        "---",
        f"entity_name: {data.name}",
        f"entity_type: {data.entity_type}",
        f"generated_at: {now}",
        "auto_generated: true",
        "---",
        "",
        f"# {data.name}",
        "",
        f"**Type:** {data.entity_type}",
    ]

    # Timeline
    if data.first_mention or data.last_mention:
        lines.append("")
        lines.append("## Timeline")
        if data.first_mention:
            lines.append(f"- **First mentioned:** {data.first_mention}")
        if data.last_mention:
            lines.append(f"- **Last mentioned:** {data.last_mention}")

    # Backlinks
    if data.backlinks:
        lines.append("")
        lines.append("## Mentions")
        lines.append("")
        for bl in data.backlinks:
            note_id = bl.get("id", "")
            content_preview = bl.get("content", "")[:100].replace("\n", " ")
            created = bl.get("created_at", "")
            lines.append(f"- [[{note_id}]] ({created}): {content_preview}")
    else:
        lines.append("")
        lines.append("## Mentions")
        lines.append("")
        lines.append("_No linked notes yet._")

    # Relationships
    if data.relationships:
        lines.append("")
        lines.append("## Related Entities")
        lines.append("")
        for rel in data.relationships:
            rel_name = rel.get("entity_name", "")
            co_count = rel.get("co_occurrence_count", 0)
            lines.append(f"- [[{rel_name}]] (co-occurs in {co_count} notes)")

    lines.append("")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════
# MOC page builder
# ══════════════════════════════════════════════════════════════════════════


def build_moc_page(domain: str, notes: list[dict[str, Any]]) -> str:
    """Build a Map of Content page for a memory domain.

    Args:
        domain: The memory domain (e.g. "identity", "health").
        notes: List of note dicts with id, content, importance, created_at.

    Returns:
        Complete markdown content including frontmatter.
    """
    now = datetime.now(UTC).isoformat(timespec="seconds")
    title = domain.replace("_", " ").title()

    lines = [
        "---",
        f"domain: {domain}",
        f"generated_at: {now}",
        "auto_generated: true",
        f"note_count: {len(notes)}",
        "---",
        "",
        f"# MOC - {title}",
        "",
    ]

    if not notes:
        lines.append("_No notes in this domain yet._")
    else:
        # Sort by importance (desc) then recency (desc)
        sorted_notes = sorted(
            notes,
            key=lambda n: (n.get("importance", 0), n.get("created_at", "")),
            reverse=True,
        )

        for note in sorted_notes:
            note_id = note.get("id", "")
            content = note.get("content", "")
            # Use the first line or first 80 chars as a brief description
            first_line = content.split("\n")[0][:80] if content else ""
            importance = note.get("importance", 0)
            created = note.get("created_at", "")[:10]  # just the date
            lines.append(
                f"- [[{note_id}]] — {first_line} "
                f"(importance: {importance:.1f}, {created})"
            )

    lines.append("")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════
# Session note builder
# ══════════════════════════════════════════════════════════════════════════


def build_session_note(
    bridge: dict[str, Any],
    session_date: str,
) -> str:
    """Build a session note from a bridge file.

    Args:
        bridge: Dict with bridge data (topics, emotional_trajectory, etc).
        session_date: ISO date string for the session.

    Returns:
        Complete markdown content including frontmatter.
    """
    now = datetime.now(UTC).isoformat(timespec="seconds")
    topics = bridge.get("topics", [])
    emotional = bridge.get("emotional_trajectory", "")
    open_threads = bridge.get("open_threads", [])
    session_id = bridge.get("session_id", "")

    topic_slug = "_".join(topics[:3]) if topics else "session"
    topic_slug = re.sub(r"[^\w]+", "_", topic_slug)[:40]

    lines = [
        "---",
        f"session_id: {session_id}",
        f"session_date: {session_date}",
        f"generated_at: {now}",
        "auto_generated: true",
        "---",
        "",
        f"# Session: {session_date}",
        "",
    ]

    if topics:
        lines.append("## Topics")
        lines.append("")
        for topic in topics:
            lines.append(f"- {topic}")
        lines.append("")

    if emotional:
        lines.append("## Emotional Trajectory")
        lines.append("")
        lines.append(str(emotional))
        lines.append("")

    if open_threads:
        lines.append("## Open Threads")
        lines.append("")
        for thread in open_threads:
            lines.append(f"- {thread}")
        lines.append("")

    return "\n".join(lines)


def build_session_index(sessions: list[dict[str, Any]]) -> str:
    """Build the session index page listing all sessions.

    Args:
        sessions: List of session dicts with date, topics, path.

    Returns:
        Complete markdown content including frontmatter.
    """
    now = datetime.now(UTC).isoformat(timespec="seconds")

    lines = [
        "---",
        f"generated_at: {now}",
        "auto_generated: true",
        f"session_count: {len(sessions)}",
        "---",
        "",
        "# Session Index",
        "",
    ]

    if not sessions:
        lines.append("_No sessions recorded yet._")
    else:
        # Group by year/month
        by_month: dict[str, list[dict[str, Any]]] = {}
        for session in sorted(sessions, key=lambda s: s.get("date", ""), reverse=True):
            date = session.get("date", "")[:7]  # YYYY-MM
            by_month.setdefault(date, []).append(session)

        for month, month_sessions in by_month.items():
            lines.append(f"## {month}")
            lines.append("")
            for session in month_sessions:
                date = session.get("date", "")
                topics = session.get("topics", [])
                topics_str = ", ".join(topics) if topics else "general"
                note_name = session.get("note_name", "")
                if note_name:
                    lines.append(f"- [[{note_name}]] ({date}): {topics_str}")
                else:
                    lines.append(f"- {date}: {topics_str}")
            lines.append("")

    lines.append("")
    return "\n".join(lines)
