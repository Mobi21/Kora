"""Custom LangGraph reducers for graph state fields.

Reducers define how state values are merged when multiple nodes
update the same field. These handle:
- Conversation messages (unbounded -- context budget tiers handle overflow)
- Working memory workspace with FIFO and capacity limits
- Reasoning trace with bounded history
- Skills accumulation (session-persistent union)
- Bounded errors, node outputs, and tool call history

Reference: LangGraph Channel documentation
"""

from datetime import UTC, datetime
from typing import Any

from langgraph.graph.message import add_messages

# =============================================================================
# Last-Value-Wins Reducers
# =============================================================================


def last_value_list_reducer(
    existing: list[Any] | None,
    new: list[Any] | None,
) -> list[Any]:
    """Last-value-wins reducer for list fields replaced (not appended) each turn.

    Without a reducer, LangGraph's LastValue channel rejects concurrent writes
    from parallel fan-out branches.  This converts the channel to a
    BinaryOperatorAggregate which merges instead of rejecting.

    If *new* is provided (even ``[]``), it replaces *existing*.
    If *new* is ``None`` (node did not write this key), keep *existing*.
    """
    if new is not None:
        return list(new)
    return list(existing) if existing is not None else []


def or_bool_reducer(existing: bool, new: bool) -> bool:
    """OR reducer for boolean flags.

    When parallel branches both write to a bool field,
    returns True if either value is True. Used for flags like
    guardrail_activated where any branch setting True should persist.
    """
    return existing or new


def last_value_bool_reducer(existing: bool, new: bool) -> bool:
    """Last-value-wins reducer for boolean flags.

    Unlike or_bool_reducer, this allows resetting a flag to False.
    Parallel branches: last writer wins.
    """
    return new


def last_value_string_reducer(
    existing: str | None,
    new: str | None,
) -> str:
    """Last-value-wins reducer for string fields.

    Keeps the last non-None value. Returns empty string if both None.
    """
    if new is not None:
        return str(new)
    return str(existing) if existing is not None else ""


# =============================================================================
# Messages Reducer (unbounded -- context budget tiers manage overflow)
# =============================================================================


def _get_msg_role(msg: Any) -> str:
    """Extract role from a message (dict or LangGraph message object)."""
    if isinstance(msg, dict):
        return msg.get("role", "")
    # LangGraph message objects: HumanMessage, AIMessage, ToolMessage, SystemMessage
    type_attr = getattr(msg, "type", "")
    if type_attr == "human":
        return "user"
    if type_attr == "ai":
        return "assistant"
    if type_attr == "tool":
        return "tool"
    if type_attr == "system":
        return "system"
    return str(type_attr)


def _msg_has_tool_use(msg: Any) -> bool:
    """Check if a message contains tool_use blocks.

    Handles both dict-format and LangGraph AIMessage objects.
    """
    if isinstance(msg, dict):
        for key in ("content", "content_blocks"):
            content = msg.get(key)
            if isinstance(content, list):
                if any(
                    isinstance(b, dict) and b.get("type") == "tool_use"
                    for b in content
                ):
                    return True
        return False

    # LangGraph AIMessage: check tool_calls attribute
    tool_calls = getattr(msg, "tool_calls", None)
    if tool_calls:
        return True

    # Also check content blocks
    content = getattr(msg, "content", "")
    if isinstance(content, list):
        return any(isinstance(b, dict) and b.get("type") == "tool_use" for b in content)
    return False


def _extract_tool_use_ids(msg: Any) -> set[str]:
    """Extract tool_use block IDs from an assistant message.

    Handles both dict-format messages (with ``content`` / ``content_blocks``
    block lists) and LangGraph AIMessage objects (with a ``tool_calls``
    attribute).
    """
    ids: set[str] = set()

    if isinstance(msg, dict):
        for key in ("content", "content_blocks"):
            val = msg.get(key)
            if isinstance(val, list):
                for b in val:
                    if (
                        isinstance(b, dict)
                        and b.get("type") == "tool_use"
                        and b.get("id")
                    ):
                        ids.add(str(b["id"]))
        return ids

    # LangGraph AIMessage: tool_calls is a list of dicts with "id" keys
    tool_calls = getattr(msg, "tool_calls", None)
    if tool_calls:
        for call in tool_calls:
            if isinstance(call, dict):
                call_id = call.get("id")
                if call_id:
                    ids.add(str(call_id))
            else:
                call_id = getattr(call, "id", None)
                if call_id:
                    ids.add(str(call_id))

    # Also check content blocks on the message object
    content = getattr(msg, "content", None)
    if isinstance(content, list):
        for b in content:
            if (
                isinstance(b, dict)
                and b.get("type") == "tool_use"
                and b.get("id")
            ):
                ids.add(str(b["id"]))

    return ids


