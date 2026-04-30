"""4-stage context compaction pipeline for Kora V2.

Each stage is more aggressive than the last, triggered by BudgetTier.

Stage 1 — PRUNE:        Observation masking (mask old tool results, strip thinking)
Stage 2 — SUMMARIZE:    Structured 6-section LLM summary of middle turns
Stage 3 — AGGRESSIVE:   UPDATE mode — merge existing summary + new turns, tiny window
Stage 4 — HARD_STOP:    Heuristic bridge note (no LLM), handled by session manager

Main entry point: run_compaction(messages, tier, llm)
"""

from __future__ import annotations

import copy
from typing import Any

import structlog

from kora_v2.context.budget import BudgetTier, count_messages_tokens
from kora_v2.core.models import CompactionResult, SessionBridge

logger = structlog.get_logger()

# ── Internal helpers ────────────────────────────────────────────────────────────


def _find_turns(messages: list[dict]) -> list[list[int]]:
    """Group message indices into turns.

    A turn = one user message + all following assistant/tool messages until
    the next user message.

    Returns:
        List of turns, where each turn is a list of message indices.
    """
    turns: list[list[int]] = []
    current: list[int] = []

    for i, msg in enumerate(messages):
        role = msg.get("role", "")
        if role == "user":
            if current:
                turns.append(current)
            current = [i]
        else:
            current.append(i)

    if current:
        turns.append(current)

    return turns


def _get_tool_name_for_call_id(
    messages: list[dict], call_index: int, tool_call_id: str
) -> str:
    """Find the tool name for a tool_call_id by searching preceding assistant messages.

    Scans backwards from call_index to find an assistant message with a
    tool_use block matching tool_call_id.

    Returns:
        Tool name string, or "unknown_tool" if not found.
    """
    for i in range(call_index - 1, -1, -1):
        msg = messages[i]
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            if (
                isinstance(block, dict)
                and block.get("type") == "tool_use"
                and block.get("id") == tool_call_id
            ):
                return block.get("name", "unknown_tool")
    return "unknown_tool"


def _first_line(text: str) -> str:
    """Return the first non-empty line of text, truncated to 80 chars."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:80]
    return text[:80]


def _mask_single_tool_result(
    messages: list[dict], idx: int, tool_call_id: str, content: str
) -> dict:
    """Build a masked version of a tool result message."""
    tool_name = _get_tool_name_for_call_id(messages, idx, tool_call_id)
    first = _first_line(content)
    return {
        **{k: v for k, v in messages[idx].items() if k != "content"},
        "content": f"[result from {tool_name}: {first}...]",
    }


# ── Stage 1: Observation Masking ───────────────────────────────────────────────


def mask_observations(
    messages: list[dict],
    preserve_last_n: int = 10,
) -> list[dict]:
    """Replace old tool results and strip old thinking blocks.

    For messages OUTSIDE the last N turns:
    - Tool result messages (role="tool") with content > 200 chars:
      Replace content with: "[result from {tool_name}: {first_line}...]"
    - Assistant messages with "thinking" type blocks:
      Remove the thinking blocks, keep only other blocks

    Messages in the last N turns are untouched.
    A "turn" = one user message + all subsequent assistant/tool messages
    until the next user message.

    Args:
        messages: The conversation message list.
        preserve_last_n: Number of turns (from the end) to leave untouched.

    Returns:
        New message list (deep copy, original untouched).
    """
    result = copy.deepcopy(messages)
    turns = _find_turns(result)

    if not turns:
        return result

    # Determine which turn indices are "old" (outside the last N turns)
    old_turns = turns[: max(0, len(turns) - preserve_last_n)]

    # Build a set of indices that are in old turns
    old_indices: set[int] = set()
    for turn in old_turns:
        old_indices.update(turn)

    masked_count = 0

    for idx in old_indices:
        msg = result[idx]
        role = msg.get("role", "")

        # Mask large tool results (OpenAI-shape: role="tool", flat string).
        if role == "tool":
            content = msg.get("content", "")
            if isinstance(content, str) and len(content) > 200:
                tool_call_id = msg.get("tool_call_id", "")
                result[idx] = _mask_single_tool_result(messages, idx, tool_call_id, content)
                masked_count += 1

        # Mask large tool results (Anthropic-shape: role="user" with
        # ``type="tool_result"`` content blocks). MiniMax's
        # ``_format_messages`` normalises to this shape on every call, so
        # the bulk of tokens in a heavy tool session live here — missing
        # this path is why PRUNE tier failed to reclaim any real space.
        elif role == "user":
            content = msg.get("content", "")
            if isinstance(content, list):
                new_blocks: list[Any] = []
                changed = False
                for block in content:
                    if (
                        isinstance(block, dict)
                        and block.get("type") == "tool_result"
                    ):
                        block_content = block.get("content", "")
                        # Content may be a string or a list of text/json blocks.
                        if isinstance(block_content, list):
                            block_text = " ".join(
                                (b.get("text", "") if isinstance(b, dict) else str(b))
                                for b in block_content
                            )
                        else:
                            block_text = str(block_content)
                        if len(block_text) > 200:
                            tool_name = _get_tool_name_for_call_id(
                                messages, idx, block.get("tool_use_id", "")
                            )
                            new_blocks.append(
                                {
                                    **block,
                                    "content": f"[result from {tool_name}: {_first_line(block_text)}...]",
                                }
                            )
                            changed = True
                            continue
                    new_blocks.append(block)
                if changed:
                    result[idx] = {**msg, "content": new_blocks}
                    masked_count += 1

        # Strip thinking blocks from assistant messages
        elif role == "assistant":
            content = msg.get("content", "")
            if isinstance(content, list):
                non_thinking = [b for b in content if b.get("type") != "thinking"]
                if len(non_thinking) != len(content):
                    # Had some thinking blocks — remove them
                    result[idx] = {**msg, "content": non_thinking}
                    masked_count += 1

    logger.debug(
        "compaction.mask_observations",
        total_messages=len(result),
        old_turns=len(old_turns),
        masked_count=masked_count,
    )
    return result


# ── Stage 2: Structured Summary ─────────────────────────────────────────────────

_SUMMARY_SYSTEM_PROMPT = """\
Summarize this conversation into the following sections.
Every section MUST be populated or explicitly marked [none].
Preserve: exact file paths, error messages verbatim, names, dates,
numeric values, and decision reasoning.