def _extract_tool_result_ids(msg: Any) -> set[str]:
    """Extract tool_result IDs from a user/tool message.

    Handles:
    - dict messages with role=user and content list containing tool_result blocks
    - dict messages with role=tool and tool_call_id field
    - LangGraph ToolMessage objects (with tool_call_id attribute)
    """
    ids: set[str] = set()

    if isinstance(msg, dict):
        # dict-format user message with content block list
        for key in ("content", "content_blocks"):
            content = msg.get(key)
            if isinstance(content, list):
                for b in content:
                    if (
                        isinstance(b, dict)
                        and b.get("type") == "tool_result"
                        and b.get("tool_use_id")
                    ):
                        ids.add(str(b["tool_use_id"]))
        # dict-format tool-role message (OpenAI style)
        if msg.get("role") == "tool":
            tool_call_id = msg.get("tool_call_id")
            if tool_call_id:
                ids.add(str(tool_call_id))
        return ids

    # LangGraph ToolMessage: look at tool_call_id attribute
    tool_call_id = getattr(msg, "tool_call_id", None)
    if tool_call_id:
        ids.add(str(tool_call_id))

    # Also scan content blocks on the message object
    content = getattr(msg, "content", None)
    if isinstance(content, list):
        for b in content:
            if (
                isinstance(b, dict)
                and b.get("type") == "tool_result"
                and b.get("tool_use_id")
            ):
                ids.add(str(b["tool_use_id"]))

    return ids


def ensure_tool_pair_integrity(messages: list[Any]) -> list[Any]:
    """Enforce tool_use / tool_result pairing across the full message list.

    MiniMax (and Anthropic-compatible APIs more broadly) requires that every
    assistant message containing ``tool_use`` blocks is followed by a user /
    tool message carrying the matching ``tool_result`` blocks — and that
    every ``tool_result`` has a preceding ``tool_use`` with the same ID.

    Previous versions only scanned for a safe *starting* boundary, which
    allowed broken pairs mid-stream to persist in state. Every turn would
    then re-log an "orphaned tool_result" warning from the LLM layer. This
    implementation mirrors the ID-matching algorithm in
    ``kora_v2.llm.minimax.MiniMaxProvider.cleanup_incomplete_messages`` but
    handles both dict-format messages and LangGraph message objects so that
    state never holds orphans in the first place.

    The algorithm:

    1. Strip trailing dangling assistant messages that contain ``tool_use``
       blocks with no results following.
    2. Scan the full list. For each assistant with ``tool_use`` blocks,
       collect all consecutive following user/tool messages that carry
       ``tool_result`` IDs. If the assistant's ``tool_use`` IDs are a
       subset of the collected ``tool_result`` IDs, keep the full pair.
       Otherwise, drop the assistant and any partial results.
    3. Drop standalone ``tool_result`` messages that have no matching
       preceding ``tool_use``.

    Args:
        messages: List of messages (dict or LangGraph message objects).

    Returns:
        Messages with all broken tool_use/tool_result pairs removed.
    """
    if not messages:
        return messages

    cleaned: list[Any] = list(messages)

    # Pass 1: strip trailing dangling assistant messages with tool_use.
    while cleaned:
        last = cleaned[-1]
        if _get_msg_role(last) != "assistant":
            break
        if _msg_has_tool_use(last):
            cleaned.pop()
        else:
            break

    # Pass 2: full-list scan for orphaned tool_use / tool_result pairs.
    result: list[Any] = []
    i = 0
    n = len(cleaned)
    while i < n:
        msg = cleaned[i]
        role = _get_msg_role(msg)

        if role == "assistant" and _msg_has_tool_use(msg):
            use_ids = _extract_tool_use_ids(msg)

            # Collect all consecutive following user/tool messages that
            # carry tool_result IDs. Results may be batched or split across
            # multiple messages (one per tool call).
            j = i + 1
            result_ids: set[str] = set()
            while j < n:
                candidate = cleaned[j]
                candidate_role = _get_msg_role(candidate)
                if candidate_role not in ("user", "tool"):
                    break
                candidate_ids = _extract_tool_result_ids(candidate)
                if not candidate_ids:
                    break
                result_ids |= candidate_ids
                j += 1

            if use_ids and use_ids <= result_ids:
                # Complete pair — keep assistant and all collected results.
                result.append(msg)
                for k in range(i + 1, j):
                    result.append(cleaned[k])
                i = j
                continue

            # Broken pair — drop the assistant and any partial results.
            i = j
            continue

        if role in ("user", "tool") and _extract_tool_result_ids(msg):
            # Standalone orphan tool_result — drop it.
            i += 1
            continue

        result.append(msg)
        i += 1

    return result


def add_messages_reducer(
    existing: list[dict[str, Any]] | None,
    new: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Delegate to LangGraph's add_messages (append-only merge).

    Tool pair integrity is enforced at LLM-send time in the ``think`` node,
    not in the reducer: mid-loop between ``think`` writing the assistant
    tool_use message and ``tool_loop`` writing the tool_result messages,
    the pair is transiently "dangling" by definition. If the reducer
    stripped dangling tool_use writes, the assistant message would vanish
    before tool_loop ran, the subsequent tool_results would become orphans
    and get stripped too, and the graph would loop forever on the same
    message count. Keep state append-only; sanitize on the way out.

    Args:
        existing: Current messages (may be None).
        new: New messages to add (may be None).

    Returns:
        Combined messages.
    """
    return add_messages(existing or [], new or [])


# =============================================================================
# Skills Reducer (session-persistent union)
# =============================================================================


def merge_skills_reducer(
    existing: list[str] | None,
    new: list[str] | None,
) -> list[str]:
    """Union reducer -- adds new skills to existing, no duplicates, no replacement.

    Skills accumulate across the session. Once a skill is active,
    it stays active for subsequent turns without re-detection.

    Args:
        existing: Current active skills (may be None).
        new: New skills to add (may be None).

    Returns:
        Merged skill list with no duplicates.
    """
    if existing is None:
        existing = []
    if new is None:
        return list(existing)
    merged = list(existing)
    for skill in new:
        if skill not in merged:
            merged.append(skill)
    return merged


# =============================================================================
# Workspace Reducer
# =============================================================================

MAX_WORKSPACE_SIZE = 7


def workspace_reducer(
    existing: list[dict[str, Any]] | None,
    new: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """FIFO workspace with capacity limit and age decay.

    Based on Baddeley's working memory model:
    - Max 7 items (magical number 7 +/- 2)
    - New items added to front (most recent)
    - Oldest items dropped when capacity exceeded
    - Items decay in salience over time

    Args:
        existing: Current workspace items (may be None).
        new: New items to add (may be None).

    Returns:
        Merged workspace respecting capacity limits.
    """
    if existing is None and new is None:
        return []
    if existing is None:
        existing = []
    if new is None:
        return existing[:MAX_WORKSPACE_SIZE]

    # Age existing items (decay salience by 10%)
    aged_existing: list[dict[str, Any]] = []
    for item in existing:
        aged_item = dict(item)
        current_salience = aged_item.get("salience_score", 0.5)
        aged_item["salience_score"] = max(0.0, current_salience * 0.9)
        aged_existing.append(aged_item)

    # Combine: new items first, then aged existing
    combined = list(new) + aged_existing

    # Deduplicate by content (keep first occurrence = newest)
    seen_content: set[str] = set()
    unique_items: list[dict[str, Any]] = []
    for item in combined:
        content = item.get("content", "")
        if content not in seen_content:
            seen_content.add(content)
            unique_items.append(item)

    # Sort by salience (highest first) for selection
    unique_items.sort(key=lambda x: -x.get("salience_score", 0.5))

    return unique_items[:MAX_WORKSPACE_SIZE]


# =============================================================================
# Trace Reducer
# =============================================================================

MAX_TRACE_SIZE = 20


def trace_reducer(
    existing: list[dict[str, Any]] | None,
    new: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """FIFO reasoning trace with bounded history.

    Maintains a rolling log of reasoning steps, limited to
    prevent unbounded growth in state size.

    Args:
        existing: Current trace entries (may be None).
        new: New trace entries to add (may be None).

    Returns:
        Combined trace limited to MAX_TRACE_SIZE entries.
    """
    if existing is None and new is None:
        return []
    if existing is None:
        existing = []
    if new is None:
        return existing[-MAX_TRACE_SIZE:]

    combined = existing + new
    return combined[-MAX_TRACE_SIZE:]


# =============================================================================
# Bounded Errors Reducer
# =============================================================================

MAX_ERRORS = 20


def bounded_errors_reducer(
    existing: list[str] | None,
    new: list[str] | None,
) -> list[str]:
    """Bounded errors list with oldest-first eviction.

    Keeps the most recent errors to prevent unbounded growth.

    Args:
        existing: Current errors (may be None).
        new: New errors to add (may be None).

    Returns:
        Combined errors limited to MAX_ERRORS (most recent kept).
    """
    if existing is None and new is None:
        return []
    if existing is None:
        existing = []
    if new is None:
        return existing[-MAX_ERRORS:]

    combined = existing + new
    return combined[-MAX_ERRORS:]


# =============================================================================
# Bounded Node Outputs Reducer
# =============================================================================

MAX_NODE_OUTPUTS = 10


def bounded_node_outputs_reducer(
    existing: dict[str, Any] | None,
    new: dict[str, Any] | None,
) -> dict[str, Any]:
    """Bounded node outputs dict with oldest-first eviction.

    Merges new outputs into existing, keeping only the most recent
    MAX_NODE_OUTPUTS entries by insertion order.

    Args:
        existing: Current outputs (may be None).
        new: New outputs to merge (may be None).

    Returns:
        Combined outputs limited to MAX_NODE_OUTPUTS entries.
    """
    if existing is None and new is None:
        return {}
    if existing is None:
        existing = {}
    if new is None:
        return dict(list(existing.items())[-MAX_NODE_OUTPUTS:])

    combined = {**existing, **new}
    items = list(combined.items())
    return dict(items[-MAX_NODE_OUTPUTS:])


# =============================================================================
# Append List Reducers (for tool results, tool call history)
# =============================================================================

MAX_TOOL_CALL_HISTORY = 50


def append_list_reducer(
    existing: list[Any] | None,
    new: list[Any] | None,
) -> list[Any]:
    """Append-only list reducer for accumulating items across turns.

    Used for session_tool_results which grow over the session.

    Args:
        existing: Current items (may be None).
        new: New items to append (may be None).

    Returns:
        Combined list with new items appended.
    """
    if existing is None and new is None:
        return []
    if existing is None:
        return list(new) if new else []
    if new is None:
        return list(existing)

    return list(existing) + list(new)


def bounded_append_list_reducer(
    existing: list[Any] | None,
    new: list[Any] | None,
) -> list[Any]:
    """Append-only list reducer with bounded size for tool call history.

    Keeps the most recent MAX_TOOL_CALL_HISTORY items.

    Args:
        existing: Current items (may be None).
        new: New items to append (may be None).

    Returns:
        Combined list capped at MAX_TOOL_CALL_HISTORY (most recent kept).
    """
    if existing is None and new is None:
        return []
    if existing is None:
        existing = []
    if new is None:
        return existing[-MAX_TOOL_CALL_HISTORY:]

    combined = list(existing) + list(new)
    return combined[-MAX_TOOL_CALL_HISTORY:]


# =============================================================================
# Helper Functions
# =============================================================================


def create_workspace_item_dict(
    content: str,
    source: str,
    salience_score: float = 0.5,
) -> dict[str, Any]:
    """Create a workspace item dict for state updates.

    Args:
        content: The item content.
        source: Where this item came from.
        salience_score: 0-1, current salience/relevance.

    Returns:
        Workspace item dict ready for state update.
    """
    return {
        "content": content,
        "source": source,
        "salience_score": salience_score,
        "added_at": datetime.now(UTC).isoformat(),
    }


def create_trace_entry_dict(
    step: str,
    content: str,
    turn_number: int = 0,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a trace entry dict for state updates.

    Args:
        step: Name of the processing step.
        content: Description of what happened.
        turn_number: Current turn number.
        metadata: Optional additional metadata.

    Returns:
        Trace entry dict ready for state update.
    """
    return {
        "step": step,
        "content": content,
        "turn_number": turn_number,
        "timestamp": datetime.now(UTC).isoformat(),
        "metadata": metadata or {},
    }