## Goal
[What the user is trying to accomplish]

## Progress
### Done
- [Completed items with key results]
### In Progress
- [Active work]
### Blocked
- [Items needing input]

## Key Decisions
- [Decision]: [Choice] — [Why]

## Emotional Context
[Mood trajectory, relationship dynamics, important moments]

## Open Threads
- [Unresolved topics, pending questions]

## Critical Context
[Names, dates, commitments, constraints that MUST survive]\
"""


def _messages_to_text(messages: list[dict]) -> str:
    """Convert a list of messages to a single text block for the LLM."""
    parts: list[str] = []
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")

        if isinstance(content, list):
            # Flatten content blocks to text
            text_parts: list[str] = []
            for block in content:
                if isinstance(block, dict):
                    btype = block.get("type", "")
                    if btype == "text":
                        text_parts.append(block.get("text", ""))
                    elif btype == "tool_use":
                        name = block.get("name", "")
                        inp = block.get("input", {})
                        text_parts.append(f"[tool_use: {name} {inp}]")
                    elif btype == "thinking":
                        text_parts.append(f"[thinking: {block.get('thinking', '')[:100]}...]")
                    elif btype == "tool_result":
                        text_parts.append(f"[tool_result: {block.get('content', '')[:200]}]")
            content_str = " ".join(text_parts)
        else:
            content_str = str(content)

        parts.append(f"{role.upper()}: {content_str}")

    return "\n\n".join(parts)


def _tool_pair_safe_boundary(
    messages: list[dict], ideal_end: int, direction: str = "back"
) -> int:
    """Adjust boundary to avoid splitting tool_call from tool_result pairs.

    When truncating at ideal_end, make sure we don't leave a tool_call without
    its corresponding tool_result (or vice-versa).

    Args:
        messages: Full message list.
        ideal_end: Ideal slice index.
        direction: "back" = search backwards (for preserve_last), "fwd" = forward.

    Returns:
        Adjusted index that respects tool pair boundaries.
    """
    if ideal_end <= 0 or ideal_end >= len(messages):
        return ideal_end

    # Check if message at ideal_end-1 is a tool_call that needs a following tool result
    msg = messages[ideal_end - 1]
    if msg.get("role") == "assistant":
        content = msg.get("content", [])
        if isinstance(content, list):
            has_tool_use = any(
                isinstance(b, dict) and b.get("type") == "tool_use" for b in content
            )
            if has_tool_use and direction == "back":
                # Move boundary back by 1 to exclude the dangling tool_call
                return ideal_end - 1

    return ideal_end


async def create_structured_summary(
    messages: list[dict],
    llm: Any,
    preserve_first_n: int = 2,
    preserve_last_n: int = 10,
) -> str:
    """Summarize middle turns using a 6-section template via LLM.

    Args:
        messages: Full conversation history.
        llm: LLM provider with a .generate() method.
        preserve_first_n: Number of messages from the start to exclude from summary.
        preserve_last_n: Number of messages from the end to exclude from summary.

    Returns:
        The structured summary text (str).
    """
    total = len(messages)
    end_of_middle = max(preserve_first_n, total - preserve_last_n)
    middle = messages[preserve_first_n:end_of_middle]

    if not middle:
        return "## Goal\n[none]\n\n## Progress\n### Done\n- [none]\n### In Progress\n- [none]\n### Blocked\n- [none]\n\n## Key Decisions\n- [none]\n\n## Emotional Context\n[none]\n\n## Open Threads\n- [none]\n\n## Critical Context\n[none]"

    conversation_text = _messages_to_text(middle)

    try:
        result = await llm.generate(
            messages=[{"role": "user", "content": conversation_text}],
            system=_SUMMARY_SYSTEM_PROMPT,
            temperature=0.1,
            max_tokens=2000,
        )
        return result.content
    except Exception as exc:
        logger.warning("compaction_llm_failed", stage="structured_summary", error=str(exc))
        return f"## Goal\n[Summary generation failed: {exc!s}]\n\n## Critical Context\n[Conversation continues from {len(middle)} messages]"


async def apply_structured_compaction(
    messages: list[dict],
    llm: Any,
    preserve_first_n: int = 2,
    preserve_last_n: int = 10,
) -> CompactionResult:
    """Apply structured compaction: first N + summary + last N.

    1. Keep first preserve_first_n messages unchanged
    2. Summarize middle messages via create_structured_summary
    3. Insert summary as a system message
    4. Keep last preserve_last_n messages unchanged
    5. Respect tool pair boundaries — never split tool_call from tool_result

    Args:
        messages: Full conversation history.
        llm: LLM provider.
        preserve_first_n: Messages from start to keep verbatim.
        preserve_last_n: Messages from end to keep verbatim.

    Returns:
        CompactionResult with new message list and metadata.
    """
    tokens_before = count_messages_tokens(messages)

    total = len(messages)
    # Determine boundaries
    start_keep = min(preserve_first_n, total)
    end_keep = max(start_keep, total - preserve_last_n)

    # Adjust to avoid splitting tool pairs
    end_keep = _tool_pair_safe_boundary(messages, end_keep, direction="back")

    first_part = messages[:start_keep]
    last_part = messages[end_keep:]
    middle = messages[start_keep:end_keep]

    messages_removed = len(middle)

    summary = await create_structured_summary(
        messages, llm, preserve_first_n=start_keep, preserve_last_n=len(last_part) or preserve_last_n
    )

    summary_msg: dict[str, Any] = {"role": "system", "content": summary}

    new_messages = first_part + [summary_msg] + last_part

    tokens_after = count_messages_tokens(new_messages)

    logger.info(
        "compaction.structured_summary",
        tokens_before=tokens_before,
        tokens_after=tokens_after,
        messages_removed=messages_removed,
    )

    return CompactionResult(
        stage="structured_summary",
        messages=new_messages,
        tokens_before=tokens_before,
        tokens_after=tokens_after,
        messages_removed=messages_removed,
        summary_text=summary,
    )


# ── Stage 3: Aggressive Re-compress ────────────────────────────────────────────

_AGGRESSIVE_UPDATE_PROMPT = """\
You have an existing conversation summary and new turns that occurred after it.
UPDATE the summary by merging the new turns into it.
Do NOT rebuild from scratch — incorporate new information into the existing structure.
Keep the same 6-section format. Maximum 1500 tokens total.

Existing summary:
{existing_summary}

New turns:
{new_turns}\
"""


async def aggressive_recompress(
    messages: list[dict],
    existing_summary: str,
    llm: Any,
) -> CompactionResult:
    """UPDATE mode: merge existing summary with new turns, shrink recent window.

    1. Keep first 2 messages (anchored)
    2. Send existing_summary + turns since last summary to LLM
    3. Ask LLM to UPDATE the summary (merge, not rebuild). Max 1500 tokens, temp 0.05
    4. Keep only last 3 turns

    Args:
        messages: Full conversation history (after masking).
        existing_summary: Prior structured summary to merge into.
        llm: LLM provider.

    Returns:
        CompactionResult with aggressively compressed messages.
    """
    tokens_before = count_messages_tokens(messages)

    anchor_msgs = messages[:2]
    turns = _find_turns(messages)

    # Keep last 3 turns
    last_3_turns = turns[-3:] if len(turns) >= 3 else turns
    last_3_indices: set[int] = set()
    for t in last_3_turns:
        last_3_indices.update(t)

    last_3_messages = [messages[i] for i in sorted(last_3_indices)]

    # Middle turns = everything between anchor and last 3
    anchor_end = len(anchor_msgs)
    first_last3_idx = min(last_3_indices) if last_3_indices else len(messages)
    new_turns_msgs = messages[anchor_end:first_last3_idx]
    new_turns_text = _messages_to_text(new_turns_msgs) if new_turns_msgs else ""

    prompt_content = _AGGRESSIVE_UPDATE_PROMPT.format(
        existing_summary=existing_summary,
        new_turns=new_turns_text,
    )

    try:
        result = await llm.generate(
            messages=[{"role": "user", "content": prompt_content}],
            system="You are a concise summarizer. Merge new turns into the existing summary.",
            temperature=0.05,
            max_tokens=1500,
        )
        updated_summary = result.content
    except Exception as exc:
        logger.warning("compaction_llm_failed", stage="aggressive_recompress", error=str(exc))
        updated_summary = existing_summary  # Fall back to existing summary
    summary_msg: dict[str, Any] = {"role": "system", "content": updated_summary}

    new_messages = anchor_msgs + [summary_msg] + last_3_messages
    tokens_after = count_messages_tokens(new_messages)

    messages_removed = len(messages) - len(new_messages)

    logger.info(
        "compaction.aggressive_recompress",
        tokens_before=tokens_before,
        tokens_after=tokens_after,
        messages_removed=messages_removed,
    )

    return CompactionResult(
        stage="aggressive_recompress",
        messages=new_messages,
        tokens_before=tokens_before,
        tokens_after=tokens_after,
        messages_removed=messages_removed,
        summary_text=updated_summary,
    )


# ── Stage 4: HARD_STOP Bridge ───────────────────────────────────────────────────


def build_hard_stop_bridge(messages: list[dict], session_id: str) -> SessionBridge:
    """Build a bridge note for HARD_STOP — no LLM call, use heuristics.

    - summary: Concatenate last 3 user messages as topic indicators
    - open_threads: Extract questions (messages ending with ?) from last 10 messages
    - emotional_trajectory: Simple — "ongoing conversation" (no LLM to assess)

    Args:
        messages: Full conversation history.
        session_id: Current session identifier.

    Returns:
        SessionBridge model.
    """
    # Collect last 3 user messages for summary
    user_msgs = [m for m in messages if m.get("role") == "user"]
    last_3_user = user_msgs[-3:] if len(user_msgs) >= 3 else user_msgs

    summary_parts: list[str] = []
    for msg in last_3_user:
        content = msg.get("content", "")
        if isinstance(content, str):
            summary_parts.append(content[:200])
        elif isinstance(content, list):
            text = " ".join(
                b.get("text", "")
                for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            )
            summary_parts.append(text[:200])

    summary = " | ".join(summary_parts) if summary_parts else "Context limit reached."

    # Extract questions from last 10 messages
    last_10 = messages[-10:]
    open_threads: list[str] = []
    for msg in last_10:
        content = msg.get("content", "")
        text = ""
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = " ".join(
                b.get("text", "")
                for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            )

        text = text.strip()
        if text.endswith("?"):
            open_threads.append(text[:300])

    logger.info(
        "compaction.hard_stop_bridge",
        session_id=session_id,
        open_threads_count=len(open_threads),
    )

    return SessionBridge(
        session_id=session_id,
        summary=summary,
        open_threads=open_threads,
        emotional_trajectory="ongoing conversation",
    )


def emergency_compaction(
    messages: list[dict],
    *,
    session_id: str,
    existing_summary: str | None = None,
    preserve_first_n: int = 2,
    preserve_last_n: int = 8,
) -> CompactionResult:
    """Compact at HARD_STOP without making another LLM call.

    This is intentionally heuristic: when the provider is already near
    context capacity, asking the model to summarize the full transcript can
    fail with the same overflow we are trying to avoid. Keep a small anchor,
    a bridge note, and the most recent complete tool-safe tail.
    """
    tokens_before = count_messages_tokens(messages)
    total = len(messages)
    start_keep = min(preserve_first_n, total)
    tail_start = max(start_keep, total - preserve_last_n)
    tail_start = _tool_pair_safe_boundary(messages, tail_start, direction="back")

    bridge = build_hard_stop_bridge(messages, session_id)
    summary_parts = [
        "## Emergency Context Bridge",
        bridge.summary,
    ]
    if existing_summary:
        summary_parts.extend(["", "## Prior Compaction Summary", existing_summary[:6000]])
    if bridge.open_threads:
        summary_parts.append("")
        summary_parts.append("## Open Threads")
        summary_parts.extend(f"- {thread}" for thread in bridge.open_threads[:6])

    summary_msg: dict[str, Any] = {
        "role": "system",
        "content": "\n".join(summary_parts),
    }
    new_messages = messages[:start_keep] + [summary_msg] + messages[tail_start:]
    tokens_after = count_messages_tokens(new_messages)
    logger.warning(
        "compaction.emergency",
        tokens_before=tokens_before,
        tokens_after=tokens_after,
        messages_removed=max(0, total - len(new_messages)),
    )
    return CompactionResult(
        stage="hard_stop",
        messages=new_messages,
        tokens_before=tokens_before,
        tokens_after=tokens_after,
        messages_removed=max(0, total - len(new_messages)),
        summary_text=summary_msg["content"],
    )


# ── Main Entry Point ────────────────────────────────────────────────────────────


async def run_compaction(
    messages: list[dict],
    tier: BudgetTier,
    llm: Any,
    existing_summary: str | None = None,
) -> CompactionResult | None:
    """Main entry point — route to correct compaction stage based on tier.

    - NORMAL:    return None (no compaction needed)
    - PRUNE:     mask_observations → return CompactionResult
    - SUMMARIZE: mask first, then structured summary → return CompactionResult
    - AGGRESSIVE: mask, then aggressive_recompress → return CompactionResult
    - HARD_STOP: return None (handled separately by session manager via
                 build_hard_stop_bridge)

    Args:
        messages: Current conversation messages.
        tier: Current BudgetTier.
        llm: LLM provider (not used for NORMAL/PRUNE/HARD_STOP).
        existing_summary: Prior summary text (used for AGGRESSIVE stage).

    Returns:
        CompactionResult or None if no compaction needed.
    """
    if tier == BudgetTier.NORMAL:
        return None

    if tier == BudgetTier.HARD_STOP:
        return emergency_compaction(
            messages,
            session_id="unknown",
            existing_summary=existing_summary,
        )

    if tier == BudgetTier.PRUNE:
        tokens_before = count_messages_tokens(messages)
        masked = mask_observations(messages)
        tokens_after = count_messages_tokens(masked)

        masked_count = sum(
            1
            for orig, new in zip(messages, masked)
            if orig != new
        )

        logger.info(
            "compaction.run",
            tier=tier,
            tokens_before=tokens_before,
            tokens_after=tokens_after,
        )

        return CompactionResult(
            stage="observation_masking",
            messages=masked,
            tokens_before=tokens_before,
            tokens_after=tokens_after,
            messages_masked=masked_count,
        )

    if tier == BudgetTier.SUMMARIZE:
        # Mask first, then summarize
        masked = mask_observations(messages)
        result = await apply_structured_compaction(masked, llm)
        # Preserve the original tokens_before (pre-masking)
        tokens_before = count_messages_tokens(messages)
        return CompactionResult(
            stage="structured_summary",
            messages=result.messages,
            tokens_before=tokens_before,
            tokens_after=result.tokens_after,
            messages_removed=result.messages_removed,
            messages_masked=result.messages_masked,
            summary_text=result.summary_text,
        )

    if tier == BudgetTier.AGGRESSIVE:
        masked = mask_observations(messages)
        summary = existing_summary or ""
        result = await aggressive_recompress(masked, existing_summary=summary, llm=llm)
        tokens_before = count_messages_tokens(messages)
        return CompactionResult(
            stage="aggressive_recompress",
            messages=result.messages,
            tokens_before=tokens_before,
            tokens_after=result.tokens_after,
            messages_removed=result.messages_removed,
            summary_text=result.summary_text,
        )

    # Fallback — unknown tier, no compaction
    logger.warning("compaction.unknown_tier", tier=tier)
    return None
